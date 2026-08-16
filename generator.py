"""
generator.py — Universal 2D Apparel Pattern Drafting Engine (v2)
==================================================================
Generates professional CAD pattern pieces from body measurements AND
parsed styling details (cowl, gathers, pleats, asymmetric hems, drop
shoulder) using standard apparel drafting formulas plus true curve
interpolation (cubic Bezier) for armholes, necklines and sleeve caps.

Exports DXF / AAMA files compatible with Optitex, Gerber, and Lectra
via ezdxf, and a labelled 2D blueprint preview PNG via blueprint.py.

Garment types supported:
    dress, kurti, bodice, skirt, shirt, sleeve
    + dynamic style overlays: cowl / wrap, gathers, pleats,
      asymmetric hem, drop shoulder

Author: EJAJ KHAN
"""
from __future__ import annotations

import math
import json
import sqlite3
import hashlib
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

import ezdxf

try:
    import config
except ImportError:  # standalone import fallback
    class _C:
        OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
        CACHE_DIR = os.path.join(os.getcwd(), "cache")
        TEMPLATE_DIR = os.path.join(os.getcwd(), "templates")
        OUTPUT_DIR = os.path.join(os.getcwd(), "output")
        DATABASE_PATH = os.path.join(os.getcwd(), "data", "templates.db")
        SEAM_ALLOWANCE = 1.0
        HEM_ALLOWANCE = 2.5
        AAMA_LAYERS = {
            "CUT": "1", "SEAM": "8", "GRAIN": "4", "NOTCH": "3",
            "INTERNAL": "4", "REFERENCE": "6", "ANNOTATION": "7", "MIRROR": "9",
        }
        EASE_BODICE = {"minimal": 2.0, "standard": 4.0, "loose": 6.0}
        EASE_SKIRT = {"minimal": 2.0, "standard": 3.0, "loose": 5.0}
    config = _C()

# ====================================================================
# DATA STRUCTURES
# ====================================================================

@dataclass
class Measurements:
    """Normalised body measurements in centimetres."""
    bust: float = 0.0
    waist: float = 0.0
    hip: float = 0.0
    shoulder_width: float = 0.0       # across-shoulder
    shoulder_length: float = 0.0      # neck-to-shoulder-tip
    back_length: float = 0.0          # nape to waist (CB)
    front_length: float = 0.0         # shoulder to waist (CF)
    armhole_depth: float = 0.0        # scye depth
    neck_width: float = 0.0
    neck_depth_front: float = 0.0
    neck_depth_back: float = 0.0
    sleeve_length: float = 0.0
    bicep: float = 0.0
    wrist: float = 0.0
    skirt_length: float = 0.0
    dress_length: float = 0.0
    shoulder_to_bust: float = 0.0
    bust_span: float = 0.0            # distance between bust points (apex)
    waist_to_hip: float = 20.0        # typical 18-22cm
    apex_to_apex: float = 0.0
    shoulder_slope: float = 4.0       # cm drop from neck to shoulder tip
    dart_intake_bust: float = 0.0
    dart_intake_waist_front: float = 0.0
    dart_intake_waist_back: float = 0.0
    ease: str = "standard"            # minimal / standard / loose

    def validate(self) -> bool:
        required = ["bust", "waist", "hip"]
        return all(getattr(self, k, 0) > 0 for k in required)

    def missing_keys(self) -> list[str]:
        return [k for k in
                ("bust", "waist", "hip", "shoulder_width", "back_length")
                if getattr(self, k, 0) <= 0]


@dataclass
class StyleDetails:
    """Parsed styling / silhouette details from the uploaded tech pack."""
    silhouette: str = ""                     # e.g. "wrap", "A-line", "fitted"
    has_cowl: bool = False
    has_gathers: bool = False
    gather_locations: list[str] = field(default_factory=list)   # e.g. ["front neckline", "shoulder"]
    has_pleats: bool = False
    pleat_count: int = 0
    asymmetric_hem: bool = False
    drop_shoulder: bool = False
    has_collar: bool = False
    collar_type: str = ""
    closure: str = ""                        # e.g. "wrap tie", "zip", "button"
    size_label: str = ""
    cut_quantities: dict = field(default_factory=dict)   # piece_name -> qty
    measurements_table: dict = field(default_factory=dict)  # raw label->value as seen on sheet
    notes: str = ""

    def is_dynamic_front(self) -> bool:
        """True if the front bodice needs the asymmetric/gathered drafting path."""
        return self.has_cowl or self.has_gathers or self.asymmetric_hem


@dataclass
class Point:
    x: float
    y: float

    def to_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def __add__(self, other: "Point") -> "Point":
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)


@dataclass
class PatternPiece:
    """A single pattern piece (e.g. front bodice, back sleeve)."""
    name: str
    piece_type: str            # "front" | "back" | "sleeve" | "collar" | "facing" | "other"
    outline: list[Point] = field(default_factory=list)      # main cutting line vertices
    outline_curves: dict = field(default_factory=dict)       # {seg_index: [Point,...]} bezier fill-in between outline[i] and outline[i+1]
    seam_line: list[Point] = field(default_factory=list)     # stitching line (inside SA)
    darts: list[dict] = field(default_factory=list)          # each: {start, end, apex}
    notches: list[Point] = field(default_factory=list)
    grainline: Optional[dict] = None                          # {start, end, direction}
    internal_lines: list[dict] = field(default_factory=list)
    fold_lines: list[dict] = field(default_factory=list)      # {points: [Point,Point], label}
    gather_guides: list[dict] = field(default_factory=list)   # {points:[Point,Point], label:"GATHER"}
    pleat_guides: list[dict] = field(default_factory=list)    # {points:[Point,Point], label:"PLEAT FOLD"}
    annotations: list[dict] = field(default_factory=list)     # {pos, text}
    mirror_axis: Optional[dict] = None                        # {start, end}
    cut_qty: int = 1
    meta: dict = field(default_factory=dict)


# ====================================================================
# MATH HELPERS
# ====================================================================

def _dist(p1: Point, p2: Point) -> float:
    return math.hypot(p2.x - p1.x, p2.y - p1.y)

def _midpoint(p1: Point, p2: Point) -> Point:
    return Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)

def _polar(origin: Point, angle_deg: float, length: float) -> Point:
    r = math.radians(angle_deg)
    return Point(
        origin.x + length * math.cos(r),
        origin.y + length * math.sin(r),
    )

def _angle(p1: Point, p2: Point) -> float:
    return math.degrees(math.atan2(p2.y - p1.y, p2.x - p1.x))

def _perp_point(p1: Point, p2: Point, offset: float, from_t: float = 0.5) -> Point:
    """Return a point offset perpendicular to segment p1-p2 at parametric t."""
    mid = Point(p1.x + (p2.x - p1.x) * from_t, p1.y + (p2.y - p1.y) * from_t)
    a = _angle(p1, p2) + 90
    return _polar(mid, a, offset)

def _offset_polyline(points: list[Point], distance: float) -> list[Point]:
    """Offset a polyline outward by *distance* (simple miter join)."""
    if len(points) < 2:
        return list(points)
    result: list[Point] = []
    n = len(points)
    for i in range(n):
        p_prev = points[i - 1] if i > 0 else points[0]
        p_curr = points[i]
        p_next = points[i + 1] if i < n - 1 else points[-1]

        a_in = _angle(p_prev, p_curr) if i > 0 else _angle(p_curr, p_next)
        a_out = _angle(p_curr, p_next) if i < n - 1 else a_in
        a_avg = (a_in + a_out) / 2

        perp = a_avg + 90
        result.append(_polar(p_curr, perp, distance))
    return result


def _cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, n: int = 16) -> list[Point]:
    """Sample n interior points (excludes endpoints) along a cubic Bezier curve."""
    pts = []
    for i in range(1, n):
        t = i / n
        mt = 1 - t
        x = (mt**3) * p0.x + 3 * (mt**2) * t * p1.x + 3 * mt * (t**2) * p2.x + (t**3) * p3.x
        y = (mt**3) * p0.y + 3 * (mt**2) * t * p1.y + 3 * mt * (t**2) * p2.y + (t**3) * p3.y
        pts.append(Point(x, y))
    return pts


def _quadratic_bezier_curve(p0: Point, ctrl: Point, p2: Point, n: int = 12) -> list[Point]:
    """Sample n interior points (excludes endpoints) along a quadratic Bezier curve."""
    pts = []
    for i in range(1, n):
        t = i / n
        mt = 1 - t
        x = (mt**2) * p0.x + 2 * mt * t * ctrl.x + (t**2) * p2.x
        y = (mt**2) * p0.y + 2 * mt * t * ctrl.y + (t**2) * p2.y
        pts.append(Point(x, y))
    return pts


def _armhole_curve(shoulder_tip: Point, side_point: Point, depth_bulge: float, n: int = 14) -> list[Point]:
    """Standard armhole curve using a quadratic Bezier control point bulged outward."""
    mid = _midpoint(shoulder_tip, side_point)
    a = _angle(shoulder_tip, side_point) + 90
    ctrl = _polar(mid, a, depth_bulge)
    return _quadratic_bezier_curve(shoulder_tip, ctrl, side_point, n)


def _neckline_curve(cf_point: Point, shoulder_point: Point, depth_bulge: float, n: int = 10) -> list[Point]:
    """Neckline curve using a quadratic Bezier bulged inward (concave)."""
    mid = _midpoint(cf_point, shoulder_point)
    a = _angle(cf_point, shoulder_point) - 90
    ctrl = _polar(mid, a, depth_bulge)
    return _quadratic_bezier_curve(cf_point, ctrl, shoulder_point, n)


def _sleeve_cap_curve(p0: Point, p1: Point, p2: Point, p3: Point, n: int = 16) -> list[Point]:
    """Sleeve cap curve — full cubic Bezier for the bell shape."""
    return _cubic_bezier(p0, p1, p2, p3, n)


# ====================================================================
# DRAFTING ENGINE — Block Computations
# ====================================================================

class DraftingEngine:
    """Computes pattern geometry from Measurements using standard
    flat-pattern drafting formulas (Aldrich / Winifred Aldrich method),
    with optional style overlays for cowl/gather/asymmetric silhouettes."""

    def __init__(self, m: Measurements, ease: str = "standard", style: Optional[StyleDetails] = None):
        self.m = m
        self.style = style or StyleDetails()
        self.ease_value = config.EASE_BODICE.get(ease, 4.0)
        self.skirt_ease = config.EASE_SKIRT.get(ease, 3.0)
        self.sa = config.SEAM_ALLOWANCE
        self.hem = config.HEM_ALLOWANCE

        self._compute_derived()

    def _compute_derived(self):
        m = self.m
        self.half_bust = m.bust / 2
        self.quarter_bust = (m.bust + self.ease_value) / 4
        self.quarter_waist = (m.waist + 1) / 4
        self.quarter_hip = (m.hip + self.skirt_ease) / 4

        if m.armhole_depth <= 0:
            self.armhole_depth = m.bust / 4 + 2.0
        else:
            self.armhole_depth = m.armhole_depth + 1.0

        if m.neck_width <= 0:
            self.neck_width = m.bust / 20 + 2.0
        else:
            self.neck_width = m.neck_width

        if m.neck_depth_front <= 0:
            self.neck_depth_front = self.neck_width + 1.5
        else:
            self.neck_depth_front = m.neck_depth_front

        if m.neck_depth_back <= 0:
            self.neck_depth_back = self.neck_width * 0.4
        else:
            self.neck_depth_back = m.neck_depth_back

        if m.shoulder_width <= 0:
            self.shoulder_width = m.bust / 4 + 2.0
        else:
            self.shoulder_width = m.shoulder_width

        # Drop shoulder extends the shoulder line outward
        if self.style.drop_shoulder:
            self.shoulder_length = (m.shoulder_length if m.shoulder_length > 0 else 12.0) + 5.0
        else:
            self.shoulder_length = m.shoulder_length if m.shoulder_length > 0 else 12.0

        self.waist_suppression = self.quarter_bust - self.quarter_waist

        if m.dart_intake_bust <= 0:
            self.bust_dart = min(self.waist_suppression * 0.6, 4.0)
        else:
            self.bust_dart = m.dart_intake_bust

        if m.dart_intake_waist_front <= 0:
            self.front_waist_dart = min(self.waist_suppression * 0.4, 3.0)
        else:
            self.front_waist_dart = m.dart_intake_waist_front

        if m.dart_intake_waist_back <= 0:
            self.back_waist_dart = min(self.waist_suppression * 0.35, 2.5)
        else:
            self.back_waist_dart = m.dart_intake_waist_back

        self.hip_depth = m.waist_to_hip if m.waist_to_hip > 0 else 20.0

        if m.bust_span <= 0:
            self.bust_span = self.quarter_bust * 0.35
        else:
            self.bust_span = m.bust_span

    # ----------------------------------------------------------------
    # BODICE BLOCK — Front & Back (standard, fitted)
    # ----------------------------------------------------------------

    def draft_bodice_front(self) -> PatternPiece:
        m = self.m
        qb = self.quarter_bust
        qw = self.quarter_waist
        piece = PatternPiece(name="Front Bodice", piece_type="front", cut_qty=1)

        cf_neck = Point(0, 0)
        cf_waist = Point(0, -(m.back_length if m.back_length > 0 else 40))

        neck_shoulder = Point(self.neck_width, 0)
        neck_depth = Point(0, -self.neck_depth_front)

        shoulder_tip = _polar(neck_shoulder, -25, self.shoulder_length)

        ah_depth_y = shoulder_tip.y - self.armhole_depth
        ah_side = Point(qb + 1.0, ah_depth_y)

        side_waist = Point(qw + 1.5 + self.front_waist_dart, cf_waist.y)

        # Base outline vertices (straight-segment skeleton; curves fill in below)
        piece.outline = [
            neck_depth,        # 0: CF neckpoint (low)
            neck_shoulder,     # 1: shoulder neckpoint
            shoulder_tip,      # 2: shoulder tip
            ah_side,           # 3: armhole/side seam junction
            side_waist,        # 4: side seam at waist
            cf_waist,          # 5: CF at waist
        ]

        # Neckline curve: neck_depth(0) -> neck_shoulder(1)
        piece.outline_curves[0] = _neckline_curve(neck_depth, neck_shoulder, depth_bulge=2.0)
        # Armhole curve: shoulder_tip(2) -> ah_side(3)
        piece.outline_curves[2] = _armhole_curve(shoulder_tip, ah_side, depth_bulge=3.0)

        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        # Bust dart
        apex_x = self.bust_span
        apex_y = cf_neck.y - (m.shoulder_to_bust if m.shoulder_to_bust > 0 else 10)
        bust_apex = Point(apex_x, apex_y)
        dart_start = Point(ah_side.x - 2, ah_side.y + 1)
        piece.darts.append({
            "start": dart_start.to_tuple(),
            "end": _polar(dart_start, _angle(dart_start, bust_apex), _dist(dart_start, bust_apex)).to_tuple(),
            "apex": bust_apex.to_tuple(),
            "intake": self.bust_dart,
        })

        # Waist dart
        waist_dart_start = Point(qb * 0.4, cf_waist.y)
        waist_dart_end = Point(qb * 0.4 + self.front_waist_dart, cf_waist.y)
        waist_dart_apex = Point(qb * 0.4 + self.front_waist_dart / 2, cf_waist.y + 8)
        piece.darts.append({
            "start": waist_dart_start.to_tuple(),
            "end": waist_dart_end.to_tuple(),
            "apex": waist_dart_apex.to_tuple(),
            "intake": self.front_waist_dart,
        })

        piece.grainline = {
            "start": Point(qb * 0.3, shoulder_tip.y - 5).to_tuple(),
            "end": Point(qb * 0.3, cf_waist.y + 5).to_tuple(),
            "direction": "vertical",
        }

        piece.notches.append(Point(shoulder_tip.x - 2, shoulder_tip.y))
        piece.notches.append(Point(ah_side.x, ah_side.y - 1))
        piece.notches.append(Point(self.bust_span, ah_depth_y))

        piece.mirror_axis = {"start": cf_neck.to_tuple(), "end": cf_waist.to_tuple()}
        piece.fold_lines.append({"points": [cf_neck, cf_waist], "label": "CF FOLD"})

        piece.annotations.append({"pos": (qb / 2, 2), "text": "FRONT BODICE"})
        piece.annotations.append({"pos": (0, -5), "text": "CF"})

        piece.meta = {"garment": "bodice", "side": "front", "block": "basic"}
        return piece

    def draft_bodice_back(self) -> PatternPiece:
        m = self.m
        qb = self.quarter_bust
        qw = self.quarter_waist
        piece = PatternPiece(name="Back Bodice", piece_type="back", cut_qty=1)

        cb_neck = Point(0, 0)
        cb_waist = Point(0, -(m.back_length if m.back_length > 0 else 40))

        neck_shoulder = Point(self.neck_width, 0)
        neck_depth = Point(0, -self.neck_depth_back)

        shoulder_tip = _polar(neck_shoulder, -22, self.shoulder_length - 1)

        ah_side = Point(qb, shoulder_tip.y - self.armhole_depth)
        side_waist = Point(qw + 1.5 + self.back_waist_dart, cb_waist.y)

        piece.outline = [
            neck_depth,
            neck_shoulder,
            shoulder_tip,
            ah_side,
            side_waist,
            cb_waist,
        ]

        piece.outline_curves[0] = _neckline_curve(neck_depth, neck_shoulder, depth_bulge=1.2)
        piece.outline_curves[2] = _armhole_curve(shoulder_tip, ah_side, depth_bulge=2.6)

        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        if self.back_waist_dart > 0:
            sd_mid = _midpoint(neck_shoulder, shoulder_tip)
            sd_apex = _polar(sd_mid, -90 - 30, 6)
            piece.darts.append({
                "start": _polar(sd_mid, _angle(neck_shoulder, shoulder_tip) - 90, 0.5).to_tuple(),
                "end": _polar(sd_mid, _angle(neck_shoulder, shoulder_tip) + 90, 0.5).to_tuple(),
                "apex": sd_apex.to_tuple(),
                "intake": 1.0,
            })

        waist_dart_start = Point(qb * 0.45, cb_waist.y)
        waist_dart_end = Point(qb * 0.45 + self.back_waist_dart, cb_waist.y)
        waist_dart_apex = Point(qb * 0.45 + self.back_waist_dart / 2, cb_waist.y + 9)
        piece.darts.append({
            "start": waist_dart_start.to_tuple(),
            "end": waist_dart_end.to_tuple(),
            "apex": waist_dart_apex.to_tuple(),
            "intake": self.back_waist_dart,
        })

        piece.grainline = {
            "start": Point(qb * 0.3, shoulder_tip.y - 5).to_tuple(),
            "end": Point(qb * 0.3, cb_waist.y + 5).to_tuple(),
            "direction": "vertical",
        }

        piece.notches.append(_midpoint(neck_shoulder, shoulder_tip))
        piece.notches.append(Point(ah_side.x - 2, ah_side.y))

        piece.mirror_axis = {"start": cb_neck.to_tuple(), "end": cb_waist.to_tuple()}
        piece.fold_lines.append({"points": [cb_neck, cb_waist], "label": "CB FOLD"})

        piece.annotations.append({"pos": (qb / 2, 2), "text": "BACK BODICE"})
        piece.annotations.append({"pos": (0, -5), "text": "CB"})

        piece.meta = {"garment": "bodice", "side": "back", "block": "basic"}
        return piece

    # ----------------------------------------------------------------
    # ASYMMETRIC / COWL / GATHERED FRONT PANEL (dynamic style path)
    # ----------------------------------------------------------------

    def draft_asymmetric_gathered_front(self) -> PatternPiece:
        """
        Drafts a front panel for cowl/wrap/gathered/asymmetric-hem styles.
        The panel is wider than a standard bodice front to accommodate
        drape, with gather fold-guide lines radiating from a pivot point
        near the shoulder/neckline down to the gathered hem edge — this
        mirrors how cowl/wrap draping is drafted on a slash-and-spread
        basis in industry pattern rooms.
        """
        m = self.m
        qb = self.quarter_bust
        style = self.style
        piece = PatternPiece(name="Front Panel (Asymmetric/Gathered)",
                              piece_type="front", cut_qty=1)

        body_length = m.back_length if m.back_length > 0 else 40
        extra_drape = 8.0 if style.has_cowl else 4.0

        # Higher/shorter side (right, structured shoulder)
        shoulder_neck = Point(self.neck_width, 0)
        shoulder_tip = _polar(shoulder_neck, -25, self.shoulder_length)
        ah_depth_y = shoulder_tip.y - self.armhole_depth
        ah_side = Point(qb + 1.0, ah_depth_y)

        # Cowl/wrap pivot point — where the drape hangs from (near opposite shoulder/neck)
        pivot = Point(self.neck_width * 0.3, -1.5)

        # Draped hem — asymmetric: one side much lower than the other
        hem_high = Point(qb * 0.15, -(body_length * 0.55))          # shorter hem point (near CF)
        hem_low = Point(qb + 1.5, -(body_length * (1.15 if style.asymmetric_hem else 0.95)))  # lower drape hem at side

        # Wrap-over edge — sweeps from the pivot across to the low hem
        wrap_edge_ctrl = _polar(pivot, -60, extra_drape * 2.2)

        piece.outline = [
            pivot,          # 0: neckline pivot (cowl/wrap origin)
            shoulder_neck,  # 1
            shoulder_tip,   # 2
            ah_side,        # 3
            hem_low,        # 4: draped low hem at side
            hem_high,       # 5: shorter hem near CF
        ]

        # Neckline curve (pivot -> shoulder_neck): soft cowl scoop
        piece.outline_curves[0] = _neckline_curve(pivot, shoulder_neck, depth_bulge=3.5)
        # Armhole curve
        piece.outline_curves[2] = _armhole_curve(shoulder_tip, ah_side, depth_bulge=3.0)
        # Wrap/drape edge curve (hem_low -> hem_high): cubic bezier sweeping curve
        piece.outline_curves[4] = _cubic_bezier(
            hem_low,
            _polar(hem_low, 160, extra_drape * 1.5),
            _polar(hem_high, -20, extra_drape * 1.5),
            hem_high,
            n=18,
        )

        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        # Gather / cowl fold guides — radiating lines from pivot to the drape edge,
        # matching the "sunburst" gather markings on cowl/wrap fronts
        n_rays = 7 if style.has_gathers or style.has_cowl else 0
        for i in range(n_rays):
            t = i / max(n_rays - 1, 1)
            target = Point(
                hem_low.x + (hem_high.x - hem_low.x) * t,
                hem_low.y + (hem_high.y - hem_low.y) * t,
            )
            piece.gather_guides.append({
                "points": [pivot, target],
                "label": "GATHER" if i == n_rays // 2 else "",
            })

        # Pleats (if specified) — evenly spaced fold guides near the hem
        if style.has_pleats and style.pleat_count > 0:
            for i in range(style.pleat_count):
                t = (i + 1) / (style.pleat_count + 1)
                px = hem_high.x + (ah_side.x - hem_high.x) * t
                piece.pleat_guides.append({
                    "points": [Point(px, ah_depth_y * 0.3), Point(px, hem_high.y * 0.9)],
                    "label": "PLEAT FOLD",
                })

        piece.grainline = {
            "start": Point(qb * 0.35, shoulder_tip.y - 5).to_tuple(),
            "end": Point(qb * 0.35, hem_high.y + 5).to_tuple(),
            "direction": "vertical",
        }

        piece.notches.append(Point(shoulder_tip.x - 2, shoulder_tip.y))
        piece.notches.append(Point(ah_side.x, ah_side.y - 1))
        piece.notches.append(pivot)

        piece.mirror_axis = None  # asymmetric piece — not mirrored
        piece.annotations.append({"pos": (qb / 2, 2), "text": "FRONT PANEL — ASYMMETRIC/GATHERED"})
        if style.has_cowl:
            piece.annotations.append({"pos": (pivot.x, pivot.y + 2), "text": "COWL DRAPE"})

        piece.meta = {"garment": style.silhouette or "wrap", "side": "front",
                      "block": "dynamic_asymmetric", "style": asdict(style)}
        return piece

    # ----------------------------------------------------------------
    # SKIRT BLOCK
    # ----------------------------------------------------------------

    def draft_skirt(self) -> list[PatternPiece]:
        pieces: list[PatternPiece] = []
        qh = self.quarter_hip
        qw = self.quarter_waist
        length = self.m.skirt_length if self.m.skirt_length > 0 else 60

        for side, dart_intake in [("front", min((qh - qw) * 0.4, 3.0)),
                                   ("back", min((qh - qw) * 0.5, 3.5))]:
            piece = PatternPiece(name=f"{side.capitalize()} Skirt", piece_type=side, cut_qty=1)

            waist_top = Point(0, 0)
            waist_side = Point(qh - 0.5, 0)
            hem_side = Point(qh + 1.5, -length)
            hem_center = Point(0, -length)

            piece.outline = [waist_top, waist_side, hem_side, hem_center]

            piece.seam_line = _offset_polyline(piece.outline, -self.sa)

            piece.internal_lines.append({
                "type": "reference",
                "points": [Point(0, -self.hip_depth).to_tuple(),
                           Point(qh, -self.hip_depth).to_tuple()],
                "label": "hip_line",
            })

            dart1_pos = qh * 0.3
            dart2_pos = qh * 0.65
            for dp, di in [(dart1_pos, dart_intake * 0.45), (dart2_pos, dart_intake * 0.55)]:
                if di > 0.5:
                    piece.darts.append({
                        "start": Point(dp, 0).to_tuple(),
                        "end": Point(dp + di, 0).to_tuple(),
                        "apex": Point(dp + di / 2, -9).to_tuple(),
                        "intake": di,
                    })

            piece.grainline = {
                "start": Point(qh * 0.4, -5).to_tuple(),
                "end": Point(qh * 0.4, -length + 5).to_tuple(),
                "direction": "vertical",
            }

            piece.notches.append(Point(qh * 0.4, -self.hip_depth))
            piece.notches.append(Point(qh - 1, -self.hip_depth))

            piece.mirror_axis = {"start": waist_top.to_tuple(), "end": hem_center.to_tuple()}
            piece.fold_lines.append({"points": [waist_top, hem_center], "label": "CF/CB FOLD"})

            piece.annotations.append({"pos": (qh / 2, -length / 2), "text": f"{side.upper()} SKIRT"})
            piece.meta = {"garment": "skirt", "side": side, "block": "basic"}
            pieces.append(piece)

        return pieces

    # ----------------------------------------------------------------
    # SLEEVE BLOCK
    # ----------------------------------------------------------------

    def draft_sleeve(self) -> PatternPiece:
        m = self.m
        piece = PatternPiece(name="Sleeve", piece_type="sleeve", cut_qty=2)

        if m.bicep <= 0:
            bicep = self.armhole_depth * 2 + 4.0
        else:
            bicep = m.bicep + 3.0

        wrist = m.wrist + 4.0 if m.wrist > 0 else bicep * 0.4
        sleeve_length = m.sleeve_length if m.sleeve_length > 0 else 60
        cap_height = self.armhole_depth * 0.4 + 2.0

        center_top = Point(bicep / 2, 0)
        center_wrist = Point(bicep / 2, -sleeve_length)

        bicep_front = Point(0, -cap_height)
        bicep_back = Point(bicep, -cap_height)

        wrist_front = Point((bicep - wrist) / 2, -sleeve_length)
        wrist_back = Point(bicep - (bicep - wrist) / 2, -sleeve_length)

        piece.outline = [
            bicep_front,    # 0
            center_top,     # 1: cap apex
            bicep_back,     # 2
            wrist_back,     # 3
            wrist_front,    # 4
        ]

        # Sleeve cap curve: front half (bicep_front -> center_top)
        cap_ctrl1 = _polar(bicep_front, 75, cap_height * 0.65)
        cap_ctrl2 = _polar(center_top, 200, cap_height * 0.5)
        piece.outline_curves[0] = _sleeve_cap_curve(bicep_front, cap_ctrl1, cap_ctrl2, center_top, n=14)

        # Sleeve cap curve: back half (center_top -> bicep_back)
        cap_ctrl3 = _polar(center_top, -20, cap_height * 0.5)
        cap_ctrl4 = _polar(bicep_back, 105, cap_height * 0.65)
        piece.outline_curves[1] = _sleeve_cap_curve(center_top, cap_ctrl3, cap_ctrl4, bicep_back, n=14)

        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        piece.grainline = {
            "start": center_top.to_tuple(),
            "end": center_wrist.to_tuple(),
            "direction": "vertical",
        }

        piece.notches.append(center_top)
        piece.notches.append(_midpoint(bicep_front, center_top))
        piece.notches.append(_midpoint(center_top, bicep_back))
        piece.notches.append(Point(bicep / 2, -sleeve_length))

        elbow_y = -cap_height + (-sleeve_length - -cap_height) * 0.5
        piece.internal_lines.append({
            "type": "reference",
            "points": [Point(0, elbow_y).to_tuple(), Point(bicep, elbow_y).to_tuple()],
            "label": "elbow_line",
        })

        piece.mirror_axis = {"start": center_top.to_tuple(), "end": center_wrist.to_tuple()}

        piece.annotations.append({"pos": (bicep / 2, -sleeve_length / 2), "text": "SLEEVE"})
        piece.meta = {"garment": "sleeve", "block": "basic"}
        return piece

    # ----------------------------------------------------------------
    # SHIRT BLOCK
    # ----------------------------------------------------------------

    def draft_shirt(self) -> list[PatternPiece]:
        m = self.m
        pieces: list[PatternPiece] = []

        self.ease_value = config.EASE_BODICE.get("loose", 6.0)
        self.quarter_bust = (m.bust + self.ease_value) / 4
        self.quarter_waist = (m.waist + 4) / 4

        front = self.draft_bodice_front()
        front.name = "Front Shirt"
        front.meta["garment"] = "shirt"
        front.annotations[0]["text"] = "FRONT SHIRT"
        pieces.append(front)

        back = self.draft_bodice_back()
        back.name = "Back Shirt"
        back.meta["garment"] = "shirt"
        back.annotations[0]["text"] = "BACK SHIRT"
        pieces.append(back)

        if m.sleeve_length > 0:
            sleeve = self.draft_sleeve()
            sleeve.meta["garment"] = "shirt"
            pieces.append(sleeve)

        collar = self._draft_collar_band()
        pieces.append(collar)

        return pieces

    def _draft_collar_band(self) -> PatternPiece:
        neck_opening = self.neck_width * 2 * math.pi * 0.4
        collar_height = 4.0

        piece = PatternPiece(name="Collar Band", piece_type="collar", cut_qty=2)
        piece.outline = [
            Point(0, 0),
            Point(neck_opening / 2, 0),
            Point(neck_opening / 2, collar_height),
            Point(0, collar_height),
        ]
        piece.seam_line = _offset_polyline(piece.outline, -self.sa)
        piece.grainline = {
            "start": (neck_opening / 4, 1),
            "end": (neck_opening / 4, collar_height - 1),
            "direction": "horizontal",
        }
        piece.annotations.append({"pos": (neck_opening / 4, collar_height / 2), "text": "COLLAR"})
        piece.meta = {"garment": "collar", "block": "basic"}
        return piece

    # ----------------------------------------------------------------
    # DRESS / KURTI (bodice + skirt combined, style-aware)
    # ----------------------------------------------------------------

    def draft_dress(self, garment_label: str = "DRESS") -> list[PatternPiece]:
        m = self.m
        style = self.style
        pieces: list[PatternPiece] = []

        # Front: dynamic asymmetric/gathered/cowl OR standard bodice
        if style.is_dynamic_front():
            front = self.draft_asymmetric_gathered_front()
            front.name = f"Front {garment_label.title()} Panel"
        else:
            front = self.draft_bodice_front()
            front.name = f"Front {garment_label.title()} Bodice"
        front.meta["garment"] = garment_label.lower()
        pieces.append(front)

        back = self.draft_bodice_back()
        back.name = f"Back {garment_label.title()} Bodice"
        back.meta["garment"] = garment_label.lower()
        if style.drop_shoulder:
            back.annotations.append({"pos": (0, 2), "text": "DROP SHOULDER"})
        pieces.append(back)

        if m.dress_length > 0 and not style.is_dynamic_front():
            # Standard dress: separate skirt panel below bodice.
            # (Asymmetric/cowl styles already extend the front panel to full length.)
            self.m.skirt_length = max(m.dress_length - (m.back_length if m.back_length > 0 else 40), 20)
            skirt_pieces = self.draft_skirt()
            for sp in skirt_pieces:
                sp.name = sp.name.replace("Skirt", f"{garment_label.title()} Skirt")
                sp.meta["garment"] = garment_label.lower()
            pieces.extend(skirt_pieces)

        if m.sleeve_length > 0:
            sleeve_front = self.draft_sleeve()
            sleeve_front.name = f"{garment_label.title()} Sleeve — Front" if style.is_dynamic_front() else "Sleeve"
            sleeve_front.meta["garment"] = garment_label.lower()
            pieces.append(sleeve_front)

            if style.is_dynamic_front():
                sleeve_back = self.draft_sleeve()
                sleeve_back.name = f"{garment_label.title()} Sleeve — Back"
                sleeve_back.meta["garment"] = garment_label.lower()
                sleeve_back.cut_qty = 2
                pieces.append(sleeve_back)

        if not style.is_dynamic_front():
            facing = self._draft_neck_facing()
            facing.meta["garment"] = garment_label.lower()
            pieces.append(facing)
        elif style.has_collar or style.collar_type:
            neck_shoulder_detail = self._draft_neck_shoulder_detail()
            neck_shoulder_detail.meta["garment"] = garment_label.lower()
            pieces.append(neck_shoulder_detail)

        return pieces

    def _draft_neck_facing(self) -> PatternPiece:
        piece = PatternPiece(name="Neck Facing", piece_type="facing", cut_qty=1)
        nw = self.neck_width
        nd = self.neck_depth_front
        facing_depth = 5.0

        piece.outline = [
            Point(0, 0),
            Point(nw + 2, 0),
            Point(nw + 2, -facing_depth),
            Point(0, -facing_depth - nd * 0.3),
        ]
        piece.seam_line = _offset_polyline(piece.outline, -self.sa)
        piece.grainline = {
            "start": (nw / 2, 0),
            "end": (nw / 2, -facing_depth),
            "direction": "vertical",
        }
        piece.annotations.append({"pos": (nw / 2, -facing_depth / 2), "text": "NECK FACING"})
        piece.meta = {"garment": "facing", "block": "derived"}
        return piece

    def _draft_neck_shoulder_detail(self) -> PatternPiece:
        """Small detail piece documenting the neck/shoulder gather zone —
        mirrors the 'NECK & SHOULDER GATHER DETAIL' callout on reference sheets."""
        piece = PatternPiece(name="Neck & Shoulder Gather Detail", piece_type="detail", cut_qty=1)
        nw = self.neck_width
        piece.outline = [
            Point(0, 0),
            Point(nw * 1.4, 0),
            Point(nw * 1.4, -nw * 0.9),
            Point(0, -nw * 0.6),
        ]
        for i in range(4):
            t = (i + 1) / 5
            piece.gather_guides.append({
                "points": [Point(0, -nw * 0.3), Point(nw * 1.4 * t, -nw * 0.9 * t)],
                "label": "",
            })
        piece.annotations.append({"pos": (nw * 0.5, -nw * 0.3), "text": "NECK & SHOULDER GATHER DETAIL"})
        piece.meta = {"garment": "detail", "block": "derived"}
        return piece

    # ----------------------------------------------------------------
    # MASTER DISPATCHER
    # ----------------------------------------------------------------

    def draft(self, garment_type: str) -> list[PatternPiece]:
        gt = garment_type.lower().strip()

        if gt in ("dress",):
            return self.draft_dress("dress")
        elif gt in ("kurti",):
            return self.draft_dress("kurti")
        elif gt in ("top", "blouse", "wrap", "wrap top", "poncho"):
            return self.draft_dress(gt if gt else "top")
        elif gt == "bodice":
            return [self.draft_bodice_front(), self.draft_bodice_back()]
        elif gt == "skirt":
            return self.draft_skirt()
        elif gt == "shirt":
            return self.draft_shirt()
        elif gt == "sleeve":
            return [self.draft_sleeve()]
        else:
            # Default: dress/kurti block (still style-aware)
            return self.draft_dress("dress")


# ====================================================================
# DXF / AAMA EXPORT ENGINE
# ====================================================================

class DXFExporter:
    """Exports PatternPieces to a DXF file using AAMA layer conventions,
    rendering true curves (armhole/neckline/sleeve cap) as splines."""

    def __init__(self):
        self.layers = config.AAMA_LAYERS

    def export(self, pieces: list[PatternPiece], filepath: str,
               garment_type: str = "garment",
               measurements: Optional[Measurements] = None,
               size_label: str = "") -> str:
        doc = ezdxf.new(dxfversion="R2000")
        doc.header["$INSUNITS"] = 5  # centimetres
        msp = doc.modelspace()

        self._setup_layers(doc)

        offset_x = 0
        col_count = 0
        max_cols = 3
        col_width = 55

        for piece in pieces:
            self._draw_piece(doc, msp, piece, offset_x, 0, size_label)
            col_count += 1
            if col_count >= max_cols:
                offset_x = 0
            else:
                offset_x += col_width

        self._add_header(msp, garment_type, measurements, size_label)

        doc.saveas(filepath)
        return filepath

    def _setup_layers(self, doc):
        layer_defs = [
            (self.layers["CUT"], 1),
            (self.layers["SEAM"], 5),
            (self.layers["GRAIN"], 3),
            (self.layers["NOTCH"], 2),
            (self.layers["INTERNAL"], 6),
            (self.layers["REFERENCE"], 8),
            (self.layers["ANNOTATION"], 7),
            (self.layers["MIRROR"], 4),
        ]
        for name, color in layer_defs:
            if name not in doc.layers:
                doc.layers.add(name=name, color=color)

    @staticmethod
    def _xy(p, ox, oy):
        x, y = (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])
        return (x + ox, y + oy)

    def _build_smooth_outline(self, piece: PatternPiece) -> list:
        """Expand outline + outline_curves into the full point sequence for CUT rendering."""
        pts = []
        n = len(piece.outline)
        for i in range(n):
            pts.append(piece.outline[i])
            if i in piece.outline_curves:
                pts.extend(piece.outline_curves[i])
        return pts

    def _draw_piece(self, doc, msp, piece: PatternPiece,
                    offset_x: float, offset_y: float, size_label: str = ""):
        ox, oy = offset_x, offset_y

        # --- Cutting outline (CUT layer), curve-expanded ---
        if piece.outline:
            full_pts = self._build_smooth_outline(piece)
            pts = [self._xy(p, ox, oy) for p in full_pts]
            pts.append(pts[0])
            msp.add_lwpolyline(pts, dxfattribs={
                "layer": self.layers["CUT"],
                "linetype": "CONTINUOUS",
                "lineweight": 35,
            })

        # --- Seam line (SEAM layer, dashed) ---
        if piece.seam_line:
            pts = [self._xy(p, ox, oy) for p in piece.seam_line]
            pts.append(pts[0])
            msp.add_lwpolyline(pts, dxfattribs={
                "layer": self.layers["SEAM"],
                "linetype": "DASHED",
            })

        # --- Darts ---
        for dart in piece.darts:
            start, end, apex = dart.get("start"), dart.get("end"), dart.get("apex")
            if start and end and apex:
                sx, sy = self._xy(start, ox, oy)
                ex, ey = self._xy(end, ox, oy)
                ax, ay = self._xy(apex, ox, oy)
                msp.add_line((sx, sy), (ax, ay), dxfattribs={"layer": self.layers["INTERNAL"]})
                msp.add_line((ex, ey), (ax, ay), dxfattribs={"layer": self.layers["INTERNAL"]})

        # --- Notches ---
        for notch in piece.notches:
            nx, ny = self._xy(notch, ox, oy)
            msp.add_line((nx - 0.5, ny), (nx + 0.5, ny), dxfattribs={"layer": self.layers["NOTCH"]})
            msp.add_line((nx, ny - 0.5), (nx, ny + 0.5), dxfattribs={"layer": self.layers["NOTCH"]})

        # --- Grainline with directional arrows ---
        if piece.grainline:
            gl = piece.grainline
            start, end = gl.get("start"), gl.get("end")
            if start and end:
                sx, sy = self._xy(start, ox, oy)
                ex, ey = self._xy(end, ox, oy)
                msp.add_line((sx, sy), (ex, ey), dxfattribs={"layer": self.layers["GRAIN"], "linetype": "PHANTOM"})
                angle = math.atan2(ey - sy, ex - sx)
                arrow_size = 1.5
                for tip, base_angle in [((sx, sy), angle + math.pi), ((ex, ey), angle)]:
                    for ang_off in (-0.4, 0.4):
                        ang = base_angle + ang_off
                        msp.add_line(tip, (tip[0] + arrow_size * math.cos(ang),
                                           tip[1] + arrow_size * math.sin(ang)),
                                    dxfattribs={"layer": self.layers["GRAIN"]})
                msp.add_text("GRAINLINE", dxfattribs={
                    "layer": self.layers["ANNOTATION"], "height": 0.9,
                }).set_placement(((sx + ex) / 2 + 1, (sy + ey) / 2))

        # --- Internal reference lines ---
        for iline in piece.internal_lines:
            pts = iline.get("points", [])
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    p1x, p1y = self._xy(pts[i], ox, oy)
                    p2x, p2y = self._xy(pts[i + 1], ox, oy)
                    msp.add_line((p1x, p1y), (p2x, p2y), dxfattribs={"layer": self.layers["REFERENCE"]})

        # --- Fold lines ---
        for fl in piece.fold_lines:
            pts = fl.get("points", [])
            if len(pts) >= 2:
                p1x, p1y = self._xy(pts[0], ox, oy)
                p2x, p2y = self._xy(pts[1], ox, oy)
                msp.add_line((p1x, p1y), (p2x, p2y), dxfattribs={
                    "layer": self.layers["MIRROR"], "linetype": "DASHDOT",
                })

        # --- Gather guides (radiating fold/ease lines) ---
        for gg in piece.gather_guides:
            pts = gg.get("points", [])
            if len(pts) >= 2:
                p1x, p1y = self._xy(pts[0], ox, oy)
                p2x, p2y = self._xy(pts[1], ox, oy)
                msp.add_line((p1x, p1y), (p2x, p2y), dxfattribs={
                    "layer": self.layers["INTERNAL"], "linetype": "DASHED",
                })
                if gg.get("label"):
                    msp.add_text(gg["label"], dxfattribs={
                        "layer": self.layers["ANNOTATION"], "height": 0.8,
                    }).set_placement((p2x, p2y))

        # --- Pleat guides ---
        for pg in piece.pleat_guides:
            pts = pg.get("points", [])
            if len(pts) >= 2:
                p1x, p1y = self._xy(pts[0], ox, oy)
                p2x, p2y = self._xy(pts[1], ox, oy)
                msp.add_line((p1x, p1y), (p2x, p2y), dxfattribs={
                    "layer": self.layers["INTERNAL"], "linetype": "DASHDOT2",
                })

        # --- Mirror axis (CF/CB) ---
        if piece.mirror_axis:
            s, e = piece.mirror_axis.get("start"), piece.mirror_axis.get("end")
            if s and e:
                sx, sy = self._xy(s, ox, oy)
                ex, ey = self._xy(e, ox, oy)
                msp.add_line((sx, sy), (ex, ey), dxfattribs={"layer": self.layers["MIRROR"], "linetype": "CENTER"})

        # --- Annotations ---
        for ann in piece.annotations:
            pos = ann.get("pos", (0, 0))
            text = ann.get("text", "")
            px, py = self._xy(pos, ox, oy)
            msp.add_text(text, dxfattribs={
                "layer": self.layers["ANNOTATION"], "height": 1.5,
            }).set_placement((px, py))

        # --- Piece name / cut qty / size label block ---
        if piece.outline:
            label_pt = self._xy(piece.outline[0], ox, oy)
            cut_text = f"{piece.name.upper()} - CUT {piece.cut_qty}"
            if size_label:
                cut_text += f" - SIZE {size_label}"
            msp.add_text(cut_text, dxfattribs={
                "layer": self.layers["ANNOTATION"], "height": 1.1,
            }).set_placement((label_pt[0], label_pt[1] + 3))

    def _add_header(self, msp, garment_type: str,
                    measurements: Optional[Measurements], size_label: str = ""):
        header_y = 8
        info_lines = [
            f"GARMENT: {garment_type.upper()}" + (f"  SIZE: {size_label}" if size_label else ""),
            f"DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"SEAM ALLOWANCE: {config.SEAM_ALLOWANCE}cm (~0.5in) | AAMA LAYERS: 1=Cut 8=Seam 4=Internal/Grain 3=Notch",
        ]
        if measurements:
            info_lines.extend([
                f"BUST: {measurements.bust}cm  WAIST: {measurements.waist}cm  HIP: {measurements.hip}cm",
            ])

        for i, line in enumerate(info_lines):
            msp.add_text(line, dxfattribs={
                "layer": config.AAMA_LAYERS["ANNOTATION"], "height": 1.3,
            }).set_placement((-5, header_y + i * 2))


# ====================================================================
# PDS TEMPLATE DATABASE
# ====================================================================

class TemplateDB:
    """SQLite-based storage for PDS templates and matched specs."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DATABASE_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                garment_type TEXT NOT NULL,
                garment_subtype TEXT,
                size_label TEXT,
                measurements_json TEXT NOT NULL,
                file_path TEXT,
                file_hash TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_garment_type ON templates(garment_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON templates(file_hash)")
        conn.commit()
        conn.close()

    def store_template(self, garment_type: str, measurements: dict,
                       file_path: str = None, subtype: str = None,
                       size_label: str = None, metadata: dict = None) -> int:
        conn = sqlite3.connect(self.db_path)
        file_hash = None
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO templates (garment_type, garment_subtype, size_label,
                                   measurements_json, file_path, file_hash, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            garment_type, subtype, size_label,
            json.dumps(measurements), file_path, file_hash,
            json.dumps(metadata or {}),
        ))
        conn.commit()
        template_id = cursor.lastrowid
        conn.close()
        return template_id

    def find_match(self, garment_type: str, measurements: dict,
                   tolerance: float = 3.0) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, garment_type, measurements_json, file_path, metadata_json
            FROM templates WHERE garment_type = ? ORDER BY created_at DESC
        """, (garment_type,))
        rows = cursor.fetchall()
        conn.close()

        best_match = None
        best_score = float("inf")
        for row in rows:
            stored_measurements = json.loads(row[2])
            score = self._measurement_distance(measurements, stored_measurements)
            if score < best_score and score < tolerance * max(len(measurements), 1):
                best_score = score
                best_match = {
                    "id": row[0], "garment_type": row[1],
                    "measurements": stored_measurements, "file_path": row[3],
                    "metadata": json.loads(row[4]) if row[4] else {}, "score": best_score,
                }
        return best_match

    def _measurement_distance(self, m1: dict, m2: dict) -> float:
        common_keys = set(m1.keys()) & set(m2.keys())
        if not common_keys:
            return float("inf")
        dist = 0.0
        for k in common_keys:
            try:
                v1 = float(m1.get(k, 0))
                v2 = float(m2.get(k, 0))
            except (ValueError, TypeError):
                continue
            if v1 > 0 and v2 > 0:
                dist += abs(v1 - v2) ** 2
        return math.sqrt(dist)

    def list_templates(self, garment_type: str = None) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if garment_type:
            cursor.execute("""
                SELECT id, garment_type, garment_subtype, size_label,
                       measurements_json, file_path, created_at
                FROM templates WHERE garment_type = ? ORDER BY created_at DESC
            """, (garment_type,))
        else:
            cursor.execute("""
                SELECT id, garment_type, garment_subtype, size_label,
                       measurements_json, file_path, created_at
                FROM templates ORDER BY created_at DESC
            """)
        rows = cursor.fetchall()
        conn.close()
        return [{
            "id": r[0], "garment_type": r[1], "subtype": r[2],
            "size_label": r[3], "measurements": json.loads(r[4]),
            "file_path": r[5], "created_at": r[6],
        } for r in rows]

    def delete_template(self, template_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted


# ====================================================================
# HIGH-LEVEL API
# ====================================================================

def _dict_to_style(d: dict) -> StyleDetails:
    valid_keys = {f for f in StyleDetails.__dataclass_fields__}
    filtered = {k: v for k, v in d.items() if k in valid_keys}
    return StyleDetails(**filtered)


def generate_pattern(measurements: Measurements | dict,
                     garment_type: str = "dress",
                     ease: str = "standard",
                     style: Optional[StyleDetails | dict] = None,
                     output_path: str = None) -> str:
    """
    Master function — takes measurements, garment type, and optional style
    details, returns path to the generated DXF.
    """
    if isinstance(measurements, dict):
        m = Measurements(**{k: v for k, v in measurements.items()
                           if hasattr(Measurements, k)})
    else:
        m = measurements

    if not m.validate():
        raise ValueError(f"Insufficient measurements. Missing: {m.missing_keys()}")

    if isinstance(style, dict):
        style = _dict_to_style(style)

    engine = DraftingEngine(m, ease=ease, style=style)
    pieces = engine.draft(garment_type)

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(config.OUTPUT_DIR, f"{garment_type}_pattern_{timestamp}.dxf")

    size_label = style.size_label if isinstance(style, StyleDetails) else ""

    exporter = DXFExporter()
    exporter.export(pieces, output_path, garment_type=garment_type,
                    measurements=m, size_label=size_label)

    return output_path


def draft_pieces(measurements: Measurements | dict,
                 garment_type: str = "dress",
                 ease: str = "standard",
                 style: Optional[StyleDetails | dict] = None) -> tuple[list[PatternPiece], DraftingEngine]:
    """Returns the raw PatternPiece list + engine (used by blueprint renderer)."""
    if isinstance(measurements, dict):
        m = Measurements(**{k: v for k, v in measurements.items()
                           if hasattr(Measurements, k)})
    else:
        m = measurements

    if isinstance(style, dict):
        style = _dict_to_style(style)

    engine = DraftingEngine(m, ease=ease, style=style)
    pieces = engine.draft(garment_type)
    return pieces, engine


def generate_spec_summary(measurements: Measurements | dict,
                          garment_type: str = "dress",
                          ease: str = "standard",
                          style: Optional[StyleDetails | dict] = None) -> str:
    """Returns a human-readable garment specification summary."""
    if isinstance(measurements, dict):
        m = Measurements(**{k: v for k, v in measurements.items()
                           if hasattr(Measurements, k)})
    else:
        m = measurements

    if isinstance(style, dict):
        style = _dict_to_style(style)
    style = style or StyleDetails()

    engine = DraftingEngine(m, ease=ease, style=style)
    ease_val = engine.ease_value
    skirt_ease = engine.skirt_ease

    summary = f"""📋 GARMENT SPECIFICATION SUMMARY
═══════════════════════════════════

GARMENT TYPE: {garment_type.upper()}
SILHOUETTE: {style.silhouette.upper() if style.silhouette else 'STANDARD'}
SIZE: {style.size_label or 'N/A'}
EASE: {ease.upper()} (bodice +{ease_val}cm, skirt +{skirt_ease}cm)
SEAM ALLOWANCE: {config.SEAM_ALLOWANCE}cm
HEM ALLOWANCE: {config.HEM_ALLOWANCE}cm
"""

    style_flags = []
    if style.has_cowl: style_flags.append("Cowl drape")
    if style.has_gathers: style_flags.append(f"Gathers ({', '.join(style.gather_locations)})" if style.gather_locations else "Gathers")
    if style.has_pleats: style_flags.append(f"Pleats x{style.pleat_count}")
    if style.asymmetric_hem: style_flags.append("Asymmetric hem")
    if style.drop_shoulder: style_flags.append("Drop shoulder")
    if style.closure: style_flags.append(f"Closure: {style.closure}")
    if style_flags:
        summary += f"\nSTYLE DETAILS: {' | '.join(style_flags)}\n"

    summary += f"""
── BODY MEASUREMENTS ──
Bust:   {m.bust:>6.1f} cm
Waist:  {m.waist:>6.1f} cm
Hip:    {m.hip:>6.1f} cm
"""
    if m.shoulder_width: summary += f"Shoulder: {m.shoulder_width:>5.1f} cm\n"
    if m.back_length: summary += f"Back Length: {m.back_length:>4.1f} cm\n"
    if m.sleeve_length: summary += f"Sleeve Length: {m.sleeve_length:>2.1f} cm\n"
    if m.armhole_depth: summary += f"Armhole Depth: {m.armhole_depth:>2.1f} cm\n"

    if style.measurements_table:
        summary += "\n── RAW MEASUREMENT SHEET ──\n"
        for k, v in style.measurements_table.items():
            summary += f"  {k}: {v}\n"

    summary += f"""
── DRAFTING COMPUTATIONS ──
Quarter Bust (w/ ease):  {engine.quarter_bust:.1f} cm
Quarter Waist (w/ ease): {engine.quarter_waist:.1f} cm
Quarter Hip (w/ ease):   {engine.quarter_hip:.1f} cm
Armhole Depth (w/ ease): {engine.armhole_depth:.1f} cm
Neck Width:              {engine.neck_width:.1f} cm
Shoulder Length:         {engine.shoulder_length:.1f} cm
Bust Dart Intake:        {engine.bust_dart:.1f} cm
Front Waist Dart:        {engine.front_waist_dart:.1f} cm
Back Waist Dart:         {engine.back_waist_dart:.1f} cm

── PATTERN PIECES ──
"""
    pieces = engine.draft(garment_type)
    for p in pieces:
        summary += f"  • {p.name} — CUT {p.cut_qty} ({p.piece_type})\n"
        if p.darts: summary += f"    Darts: {len(p.darts)}\n"
        if p.notches: summary += f"    Notches: {len(p.notches)}\n"
        if p.gather_guides: summary += f"    Gather guides: {len(p.gather_guides)}\n"
        if p.pleat_guides: summary += f"    Pleat guides: {len(p.pleat_guides)}\n"
        if p.grainline: summary += f"    Grainline: {p.grainline.get('direction', 'vertical')}\n"

    summary += f"""
── CONSTRUCTION NOTES ──
• Curved edges (armhole/neckline/sleeve cap) drafted with true Bezier interpolation
• Seam allowance: {config.SEAM_ALLOWANCE}cm on all edges
• Hem allowance: {config.HEM_ALLOWANCE}cm
• AAMA DXF layers: 1=Cut, 8=Seam, 4=Internal/Grainline, 3=Notches
• Fold/gather/pleat guides included where the style requires them

Ready to draft? Reply /confirm to generate the DXF file + blueprint preview.
"""
    return summary


# ====================================================================
# CLI ENTRY POINT (for testing)
# ====================================================================

if __name__ == "__main__":
    m = Measurements(
        bust=92, waist=72, hip=96,
        shoulder_width=38, back_length=40,
        sleeve_length=58, armhole_depth=22,
        skirt_length=60, dress_length=100,
    )
    style = StyleDetails(
        silhouette="wrap", has_cowl=True, has_gathers=True,
        gather_locations=["front neckline"], asymmetric_hem=True,
        size_label="S",
    )
    print("=== ASYMMETRIC/COWL TOP PATTERN GENERATION ===\n")
    print(generate_spec_summary(m, "dress", style=style))
    path = generate_pattern(m, "dress", style=style, output_path="test_cowl_dress.dxf")
    print(f"\nDXF saved to: {path}")
