"""
generator.py — Core apparel pattern drafting engine.

This module provides a complete, measurement-driven pattern drafting system
for an apparel pattern drafting Telegram bot. It computes bodice, sleeve,
skirt, collar, cowl, and facing pattern pieces from body measurements and
style details, then exports them to DXF (AAMA-compliant) using ezdxf.

All coordinates are in centimetres.  Origin (0, 0) is:
    - centre-front-neck for bodice pieces (front faces right, back faces left)
    - centre-top for sleeves (grainline vertical)

Every pattern piece is computed from actual measurements — no hardcoded
static patterns.  If an unknown garment type is requested, a basic bodice
block is drafted.
"""

from dataclasses import dataclass, field
from typing import Optional
import math


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEAM_ALLOWANCE = 1.0      # cm
HEM_ALLOWANCE = 2.5       # cm

EASE_VALUES = {
    "minimal": 2.0,
    "standard": 4.0,
    "loose": 8.0,
    "none": 0.0,
    "fitted": 1.0,
    "comfort": 6.0,
}

# AAMA layer names and DXF colours
LAYER_CUT = "1"
LAYER_SEAM = "8"
LAYER_GRAIN = "4"
LAYER_NOTCH = "3"
LAYER_INTERNAL = "4"
LAYER_REFERENCE = "6"
LAYER_ANNOTATION = "7"
LAYER_MIRROR = "9"

AAMA_LAYERS = {
    LAYER_CUT:        {"color": 1,   "name": "1"},
    LAYER_SEAM:       {"color": 8,   "name": "8"},
    LAYER_GRAIN:      {"color": 4,   "name": "4"},
    LAYER_NOTCH:      {"color": 3,   "name": "3"},
    LAYER_INTERNAL:   {"color": 4,   "name": "4"},
    LAYER_REFERENCE:  {"color": 6,   "name": "6"},
    LAYER_ANNOTATION: {"color": 7,   "name": "7"},
    LAYER_MIRROR:     {"color": 9,   "name": "9"},
}

# Garment types supported
GARMENT_TYPES = [
    "dress", "kurti", "top", "blouse", "shirt", "skirt",
    "sleeve", "wrap", "bodice", "gown", "kaftan",
]

DEFAULT_BEZIER_SEGMENTS = 16


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Measurements:
    bust: float = 0.0
    waist: float = 0.0
    hip: float = 0.0
    shoulder_width: float = 0.0
    shoulder_length: float = 0.0
    back_length: float = 0.0
    front_length: float = 0.0
    armhole_depth: float = 0.0
    neck_width: float = 0.0
    neck_depth_front: float = 0.0
    neck_depth_back: float = 0.0
    sleeve_length: float = 0.0
    bicep: float = 0.0
    wrist: float = 0.0
    skirt_length: float = 0.0
    dress_length: float = 0.0
    shoulder_to_bust: float = 0.0
    bust_span: float = 0.0
    waist_to_hip: float = 20.0
    apex_to_apex: float = 0.0
    shoulder_slope: float = 4.0
    dart_intake_bust: float = 0.0
    dart_intake_waist_front: float = 0.0
    dart_intake_waist_back: float = 0.0
    ease: str = 'standard'

    def validate(self) -> bool:
        required = ['bust', 'waist', 'hip']
        return all(getattr(self, k, 0) > 0 for k in required)

    def missing_keys(self) -> list[str]:
        return [k for k in ('bust', 'waist', 'hip', 'shoulder_width', 'back_length')
                if getattr(self, k, 0) <= 0]


@dataclass
class StyleDetails:
    silhouette: str = ''
    has_cowl: bool = False
    has_gathers: bool = False
    gather_locations: list = field(default_factory=list)
    has_pleats: bool = False
    pleat_count: int = 0
    asymmetric_hem: bool = False
    drop_shoulder: bool = False
    has_collar: bool = False
    collar_type: str = ''
    closure: str = ''
    size_label: str = ''
    cut_quantities: dict = field(default_factory=dict)
    measurements_table: dict = field(default_factory=dict)
    has_laces: bool = False
    has_notches: bool = True
    has_darts: bool = True


@dataclass
class Point:
    x: float
    y: float

    def to_tuple(self):
        return (self.x, self.y)

    def __add__(self, o):
        return Point(self.x + o.x, self.y + o.y)

    def __sub__(self, o):
        return Point(self.x - o.x, self.y - o.y)

    def __mul__(self, s):
        return Point(self.x * s, self.y * s)

    def __rmul__(self, s):
        return Point(self.x * s, self.y * s)

    def distance_to(self, o):
        return _dist(self, o)


@dataclass
class Dart:
    start: Point
    end: Point
    width: float = 2.0


@dataclass
class Notch:
    point: Point
    depth: float = 0.3
    angle: float = 90.0


@dataclass
class PatternPiece:
    name: str
    points: list          # list of Point objects (outline)
    curves: list          # list of dicts {"start_idx", "end_idx", "control_points", "type"}
    darts: list           # list of Dart
    notches: list         # list of Notch
    grainline: list       # [Point, Point]
    label: str = ''
    cut_quantity: int = 1
    layer: str = '1'

    def to_dict(self) -> dict:
        d = {
            'name': self.name,
            'points': [p.to_tuple() for p in self.points],
            'curves': [
                {
                    'start_idx': c.get('start_idx', 0),
                    'end_idx': c.get('end_idx', 0),
                    'type': c.get('type', 'bezier'),
                }
                for c in self.curves
            ],
            'darts': [
                {'start': d.start.to_tuple(), 'end': d.end.to_tuple(), 'width': d.width}
                for d in self.darts
            ],
            'notches': [
                {'point': n.point.to_tuple(), 'depth': n.depth, 'angle': n.angle}
                for n in self.notches
            ],
            'grainline': [g.to_tuple() for g in self.grainline],
            'label': self.label,
            'cut_quantity': self.cut_quantity,
            'layer': self.layer,
        }
        # Include slash fold lines if present (for blueprint rendering)
        if hasattr(self, '_slash_fold_lines') and self._slash_fold_lines:
            d['slash_fold_lines'] = self._slash_fold_lines
        return d


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _dist(p1: Point, p2: Point) -> float:
    """Euclidean distance between two points."""
    return math.hypot(p2.x - p1.x, p2.y - p1.y)


def _midpoint(p1: Point, p2: Point) -> Point:
    """Midpoint between two points."""
    return Point((p1.x + p2.x) / 2.0, (p1.y + p2.y) / 2.0)


def _polar(origin: Point, angle_deg: float, length: float) -> Point:
    """Point at *angle_deg* and *length* from *origin*.

    Angle is measured in standard math convention (0 deg = +X, 90 deg = +Y).
    """
    rad = math.radians(angle_deg)
    return Point(origin.x + length * math.cos(rad),
                 origin.y + length * math.sin(rad))


def _angle(p1: Point, p2: Point) -> float:
    """Angle in degrees from p1 to p2."""
    return math.degrees(math.atan2(p2.y - p1.y, p2.x - p1.x))


def _perp_point(p1: Point, p2: Point, offset: float) -> Point:
    """Point offset perpendicular to the line p1->p2 by *offset* distance.

    Positive offset is to the left of the direction p1->p2.
    """
    d = _dist(p1, p2)
    if d == 0:
        return Point(p1.x, p1.y)
    dx = (p2.x - p1.x) / d
    dy = (p2.y - p1.y) / d
    nx = -dy
    ny = dx
    return Point(p1.x + nx * offset, p1.y + ny * offset)


def _offset_polyline(points: list, distance: float) -> list:
    """Offset a polyline by *distance* outward (to the left of traversal).

    Returns a new list of Point objects.  Each vertex is shifted by the
    average of the perpendiculars of its two adjacent edges.
    """
    if len(points) < 2:
        return [Point(p.x, p.y) for p in points]

    result = []
    n = len(points)
    for i in range(n):
        if i == 0:
            p_prev = points[0]
            p_curr = points[1]
        elif i == n - 1:
            p_prev = points[n - 2]
            p_curr = points[n - 1]
        else:
            p_prev = points[i - 1]
            p_curr = points[i]

        d = _dist(p_prev, p_curr)
        if d == 0:
            result.append(Point(points[i].x, points[i].y))
            continue
        dx = (p_curr.x - p_prev.x) / d
        dy = (p_curr.y - p_prev.y) / d
        nx = -dy
        ny = dx
        result.append(Point(points[i].x + nx * distance,
                            points[i].y + ny * distance))
    return result


def _cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point,
                  n: int = DEFAULT_BEZIER_SEGMENTS) -> list:
    """Cubic Bezier curve from p0 to p3 with control points p1, p2.

    Returns n+1 Point objects.
    """
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = (mt**3 * p0.x + 3 * mt**2 * t * p1.x +
             3 * mt * t**2 * p2.x + t**3 * p3.x)
        y = (mt**3 * p0.y + 3 * mt**2 * t * p1.y +
             3 * mt * t**2 * p2.y + t**3 * p3.y)
        pts.append(Point(x, y))
    return pts


def _quadratic_bezier(p0: Point, ctrl: Point, p2: Point,
                      n: int = 12) -> list:
    """Quadratic Bezier curve from p0 to p2 with control point *ctrl*.

    Returns n+1 Point objects.
    """
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt**2 * p0.x + 2 * mt * t * ctrl.x + t**2 * p2.x
        y = mt**2 * p0.y + 2 * mt * t * ctrl.y + t**2 * p2.y
        pts.append(Point(x, y))
    return pts


def _arc_points(center: Point, radius: float, start_angle: float,
                end_angle: float, n: int = 16) -> list:
    """Arc interpolation.  Angles in degrees.  Returns n+1 Point objects."""
    pts = []
    for i in range(n + 1):
        t = i / n
        ang = math.radians(start_angle + t * (end_angle - start_angle))
        pts.append(Point(center.x + radius * math.cos(ang),
                        center.y + radius * math.sin(ang)))
    return pts


def _armhole_curve(shoulder_tip: Point, side_point: Point,
                   depth_bulge: float) -> list:
    """Smooth armhole curve from shoulder tip down to side-seam point.

    *depth_bulge* controls how far the curve bulges inward (toward body).
    """
    mid = _midpoint(shoulder_tip, side_point)
    ang = _angle(shoulder_tip, side_point)
    inward = _polar(mid, ang + 90, depth_bulge)
    return _quadratic_bezier(shoulder_tip, inward, side_point, n=20)


def _neckline_curve(cf_point: Point, shoulder_point: Point,
                    depth_bulge: float) -> list:
    """Smooth neckline curve from centre-front point to shoulder-neck point.

    *depth_bulge* controls how far the curve dips below the straight line.
    """
    mid = _midpoint(cf_point, shoulder_point)
    ang = _angle(cf_point, shoulder_point)
    ctrl = _polar(mid, ang - 90, depth_bulge)
    return _quadratic_bezier(cf_point, ctrl, shoulder_point, n=16)


def _sleeve_cap_curve(p0: Point, p1: Point, p2: Point, p3: Point) -> list:
    """Sleeve cap curve — cubic Bezier through control points p1, p2."""
    return _cubic_bezier(p0, p1, p2, p3, n=24)


def _smooth_closed_curve(points: list, bulge: float = 0.3) -> list:
    """Smooth a closed polyline by inserting quadratic Bezier arcs at each
    vertex.  Returns a denser list of points.
    """
    if len(points) < 3:
        return points
    result = []
    n = len(points)
    for i in range(n):
        prev = points[(i - 1) % n]
        curr = points[i]
        nxt = points[(i + 1) % n]
        mid_prev = _midpoint(prev, curr)
        mid_next = _midpoint(curr, nxt)
        result.extend(_quadratic_bezier(mid_prev, curr, mid_next, n=8)[:-1])
    result.append(result[0])
    return result


# ---------------------------------------------------------------------------
# Drafting Engine
# ---------------------------------------------------------------------------

class DraftingEngine:
    """Measurement-driven pattern drafting engine.

    Computes derived measurements and drafts pattern pieces for various
    garment types.  Every piece is computed from actual body measurements;
    no static/hardcoded patterns are used.
    """

    def __init__(self, measurements: Measurements,
                 style: Optional[StyleDetails] = None,
                 ease: str = 'standard'):
        self.m = measurements
        self.style = style if style is not None else StyleDetails()
        self.ease_label = ease if ease in EASE_VALUES else 'standard'
        self.ease_amount = EASE_VALUES.get(self.ease_label, 4.0)

        # Derived measurements (all in cm)
        self._compute_derived()

        # Pieces assembled during drafting
        self.pieces: list = []

    # ---- derived measurements ------------------------------------------------

    def _compute_derived(self):
        """Compute half-measurements and ease-adjusted values."""
        m = self.m
        e = self.ease_amount

        self.half_bust = (m.bust + e) / 2.0
        self.half_waist = (m.waist + e) / 2.0
        self.half_hip = (m.hip + e) / 2.0

        self.quarter_bust = (m.bust + e) / 4.0
        self.quarter_waist = (m.waist + e) / 4.0
        self.quarter_hip = (m.hip + e) / 4.0

        if m.neck_width <= 0:
            self.neck_width = m.bust / 6.0 + 2.5 if m.bust > 0 else 14.0
        else:
            self.neck_width = m.neck_width

        self.neck_depth_front = (m.neck_depth_front
                                  if m.neck_depth_front > 0
                                  else self.neck_width / 2.0 + 1.0)
        self.neck_depth_back = (m.neck_depth_back
                                 if m.neck_depth_back > 0
                                 else self.neck_width / 4.0 + 0.5)

        if m.armhole_depth <= 0:
            self.armhole_depth = m.bust / 8.0 + 7.0 if m.bust > 0 else 21.0
        else:
            self.armhole_depth = m.armhole_depth

        if m.shoulder_width <= 0:
            self.shoulder_width = m.bust / 4.0 if m.bust > 0 else 22.0
        else:
            self.shoulder_width = m.shoulder_width

        self.half_shoulder = self.shoulder_width / 2.0

        if m.back_length <= 0:
            self.back_length = m.bust / 4.0 + 10.0 if m.bust > 0 else 40.0
        else:
            self.back_length = m.back_length

        if m.front_length <= 0:
            self.front_length = self.back_length
        else:
            self.front_length = m.front_length

        if m.apex_to_apex <= 0:
            self.apex_to_apex = m.bust / 5.0 if m.bust > 0 else 18.0
        else:
            self.apex_to_apex = m.apex_to_apex

        self.half_apex = self.apex_to_apex / 2.0

        if m.shoulder_to_bust <= 0:
            self.shoulder_to_bust = self.back_length / 2.0 + 3.0
        else:
            self.shoulder_to_bust = m.shoulder_to_bust

        if m.bust_span <= 0:
            self.bust_span = self.apex_to_apex / 2.0 + 2.0
        else:
            self.bust_span = m.bust_span

        self.bust_dart_intake = (m.dart_intake_bust
                                 if m.dart_intake_bust > 0 else 3.0)
        self.waist_dart_front = (m.dart_intake_waist_front
                                  if m.dart_intake_waist_front > 0 else 2.5)
        self.waist_dart_back = (m.dart_intake_waist_back
                                 if m.dart_intake_waist_back > 0 else 3.0)

        if m.bicep <= 0:
            self.bicep = m.bust / 3.0 + 4.0 if m.bust > 0 else 30.0
        else:
            self.bicep = m.bicep

        if m.wrist <= 0:
            self.wrist = m.bust / 6.0 + 6.0 if m.bust > 0 else 20.0
        else:
            self.wrist = m.wrist

        if m.skirt_length <= 0:
            self.skirt_length = 60.0
        else:
            self.skirt_length = m.skirt_length

        if m.dress_length <= 0:
            self.dress_length = self.back_length + self.m.waist_to_hip + 30.0
        else:
            self.dress_length = m.dress_length

        self.shoulder_slope = (m.shoulder_slope
                                if m.shoulder_slope > 0 else 4.0)
        self.waist_to_hip = m.waist_to_hip if m.waist_to_hip > 0 else 20.0

    # ---- dispatch ------------------------------------------------------------

    def draft(self, garment_type: str) -> list:
        """Master dispatch: draft pattern pieces for the given garment type.

        Returns a list of PatternPiece objects.
        """
        garment_type = garment_type.lower().strip()
        self.pieces = []

        # Asymmetric cowl-drape top takes priority when both cowl and
        # gathers are flagged — regardless of garment_type keyword.
        if self.style.has_cowl and self.style.has_gathers:
            self._draft_asymmetric_cowl_top()
            return self.pieces

        dispatch = {
            "dress": self._draft_dress,
            "kurti": self._draft_kurti,
            "top": self._draft_top,
            "blouse": self._draft_blouse,
            "shirt": self._draft_shirt,
            "skirt": self._draft_skirt,
            "sleeve": self._draft_sleeve_only,
            "wrap": self._draft_wrap,
            "bodice": self._draft_bodice,
            "gown": self._draft_gown,
            "kaftan": self._draft_kaftan,
        }

        handler = dispatch.get(garment_type)
        if handler is None:
            handler = self._draft_bodice

        handler()
        return self.pieces

    # ---- bodice front --------------------------------------------------------

    def draft_bodice_front(self) -> PatternPiece:
        """Draft a bodice front block.  Origin at centre-front-neck (0, 0).

        Front piece faces right (+x is away from CF toward side seam).
        """
        half_bust = self.quarter_bust
        half_waist = self.quarter_waist

        cf_neck = Point(0, 0)
        cf_waist = Point(0, -self.back_length)
        cf_hem = Point(0, -self.back_length - 4.0)

        neck_shoulder = Point(self.neck_width / 2.0, -self.neck_depth_back * 0.3)
        shoulder_tip = _polar(neck_shoulder, -self.shoulder_slope,
                             self.half_shoulder)

        armhole_top = Point(shoulder_tip.x, -self.armhole_depth * 0.7)
        side_top = Point(half_bust, -self.armhole_depth)
        side_waist = Point(half_waist, -self.back_length)
        side_hem = Point(half_waist, -self.back_length - 4.0)

        neckline_pts = _neckline_curve(cf_neck, neck_shoulder,
                                       self.neck_depth_front * 0.6)
        armhole_pts = _armhole_curve(shoulder_tip, side_top,
                                     self.armhole_depth * 0.15)

        full_outline = [cf_neck]
        full_outline.extend(neckline_pts[1:-1])
        full_outline.append(neck_shoulder)
        full_outline.append(shoulder_tip)
        full_outline.extend(armhole_pts[1:-1])
        full_outline.append(side_top)
        full_outline.append(side_waist)
        full_outline.append(cf_waist)
        full_outline.append(cf_hem)
        full_outline.append(side_hem)

        neck_curve_end_idx = len(neckline_pts) - 1
        arm_curve_end_idx = neck_curve_end_idx + len(armhole_pts)

        curves = [
            {"start_idx": 0, "end_idx": neck_curve_end_idx,
             "control_points": [cf_neck, neck_shoulder], "type": "bezier"},
            {"start_idx": neck_curve_end_idx + 1,
             "end_idx": arm_curve_end_idx,
             "control_points": [shoulder_tip, side_top], "type": "bezier"},
        ]

        darts = []
        if self.style.has_darts:
            apex = Point(self.bust_span, -self.shoulder_to_bust)
            dart_start = Point(side_top.x - 1.5, side_top.y - 2.0)
            darts.append(Dart(start=dart_start, end=apex,
                              width=self.bust_dart_intake))

            waist_dart_start = Point(0, -self.back_length + 0.5)
            waist_dart_end = Point(self.half_apex, -self.shoulder_to_bust - 2.0)
            darts.append(Dart(start=waist_dart_start, end=waist_dart_end,
                              width=self.waist_dart_front))

        notches = []
        if self.style.has_notches:
            arm_notch_pt = _midpoint(shoulder_tip, side_top)
            notches.append(Notch(point=arm_notch_pt, depth=0.3, angle=0))
            notches.append(Notch(point=Point(self.half_apex, -self.back_length),
                                depth=0.3, angle=90))
            notches.append(Notch(point=Point(0, -self.back_length / 2.0),
                                depth=0.3, angle=90))

        cx = half_bust / 2.0
        grainline = [Point(cx, -2.0), Point(cx, -self.back_length + 2.0)]

        piece = PatternPiece(
            name="Bodice Front",
            points=full_outline,
            curves=curves,
            darts=darts,
            notches=notches,
            grainline=grainline,
            label=f"Bodice Front - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("bodice_front", 1),
            layer=LAYER_CUT,
        )

        self._apply_styles_to_piece(piece, is_front=True)
        return piece

    # ---- bodice back ---------------------------------------------------------

    def draft_bodice_back(self) -> PatternPiece:
        """Draft a bodice back block.  Origin at centre-back-neck (0, 0).

        Back piece faces left (mirrored x).
        """
        half_bust = self.quarter_bust
        half_waist = self.quarter_waist

        cb_neck = Point(0, 0)
        cb_waist = Point(0, -self.back_length)
        cb_hem = Point(0, -self.back_length - 4.0)

        neck_shoulder = Point(-self.neck_width / 2.0, -self.neck_depth_back * 0.3)
        shoulder_tip = _polar(neck_shoulder, 180 + self.shoulder_slope,
                             self.half_shoulder)

        armhole_top = Point(shoulder_tip.x, -self.armhole_depth * 0.7)
        side_top = Point(-half_bust, -self.armhole_depth)
        side_waist = Point(-half_waist, -self.back_length)
        side_hem = Point(-half_waist, -self.back_length - 4.0)

        neckline_pts = _neckline_curve(cb_neck, neck_shoulder,
                                        self.neck_depth_back * 0.5)
        armhole_pts = _armhole_curve(shoulder_tip, side_top,
                                     self.armhole_depth * 0.15)

        full_outline = [cb_neck]
        full_outline.extend(neckline_pts[1:-1])
        full_outline.append(neck_shoulder)
        full_outline.append(shoulder_tip)
        full_outline.extend(armhole_pts[1:-1])
        full_outline.append(side_top)
        full_outline.append(side_waist)
        full_outline.append(cb_waist)
        full_outline.append(cb_hem)
        full_outline.append(side_hem)

        neck_curve_end_idx = len(neckline_pts) - 1
        arm_curve_end_idx = neck_curve_end_idx + len(armhole_pts)

        curves = [
            {"start_idx": 0, "end_idx": neck_curve_end_idx,
             "control_points": [cb_neck, neck_shoulder], "type": "bezier"},
            {"start_idx": neck_curve_end_idx + 1,
             "end_idx": arm_curve_end_idx,
             "control_points": [shoulder_tip, side_top], "type": "bezier"},
        ]

        darts = []
        if self.style.has_darts:
            shoulder_dart_start = _midpoint(neck_shoulder, shoulder_tip)
            shoulder_dart_end = Point(shoulder_dart_start.x - 1.0,
                                       shoulder_dart_start.y - 4.0)
            darts.append(Dart(start=shoulder_dart_start, end=shoulder_dart_end,
                             width=1.5))

            waist_dart_start = Point(-self.half_apex * 0.7, -self.back_length + 0.5)
            waist_dart_end = Point(-self.half_apex * 0.7, -self.shoulder_to_bust)
            darts.append(Dart(start=waist_dart_start, end=waist_dart_end,
                             width=self.waist_dart_back))

        notches = []
        if self.style.has_notches:
            arm_notch_pt = _midpoint(shoulder_tip, side_top)
            notches.append(Notch(point=arm_notch_pt, depth=0.3, angle=180))
            notches.append(Notch(point=Point(-self.half_apex, -self.back_length),
                                depth=0.3, angle=90))
            notches.append(Notch(point=Point(0, -self.back_length / 2.0),
                                depth=0.3, angle=90))

        cx = -half_bust / 2.0
        grainline = [Point(cx, -2.0), Point(cx, -self.back_length + 2.0)]

        piece = PatternPiece(
            name="Bodice Back",
            points=full_outline,
            curves=curves,
            darts=darts,
            notches=notches,
            grainline=grainline,
            label=f"Bodice Back - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("bodice_back", 1),
            layer=LAYER_CUT,
        )

        self._apply_styles_to_piece(piece, is_front=False)
        return piece

    # ---- sleeve ---------------------------------------------------------------

    def draft_sleeve(self) -> PatternPiece:
        """Draft a sleeve.  Origin at centre-top of sleeve cap (0, 0).

        Grainline is vertical (downward = -y).
        """
        sleeve_len = self.m.sleeve_length if self.m.sleeve_length > 0 else 60.0
        half_bicep = self.bicep / 2.0
        half_wrist = self.wrist / 2.0

        cap_height = self.bicep / 3.0 + 3.0
        cap_width = half_bicep + 1.0

        cap_top = Point(0, 0)
        cap_left = Point(-cap_width, -cap_height)
        cap_right = Point(cap_width, -cap_height)

        wrist_left = Point(-half_wrist, -sleeve_len)
        wrist_right = Point(half_wrist, -sleeve_len)

        ctrl1 = Point(cap_width * 0.6, -cap_height * 0.3)
        ctrl2 = Point(cap_width * 0.3, -cap_height * 0.9)
        cap_curve_right = _sleeve_cap_curve(cap_top, ctrl1, ctrl2, cap_right)

        ctrl1l = Point(-cap_width * 0.6, -cap_height * 0.3)
        ctrl2l = Point(-cap_width * 0.3, -cap_height * 0.9)
        cap_curve_left = _sleeve_cap_curve(cap_top, ctrl1l, ctrl2l, cap_left)

        full_outline = [cap_top]
        full_outline.extend(cap_curve_right[1:-1])
        full_outline.append(cap_right)
        full_outline.append(wrist_right)
        full_outline.append(wrist_left)
        full_outline.append(cap_left)
        full_outline.extend(list(reversed(cap_curve_left))[1:-1])

        right_curve_end = len(cap_curve_right)
        curves = [
            {"start_idx": 0, "end_idx": right_curve_end,
             "control_points": [cap_top, ctrl1, ctrl2, cap_right],
             "type": "bezier"},
            {"start_idx": right_curve_end + 3,
             "end_idx": len(full_outline) - 1,
             "control_points": [cap_left, ctrl2l, ctrl1l, cap_top],
             "type": "bezier"},
        ]

        notches = []
        if self.style.has_notches:
            notch_idx = len(cap_curve_right) // 3
            if notch_idx < len(cap_curve_right):
                notches.append(Notch(point=cap_curve_right[notch_idx],
                                    depth=0.3, angle=90))
            notch_idx = len(cap_curve_left) // 3
            if notch_idx < len(cap_curve_left):
                notches.append(Notch(point=cap_curve_left[notch_idx],
                                    depth=0.3, angle=90))
            notches.append(Notch(point=Point(0, -sleeve_len * 0.5),
                                depth=0.3, angle=90))

        grainline = [Point(0, -cap_height), Point(0, -sleeve_len + 1.0)]

        piece = PatternPiece(
            name="Sleeve",
            points=full_outline,
            curves=curves,
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Sleeve - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("sleeve", 2),
            layer=LAYER_CUT,
        )

        if sleeve_len > 40 and self.style.has_darts:
            elbow = Point(0, -sleeve_len * 0.5)
            dart_start = Point(-half_bicep * 0.5, -sleeve_len * 0.5)
            dart_end = Point(half_bicep * 0.5, -sleeve_len * 0.5)
            piece.darts.append(Dart(start=dart_start, end=dart_end, width=1.5))

        return piece

    # ---- skirt front ----------------------------------------------------------

    def draft_skirt_front(self) -> PatternPiece:
        """Draft a skirt front panel.  Origin at centre-front-waist (0, 0).

        Front faces right (+x toward side seam).
        """
        half_hip = self.quarter_hip
        half_waist = self.quarter_waist
        skirt_len = self.skirt_length

        cf_waist = Point(0, 0)
        cf_hem = Point(0, -skirt_len)
        side_waist = Point(half_hip, -1.5)
        side_hem = Point(half_hip + 3.0, -skirt_len)

        hip_y = -self.waist_to_hip
        side_hip = Point(half_hip, hip_y)

        waist_hip_curve = _quadratic_bezier(side_waist,
                                             Point(half_hip - 0.5, hip_y / 2),
                                             side_hip, n=12)

        full_outline = [cf_waist]
        full_outline.extend(waist_hip_curve[1:-1])
        full_outline.append(side_hip)
        full_outline.append(side_hem)
        full_outline.append(cf_hem)

        curves = [
            {"start_idx": 0, "end_idx": len(waist_hip_curve),
             "control_points": [side_waist, side_hip], "type": "bezier"},
        ]

        darts = []
        if self.style.has_darts:
            dart1_start = Point(self.half_apex, 0)
            dart1_end = Point(self.half_apex, -self.waist_to_hip * 0.6)
            darts.append(Dart(start=dart1_start, end=dart1_end,
                             width=self.waist_dart_front))

            if half_waist < half_hip - 5.0:
                dart2_start = Point(self.half_apex * 0.4, 0)
                dart2_end = Point(self.half_apex * 0.4,
                                  -self.waist_to_hip * 0.5)
                darts.append(Dart(start=dart2_start, end=dart2_end,
                                 width=1.5))

        notches = []
        if self.style.has_notches:
            notches.append(Notch(point=Point(0, hip_y), depth=0.3, angle=90))
            notches.append(Notch(point=side_hip, depth=0.3, angle=0))

        cx = half_hip / 2.0
        grainline = [Point(cx, -2.0), Point(cx, -skirt_len + 2.0)]

        piece = PatternPiece(
            name="Skirt Front",
            points=full_outline,
            curves=curves,
            darts=darts,
            notches=notches,
            grainline=grainline,
            label=f"Skirt Front - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("skirt_front", 1),
            layer=LAYER_CUT,
        )

        if self.style.asymmetric_hem:
            piece.points = self._make_asymmetric_hem(piece.points)

        return piece

    # ---- skirt back -----------------------------------------------------------

    def draft_skirt_back(self) -> PatternPiece:
        """Draft a skirt back panel (mirrored x)."""
        half_hip = self.quarter_hip
        half_waist = self.quarter_waist
        skirt_len = self.skirt_length

        cb_waist = Point(0, 0)
        cb_hem = Point(0, -skirt_len)
        side_waist = Point(-half_hip, -1.5)
        side_hem = Point(-half_hip - 3.0, -skirt_len)

        hip_y = -self.waist_to_hip
        side_hip = Point(-half_hip, hip_y)

        waist_hip_curve = _quadratic_bezier(side_waist,
                                             Point(-half_hip + 0.5, hip_y / 2),
                                             side_hip, n=12)

        full_outline = [cb_waist]
        full_outline.extend(waist_hip_curve[1:-1])
        full_outline.append(side_hip)
        full_outline.append(side_hem)
        full_outline.append(cb_hem)

        curves = [
            {"start_idx": 0, "end_idx": len(waist_hip_curve),
             "control_points": [side_waist, side_hip], "type": "bezier"},
        ]

        darts = []
        if self.style.has_darts:
            dart1_start = Point(-self.half_apex, 0)
            dart1_end = Point(-self.half_apex, -self.waist_to_hip * 0.6)
            darts.append(Dart(start=dart1_start, end=dart1_end,
                             width=self.waist_dart_back))

            if half_waist < half_hip - 5.0:
                dart2_start = Point(-self.half_apex * 0.4, 0)
                dart2_end = Point(-self.half_apex * 0.4,
                                  -self.waist_to_hip * 0.5)
                darts.append(Dart(start=dart2_start, end=dart2_end,
                                 width=1.5))

        notches = []
        if self.style.has_notches:
            notches.append(Notch(point=Point(0, hip_y), depth=0.3, angle=90))
            notches.append(Notch(point=side_hip, depth=0.3, angle=180))

        cx = -half_hip / 2.0
        grainline = [Point(cx, -2.0), Point(cx, -skirt_len + 2.0)]

        piece = PatternPiece(
            name="Skirt Back",
            points=full_outline,
            curves=curves,
            darts=darts,
            notches=notches,
            grainline=grainline,
            label=f"Skirt Back - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("skirt_back", 1),
            layer=LAYER_CUT,
        )

        if self.style.asymmetric_hem:
            piece.points = self._make_asymmetric_hem(piece.points)

        return piece

    # ---- collar ---------------------------------------------------------------

    def draft_collar(self) -> PatternPiece:
        """Draft a collar piece (stand-and-fall, band, mandarin, shawl, etc.).

        Collar is drafted as a rectangle with curved ends; length follows the
        neck circumference, width depends on the collar type.
        """
        neck_circ = self.neck_width * 2.0 + math.pi * (self.neck_width / 2.0)
        collar_length = neck_circ
        collar_type = (self.style.collar_type.lower()
                        if self.style.collar_type else "band")

        if "stand" in collar_type or "fall" in collar_type:
            collar_width = 6.0
            stand_width = 3.5
            total_width = collar_width + stand_width
        elif "mandarin" in collar_type:
            collar_width = 4.0
            total_width = collar_width
        elif "shawl" in collar_type:
            collar_width = 8.0
            total_width = collar_width
        else:
            collar_width = 5.0
            total_width = collar_width

        tl = Point(-collar_length / 2.0, total_width)
        tr = Point(collar_length / 2.0, total_width)
        br = Point(collar_length / 2.0, 0)
        bl = Point(-collar_length / 2.0, 0)

        end_curve_r = _quadratic_bezier(
            tr, Point(collar_length / 2.0 + 1.0, total_width / 2.0), br, n=10)
        end_curve_l = _quadratic_bezier(
            bl, Point(-collar_length / 2.0 - 1.0, total_width / 2.0), tl, n=10)

        full_outline = [tl]
        full_outline.extend(end_curve_r[1:-1])
        full_outline.append(br)
        full_outline.append(bl)
        full_outline.extend(list(reversed(end_curve_l))[1:-1])

        curves = [
            {"start_idx": 0, "end_idx": len(end_curve_r),
             "control_points": [tr, br], "type": "bezier"},
            {"start_idx": len(end_curve_r) + 2,
             "end_idx": len(full_outline) - 1,
             "control_points": [bl, tl], "type": "bezier"},
        ]

        grainline = [Point(-collar_length / 2.0 + 1.0, total_width / 2.0),
                     Point(collar_length / 2.0 - 1.0, total_width / 2.0)]

        notches = []
        if self.style.has_notches:
            notches.append(Notch(point=Point(0, total_width / 2.0),
                                 depth=0.3, angle=90))
            notches.append(Notch(point=Point(-collar_length / 2.0 + 1.0,
                                             total_width / 2.0),
                                 depth=0.3, angle=0))
            notches.append(Notch(point=Point(collar_length / 2.0 - 1.0,
                                             total_width / 2.0),
                                 depth=0.3, angle=0))

        piece = PatternPiece(
            name="Collar",
            points=full_outline,
            curves=curves,
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Collar ({collar_type}) - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("collar", 1),
            layer=LAYER_CUT,
        )
        return piece

    # ---- cowl -----------------------------------------------------------------

    def draft_cowl(self) -> PatternPiece:
        """Draft a cowl neckline drape piece.

        The cowl is a draped extension from the shoulder/neck area, drafted
        as a rectangle with a cowl curve cut from one side.
        """
        cowl_depth = 12.0
        cowl_width = self.neck_width * 1.5

        tl = Point(0, cowl_depth)
        tr = Point(cowl_width, cowl_depth)
        br = Point(cowl_width, 0)
        bl = Point(0, 0)

        cowl_curve = _cubic_bezier(
            tl,
            Point(cowl_width * 0.1, cowl_depth * 0.5),
            Point(cowl_width * 0.3, cowl_depth * 0.8),
            Point(cowl_width * 0.5, cowl_depth * 0.5),
            n=20)

        full_outline = [tl]
        full_outline.extend(cowl_curve[1:-1])
        full_outline.append(Point(cowl_width * 0.5, cowl_depth * 0.5))
        full_outline.append(tr)
        full_outline.append(br)
        full_outline.append(bl)

        curves = [
            {"start_idx": 0, "end_idx": len(cowl_curve),
             "control_points": [tl, Point(cowl_width * 0.3, cowl_depth * 0.8)],
             "type": "bezier"},
        ]

        grainline = [Point(cowl_width / 2.0, cowl_depth / 2.0),
                     Point(cowl_width / 2.0, cowl_depth / 2.0 - 3.0)]

        notches = []
        if self.style.has_notches:
            notches.append(Notch(point=Point(0, cowl_depth / 2.0),
                                 depth=0.3, angle=90))

        piece = PatternPiece(
            name="Cowl",
            points=full_outline,
            curves=curves,
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Cowl Drape - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("cowl", 1),
            layer=LAYER_CUT,
        )
        return piece

    # ---- facing ---------------------------------------------------------------

    def draft_facing(self) -> PatternPiece:
        """Draft a front facing piece.

        The facing follows the neckline and front edge, extending inward by
        a facing width.
        """
        facing_width = 5.0

        cf_neck = Point(0, 0)
        neck_shoulder = Point(self.neck_width / 2.0,
                              -self.neck_depth_back * 0.3)
        shoulder_tip = _polar(neck_shoulder, -self.shoulder_slope,
                              self.half_shoulder)

        neckline_pts = _neckline_curve(cf_neck, neck_shoulder,
                                        self.neck_depth_front * 0.6)

        shoulder_facing_end = _polar(
            shoulder_tip,
            _angle(neck_shoulder, shoulder_tip),
            facing_width)

        facing_bottom_inner = Point(0, -self.neck_depth_front - facing_width - 3.0)
        facing_bottom_outer = Point(self.neck_width / 2.0 + facing_width,
                                     -self.neck_depth_front - facing_width - 3.0)

        full_outline = [cf_neck]
        full_outline.extend(neckline_pts[1:-1])
        full_outline.append(neck_shoulder)
        full_outline.append(shoulder_tip)
        full_outline.append(shoulder_facing_end)
        full_outline.append(facing_bottom_outer)
        full_outline.append(facing_bottom_inner)

        curves = [
            {"start_idx": 0, "end_idx": len(neckline_pts),
             "control_points": [cf_neck, neck_shoulder], "type": "bezier"},
        ]

        grainline = [Point(facing_width / 2.0, -2.0),
                     Point(facing_width / 2.0, -self.neck_depth_front - 2.0)]

        notches = []
        if self.style.has_notches:
            notches.append(Notch(point=_midpoint(cf_neck, neck_shoulder),
                                 depth=0.3, angle=90))

        piece = PatternPiece(
            name="Front Facing",
            points=full_outline,
            curves=curves,
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Front Facing - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("facing", 1),
            layer=LAYER_CUT,
        )
        return piece

    # ---- style modifications --------------------------------------------------

    def _apply_styles_to_piece(self, piece: PatternPiece, is_front: bool):
        """Apply style modifications to a drafted piece in-place."""
        style = self.style

        if style.has_gathers:
            locations = style.gather_locations if style.gather_locations else ["waist"]
            for loc in locations:
                if loc == "waist":
                    notch_pt = Point(piece.points[0].x / 2.0,
                                     -self.back_length + 1.0)
                    piece.notches.append(Notch(point=notch_pt, depth=0.5, angle=90))
                    piece.label += " (Gathered at waist)"
                elif loc == "neckline":
                    if len(piece.points) > 1:
                        notch_pt = _midpoint(piece.points[0], piece.points[1])
                    else:
                        notch_pt = piece.points[0]
                    piece.notches.append(Notch(point=notch_pt, depth=0.5, angle=90))
                    piece.label += " (Gathered neckline)"
                elif loc == "hem":
                    notch_pt = Point(piece.points[0].x / 2.0,
                                     -self.back_length - 2.0)
                    piece.notches.append(Notch(point=notch_pt, depth=0.5, angle=90))
                    piece.label += " (Gathered hem)"
                elif loc == "sleeve_cap":
                    notch_pt = Point(piece.points[0].x / 2.0,
                                     -self.armhole_depth / 2.0)
                    piece.notches.append(Notch(point=notch_pt, depth=0.5, angle=90))
                    piece.label += " (Gathered sleeve cap)"

        if style.has_pleats and style.pleat_count > 0:
            pleat_spacing = 3.0
            for i in range(style.pleat_count):
                offset = (i + 1) * pleat_spacing
                px = offset if is_front else -offset
                pleat_top = Point(px, -self.back_length * 0.3)
                pleat_bottom = Point(px, -self.back_length - 2.0)
                piece.darts.append(Dart(start=pleat_top, end=pleat_bottom, width=2.0))
            piece.label += f" ({style.pleat_count} pleats)"

        if style.has_cowl and is_front:
            for i in range(min(5, len(piece.points))):
                piece.points[i] = Point(piece.points[i].x,
                                        piece.points[i].y - 3.0)
            piece.label += " (Cowl neck)"

        if style.asymmetric_hem:
            piece.points = self._make_asymmetric_hem(piece.points)
            piece.label += " (Asymmetric hem)"

        if style.drop_shoulder:
            for i, pt in enumerate(piece.points):
                if pt.y < -self.armhole_depth * 0.5 and abs(pt.x) > self.quarter_bust * 0.5:
                    piece.points[i] = Point(pt.x + (2.0 if pt.x > 0 else -2.0),
                                           pt.y - 3.0)
            piece.label += " (Drop shoulder)"

    def _make_asymmetric_hem(self, points: list) -> list:
        """Make the hemline asymmetric by varying the y-coordinate."""
        result = []
        for pt in points:
            if pt.y < -self.back_length:
                asym = abs(pt.x) * 0.15
                result.append(Point(pt.x, pt.y - asym))
            else:
                result.append(pt)
        return result

    # ---- garment type handlers ------------------------------------------------

    # ---- asymmetric cowl-drape top -------------------------------------------

    def draft_asymmetric_cowl_front(self) -> PatternPiece:
        """Front bodice with slash-and-spread radiating drape.

        Uses the classic slash-and-spread technique: a fitted bodice front
        is drafted, then multiple radiating fold lines fan out from a single
        pivot point near one shoulder toward the opposite side/hem.  Each
        slash is spread open progressively to add fullness that becomes the
        draped cowl fabric.

        Origin at centre-front-neck (0, 0).  +x = away from CF toward side.
        The drape cascades from the LEFT shoulder down to the RIGHT side/hem.
        """
        half_bust = self.quarter_bust
        half_waist = self.quarter_waist

        # ── Build fitted bodice front sloper outline ──
        cf_neck = Point(0, 0)
        cf_waist = Point(0, -self.back_length)
        cf_hem = Point(0, -self.back_length - 4.0)

        neck_shoulder_l = Point(-self.neck_width / 2.0, -self.neck_depth_back * 0.3)
        shoulder_tip_l = _polar(neck_shoulder_l, -self.shoulder_slope - 180,
                                self.half_shoulder)

        neck_shoulder_r = Point(self.neck_width / 2.0, -self.neck_depth_back * 0.3)
        shoulder_tip_r = _polar(neck_shoulder_r, -self.shoulder_slope,
                                self.half_shoulder)

        armhole_top = Point(shoulder_tip_r.x, -self.armhole_depth * 0.7)
        side_top = Point(half_bust, -self.armhole_depth)
        side_waist = Point(half_waist, -self.back_length)

        # Lower hem — asymmetric: left side shorter, right side longer
        hem_drop = 6.0  # extra length on the draped (right) side
        side_hem_r = Point(half_bust + 2.0, -self.back_length - 4.0 - hem_drop)
        side_hem_l = Point(-half_bust * 0.3, -self.back_length - 2.0)

        # Neckline — deep cowl on the left side, sweeping to right shoulder
        neckline_pts = _neckline_curve(cf_neck, neck_shoulder_r,
                                       self.neck_depth_front * 0.8)

        # Armhole
        armhole_pts = _armhole_curve(shoulder_tip_r, side_top,
                                     self.armhole_depth * 0.15)

        # ── Slash-and-spread: radiating fold lines ──
        # Pivot near LEFT shoulder neck point
        pivot = Point(neck_shoulder_l.x + 1.0, neck_shoulder_l.y - 2.0)

        num_slashes = 8
        slash_lines = []  # list of (start=pivot, end=Point on outline)
        # Fan from pivot toward right side-seam and lower-right hem
        # Angles sweep from roughly -80° (toward waist) to -30° (toward side)
        angle_start = -100.0
        angle_end = -25.0
        for i in range(num_slashes):
            t = i / (num_slashes - 1)
            ang = angle_start + t * (angle_end - angle_start)
            # Length to reach the opposite edge of the bodice
            spread_len = self.back_length * (0.6 + t * 0.5)
            end = _polar(pivot, ang, spread_len)
            slash_lines.append((pivot, end))

        # Progressive spread amounts (cm per slash, increasing outward)
        spread_per_slash = 2.5  # base spread
        spreads = [spread_per_slash * (1.0 + i * 0.4) for i in range(num_slashes)]

        # ── Build final outline with spread fullness ──
        # Start from CF neck, go right along neckline to right shoulder,
        # down armhole to side seam, down to hem (with asymmetric drop),
        # across hem to left side, up to left shoulder, back to CF neck.
        full_outline = [cf_neck]
        full_outline.extend(neckline_pts[1:-1])
        full_outline.append(neck_shoulder_r)
        full_outline.append(shoulder_tip_r)
        full_outline.extend(armhole_pts[1:-1])
        full_outline.append(side_top)
        full_outline.append(side_waist)
        full_outline.append(side_hem_r)

        # Asymmetric hem: curve from right side hem to left side hem
        # Use a cubic bezier for a graceful draped hemline
        hem_curve = _cubic_bezier(
            side_hem_r,
            Point(half_bust * 0.3, side_hem_r.y + 3.0),
            Point(0, side_hem_l.y + 1.0),
            side_hem_l,
            n=16)
        full_outline.extend(hem_curve[1:-1])
        full_outline.append(side_hem_l)

        # Left side up to left shoulder
        left_side_waist = Point(-half_waist * 0.5, -self.back_length)
        full_outline.append(left_side_waist)
        full_outline.append(shoulder_tip_l)
        full_outline.append(neck_shoulder_l)

        # Back to CF neck
        left_neckline = _neckline_curve(neck_shoulder_l, cf_neck,
                                        self.neck_depth_front * 0.3)
        full_outline.extend(left_neckline[1:-1])

        # ── Curves for DXF ──
        neck_curve_end = len(neckline_pts) - 1
        arm_curve_end = neck_curve_end + len(armhole_pts)
        hem_curve_start = arm_curve_end + 4  # after side_top, side_waist, side_hem_r

        curves = [
            {"start_idx": 0, "end_idx": neck_curve_end,
             "control_points": [cf_neck, neck_shoulder_r], "type": "bezier"},
            {"start_idx": neck_curve_end + 1, "end_idx": arm_curve_end,
             "control_points": [shoulder_tip_r, side_top], "type": "bezier"},
            {"start_idx": hem_curve_start,
             "end_idx": hem_curve_start + len(hem_curve),
             "control_points": [side_hem_r, side_hem_l], "type": "bezier"},
        ]

        # ── Store slash fold lines as internal reference curves ──
        # We'll store them in a special attribute that blueprint can render
        slash_fold_lines = []
        for i, (s, e) in enumerate(slash_lines):
            # Offset endpoint by the spread amount (perpendicular outward)
            ang = _angle(s, e)
            spread_pt = _polar(e, ang + 90, spreads[i] * 0.5)
            slash_fold_lines.append((s.to_tuple(), spread_pt.to_tuple()))

        # ── Notches ──
        notches = []
        if self.style.has_notches:
            # Notch at armhole midpoint
            notches.append(Notch(point=_midpoint(shoulder_tip_r, side_top),
                                 depth=0.3, angle=0))
            # Notch at gather pivot point
            notches.append(Notch(point=pivot, depth=0.5, angle=90))
            # Notch at bust point
            notches.append(Notch(point=Point(self.half_apex, -self.shoulder_to_bust),
                                depth=0.3, angle=90))
            # Notches along the draped side seam marking gather points
            for i in range(3):
                t = (i + 1) / 4.0
                nx = half_bust * (1.0 - t * 0.3)
                ny = -self.armhole_depth + t * (self.back_length - self.armhole_depth)
                notches.append(Notch(point=Point(nx, ny), depth=0.4, angle=180))

        # ── Darts ──
        darts = []
        # No waist dart on the front — fullness is taken up by the drape,
        # but add a small bust dart for shaping
        if self.style.has_darts:
            apex = Point(self.half_apex, -self.shoulder_to_bust)
            dart_start = Point(side_top.x - 1.5, side_top.y - 2.0)
            darts.append(Dart(start=dart_start, end=apex,
                              width=self.bust_dart_intake * 0.6))

        # ── Grainline ──
        grainline = [Point(half_bust * 0.3, -2.0),
                     Point(half_bust * 0.3, -self.back_length + 2.0)]

        piece = PatternPiece(
            name="Front Bodice (Cowl Drape)",
            points=full_outline,
            curves=curves,
            darts=darts,
            notches=notches,
            grainline=grainline,
            label=f"Front Bodice Cowl Drape - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("bodice_front", 1),
            layer=LAYER_CUT,
        )

        # Store slash fold lines as a custom attribute for blueprint rendering
        piece._slash_fold_lines = slash_fold_lines

        return piece

    def draft_two_piece_sleeve(self) -> tuple:
        """Draft a two-piece tailored long sleeve.

        Returns (sleeve_front, sleeve_back) — two separate PatternPiece
        objects that seam together along inner (underarm) and outer (elbow)
        seams.

        Origin at sleeve cap top (0, 0).  +y = downward.
        Front panel: shallower/wider cap, single notch at cap.
        Back panel: deeper/narrower cap, double notch at cap.
        """
        sleeve_len = self.m.sleeve_length if self.m.sleeve_length > 0 else 58.0
        half_bicep = self.bicep / 2.0
        half_wrist = self.wrist / 2.0

        # Two-piece sleeve: split at inner (underarm) and outer (elbow) seams
        # Front panel is slightly narrower at bicep, back panel slightly wider
        front_bicep_half = half_bicep * 0.45
        back_bicep_half = half_bicep * 0.55
        front_wrist_half = half_wrist * 0.45
        back_wrist_half = half_wrist * 0.55

        # Cap heights — front cap is shallower, back cap is deeper
        front_cap_h = self.bicep / 3.5 + 2.0
        back_cap_h = self.bicep / 3.0 + 3.5

        # ── Front sleeve panel ──
        # Cap top is shared centerline point
        cap_top_f = Point(0, 0)
        cap_outer_f = Point(front_bicep_half, -front_cap_h * 0.7)
        cap_inner_f = Point(-back_bicep_half * 0.3, -front_cap_h * 0.5)

        # Outer seam (elbow side) — slight inward curve at elbow
        elbow_y = -sleeve_len * 0.5
        wrist_outer_f = Point(front_wrist_half, -sleeve_len)
        elbow_outer_f = Point(front_bicep_half * 0.85, elbow_y)

        # Inner seam (underarm side)
        wrist_inner_f = Point(-back_wrist_half * 0.3, -sleeve_len)
        elbow_inner_f = Point(-back_bicep_half * 0.25, elbow_y)

        # Front cap curve (shallower)
        ctrl1_f = Point(front_bicep_half * 0.5, -front_cap_h * 0.2)
        ctrl2_f = Point(front_bicep_half * 0.8, -front_cap_h * 0.6)
        cap_curve_f = _cubic_bezier(cap_top_f, ctrl1_f, ctrl2_f, cap_outer_f, n=20)

        # Inner cap curve
        ctrl1i_f = Point(-back_bicep_half * 0.15, -front_cap_h * 0.15)
        ctrl2i_f = Point(-back_bicep_half * 0.25, -front_cap_h * 0.4)
        cap_inner_curve_f = _cubic_bezier(cap_top_f, ctrl1i_f, ctrl2i_f, cap_inner_f, n=16)

        # Outer seam curve (slight elbow bend)
        outer_seam_f = _quadratic_bezier(cap_outer_f, elbow_outer_f, wrist_outer_f, n=12)

        # Inner seam (relatively straight)
        inner_seam_f = _quadratic_bezier(cap_inner_f, elbow_inner_f, wrist_inner_f, n=10)

        # Wrist line
        wrist_pts_f = _quadratic_bezier(wrist_outer_f,
                                        Point(0, -sleeve_len + 0.5),
                                        wrist_inner_f, n=8)

        # Assemble front outline
        front_outline = [cap_top_f]
        front_outline.extend(cap_curve_f[1:-1])
        front_outline.append(cap_outer_f)
        front_outline.extend(outer_seam_f[1:-1])
        front_outline.append(wrist_outer_f)
        front_outline.extend(wrist_pts_f[1:-1])
        front_outline.append(wrist_inner_f)
        front_outline.extend(list(reversed(inner_seam_f))[1:-1])
        front_outline.append(cap_inner_f)
        front_outline.extend(list(reversed(cap_inner_curve_f))[1:-1])

        # Front curves
        f_cap_end = len(cap_curve_f)
        f_outer_end = f_cap_end + len(outer_seam_f)
        f_wrist_end = f_outer_end + len(wrist_pts_f)
        f_inner_end = f_wrist_end + len(inner_seam_f)

        front_curves = [
            {"start_idx": 0, "end_idx": f_cap_end,
             "control_points": [cap_top_f, ctrl1_f, ctrl2_f, cap_outer_f],
             "type": "bezier"},
            {"start_idx": f_cap_end, "end_idx": f_outer_end,
             "control_points": [cap_outer_f, elbow_outer_f, wrist_outer_f],
             "type": "bezier"},
            {"start_idx": f_outer_end, "end_idx": f_wrist_end,
             "control_points": [wrist_outer_f, wrist_inner_f],
             "type": "bezier"},
        ]

        # Front notches — single notch at cap (front convention)
        front_notches = []
        if self.style.has_notches:
            cap_mid_f = cap_curve_f[len(cap_curve_f) // 2]
            front_notches.append(Notch(point=cap_mid_f, depth=0.3, angle=90))
            # Elbow notch on outer seam
            front_notches.append(Notch(point=elbow_outer_f, depth=0.3, angle=0))
            # Wrist notch
            front_notches.append(Notch(point=Point(0, -sleeve_len), depth=0.3, angle=90))

        front_grainline = [Point(0, -front_cap_h), Point(0, -sleeve_len + 1.0)]

        sleeve_front = PatternPiece(
            name="Long Sleeve Front",
            points=front_outline,
            curves=front_curves,
            darts=[],
            notches=front_notches,
            grainline=front_grainline,
            label=f"Long Sleeve Front - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("sleeve_front", 1),
            layer=LAYER_CUT,
        )

        # ── Back sleeve panel ──
        cap_top_b = Point(0, 0)
        cap_outer_b = Point(back_bicep_half, -back_cap_h * 0.7)
        cap_inner_b = Point(-front_bicep_half * 0.3, -back_cap_h * 0.5)

        wrist_outer_b = Point(back_wrist_half, -sleeve_len)
        wrist_inner_b = Point(-front_wrist_half * 0.3, -sleeve_len)
        elbow_outer_b = Point(back_bicep_half * 0.88, elbow_y)
        elbow_inner_b = Point(-front_bicep_half * 0.25, elbow_y)

        # Back cap curve (deeper)
        ctrl1_b = Point(back_bicep_half * 0.5, -back_cap_h * 0.2)
        ctrl2_b = Point(back_bicep_half * 0.8, -back_cap_h * 0.65)
        cap_curve_b = _cubic_bezier(cap_top_b, ctrl1_b, ctrl2_b, cap_outer_b, n=20)

        # Inner cap curve
        ctrl1i_b = Point(-front_bicep_half * 0.15, -back_cap_h * 0.15)
        ctrl2i_b = Point(-front_bicep_half * 0.25, -back_cap_h * 0.4)
        cap_inner_curve_b = _cubic_bezier(cap_top_b, ctrl1i_b, ctrl2i_b, cap_inner_b, n=16)

        # Outer seam with elbow bend (more pronounced on back)
        outer_seam_b = _quadratic_bezier(cap_outer_b, elbow_outer_b, wrist_outer_b, n=12)
        inner_seam_b = _quadratic_bezier(cap_inner_b, elbow_inner_b, wrist_inner_b, n=10)
        wrist_pts_b = _quadratic_bezier(wrist_outer_b,
                                         Point(0, -sleeve_len + 0.5),
                                         wrist_inner_b, n=8)

        back_outline = [cap_top_b]
        back_outline.extend(cap_curve_b[1:-1])
        back_outline.append(cap_outer_b)
        back_outline.extend(outer_seam_b[1:-1])
        back_outline.append(wrist_outer_b)
        back_outline.extend(wrist_pts_b[1:-1])
        back_outline.append(wrist_inner_b)
        back_outline.extend(list(reversed(inner_seam_b))[1:-1])
        back_outline.append(cap_inner_b)
        back_outline.extend(list(reversed(cap_inner_curve_b))[1:-1])

        b_cap_end = len(cap_curve_b)
        b_outer_end = b_cap_end + len(outer_seam_b)
        b_wrist_end = b_outer_end + len(wrist_pts_b)
        b_inner_end = b_wrist_end + len(inner_seam_b)

        back_curves = [
            {"start_idx": 0, "end_idx": b_cap_end,
             "control_points": [cap_top_b, ctrl1_b, ctrl2_b, cap_outer_b],
             "type": "bezier"},
            {"start_idx": b_cap_end, "end_idx": b_outer_end,
             "control_points": [cap_outer_b, elbow_outer_b, wrist_outer_b],
             "type": "bezier"},
            {"start_idx": b_outer_end, "end_idx": b_wrist_end,
             "control_points": [wrist_outer_b, wrist_inner_b],
             "type": "bezier"},
        ]

        # Back notches — double notch at cap (back convention)
        back_notches = []
        if self.style.has_notches:
            cap_mid_b = cap_curve_b[len(cap_curve_b) // 2]
            # Double notch: two notches close together
            back_notches.append(Notch(point=cap_mid_b, depth=0.3, angle=90))
            if len(cap_curve_b) > 4:
                cap_mid2_b = cap_curve_b[len(cap_curve_b) // 2 + 2]
                back_notches.append(Notch(point=cap_mid2_b, depth=0.3, angle=90))
            back_notches.append(Notch(point=elbow_outer_b, depth=0.3, angle=0))
            back_notches.append(Notch(point=Point(0, -sleeve_len), depth=0.3, angle=90))

        back_grainline = [Point(0, -back_cap_h), Point(0, -sleeve_len + 1.0)]

        sleeve_back = PatternPiece(
            name="Long Sleeve Back",
            points=back_outline,
            curves=back_curves,
            darts=[],
            notches=back_notches,
            grainline=back_grainline,
            label=f"Long Sleeve Back - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("sleeve_back", 1),
            layer=LAYER_CUT,
        )

        return (sleeve_front, sleeve_back)

    def draft_neck_shoulder_gather_detail(self) -> PatternPiece:
        """Small stay/facing piece at the neck-shoulder gather point.

        A narrow curved strip following the neckline-to-shoulder edge on
        the draped side, used as a stay where the gathered drape fabric
        is anchored.
        """
        strip_width = 4.0  # cm
        neck_pt = Point(-self.neck_width / 2.0, -self.neck_depth_back * 0.3)
        shoulder_pt = _polar(neck_pt, -self.shoulder_slope - 180,
                             self.half_shoulder)

        # Inner edge follows neckline-to-shoulder
        inner_curve = _neckline_curve(neck_pt, shoulder_pt,
                                       self.neck_depth_front * 0.3)

        # Outer edge offset by strip_width (perpendicular outward)
        outer_curve = []
        for pt in inner_curve:
            # Offset perpendicular to the direction of travel
            if len(outer_curve) == 0:
                outer_curve.append(_perp_point(neck_pt, shoulder_pt, strip_width))
            else:
                prev = inner_curve[max(0, len(outer_curve) - 1)]
                outer_curve.append(_perp_point(prev, pt, strip_width))

        # Assemble: inner curve forward, then outer curve reversed
        outline = list(inner_curve)
        outline.extend(reversed(outer_curve))

        # Notches at gather concentration points
        notches = []
        if self.style.has_notches:
            for i in range(3):
                t = (i + 1) / 4.0
                idx = int(t * len(inner_curve))
                if idx < len(inner_curve):
                    notches.append(Notch(point=inner_curve[idx],
                                         depth=0.4, angle=90))

        # Grainline along the strip
        mid = _midpoint(neck_pt, shoulder_pt)
        grainline = [_polar(mid, _angle(neck_pt, shoulder_pt) + 90, 1.0),
                     _polar(mid, _angle(neck_pt, shoulder_pt) + 90, strip_width - 1.0)]

        piece = PatternPiece(
            name="Neck & Shoulder Gather Detail",
            points=outline,
            curves=[{"start_idx": 0, "end_idx": len(inner_curve),
                     "control_points": [neck_pt, shoulder_pt], "type": "bezier"}],
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Neck Shoulder Gather Stay - {self.style.size_label or 'Custom'}",
            cut_quantity=self.style.cut_quantities.get("gather_detail", 1),
            layer=LAYER_CUT,
        )

        return piece

    def _draft_asymmetric_cowl_top(self):
        """Draft the full asymmetric cowl-drape top.

        Pieces: Front Bodice (Cowl Drape), Back Bodice, Long Sleeve Front,
        Long Sleeve Back, Neck & Shoulder Gather Detail.
        """
        self.pieces.append(self.draft_asymmetric_cowl_front())
        self.pieces.append(self.draft_bodice_back())
        sleeve_front, sleeve_back = self.draft_two_piece_sleeve()
        self.pieces.append(sleeve_front)
        self.pieces.append(sleeve_back)
        self.pieces.append(self.draft_neck_shoulder_gather_detail())

    def _draft_bodice(self):
        """Draft a basic bodice block (front + back)."""
        self.pieces.append(self.draft_bodice_front())
        self.pieces.append(self.draft_bodice_back())

    def _draft_top(self):
        """Draft a top: bodice front + back + sleeves + facing + optional collar."""
        self.pieces.append(self.draft_bodice_front())
        self.pieces.append(self.draft_bodice_back())
        self.pieces.append(self.draft_sleeve())
        if self.style.has_collar:
            self.pieces.append(self.draft_collar())
        self.pieces.append(self.draft_facing())

    def _draft_blouse(self):
        """Draft a blouse: bodice front + back + optional collar + facing."""
        self.pieces.append(self.draft_bodice_front())
        self.pieces.append(self.draft_bodice_back())
        if self.style.has_collar:
            self.pieces.append(self.draft_collar())
        self.pieces.append(self.draft_facing())

    def _draft_shirt(self):
        """Draft a shirt: bodice front + back + sleeves + collar + facing."""
        self.pieces.append(self.draft_bodice_front())
        self.pieces.append(self.draft_bodice_back())
        self.pieces.append(self.draft_sleeve())
        if not self.style.collar_type:
            self.style.collar_type = "band"
        self.pieces.append(self.draft_collar())
        self.pieces.append(self.draft_facing())

    def _draft_skirt(self):
        """Draft a skirt: front + back panels."""
        self.pieces.append(self.draft_skirt_front())
        self.pieces.append(self.draft_skirt_back())

    def _draft_sleeve_only(self):
        """Draft just a sleeve piece."""
        self.pieces.append(self.draft_sleeve())

    def _draft_wrap(self):
        """Draft a wrap garment: extended front + back + sleeves + facing."""
        front = self.draft_bodice_front()
        extended_points = []
        for pt in front.points:
            if pt.x > 0:
                extended_points.append(Point(pt.x + 8.0, pt.y))
            else:
                extended_points.append(pt)
        front.points = extended_points
        front.name = "Wrap Front"
        front.label = f"Wrap Front - {self.style.size_label or 'Custom'}"
        self.pieces.append(front)
        self.pieces.append(self.draft_bodice_back())
        self.pieces.append(self.draft_sleeve())
        self.pieces.append(self.draft_facing())

    def _draft_dress(self):
        """Draft a dress: bodice front + back + skirt front + back + sleeves + extras."""
        self.pieces.append(self.draft_bodice_front())
        self.pieces.append(self.draft_bodice_back())
        self.pieces.append(self.draft_skirt_front())
        self.pieces.append(self.draft_skirt_back())
        self.pieces.append(self.draft_sleeve())
        if self.style.has_collar:
            self.pieces.append(self.draft_collar())
        if self.style.has_cowl:
            self.pieces.append(self.draft_cowl())
        self.pieces.append(self.draft_facing())

    def _draft_kurti(self):
        """Draft a kurti: long bodice (extended) + sleeves + facing + extras.

        A kurti is a long tunic, so the bodice length is extended.
        """
        original_back_length = self.back_length
        self.back_length = self.dress_length

        front = self.draft_bodice_front()
        front.name = "Kurti Front"
        front.label = f"Kurti Front - {self.style.size_label or 'Custom'}"
        self.pieces.append(front)

        back = self.draft_bodice_back()
        back.name = "Kurti Back"
        back.label = f"Kurti Back - {self.style.size_label or 'Custom'}"
        self.pieces.append(back)

        self.back_length = original_back_length

        self.pieces.append(self.draft_sleeve())
        if self.style.has_collar:
            self.pieces.append(self.draft_collar())
        if self.style.has_cowl:
            self.pieces.append(self.draft_cowl())
        self.pieces.append(self.draft_facing())

    def _draft_gown(self):
        """Draft a gown: bodice + long skirt + sleeves + optional cowl."""
        self.pieces.append(self.draft_bodice_front())
        self.pieces.append(self.draft_bodice_back())

        original_skirt = self.skirt_length
        self.skirt_length = max(self.dress_length - self.back_length, 80.0)
        self.pieces.append(self.draft_skirt_front())
        self.pieces.append(self.draft_skirt_back())
        self.skirt_length = original_skirt

        self.pieces.append(self.draft_sleeve())
        if self.style.has_cowl:
            self.pieces.append(self.draft_cowl())
        self.pieces.append(self.draft_facing())

    def _draft_kaftan(self):
        """Draft a kaftan: loose rectangular T-shape with neck opening.

        A kaftan is a loose-fitting garment with a very wide silhouette.
        Drafted as a large rectangular piece with a neck opening.
        """
        original_ease = self.ease_amount
        self.ease_amount = EASE_VALUES["loose"]
        self.quarter_bust = (self.m.bust + self.ease_amount) / 4.0
        self.quarter_waist = self.quarter_bust
        self.quarter_hip = (self.m.hip + self.ease_amount) / 4.0

        kaftan_length = self.dress_length if self.dress_length > 0 else 100.0
        half_width = self.quarter_bust + 10.0

        tl = Point(-half_width, 0)
        tr = Point(half_width, 0)
        br = Point(half_width, -kaftan_length)
        bl = Point(-half_width, -kaftan_length)

        neck_w = self.neck_width
        neck_d = self.neck_depth_front
        neck_left = Point(-neck_w / 2.0, 0)
        neck_right = Point(neck_w / 2.0, 0)

        neckline = _neckline_curve(neck_left, neck_right, neck_d * 0.5)

        full_outline = [neck_left]
        full_outline.extend(neckline[1:-1])
        full_outline.append(neck_right)
        full_outline.append(tr)
        full_outline.append(br)
        full_outline.append(bl)
        full_outline.append(tl)
        full_outline.append(neck_left)

        curves = [
            {"start_idx": 0, "end_idx": len(neckline),
             "control_points": [neck_left, neck_right], "type": "bezier"},
        ]

        notches = []
        if self.style.has_notches:
            notches.append(Notch(point=Point(half_width * 0.8, -self.armhole_depth),
                                 depth=0.3, angle=90))
            notches.append(Notch(point=Point(-half_width * 0.8, -self.armhole_depth),
                                 depth=0.3, angle=90))

        grainline = [Point(0, -5.0), Point(0, -kaftan_length + 5.0)]

        front = PatternPiece(
            name="Kaftan Front",
            points=full_outline,
            curves=curves,
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Kaftan Front - {self.style.size_label or 'Custom'}",
            cut_quantity=1,
            layer=LAYER_CUT,
        )

        neck_d_back = self.neck_depth_back
        neckline_back = _neckline_curve(neck_left, neck_right, neck_d_back * 0.5)

        back_outline = [neck_left]
        back_outline.extend(neckline_back[1:-1])
        back_outline.append(neck_right)
        back_outline.append(tr)
        back_outline.append(br)
        back_outline.append(bl)
        back_outline.append(tl)
        back_outline.append(neck_left)

        back_curves = [
            {"start_idx": 0, "end_idx": len(neckline_back),
             "control_points": [neck_left, neck_right], "type": "bezier"},
        ]

        back = PatternPiece(
            name="Kaftan Back",
            points=back_outline,
            curves=back_curves,
            darts=[],
            notches=notches,
            grainline=grainline,
            label=f"Kaftan Back - {self.style.size_label or 'Custom'}",
            cut_quantity=1,
            layer=LAYER_CUT,
        )

        self.pieces.append(front)
        self.pieces.append(back)

        self.ease_amount = original_ease
        self._compute_derived()


# ---------------------------------------------------------------------------
# DXF Exporter
# ---------------------------------------------------------------------------

class DXFExporter:
    """Export PatternPiece objects to an AAMA-compliant DXF file using ezdxf.

    ezdxf is imported lazily inside methods so that the module can be used
    for drafting even when ezdxf is not installed.
    """

    LAYER_DEFS = {
        "1": (1, "CUT"),
        "3": (3, "NOTCH"),
        "4": (4, "GRAIN / INTERNAL"),
        "6": (6, "REFERENCE"),
        "7": (7, "ANNOTATION"),
        "8": (8, "SEAM"),
        "9": (9, "MIRROR"),
    }

    def _setup_layers(self, doc):
        """Create AAMA layers in the DXF document."""
        for layer_name, (color, desc) in self.LAYER_DEFS.items():
            if layer_name not in doc.layers:
                doc.layers.add(name=layer_name, color=color)

    def _write_piece(self, msp, piece: PatternPiece, offset_x: float,
                     offset_y: float):
        """Write a single PatternPiece to the modelspace."""
        pts = piece.points
        if not pts:
            return

        poly_pts = [(p.x + offset_x, p.y + offset_y) for p in pts]
        msp.add_lwpolyline(
            poly_pts,
            dxfattribs={"layer": piece.layer or "1", "closed": True},
        )

        for curve in piece.curves:
            start_idx = curve.get("start_idx", 0)
            end_idx = curve.get("end_idx", 0)
            if 0 <= start_idx < len(pts) and 0 <= end_idx < len(pts):
                sp = pts[start_idx]
                ep = pts[end_idx]
                ctrl_pts = curve.get("control_points", [])
                if len(ctrl_pts) >= 2:
                    cps = ctrl_pts
                    if len(cps) == 2:
                        bez = _quadratic_bezier(
                            Point(sp.x + offset_x, sp.y + offset_y),
                            Point(cps[0].x + offset_x, cps[0].y + offset_y),
                            Point(ep.x + offset_x, ep.y + offset_y),
                        )
                    elif len(cps) >= 4:
                        bez = _cubic_bezier(
                            Point(sp.x + offset_x, sp.y + offset_y),
                            Point(cps[1].x + offset_x, cps[1].y + offset_y),
                            Point(cps[2].x + offset_x, cps[2].y + offset_y),
                            Point(ep.x + offset_x, ep.y + offset_y),
                        )
                    else:
                        bez = [Point(sp.x + offset_x, sp.y + offset_y),
                               Point(ep.x + offset_x, ep.y + offset_y)]
                    msp.add_lwpolyline(
                        [(b.x, b.y) for b in bez],
                        dxfattribs={"layer": "8"},
                    )

        for dart in piece.darts:
            sp = (dart.start.x + offset_x, dart.start.y + offset_y)
            ep = (dart.end.x + offset_x, dart.end.y + offset_y)
            msp.add_line(sp, ep, dxfattribs={"layer": "4"})
            ang = _angle(dart.start, dart.end)
            w = dart.width / 2.0
            leg1 = _polar(dart.start, ang + 90, w)
            leg2 = _polar(dart.start, ang - 90, w)
            msp.add_line(
                (leg1.x + offset_x, leg1.y + offset_y),
                (dart.end.x + offset_x, dart.end.y + offset_y),
                dxfattribs={"layer": "4"},
            )
            msp.add_line(
                (leg2.x + offset_x, leg2.y + offset_y),
                (dart.end.x + offset_x, dart.end.y + offset_y),
                dxfattribs={"layer": "4"},
            )

        for notch in piece.notches:
            np_ = notch.point
            ang = notch.angle
            d = notch.depth
            p1 = _polar(Point(np_.x + offset_x, np_.y + offset_y), ang, d)
            p2 = _polar(Point(np_.x + offset_x, np_.y + offset_y), ang + 180, d)
            msp.add_line((p1.x, p1.y), (p2.x, p2.y), dxfattribs={"layer": "3"})

        if piece.grainline and len(piece.grainline) >= 2:
            g0 = piece.grainline[0]
            g1 = piece.grainline[1]
            msp.add_line(
                (g0.x + offset_x, g0.y + offset_y),
                (g1.x + offset_x, g1.y + offset_y),
                dxfattribs={"layer": "4"},
            )
            ang = math.degrees(math.atan2(g1.y - g0.y, g1.x - g0.x))
            arrow_size = 1.5
            a1 = _polar(Point(g1.x + offset_x, g1.y + offset_y),
                        ang + 150, arrow_size)
            a2 = _polar(Point(g1.x + offset_x, g1.y + offset_y),
                        ang - 150, arrow_size)
            msp.add_line(
                (g1.x + offset_x, g1.y + offset_y),
                (a1.x, a1.y),
                dxfattribs={"layer": "4"},
            )
            msp.add_line(
                (g1.x + offset_x, g1.y + offset_y),
                (a2.x, a2.y),
                dxfattribs={"layer": "4"},
            )

        if piece.label:
            label_x = offset_x
            label_y = offset_y
            if pts:
                label_x = offset_x + sum(p.x for p in pts) / len(pts)
                label_y = offset_y + sum(p.y for p in pts) / len(pts)
            msp.add_text(
                piece.label,
                dxfattribs={
                    "layer": "7",
                    "height": 2.0,
                    "rotation": 0,
                    "insert": (label_x, label_y),
                },
            )

    def _write_measurements_table(self, msp, measurements_dict: dict,
                                  offset_x: float, offset_y: float):
        """Write a measurements reference table on the REFERENCE layer."""
        y = offset_y
        msp.add_text(
            "MEASUREMENTS",
            dxfattribs={
                "layer": "6",
                "height": 3.0,
                "insert": (offset_x, y),
            },
        )
        y -= 5.0
        for key, value in measurements_dict.items():
            text = f"{key}: {value}"
            msp.add_text(
                text,
                dxfattribs={
                    "layer": "6",
                    "height": 1.5,
                    "insert": (offset_x, y),
                },
            )
            y -= 3.0

    def _write_style_info(self, msp, style: Optional[StyleDetails],
                          offset_x: float, offset_y: float):
        """Write style information on the REFERENCE layer."""
        if style is None:
            return
        y = offset_y
        msp.add_text(
            "STYLE DETAILS",
            dxfattribs={
                "layer": "6",
                "height": 3.0,
                "insert": (offset_x, y),
            },
        )
        y -= 5.0
        style_lines = [
            f"Silhouette: {style.silhouette}",
            f"Cowl: {'Yes' if style.has_cowl else 'No'}",
            f"Gathers: {'Yes' if style.has_gathers else 'No'}",
            f"Pleats: {style.pleat_count if style.has_pleats else 0}",
            f"Drop shoulder: {'Yes' if style.drop_shoulder else 'No'}",
            f"Collar: {style.collar_type if style.has_collar else 'None'}",
            f"Asymmetric hem: {'Yes' if style.asymmetric_hem else 'No'}",
            f"Closure: {style.closure or 'None'}",
            f"Size: {style.size_label or 'Custom'}",
        ]
        for line in style_lines:
            msp.add_text(
                line,
                dxfattribs={
                    "layer": "6",
                    "height": 1.5,
                    "insert": (offset_x, y),
                },
            )
            y -= 3.0

    def export(self, pieces: list, filepath: str,
               measurements_dict: dict, style: Optional[StyleDetails] = None):
        """Export a list of PatternPiece objects to a DXF file.

        Parameters
        ----------
        pieces : list of PatternPiece
            The pattern pieces to export.
        filepath : str
            Output file path for the DXF file.
        measurements_dict : dict
            Dictionary of measurement key -> value for the reference table.
        style : StyleDetails, optional
            Style details for the reference table.
        """
        import ezdxf

        doc = ezdxf.new("R2000")
        self._setup_layers(doc)
        msp = doc.modelspace()

        col_spacing = 80.0
        row_spacing = 120.0
        pieces_per_row = 4

        for i, piece in enumerate(pieces):
            col = i % pieces_per_row
            row = i // pieces_per_row
            offset_x = col * col_spacing
            offset_y = -row * row_spacing
            self._write_piece(msp, piece, offset_x, offset_y)

        ref_x = 0.0
        ref_y = -((len(pieces) // pieces_per_row) + 1) * row_spacing
        self._write_measurements_table(msp, measurements_dict, ref_x, ref_y)
        self._write_style_info(msp, style, ref_x + 50.0, ref_y)

        doc.saveas(filepath)


# ---------------------------------------------------------------------------
# Master functions
# ---------------------------------------------------------------------------

def draft_pieces(measurements: Measurements, garment_type: str,
                 ease: str = 'standard',
                 style: Optional[StyleDetails] = None) -> tuple:
    """Draft pattern pieces for the given measurements and garment type.

    Parameters
    ----------
    measurements : Measurements
        Body measurements dataclass.
    garment_type : str
        One of: dress, kurti, top, blouse, shirt, skirt, sleeve, wrap,
        bodice, gown, kaftan.
    ease : str
        Ease level: minimal, standard, loose, none, fitted, comfort.
    style : StyleDetails, optional
        Style modifications to apply.

    Returns
    -------
    tuple of (list[PatternPiece], DraftingEngine)
    """
    if style is None:
        style = StyleDetails()

    engine = DraftingEngine(measurements, style, ease)
    pieces = engine.draft(garment_type)
    return pieces, engine


def export_dxf(pieces: list, filepath: str,
               measurements_dict: dict,
               style: Optional[StyleDetails] = None):
    """Export pattern pieces to a DXF file.

    Parameters
    ----------
    pieces : list of PatternPiece
        Pattern pieces to export.
    filepath : str
        Output DXF file path.
    measurements_dict : dict
        Measurement values for the reference table.
    style : StyleDetails, optional
        Style details for the reference table.
    """
    exporter = DXFExporter()
    exporter.export(pieces, filepath, measurements_dict, style)


def generate_pattern(measurements_data: dict, garment_type: str,
                     ease: str = 'standard',
                     style_data: Optional[dict] = None) -> dict:
    """Convenience function: generate a complete pattern from raw data.

    Parameters
    ----------
    measurements_data : dict
        Dictionary of measurement key -> value.
    garment_type : str
        Garment type to draft.
    ease : str
        Ease level.
    style_data : dict, optional
        Dictionary of style parameters.

    Returns
    -------
    dict
        Dictionary with keys:
            - 'pieces': list of piece dictionaries (via to_dict())
            - 'measurements': the Measurements object
            - 'style': the StyleDetails object
            - 'engine': the DraftingEngine instance
            - 'piece_count': number of pieces
    """
    measurements = Measurements()
    for key, value in measurements_data.items():
        if hasattr(measurements, key):
            try:
                setattr(measurements, key, float(value))
            except (ValueError, TypeError):
                setattr(measurements, key, value)

    style = StyleDetails()
    if style_data:
        for key, value in style_data.items():
            if hasattr(style, key):
                setattr(style, key, value)

    pieces, engine = draft_pieces(measurements, garment_type, ease, style)

    return {
        'pieces': [p.to_dict() for p in pieces],
        'measurements': measurements,
        'style': style,
        'engine': engine,
        'piece_count': len(pieces),
    }


# ---------------------------------------------------------------------------
# Utility: add seam allowance to a piece
# ---------------------------------------------------------------------------

def add_seam_allowance(piece: PatternPiece, allowance: float = SEAM_ALLOWANCE,
                       add_hem: bool = True,
                       hem_allowance: float = HEM_ALLOWANCE) -> PatternPiece:
    """Return a new PatternPiece with seam allowance added as an offset
    polyline.  If *add_hem* is True, the bottom (lowest-y) edge is extended
    by *hem_allowance*.
    """
    offset_pts = _offset_polyline(piece.points, allowance)

    if add_hem and offset_pts:
        min_y = min(p.y for p in offset_pts)
        for i, pt in enumerate(offset_pts):
            if abs(pt.y - min_y) < 0.5:
                offset_pts[i] = Point(pt.x, pt.y - hem_allowance)

    new_piece = PatternPiece(
        name=piece.name + " (with SA)",
        points=offset_pts,
        curves=piece.curves,
        darts=piece.darts,
        notches=piece.notches,
        grainline=piece.grainline,
        label=piece.label,
        cut_quantity=piece.cut_quantity,
        layer=piece.layer,
    )
    return new_piece


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = generate_pattern(
        {"bust": 88, "waist": 68, "hip": 94, "shoulder_width": 38,
         "back_length": 40, "sleeve_length": 56},
        "dress",
        "standard",
        {"silhouette": "fitted", "has_darts": True, "has_notches": True,
         "size_label": "M"},
    )
    print(f"Generated {result['piece_count']} pattern pieces:")
    for p in result['pieces']:
        print(f"  - {p['name']}: {len(p['points'])} points, "
              f"{len(p['darts'])} darts, {len(p['notches'])} notches")
