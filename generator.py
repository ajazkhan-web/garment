"""
generator.py — Universal 2D Apparel Pattern Drafting Engine
============================================================
Generates professional CAD pattern pieces from body measurements using
standard apparel drafting formulas.  Exports DXF / AAMA files compatible
with Optitex, Gerber, and Lectra via ezdxf.

Garment types supported:
    dress, kurti, bodice, skirt, shirt, sleeve

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
from ezdxf.entities import DXFGraphic

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
            "CUT": "1", "SEAM": "5", "GRAIN": "7", "NOTCH": "8",
            "INTERNAL": "3", "REFERENCE": "4", "ANNOTATION": "6", "MIRROR": "9",
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
    outline: list[Point] = field(default_factory=list)      # main cutting line
    seam_line: list[Point] = field(default_factory=list)     # stitching line (inside SA)
    darts: list[dict] = field(default_factory=list)          # each: {start, end, apex}
    notches: list[Point] = field(default_factory=list)
    grainline: Optional[dict] = None                          # {start, end, direction}
    internal_lines: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)     # {pos, text}
    mirror_axis: Optional[dict] = None                        # {start, end}
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

        # direction of incoming and outgoing edges
        a_in = _angle(p_prev, p_curr) if i > 0 else _angle(p_curr, p_next)
        a_out = _angle(p_curr, p_next) if i < n - 1 else a_in
        a_avg = (a_in + a_out) / 2

        perp = a_avg + 90
        result.append(_polar(p_curr, perp, distance))
    return result


# ====================================================================
# DRAFTING ENGINE — Block Computations
# ====================================================================

class DraftingEngine:
    """Computes pattern geometry from Measurements using standard
    flat-pattern drafting formulas (Aldrich / Winifred Aldrich method)."""

    def __init__(self, m: Measurements, ease: str = "standard"):
        self.m = m
        self.ease_value = config.EASE_BODICE.get(ease, 4.0)
        self.skirt_ease = config.EASE_SKIRT.get(ease, 3.0)
        self.sa = config.SEAM_ALLOWANCE
        self.hem = config.HEM_ALLOWANCE

        # Derived values
        self._compute_derived()

    def _compute_derived(self):
        m = self.m
        # Half-measurements (drafting uses quarters & halves)
        self.half_bust = m.bust / 2
        self.quarter_bust = (m.bust + self.ease_value) / 4
        self.quarter_waist = (m.waist + 1) / 4      # minimal waist ease
        self.quarter_hip = (m.hip + self.skirt_ease) / 4

        # Auto armhole if not provided
        if m.armhole_depth <= 0:
            self.armhole_depth = m.bust / 4 + 2.0
        else:
            self.armhole_depth = m.armhole_depth + 1.0  # +1cm ease

        # Auto neck if not provided
        if m.neck_width <= 0:
            self.neck_width = m.bust / 20 + 2.0
        else:
            self.neck_width = m.neck_width

        if m.neck_depth_front <= 0:
            self.neck_depth_front = self.neck_width + 1.5
        if m.neck_depth_back <= 0:
            self.neck_depth_back = self.neck_width * 0.4

        # Shoulder
        if m.shoulder_width <= 0:
            self.shoulder_width = m.bust / 4 + 2.0
        else:
            self.shoulder_width = m.shoulder_width

        if m.shoulder_length <= 0:
            self.shoulder_length = 12.0  # typical
        else:
            self.shoulder_length = m.shoulder_length

        # Waist dart intake (suppression)
        self.waist_suppression = self.quarter_bust - self.quarter_waist

        # Bust dart
        if m.dart_intake_bust <= 0:
            self.bust_dart = min(self.waist_suppression * 0.6, 4.0)
        else:
            self.bust_dart = m.dart_intake_bust

        # Waist darts
        if m.dart_intake_waist_front <= 0:
            self.front_waist_dart = min(self.waist_suppression * 0.4, 3.0)
        else:
            self.front_waist_dart = m.dart_intake_waist_front

        if m.dart_intake_waist_back <= 0:
            self.back_waist_dart = min(self.waist_suppression * 0.35, 2.5)
        else:
            self.back_waist_dart = m.dart_intake_waist_back

        # Hip depth
        if m.waist_to_hip <= 0:
            self.hip_depth = 20.0
        else:
            self.hip_depth = m.waist_to_hip

        # Bust span
        if m.bust_span <= 0:
            self.bust_span = self.quarter_bust * 0.35
        else:
            self.bust_span = m.bust_span

    # ----------------------------------------------------------------
    # BODICE BLOCK — Front & Back
    # ----------------------------------------------------------------

    def draft_bodice_front(self) -> PatternPiece:
        m = self.m
        qb = self.quarter_bust
        qw = self.quarter_waist
        piece = PatternPiece(name="Front Bodice", piece_type="front")

        # Origin at top-left (CF neck point)
        cf_neck = Point(0, 0)

        # CF line goes down to waist
        cf_waist = Point(0, -(m.back_length if m.back_length > 0 else 40))

        # Neckline
        neck_shoulder = Point(self.neck_width, 0)
        neck_depth = Point(0, -self.neck_depth_front)

        # Shoulder tip
        shoulder_tip = _polar(neck_shoulder, -25, self.shoulder_length)

        # Armhole
        ah_top = Point(self.shoulder_width + 1.5, shoulder_tip.y)  # shoulder tip extended
        ah_depth_y = shoulder_tip.y - self.armhole_depth
        ah_side = Point(qb + 1.0, ah_depth_y)  # side seam at armhole depth

        # Side seam at waist
        side_waist = Point(qw + 1.5 + self.front_waist_dart, cf_waist.y)

        # Build outline (CF grain, going clockwise)
        piece.outline = [
            cf_neck,
            neck_shoulder,
            shoulder_tip,
            Point(qb + 0.5, shoulder_tip.y - 2),  # armhole curve start
            ah_side,
            side_waist,
            cf_waist,
        ]

        # Seam line (inset by SA)
        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        # Neckline curve
        piece.internal_lines.append({
            "type": "curve",
            "points": [neck_depth, neck_shoulder],
            "label": "front_neckline",
        })

        # Bust dart (from side seam pointing to bust apex)
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

        # Waist dart (CF to side)
        waist_dart_start = Point(self.quarter_bust * 0.4, cf_waist.y)
        waist_dart_end = Point(self.quarter_bust * 0.4 + self.front_waist_dart, cf_waist.y)
        waist_dart_apex = Point(self.quarter_bust * 0.4 + self.front_waist_dart / 2, cf_waist.y + 8)
        piece.darts.append({
            "start": waist_dart_start.to_tuple(),
            "end": waist_dart_end.to_tuple(),
            "apex": waist_dart_apex.to_tuple(),
            "intake": self.front_waist_dart,
        })

        # Grainline (vertical, CF direction)
        piece.grainline = {
            "start": Point(self.quarter_bust * 0.3, shoulder_tip.y - 5).to_tuple(),
            "end": Point(self.quarter_bust * 0.3, cf_waist.y + 5).to_tuple(),
            "direction": "vertical",
        }

        # Notches
        piece.notches.append(Point(shoulder_tip.x - 2, shoulder_tip.y))     # shoulder notch
        piece.notches.append(Point(ah_side.x, ah_side.y - 1))                # armhole balance notch
        piece.notches.append(Point(self.bust_span, ah_depth_y))              # bust notch

        # Mirror axis (CF)
        piece.mirror_axis = {
            "start": cf_neck.to_tuple(),
            "end": cf_waist.to_tuple(),
        }

        # Annotations
        piece.annotations.append({"pos": (self.quarter_bust / 2, 2), "text": "FRONT BODICE"})
        piece.annotations.append({"pos": (0, -5), "text": "CF"})

        piece.meta = {"garment": "bodice", "side": "front", "block": "basic"}
        return piece

    def draft_bodice_back(self) -> PatternPiece:
        m = self.m
        qb = self.quarter_bust
        qw = self.quarter_waist
        piece = PatternPiece(name="Back Bodice", piece_type="back")

        # Origin at CB neck
        cb_neck = Point(0, 0)
        cb_waist = Point(0, -(m.back_length if m.back_length > 0 else 40))

        # Neckline (back, shallower)
        neck_shoulder = Point(self.neck_width, 0)
        neck_depth = Point(0, -self.neck_depth_back)

        # Shoulder tip (back shoulder is typically 1cm shorter)
        shoulder_tip = _polar(neck_shoulder, -22, self.shoulder_length - 1)

        # Armhole
        ah_side = Point(qb, shoulder_tip.y - self.armhole_depth)

        # Side seam
        side_waist = Point(qw + 1.5 + self.back_waist_dart, cb_waist.y)

        piece.outline = [
            cb_neck,
            neck_shoulder,
            shoulder_tip,
            Point(qb - 0.5, shoulder_tip.y - 2),
            ah_side,
            side_waist,
            cb_waist,
        ]

        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        # Neckline curve
        piece.internal_lines.append({
            "type": "curve",
            "points": [neck_depth, neck_shoulder],
            "label": "back_neckline",
        })

        # Shoulder dart (back)
        if self.back_waist_dart > 0:
            sd_mid = _midpoint(neck_shoulder, shoulder_tip)
            sd_apex = _polar(sd_mid, -90 - 30, 6)
            piece.darts.append({
                "start": _polar(sd_mid, _angle(neck_shoulder, shoulder_tip) - 90, 0.5).to_tuple(),
                "end": _polar(sd_mid, _angle(neck_shoulder, shoulder_tip) + 90, 0.5).to_tuple(),
                "apex": sd_apex.to_tuple(),
                "intake": 1.0,
            })

        # Waist dart (back)
        waist_dart_start = Point(self.quarter_bust * 0.45, cb_waist.y)
        waist_dart_end = Point(self.quarter_bust * 0.45 + self.back_waist_dart, cb_waist.y)
        waist_dart_apex = Point(self.quarter_bust * 0.45 + self.back_waist_dart / 2, cb_waist.y + 9)
        piece.darts.append({
            "start": waist_dart_start.to_tuple(),
            "end": waist_dart_end.to_tuple(),
            "apex": waist_dart_apex.to_tuple(),
            "intake": self.back_waist_dart,
        })

        # Grainline
        piece.grainline = {
            "start": Point(self.quarter_bust * 0.3, shoulder_tip.y - 5).to_tuple(),
            "end": Point(self.quarter_bust * 0.3, cb_waist.y + 5).to_tuple(),
            "direction": "vertical",
        }

        # Notches
        piece.notches.append(_midpoint(neck_shoulder, shoulder_tip))  # shoulder notch
        piece.notches.append(Point(ah_side.x - 2, ah_side.y))         # armhole notch

        # Mirror axis (CB)
        piece.mirror_axis = {
            "start": cb_neck.to_tuple(),
            "end": cb_waist.to_tuple(),
        }

        piece.annotations.append({"pos": (self.quarter_bust / 2, 2), "text": "BACK BODICE"})
        piece.annotations.append({"pos": (0, -5), "text": "CB"})

        piece.meta = {"garment": "bodice", "side": "back", "block": "basic"}
        return piece

    # ----------------------------------------------------------------
    # SKIRT BLOCK
    # ----------------------------------------------------------------

    def draft_skirt(self) -> list[PatternPiece]:
        """Drafts front and back skirt panels."""
        pieces: list[PatternPiece] = []
        qh = self.quarter_hip
        qw = self.quarter_waist
        length = self.m.skirt_length if self.m.skirt_length > 0 else 60

        for side, dart_intake in [("front", min((qh - qw) * 0.4, 3.0)),
                                   ("back", min((qh - qw) * 0.5, 3.5))]:
            piece = PatternPiece(
                name=f"{side.capitalize()} Skirt",
                piece_type=side,
            )

            # Origin at CF/CB waist
            waist_top = Point(0, 0)
            waist_side = Point(qh - 0.5, 0)
            hip_line = Point(qh - 0.5, -self.hip_depth)
            hem_side = Point(qh + 1.5, -length)
            hem_center = Point(0, -length)

            piece.outline = [waist_top, waist_side, hem_side, hem_center]

            piece.seam_line = _offset_polyline(piece.outline, -self.sa)

            # Hip line
            piece.internal_lines.append({
                "type": "reference",
                "points": [Point(0, -self.hip_depth).to_tuple(),
                           Point(qh, -self.hip_depth).to_tuple()],
                "label": "hip_line",
            })

            # Waist darts (2 darts per panel)
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

            # Grainline (centered, vertical)
            piece.grainline = {
                "start": Point(qh * 0.4, -5).to_tuple(),
                "end": Point(qh * 0.4, -length + 5).to_tuple(),
                "direction": "vertical",
            }

            # Notches
            piece.notches.append(Point(qh * 0.4, -self.hip_depth))  # hip notch
            piece.notches.append(Point(qh - 1, -self.hip_depth))    # side seam notch

            # Mirror axis (CF/CB)
            piece.mirror_axis = {
                "start": waist_top.to_tuple(),
                "end": hem_center.to_tuple(),
            }

            piece.annotations.append({
                "pos": (qh / 2, -length / 2),
                "text": f"{side.upper()} SKIRT",
            })
            piece.meta = {"garment": "skirt", "side": side, "block": "basic"}
            pieces.append(piece)

        return pieces

    # ----------------------------------------------------------------
    # SLEEVE BLOCK
    # ----------------------------------------------------------------

    def draft_sleeve(self) -> PatternPiece:
        m = self.m
        piece = PatternPiece(name="Sleeve", piece_type="sleeve")

        # Bicep = half armhole + ease
        if m.bicep <= 0:
            bicep = self.armhole_depth * 2 + 4.0  # approx from scye
        else:
            bicep = m.bicep + 3.0  # ease

        wrist = m.wrist + 4.0 if m.wrist > 0 else bicep * 0.4

        sleeve_length = m.sleeve_length if m.sleeve_length > 0 else 60

        # Cap height = roughly 1/3 of armhole depth
        cap_height = self.armhole_depth * 0.4 + 2.0

        # Construction
        bicep_line_y = -cap_height
        wrist_line_y = -cap_height - sleeve_length + cap_height

        # Center line vertical
        center_top = Point(bicep / 2, 0)       # cap top
        center_wrist = Point(bicep / 2, -sleeve_length)

        # Bicep ends
        bicep_front = Point(0, bicep_line_y)
        bicep_back = Point(bicep, bicep_line_y)

        # Wrist
        wrist_front = Point((bicep - wrist) / 2, -sleeve_length)
        wrist_back = Point(bicep - (bicep - wrist) / 2, -sleeve_length)

        # Cap curve (simplified bell curve)
        cap_front = _polar(bicep_front, 80, cap_height * 0.7)
        cap_back = _polar(bicep_back, 100, cap_height * 0.7)

        piece.outline = [
            bicep_front,
            cap_front,
            center_top,
            cap_back,
            bicep_back,
            wrist_back,
            wrist_front,
        ]

        piece.seam_line = _offset_polyline(piece.outline, -self.sa)

        # Grainline (center, vertical)
        piece.grainline = {
            "start": center_top.to_tuple(),
            "end": center_wrist.to_tuple(),
            "direction": "vertical",
        }

        # Notches
        piece.notches.append(center_top)                       # cap notch (balance)
        piece.notches.append(_midpoint(bicep_front, cap_front))  # front armhole notch
        piece.notches.append(_midpoint(bicep_back, cap_back))    # back armhole notch
        piece.notches.append(Point(bicep / 2, -sleeve_length))   # wrist center notch

        # Elbow line
        elbow_y = bicep_line_y + (wrist_line_y - bicep_line_y) * 0.5
        piece.internal_lines.append({
            "type": "reference",
            "points": [Point(0, elbow_y).to_tuple(),
                       Point(bicep, elbow_y).to_tuple()],
            "label": "elbow_line",
        })

        # Mirror axis (bicep center)
        piece.mirror_axis = {
            "start": center_top.to_tuple(),
            "end": center_wrist.to_tuple(),
        }

        piece.annotations.append({"pos": (bicep / 2, -sleeve_length / 2), "text": "SLEEVE"})
        piece.meta = {"garment": "sleeve", "block": "basic"}
        return piece

    # ----------------------------------------------------------------
    # SHIRT BLOCK (looser bodice with more ease)
    # ----------------------------------------------------------------

    def draft_shirt(self) -> list[PatternPiece]:
        m = self.m
        pieces: list[PatternPiece] = []

        # Use loose ease for shirts
        self.ease_value = config.EASE_BODICE.get("loose", 6.0)
        self.quarter_bust = (m.bust + self.ease_value) / 4
        self.quarter_waist = (m.waist + 4) / 4   # shirts have more waist ease

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

        # Add sleeve if measurements available
        if m.sleeve_length > 0:
            sleeve = self.draft_sleeve()
            sleeve.meta["garment"] = "shirt"
            pieces.append(sleeve)

        # Shirt collar (simple band)
        collar = self._draft_collar_band()
        pieces.append(collar)

        return pieces

    def _draft_collar_band(self) -> PatternPiece:
        neck_opening = self.neck_width * 2 * math.pi * 0.4  # approximate neck circumference
        collar_height = 4.0

        piece = PatternPiece(name="Collar Band", piece_type="collar")
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
            "direction": "vertical",
        }
        piece.annotations.append({"pos": (neck_opening / 4, collar_height / 2), "text": "COLLAR"})
        piece.meta = {"garment": "collar", "block": "basic"}
        return piece

    # ----------------------------------------------------------------
    # DRESS / KURTI (bodice + skirt combined)
    # ----------------------------------------------------------------

    def draft_dress(self, garment_label: str = "DRESS") -> list[PatternPiece]:
        m = self.m
        pieces: list[PatternPiece] = []

        # Bodice pieces
        front_bodice = self.draft_bodice_front()
        front_bodice.name = f"Front {garment_label.title()} Bodice"
        front_bodice.meta["garment"] = garment_label.lower()
        pieces.append(front_bodice)

        back_bodice = self.draft_bodice_back()
        back_bodice.name = f"Back {garment_label.title()} Bodice"
        back_bodice.meta["garment"] = garment_label.lower()
        pieces.append(back_bodice)

        # Skirt portion (full length minus bodice)
        if m.dress_length > 0:
            self.m.skirt_length = max(m.dress_length - (m.back_length if m.back_length > 0 else 40), 20)
            skirt_pieces = self.draft_skirt()
            for sp in skirt_pieces:
                sp.name = sp.name.replace("Skirt", f"{garment_label.title()} Skirt")
                sp.meta["garment"] = garment_label.lower()
            pieces.extend(skirt_pieces)

        # Sleeve
        if m.sleeve_length > 0:
            sleeve = self.draft_sleeve()
            sleeve.meta["garment"] = garment_label.lower()
            pieces.append(sleeve)

        # Facing for neckline
        facing = self._draft_neck_facing()
        facing.meta["garment"] = garment_label.lower()
        pieces.append(facing)

        return pieces

    # ----------------------------------------------------------------
    # NECK FACING
    # ----------------------------------------------------------------

    def _draft_neck_facing(self) -> PatternPiece:
        piece = PatternPiece(name="Neck Facing", piece_type="facing")
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

    # ----------------------------------------------------------------
    # MASTER DISPATCHER
    # ----------------------------------------------------------------

    def draft(self, garment_type: str) -> list[PatternPiece]:
        """Main dispatch — routes to the correct drafting method."""
        gt = garment_type.lower().strip()

        if gt in ("dress",):
            return self.draft_dress("dress")
        elif gt in ("kurti", "kurti"):
            return self.draft_dress("kurti")
        elif gt == "bodice":
            return [self.draft_bodice_front(), self.draft_bodice_back()]
        elif gt == "skirt":
            return self.draft_skirt()
        elif gt == "shirt":
            return self.draft_shirt()
        elif gt == "sleeve":
            return [self.draft_sleeve()]
        else:
            # Default: dress/kurti block
            return self.draft_dress("dress")


# ====================================================================
# DXF / AAMA EXPORT ENGINE
# ====================================================================

class DXFExporter:
    """Exports PatternPieces to a DXF file using AAMA layer conventions."""

    def __init__(self):
        self.layers = config.AAMA_LAYERS

    def export(self, pieces: list[PatternPiece], filepath: str,
               garment_type: str = "garment",
               measurements: Optional[Measurements] = None) -> str:
        doc = ezdxf.new(dxfversion="R2000")
        msp = doc.modelspace()

        # Set up AAMA layers
        self._setup_layers(doc)

        # Each piece gets its own block for clean nesting
        offset_x = 0
        col_count = 0
        max_cols = 3  # 3 pieces per row
        col_width = 50  # cm spacing

        for piece in pieces:
            self._draw_piece(doc, msp, piece, offset_x, 0)
            col_count += 1
            if col_count >= max_cols:
                offset_x = 0
                # Move to next row (downward in Y)
            else:
                offset_x += col_width

        # Header info
        self._add_header(msp, garment_type, measurements)

        doc.saveas(filepath)
        return filepath

    def _setup_layers(self, doc):
        """Create AAMA-standard layers with appropriate colors."""
        layer_defs = [
            (self.layers["CUT"], 1),         # Red — cutting line
            (self.layers["SEAM"], 5),         # Blue — seam line
            (self.layers["GRAIN"], 3),        # Green — grainline
            (self.layers["NOTCH"], 2),        # Yellow — notches
            (self.layers["INTERNAL"], 6),     # Magenta — internal
            (self.layers["REFERENCE"], 8),    # Gray — reference
            (self.layers["ANNOTATION"], 7),   # White — text
            (self.layers["MIRROR"], 4),       # Cyan — mirror
        ]
        for name, color in layer_defs:
            if name not in doc.layers:
                doc.layers.add(name=name, color=color)

    def _draw_piece(self, doc, msp, piece: PatternPiece,
                    offset_x: float, offset_y: float):
        """Draw a single pattern piece with all AAMA elements."""
        ox, oy = offset_x, offset_y

        # --- Cutting outline (CUT layer) ---
        if piece.outline:
            pts = [(p.x + ox, p.y + oy) for p in piece.outline]
            pts.append(pts[0])  # close
            msp.add_lwpolyline(pts, dxfattribs={
                "layer": self.layers["CUT"],
                "linetype": "CONTINUOUS",
                "lineweight": 35,  # thick
            })

        # --- Seam line (SEAM layer, dashed) ---
        if piece.seam_line:
            pts = [(p.x + ox, p.y + oy) for p in piece.seam_line]
            pts.append(pts[0])
            msp.add_lwpolyline(pts, dxfattribs={
                "layer": self.layers["SEAM"],
                "linetype": "DASHED",
            })

        # --- Darts (INTERNAL layer) ---
        for dart in piece.darts:
            start = dart.get("start")
            end = dart.get("end")
            apex = dart.get("apex")
            if start and end and apex:
                # Handle both Point objects and tuples
                sx, sy = (start.x, start.y) if hasattr(start, 'x') else (start[0], start[1])
                ex, ey = (end.x, end.y) if hasattr(end, 'x') else (end[0], end[1])
                ax, ay = (apex.x, apex.y) if hasattr(apex, 'x') else (apex[0], apex[1])
                # Dart legs (two lines converging at apex)
                msp.add_line(
                    (sx + ox, sy + oy),
                    (ax + ox, ay + oy),
                    dxfattribs={"layer": self.layers["INTERNAL"]},
                )
                msp.add_line(
                    (ex + ox, ey + oy),
                    (ax + ox, ay + oy),
                    dxfattribs={"layer": self.layers["INTERNAL"]},
                )

        # --- Notches (NOTCH layer) ---
        for notch in piece.notches:
            nx, ny = notch.x + ox, notch.y + oy
            # Perpendicular notch mark (small line)
            msp.add_line(
                (nx - 0.5, ny), (nx + 0.5, ny),
                dxfattribs={"layer": self.layers["NOTCH"]},
            )
            msp.add_line(
                (nx, ny - 0.5), (nx, ny + 0.5),
                dxfattribs={"layer": self.layers["NOTCH"]},
            )

        # --- Grainline (GRAIN layer) ---
        if piece.grainline:
            gl = piece.grainline
            start = gl.get("start")
            end = gl.get("end")
            if start and end:
                sx, sy = (start.x, start.y) if hasattr(start, 'x') else (start[0], start[1])
                ex, ey = (end.x, end.y) if hasattr(end, 'x') else (end[0], end[1])
                msp.add_line(
                    (sx + ox, sy + oy), (ex + ox, ey + oy),
                    dxfattribs={"layer": self.layers["GRAIN"], "linetype": "PHANTOM"},
                )
                # Arrow heads (simple)
                angle = math.atan2(ey - sy, ex - sx)
                arrow_size = 1.5
                for ang in [angle + math.pi - 0.4, angle + math.pi + 0.4]:
                    msp.add_line(
                        (sx + ox, sy + oy),
                        (sx + ox + arrow_size * math.cos(ang),
                         sy + oy + arrow_size * math.sin(ang)),
                        dxfattribs={"layer": self.layers["GRAIN"]},
                    )

        # --- Internal lines ---
        for iline in piece.internal_lines:
            pts = iline.get("points", [])
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    p1 = pts[i]
                    p2 = pts[i + 1]
                    # Handle both Point objects and tuples
                    x1, y1 = (p1.x, p1.y) if hasattr(p1, 'x') else (p1[0], p1[1])
                    x2, y2 = (p2.x, p2.y) if hasattr(p2, 'x') else (p2[0], p2[1])
                    msp.add_line(
                        (x1 + ox, y1 + oy),
                        (x2 + ox, y2 + oy),
                        dxfattribs={"layer": self.layers["INTERNAL"]},
                    )

        # --- Mirror axis ---
        if piece.mirror_axis:
            ma = piece.mirror_axis
            s = ma.get("start")
            e = ma.get("end")
            if s and e:
                sx, sy = (s.x, s.y) if hasattr(s, 'x') else (s[0], s[1])
                ex, ey = (e.x, e.y) if hasattr(e, 'x') else (e[0], e[1])
                msp.add_line(
                    (sx + ox, sy + oy),
                    (ex + ox, ey + oy),
                    dxfattribs={
                        "layer": self.layers["MIRROR"],
                        "linetype": "CENTER",
                    },
                )

        # --- Annotations (text) ---
        for ann in piece.annotations:
            pos = ann.get("pos", (0, 0))
            text = ann.get("text", "")
            msp.add_text(
                text,
                dxfattribs={
                    "layer": self.layers["ANNOTATION"],
                    "height": 1.5,
                    "rotation": 0,
                },
            ).set_placement((pos[0] + ox, pos[1] + oy))

    def _add_header(self, msp, garment_type: str,
                    measurements: Optional[Measurements]):
        """Add a header block with garment info."""
        header_y = 5
        info_lines = [
            f"GARMENT: {garment_type.upper()}",
            f"DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
        if measurements:
            info_lines.extend([
                f"BUST: {measurements.bust}cm",
                f"WAIST: {measurements.waist}cm",
                f"HIP: {measurements.hip}cm",
            ])

        for i, line in enumerate(info_lines):
            msp.add_text(
                line,
                dxfattribs={
                    "layer": self.layers["ANNOTATION"],
                    "height": 1.2,
                },
            ).set_placement((-5, header_y + i * 2))


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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_garment_type
            ON templates(garment_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_hash
            ON templates(file_hash)
        """)
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
        """Find the closest matching template by measurements."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, garment_type, measurements_json, file_path, metadata_json
            FROM templates
            WHERE garment_type = ?
            ORDER BY created_at DESC
        """, (garment_type,))
        rows = cursor.fetchall()
        conn.close()

        best_match = None
        best_score = float("inf")

        for row in rows:
            stored_measurements = json.loads(row[2])
            score = self._measurement_distance(measurements, stored_measurements)
            if score < best_score and score < tolerance * len(measurements):
                best_score = score
                best_match = {
                    "id": row[0],
                    "garment_type": row[1],
                    "measurements": stored_measurements,
                    "file_path": row[3],
                    "metadata": json.loads(row[4]) if row[4] else {},
                    "score": best_score,
                }

        return best_match

    def _measurement_distance(self, m1: dict, m2: dict) -> float:
        """Euclidean distance between two measurement sets (numeric fields only)."""
        common_keys = set(m1.keys()) & set(m2.keys())
        if not common_keys:
            return float("inf")
        dist = 0.0
        for k in common_keys:
            try:
                v1 = float(m1.get(k, 0))
                v2 = float(m2.get(k, 0))
            except (ValueError, TypeError):
                continue  # skip non-numeric fields like 'ease'
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
                FROM templates WHERE garment_type = ?
                ORDER BY created_at DESC
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

def generate_pattern(measurements: Measurements | dict,
                     garment_type: str = "dress",
                     ease: str = "standard",
                     output_path: str = None) -> str:
    """
    Master function — takes measurements and garment type, returns path to DXF.

    Args:
        measurements: Measurements object or dict of measurement values (cm)
        garment_type: dress | kurti | bodice | skirt | shirt | sleeve
        ease: minimal | standard | loose
        output_path: where to save the DXF file (auto-generated if None)

    Returns:
        Path to the generated DXF file.
    """
    # Normalise measurements
    if isinstance(measurements, dict):
        m = Measurements(**{k: v for k, v in measurements.items()
                           if hasattr(Measurements, k)})
    else:
        m = measurements

    if not m.validate():
        raise ValueError(
            f"Insufficient measurements. Missing: {m.missing_keys()}"
        )

    # Draft
    engine = DraftingEngine(m, ease=ease)
    pieces = engine.draft(garment_type)

    # Export
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(config.OUTPUT_DIR,
                                   f"{garment_type}_pattern_{timestamp}.dxf")

    exporter = DXFExporter()
    exporter.export(pieces, output_path, garment_type=garment_type,
                    measurements=m)

    return output_path


def generate_spec_summary(measurements: Measurements | dict,
                          garment_type: str = "dress",
                          ease: str = "standard") -> str:
    """
    Returns a human-readable garment specification summary.
    """
    if isinstance(measurements, dict):
        m = Measurements(**{k: v for k, v in measurements.items()
                           if hasattr(Measurements, k)})
    else:
        m = measurements

    engine = DraftingEngine(m, ease=ease)
    ease_val = engine.ease_value
    skirt_ease = engine.skirt_ease

    summary = f"""📋 GARMENT SPECIFICATION SUMMARY
═══════════════════════════════════

GARMENT TYPE: {garment_type.upper()}
EASE: {ease.upper()} (bodice +{ease_val}cm, skirt +{skirt_ease}cm)
SEAM ALLOWANCE: {config.SEAM_ALLOWANCE}cm
HEM ALLOWANCE: {config.HEM_ALLOWANCE}cm

── BODY MEASUREMENTS ──
Bust:   {m.bust:>6.1f} cm
Waist:  {m.waist:>6.1f} cm
Hip:    {m.hip:>6.1f} cm
"""

    if m.shoulder_width:
        summary += f"Shoulder: {m.shoulder_width:>5.1f} cm\n"
    if m.back_length:
        summary += f"Back Length: {m.back_length:>4.1f} cm\n"
    if m.sleeve_length:
        summary += f"Sleeve Length: {m.sleeve_length:>2.1f} cm\n"
    if m.armhole_depth:
        summary += f"Armhole Depth: {m.armhole_depth:>2.1f} cm\n"

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
        summary += f"  • {p.name} ({p.piece_type})\n"
        if p.darts:
            summary += f"    Darts: {len(p.darts)}\n"
        if p.notches:
            summary += f"    Notches: {len(p.notches)}\n"
        if p.grainline:
            summary += f"    Grainline: {p.grainline.get('direction', 'vertical')}\n"

    summary += f"""
── CONSTRUCTION NOTES ──
• All pieces drafted on grain (vertical grainlines)
• Seam allowance: {config.SEAM_ALLOWANCE}cm on all edges
• Hem allowance: {config.HEM_ALLOWANCE}cm
• Mirror axes marked for CF/CB pieces
• Notches indicate balance points and alignment marks
• DXF export uses AAMA layer standards (Optitex/Gerber/Lectra compatible)

Ready to draft? Reply /confirm to generate the DXF file.
"""
    return summary


# ====================================================================
# CLI ENTRY POINT (for testing)
# ====================================================================

if __name__ == "__main__":
    # Example usage
    m = Measurements(
        bust=92, waist=72, hip=96,
        shoulder_width=38, back_length=40,
        sleeve_length=58, armhole_depth=22,
        skirt_length=60, dress_length=100,
    )
    print("=== DRESS PATTERN GENERATION ===\n")
    print(generate_spec_summary(m, "dress"))
    path = generate_pattern(m, "dress", output_path="test_dress.dxf")
    print(f"\nDXF saved to: {path}")

    # Test template DB
    db = TemplateDB()
    db.store_template("dress", asdict(m),
                      file_path="test_dress.dxf",
                      size_label="M")
    print(f"\nTemplates: {len(db.list_templates('dress'))} dress templates stored")
    match = db.find_match("dress", asdict(m))
    if match:
        print(f"Best match: template #{match['id']} (score: {match['score']:.2f})")
