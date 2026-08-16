"""
generator.py - 2D Garment Pattern Generator
Generates AAMA/ASTM DXF files + SVG/PNG previews from measurement JSON.

Usage:
    python generator.py --measurements measurements.json --garment-type tshirt_dress

Author: Built with Solene (Base44 Superagent) pattern drafting methodology
"""

import math
import json
import argparse
import os
from typing import Dict, List, Tuple, Any

# ============================================================
# CONSTANTS & HELPERS
# ============================================================

IN_TO_CM = 2.54

def cm(inches: float) -> float:
    """Convert inches to centimeters."""
    return round(inches * IN_TO_CM, 3)

def catmull_rom_spline(p0, p1, p2, p3, points=12):
    """Generate smooth curve points using Catmull-Rom interpolation."""
    result = []
    for i in range(points):
        t = i / points
        t2 = t * t
        t3 = t2 * t
        x = 0.5 * ((2 * p1[0]) +
                   (-p0[0] + p2[0]) * t +
                   (2*p0[0] - 5*p1[0] + 4*p2[0] - p3[0]) * t2 +
                   (-p0[0] + 3*p1[0] - 3*p2[0] + p3[0]) * t3)
        y = 0.5 * ((2 * p1[1]) +
                   (-p0[1] + p2[1]) * t +
                   (2*p0[1] - 5*p1[1] + 4*p2[1] - p3[1]) * t2 +
                   (-p0[1] + 3*p1[1] - 3*p2[1] + p3[1]) * t3)
        result.append((x, y))
    return result

def smooth_points(pts: List[Tuple[float, float]], smoothness=12) -> List[Tuple[float, float]]:
    """Apply Catmull-Rom smoothing to a list of points, returning a smooth curve."""
    if len(pts) < 3:
        return pts
    result = []
    n = len(pts)
    for i in range(n):
        p0 = pts[(i - 1) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        result.extend(catmull_rom_spline(p0, p1, p2, p3, smoothness))
    return result


# ============================================================
# PATTERN DRAFTING - EXACT WIDTH FORMULA
# ============================================================

def get_quarter_width(measurement_in: float) -> float:
    """
    EXACT WIDTH FORMULA: width = measurement / 4 (no ease added).
    This is the user's critical rule - never add ease unless explicitly told.
    """
    return cm(measurement_in / 4.0)


# ============================================================
# GARMENT DRAFTING FUNCTIONS
# ============================================================

def draft_tshirt_dress(M: Dict[str, float]) -> Dict[str, Any]:
    """
    Draft a T-shirt dress pattern.
    Expects: length, bust, waist, hip, bottom, shoulder, armhole, sleeve_length,
             sleeve_opening, neck_width, fnd (front neck drop), bnd (back neck drop)
    Returns: dict of piece_name -> list of (x, y) points in cm
    """
    bust_q = get_quarter_width(M["bust"])
    waist_q = get_quarter_width(M["waist"])
    hip_q = get_quarter_width(M["hip"])
    bottom_q = get_quarter_width(M["bottom"])
    half_shoulder = cm(M["shoulder"] / 2)
    half_neck = cm(M["neck_width"] / 2)
    armhole_depth = cm(M["armhole"])
    length = cm(M["length"])
    fnd = cm(M["fnd"])
    bnd = cm(M["bnd"])

    # --- BACK (on fold) ---
    back_pts = [
        (0, 0),                                    # CB neck top
        (half_neck * 0.5, bnd * 0.15),             # neck-shoulder curve start
        (half_shoulder, cm(0.6)),                  # shoulder tip
        (bust_q, armhole_depth),                   # underarm
        (waist_q, armhole_depth + cm(6)),          # waist point
        (hip_q, armhole_depth + cm(9)),             # hip point
        (bottom_q, length),                        # hem side
        (0, length),                               # CB hem
        (0, bnd),                                  # CB neck point
    ]

    # --- FRONT (on fold) ---
    front_pts = [
        (0, 0),                                    # CF neck top
        (half_neck * 0.5, fnd * 0.15),             # neck-shoulder curve start
        (half_shoulder, cm(0.6)),                  # shoulder tip
        (bust_q, armhole_depth),                   # underarm (front armhole curves more at bottom)
        (waist_q, armhole_depth + cm(6)),          # waist point
        (hip_q, armhole_depth + cm(9)),             # hip point
        (bottom_q, length),                        # hem side
        (0, length),                               # CF hem
        (0, fnd),                                  # CF neck point
    ]

    # --- SLEEVE (HJA method) ---
    armhole_circumference = cm(M["armhole"]) * 2 * 0.9  # approx front+back armhole
    cap_height = round(armhole_circumference / 3 + cm(0.4), 3)
    bicep_half = cm(M.get("bicep", M["sleeve_opening"] * 1.3) / 2)
    sleeve_length = cm(M["sleeve_length"])
    cuff_half = cm(M["sleeve_opening"] / 2)

    sleeve_pts = [
        (bicep_half, 0),                           # cap center (shoulder match)
        (bicep_half * 1.6, cap_height * 0.55),     # front cap mid (curvier)
        (bicep_half * 2, cap_height),              # front bicep
        (bicep_half * 2 - (bicep_half - cuff_half), sleeve_length),  # front cuff
        (bicep_half - cuff_half, sleeve_length),    # back cuff
        (0, cap_height),                           # back bicep
        (bicep_half * 0.5, cap_height * 0.75),     # back cap mid (flatter)
    ]

    # --- NECK BAND ---
    neck_perimeter = cm(M["neck_width"] + M["fnd"] + M["bnd"] + 4)  # approximate
    neckband_pts = [
        (0, 0), (neck_perimeter, 0),
        (neck_perimeter, cm(1.5)), (0, cm(1.5))
    ]

    return {
        "BACK": {"points": back_pts, "on_fold": True, "cut": 1,
                 "label": f"BACK bust/4={bust_q}cm, BND={bnd}cm"},
        "FRONT": {"points": front_pts, "on_fold": True, "cut": 1,
                  "label": f"FRONT bust/4={bust_q}cm, FND={fnd}cm"},
        "SLEEVE": {"points": sleeve_pts, "on_fold": False, "cut": 2,
                   "label": f"SLEEVE cap={cap_height}cm, bicep={bicep_half*2}cm"},
        "NECK_BAND": {"points": neckband_pts, "on_fold": False, "cut": 1,
                      "label": "NECK BAND"},
    }


def draft_shirt(M: Dict[str, float]) -> Dict[str, Any]:
    """
    Draft a basic shirt pattern (with yoke, collar, cuff, placket).
    Expects: length, bust, waist, hip, shoulder, armhole, sleeve_length,
             sleeve_opening, neck_width, fnd, bnd, yoke_height, collar_height, cuff_height
    """
    bust_q = get_quarter_width(M["bust"])
    waist_q = get_quarter_width(M.get("waist", M["bust"]))
    hip_q = get_quarter_width(M.get("hip", M["bust"]))
    bottom_q = get_quarter_width(M.get("bottom", M["bust"]))
    half_shoulder = cm(M["shoulder"] / 2)
    half_neck = cm(M["neck_width"] / 2)
    armhole_depth = cm(M["armhole"])
    length = cm(M["length"])
    fnd = cm(M["fnd"])
    bnd = cm(M["bnd"])
    yoke_h = cm(M.get("yoke_height", 3))
    collar_h = cm(M.get("collar_height", 3.5))
    cuff_h = cm(M.get("cuff_height", 2.5))
    placket_w = cm(M.get("placket_width", 1.5))

    # --- BACK (on fold) ---
    back_pts = [
        (0, 0), (half_neck * 0.5, bnd * 0.15),
        (half_shoulder, cm(0.6)), (bust_q, armhole_depth),
        (waist_q, armhole_depth + cm(6)), (hip_q, armhole_depth + cm(9)),
        (bottom_q, length), (0, length), (0, bnd),
    ]

    # --- FRONT (with placket extension) ---
    front_pts = [
        (0, fnd), (half_neck * 0.5, fnd * 0.15),
        (half_shoulder, cm(0.5)), (bust_q, armhole_depth),
        (waist_q, armhole_depth + cm(6)), (hip_q, armhole_depth + cm(9)),
        (bottom_q, length), (placket_w, length),
        (placket_w, 0), (0, fnd),
    ]

    # --- YOKE ---
    yoke_pts = [
        (0, 0), (half_shoulder + cm(0.5), 0),
        (half_shoulder + cm(0.5), yoke_h),
        (0, yoke_h),
    ]

    # --- SLEEVE ---
    cap_height = round(cm(M["armhole"]) / 3 + cm(0.5), 3)
    bicep_half = cm(M.get("bicep", M["sleeve_opening"] * 1.5) / 2)
    sleeve_length = cm(M["sleeve_length"])
    cuff_half = cm(M["sleeve_opening"] / 2)

    sleeve_pts = [
        (bicep_half, 0), (bicep_half * 1.6, cap_height * 0.55),
        (bicep_half * 2, cap_height),
        (bicep_half * 2 - (bicep_half - cuff_half), sleeve_length),
        (bicep_half - cuff_half, sleeve_length),
        (0, cap_height), (bicep_half * 0.5, cap_height * 0.75),
    ]

    # --- COLLAR ---
    collar_pts = [
        (0, 0), (cm(M["neck_width"] + 3), 0),
        (cm(M["neck_width"] + 3), collar_h), (0, collar_h),
    ]

    # --- CUFF ---
    cuff_pts = [
        (0, 0), (cm(M["sleeve_opening"] + 2), 0),
        (cm(M["sleeve_opening"] + 2), cuff_h), (0, cuff_h),
    ]

    return {
        "BACK": {"points": back_pts, "on_fold": True, "cut": 1,
                 "label": f"BACK bust/4={bust_q}cm"},
        "FRONT": {"points": front_pts, "on_fold": False, "cut": 1,
                  "label": f"FRONT +placket {placket_w}cm"},
        "YOKE": {"points": yoke_pts, "on_fold": True, "cut": 2,
                 "label": "YOKE (back)"},
        "SLEEVE": {"points": sleeve_pts, "on_fold": False, "cut": 2,
                   "label": f"SLEEVE cap={cap_height}cm"},
        "COLLAR": {"points": collar_pts, "on_fold": True, "cut": 2,
                   "label": "COLLAR"},
        "CUFF": {"points": cuff_pts, "on_fold": False, "cut": 2,
                 "label": "CUFF"},
    }


def draft_jacket(M: Dict[str, float]) -> Dict[str, Any]:
    """
    Draft a bomber/track jacket pattern.
    Expects: length, chest, bottom_relax, shoulder, neck_width, armhole,
             bicep, sleeve_length, sleeve_opening, rib_height, neck_drop_front, neck_drop_back
    """
    chest_q = get_quarter_width(M["chest"])
    bottom_q = cm(M.get("bottom_relax", M["chest"]) / 2)  # bottom rib = half circumference per piece
    half_shoulder = cm(M["shoulder"] / 2)
    half_neck = cm(M["neck_width"] / 2)
    armhole_depth = cm(M["armhole"])
    length = cm(M["length"])
    fnd = cm(M.get("neck_drop_front", 0.75))
    bnd = cm(M.get("neck_drop_back", 1.0))
    rib_h = cm(M.get("rib_height", 2.5))

    # --- BACK (on fold) ---
    back_pts = [
        (0, 0), (half_neck * 0.5, bnd * 0.15),
        (half_shoulder, cm(0.6)), (chest_q, armhole_depth),
        (chest_q, length), (0, length), (0, bnd),
    ]

    # --- FRONT (on fold for pullover, or split for full zip) ---
    full_zip = M.get("full_zip", False)
    if full_zip:
        front_pts = [
            (0, fnd), (half_neck * 0.5, fnd * 0.15),
            (half_shoulder, cm(0.5)), (chest_q, armhole_depth),
            (chest_q, length), (0, length),
        ]
    else:
        front_pts = [
            (0, 0), (half_neck * 0.5, fnd * 0.15),
            (half_shoulder, cm(0.5)), (chest_q, armhole_depth),
            (chest_q, length), (0, length), (0, fnd),
        ]

    # --- SLEEVE ---
    cap_height = round(cm(M["armhole"]) / 3 + cm(1.25), 3)  # jacket ease = 1.25"
    bicep_half = cm(M["bicep"] / 2)
    sleeve_length = cm(M["sleeve_length"])
    cuff_half = cm(M["sleeve_opening"] / 2)

    sleeve_pts = [
        (bicep_half, 0), (bicep_half * 1.6, cap_height * 0.55),
        (bicep_half * 2, cap_height),
        (bicep_half * 2 - (bicep_half - cuff_half), sleeve_length),
        (bicep_half - cuff_half, sleeve_length),
        (0, cap_height), (bicep_half * 0.5, cap_height * 0.75),
    ]

    # --- NECK RIB ---
    neck_rib_pts = [
        (0, 0), (cm(M["neck_width"] + 2), 0),
        (cm(M["neck_width"] + 2), rib_h), (0, rib_h),
    ]

    # --- BOTTOM RIB (half circumference) ---
    bottom_rib_pts = [
        (0, 0), (bottom_q, 0),
        (bottom_q, rib_h), (0, rib_h),
    ]

    # --- SLEEVE RIB ---
    sleeve_rib_pts = [
        (0, 0), (cm(M["sleeve_opening"] + 1), 0),
        (cm(M["sleeve_opening"] + 1), rib_h), (0, rib_h),
    ]

    pieces = {
        "BACK": {"points": back_pts, "on_fold": True, "cut": 1,
                 "label": f"BACK chest/4={chest_q}cm"},
        "FRONT": {"points": front_pts, "on_fold": not full_zip, "cut": 1,
                  "label": f"FRONT chest/4={chest_q}cm"},
        "SLEEVE": {"points": sleeve_pts, "on_fold": False, "cut": 2,
                   "label": f"SLEEVE cap={cap_height}cm"},
        "NECK_RIB": {"points": neck_rib_pts, "on_fold": False, "cut": 1,
                     "label": "NECK RIB"},
        "BOTTOM_RIB": {"points": bottom_rib_pts, "on_fold": False, "cut": 2,
                       "label": f"BOTTOM RIB {bottom_q}cm"},
        "SLEEVE_RIB": {"points": sleeve_rib_pts, "on_fold": False, "cut": 2,
                       "label": "SLEEVE RIB"},
    }

    # Add side pockets if specified
    if M.get("side_pocket_opening"):
        pocket_w = cm(M["side_pocket_opening"])
        pocket_pts = [
            (0, 0), (pocket_w, 0), (pocket_w, cm(8)),
            (0, cm(8)),
        ]
        pieces["SIDE_POCKET"] = {"points": pocket_pts, "on_fold": False, "cut": 4,
                                  "label": "SIDE POCKET"}

    return pieces


def draft_pants(M: Dict[str, float]) -> Dict[str, Any]:
    """
    Draft wide-leg pants / palazzo pattern.
    Expects: length, waist, hip, front_rise, back_rise, bottom_width, side_pocket_opening
    """
    waist_q = get_quarter_width(M["waist"])
    hip_q = get_quarter_width(M["hip"])
    bottom_q = cm(M["bottom_width"] / 4)
    front_rise = cm(M["front_rise"])
    back_rise = cm(M["back_rise"])
    length = cm(M["length"])
    hip_drop = cm(M.get("hip_drop", 8))  # distance from waist to hip line

    # Crotch extensions (professional trouser drafting)
    front_crotch_ext = hip_q / 14 * 2.54  # hip/14
    back_crotch_ext = front_crotch_ext + cm(3)  # back needs more room

    # --- FRONT PANT ---
    front_pts = [
        (0, 0),                                    # CF waist
        (waist_q, 0),                               # side waist
        (hip_q, hip_drop),                          # side hip
        (bottom_q, length),                         # side hem
        (0, length),                                # CF hem
        (0, front_rise),                            # CF crotch point
        (-front_crotch_ext, front_rise * 0.7),     # crotch extension
        (-front_crotch_ext * 0.5, 0),              # crotch to waist
    ]

    # --- BACK PANT ---
    back_pts = [
        (0, 0),                                     # CB waist
        (waist_q, 0),                                # side waist
        (hip_q, hip_drop),                           # side hip
        (bottom_q, length),                          # side hem
        (0, length),                                 # CB hem
        (0, back_rise),                              # CB crotch point
        (-back_crotch_ext, back_rise * 0.7),        # crotch extension (more than front)
        (-back_crotch_ext * 0.5, 0),               # crotch to waist
    ]

    pieces = {
        "FRONT_PANT": {"points": front_pts, "on_fold": False, "cut": 2,
                        "label": f"FRONT rise={front_rise}cm"},
        "BACK_PANT": {"points": back_pts, "on_fold": False, "cut": 2,
                      "label": f"BACK rise={back_rise}cm"},
    }

    # Add waistband
    waistband_pts = [(0, 0), (waist_q, 0), (waist_q, cm(1.5)), (0, cm(1.5))]
    pieces["WAISTBAND"] = {"points": waistband_pts, "on_fold": False, "cut": 1,
                           "label": "WAISTBAND"}

    # Add pockets if specified
    if M.get("side_pocket_opening"):
        pocket_w = cm(M["side_pocket_opening"])
        pocket_pts = [(0, 0), (pocket_w, 0), (pocket_w, cm(10)), (0, cm(10))]
        pieces["SIDE_POCKET"] = {"points": pocket_pts, "on_fold": False, "cut": 4,
                                  "label": "SIDE POCKET"}

    return pieces


# ============================================================
# DXF GENERATION (AAMA/ASTM, native cm)
# ============================================================

def generate_dxf(pieces: Dict[str, Any], output_path: str, title: str = "PATTERN"):
    """
    Generate AAMA/ASTM compliant DXF file in native centimeters.
    Uses R2000 format, numbered layers, BLOCK structure per piece.
    """
    try:
        import ezdxf
    except ImportError:
        print("ERROR: ezdxf not installed. Run: pip install ezdxf")
        return False

    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 5       # 5 = centimeters (CRITICAL for Optitex)
    doc.header["$MEASUREMENT"] = 1   # Metric

    # Create numbered layers (AAMA/ASTM standard)
    for i in range(1, 16):
        try:
            doc.layers.add(str(i), color=i)
        except:
            pass

    blocks = doc.blocks
    msp = doc.modelspace()

    markings = pieces.get("_markings", {})
    offset_x = 0
    offsets_map = {}
    for name, piece_data in pieces.items():
        if name == "_markings":
            continue
        pts = piece_data["points"]
        label = piece_data.get("label", name)
        cut_qty = piece_data.get("cut", 1)
        on_fold = piece_data.get("on_fold", False)

        # Create BLOCK for this piece
        if name in blocks:
            blocks.delete_block(name)
        blk = blocks.new(name=name)

        # Offset points
        pts_off = [(x + offset_x, y) for x, y in pts]

        # Smooth curved silhouette pieces (Catmull-Rom) for a professional draft look.
        # Rectangular trim pieces (bands/cuffs/waistbands/collars with 4 corners) stay sharp.
        do_smooth = piece_data.get("smooth", len(pts_off) > 4)
        outline_pts = smooth_points(pts_off, smoothness=10) if do_smooth else pts_off

        # Piece outline (layer 1 = boundary)
        blk.add_lwpolyline(outline_pts, dxfattribs={"layer": "1"}, close=True)

        # Grain line (layer 7, vertical center)
        cx = sum(p[0] for p in pts_off) / len(pts_off)
        ys = [p[1] for p in pts_off]
        blk.add_line((cx, min(ys) + 0.8), (cx, max(ys) - 0.8),
                     dxfattribs={"layer": "7"})

        # Fold line if on fold (layer 6)
        if on_fold:
            blk.add_line((pts_off[0][0], min(ys)), (pts_off[0][0], max(ys)),
                         dxfattribs={"layer": "6"})

        # Labels (layer 15)
        blk.add_text(label, dxfattribs={"layer": "15", "height": 0.5}).set_placement(
            (pts_off[0][0] + 0.3, pts_off[0][1] + 0.3))
        fold_text = "ON FOLD" if on_fold else "NOT ON FOLD"
        blk.add_text(f"CUT {cut_qty} {fold_text}",
                     dxfattribs={"layer": "15", "height": 0.45}).set_placement(
            (pts_off[0][0] + 0.3, pts_off[0][1] - 0.5))

        # Draw darts (BACK_SKIRT) and pleats (SKIRT_LEFT) and buttons (SKIRT_RIGHT)
        if name == "BACK_SKIRT" and markings.get("dart_positions"):
            for dp in markings["dart_positions"]:
                dx = dp + offset_x
                blk.add_line((dx, 0.5), (dx, 0.5 + markings["dart_length"]), dxfattribs={"layer": "4"})
            blk.add_text(f"{len(markings['dart_positions'])} WAIST DARTS", dxfattribs={"layer": "4", "height": 0.45}).set_placement((offset_x + 1, 2))
        if name == "SKIRT_LEFT" and markings.get("pleat_positions"):
            max_x_local = max(x for x, y in pts)
            for pp in markings["pleat_positions"]:
                blk.add_line((0.5 + offset_x, pp), (max_x_local + offset_x - 0.5, pp), dxfattribs={"layer": "5"})
            blk.add_text(f"{len(markings['pleat_positions'])} PLEATS x {markings['pleat_depth']}cm", dxfattribs={"layer": "5", "height": 0.45}).set_placement((offset_x + 0.5, markings["pleat_positions"][0] - 1.5))
        if name == "SKIRT_RIGHT" and markings.get("num_buttons"):
            op = pts_off[4]
            blk.add_circle((op[0], op[1] - 1), 0.5, dxfattribs={"layer": "6"})
            for i in range(markings["num_buttons"]):
                t = (i + 0.5) / markings["num_buttons"]
                bx = pts_off[0][0] + (op[0] - pts_off[0][0]) * t
                by = pts_off[0][1] + (op[1] - pts_off[0][1]) * t * 0.3
                blk.add_circle((bx, by), 0.25, dxfattribs={"layer": "2"})

        # Add block reference to modelspace
        msp.add_blockref(name, insert=(0, 0), dxfattribs={"layer": "0"})

        # Advance offset
        xs = [p[0] for p in pts]
        offset_x += (max(xs) - min(xs)) + 4

    # Title text
    msp.add_text(f"{title} - NATIVE CM - INSUNITS=5",
                 dxfattribs={"layer": "15", "height": 1.0}).set_placement((0, -6))

    doc.saveas(output_path)
    return True


# ============================================================
# SVG / PNG PREVIEW GENERATION
# ============================================================

def generate_svg(pieces: Dict[str, Any], output_path: str, title: str = "Pattern"):
    """Generate an SVG preview of all pattern pieces."""
    colors = ["#5a4d3a", "#4a5d6a", "#6a4d5a", "#4d6a4d", "#5a5a4d",
              "#3a5d7a", "#7a4d3a", "#4d7a6a", "#6a6a4d", "#5d3a7a"]

    # Calculate layout
    real_pieces = {k: v for k, v in pieces.items() if k != "_markings"}
    offsets = {}
    cur = 0
    for name, pd in real_pieces.items():
        pts = pd["points"]
        xs = [p[0] for p in pts]
        offsets[name] = cur
        cur += (max(xs) - min(xs)) + 4
    total_w = cur

    all_shifted = [(x + offsets[name], y)
                   for name, pd in real_pieces.items()
                   for x, y in pd["points"]]
    min_x = min(x for x, y in all_shifted) - 2
    max_x = total_w + 2
    min_y = min(y for x, y in all_shifted) - 2
    max_y = max(y for x, y in all_shifted) + 2

    scale = 3.2
    margin = 70
    w = int((max_x - min_x) * scale + margin * 2)
    h = int((max_y - min_y) * scale + margin * 2 + 40)

    def tx(x):
        return (x - min_x) * scale + margin

    def ty(y):
        return (max_y - y) * scale + margin + 40

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">')
    lines.append(f'<rect width="{w}" height="{h}" fill="#faf7f2"/>')
    lines.append(f'<text x="{margin}" y="32" font-family="Arial" font-size="18" font-weight="bold" fill="#2d2d2d">{title} - {len(real_pieces)} Pieces</text>')

    for i, (name, pd) in enumerate(real_pieces.items()):
        pts = pd["points"]
        off = offsets[name]
        color = colors[i % len(colors)]
        shifted_raw = [(x + off, y) for x, y in pts]
        do_smooth = pd.get("smooth", len(shifted_raw) > 4)
        shifted = smooth_points(shifted_raw, smoothness=10) if do_smooth else shifted_raw

        path_str = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in shifted)
        lines.append(f'<polygon points="{path_str}" fill="{color}33" stroke="{color}" stroke-width="2"/>')

        w_cm = max(x for x, y in pts) - min(x for x, y in pts)
        h_cm = max(y for x, y in pts) - min(y for x, y in pts)
        lines.append(f'<text x="{tx(shifted[0][0]):.1f}" y="{ty(shifted[0][1]) - 8:.1f}" font-family="Arial" font-size="11" font-weight="bold" fill="{color}">{name.replace("_", " ")}</text>')
        lines.append(f'<text x="{tx(shifted[0][0]):.1f}" y="{ty(shifted[0][1]) + 10:.1f}" font-family="Arial" font-size="9" fill="{color}">{w_cm:.1f}cm x {h_cm:.1f}cm ({w_cm/2.54:.1f}in x {h_cm/2.54:.1f}in)</text>')

    lines.append(f'<text x="{margin}" y="{h - 15}" font-family="Arial" font-size="10" fill="#666">All widths EXACT per /4 rule. DXF in native cm for Optitex 15.</text>')
    lines.append('</svg>')

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    # Convert to PNG if cairosvg is available
    png_path = output_path.replace(".svg", ".png")
    try:
        import cairosvg
        cairosvg.svg2png(url=output_path, write_to=png_path, scale=1.5)
        return png_path
    except ImportError:
        print("WARNING: cairosvg not installed. SVG generated but no PNG. Run: pip install cairosvg")
        return output_path



def draft_wrap_blazer_dress(M: Dict[str, float]) -> Dict[str, Any]:
    """
    Draft a tailored wrap blazer dress with shawl collar (Himanshi-style).
    Handles: waist darts, side pleats, shawl collar overlap, waistband, asymmetric hem.

    Expects: length, bust, waist, hip, bottom, shoulder, back_neck_width,
             front_neck_drop_to_cross, back_neck_drop, cb_collar_height,
             armhole, bicep, sleeve_length, sleeve_opening,
             waistband_top_below_ah, waistband_height,
    Optional: num_left_pleats (default 4), pleat_depth (default 1),
              pleat_to_pleat_dist (default 1.25), pleat_dist_from_waist (default 1.25),
              overlap_side_facing_width (default 2), num_buttons (default 5),
              overlap_gap_from_left_side (default 6), overlap_extra_bottom_point (default 1.5),
              above_waist_dart_length (default 4.5), num_waist_darts (default 3)
    """
    bust_q = get_quarter_width(M["bust"])
    waist_q = get_quarter_width(M["waist"])
    hip_q = get_quarter_width(M.get("hip", M["waist"] * 1.25))
    bottom_q = get_quarter_width(M.get("bottom", M.get("hip", M["waist"] * 1.3)))
    half_shoulder = cm(M["shoulder"] / 2)
    half_back_neck = cm(M.get("back_neck_width", M["shoulder"] * 0.5) / 2)
    bicep_half = cm(M.get("bicep", M["sleeve_opening"] * 1.5) / 2)

    front_neck_drop_to_cross = cm(M.get("front_neck_drop_to_cross", 7))
    back_neck_drop = cm(M.get("back_neck_drop", 0.5))
    cb_collar_height = cm(M.get("cb_collar_height", 2.5))
    armhole_straight = cm(M["armhole"])
    sleeve_length = cm(M["sleeve_length"])
    sleeve_opening = cm(M["sleeve_opening"])

    waistband_top_below_ah = cm(M.get("waistband_top_below_ah", 6.5))
    waistband_height = cm(M.get("waistband_height", 1))

    # Lengths derived directly from spec (no assumptions if given)
    bodice_len = armhole_straight + waistband_top_below_ah
    total_length = cm(M["length"])
    skirt_len = total_length - bodice_len - waistband_height

    # Pleat/dart/overlap details
    num_left_pleats = int(M.get("num_left_pleats", 4))
    pleat_depth = cm(M.get("pleat_depth", 1))
    pleat_to_pleat_dist = cm(M.get("pleat_to_pleat_dist", 1.25))
    pleat_dist_from_waist = cm(M.get("pleat_dist_from_waist", 1.25))
    overlap_ext = cm(M.get("overlap_side_facing_width", 2))
    num_buttons = int(M.get("num_buttons", 5))
    overlap_gap = cm(M.get("overlap_gap_from_left_side", 6))
    overlap_extra_bottom = cm(M.get("overlap_extra_bottom_point", 1.5))
    dart_length = cm(M.get("above_waist_dart_length", 4.5))
    num_waist_darts = int(M.get("num_waist_darts", 3))

    # --- BACK BODICE (on fold) ---
    back_bodice_pts = [
        (0, 0), (half_back_neck, back_neck_drop * 0.3),
        (half_shoulder, cm(0.6)), (bust_q, armhole_straight),
        (waist_q, bodice_len), (0, bodice_len), (0, back_neck_drop),
    ]

    # --- BACK SKIRT (on fold, darts) ---
    back_skirt_pts = [
        (0, 0), (waist_q, 0), (hip_q, cm(9.0)), (bottom_q, skirt_len), (0, skirt_len),
    ]
    dart_positions = [waist_q * 0.3, waist_q * 0.55, waist_q * 0.8][:num_waist_darts]

    # --- FRONT LEFT BODICE (under layer, standard) ---
    front_left_pts = [
        (0, front_neck_drop_to_cross), (bust_q * 0.3, back_neck_drop * 0.5),
        (half_shoulder, cm(0.5)), (bust_q, armhole_straight),
        (waist_q, bodice_len), (0, bodice_len),
    ]

    # --- FRONT RIGHT BODICE (wrap over-layer, +overlap) ---
    front_right_pts = [
        (0, front_neck_drop_to_cross), (bust_q * 0.3, back_neck_drop * 0.5),
        (half_shoulder, cm(0.5)), (bust_q + overlap_ext * 0.5, armhole_straight),
        (waist_q + overlap_ext, bodice_len), (overlap_ext, bodice_len),
    ]

    # --- SHAWL COLLAR (cut 1 on fold) ---
    shawl_collar_pts = [
        (0, 0), (half_back_neck * 0.8, cm(0.5)),
        (half_shoulder * 0.9, cb_collar_height * 0.6),
        (half_shoulder * 1.3, front_neck_drop_to_cross * 0.85),
        (half_shoulder * 0.6, front_neck_drop_to_cross),
        (0, cb_collar_height),
    ]

    # --- WAISTBAND (cut 2) ---
    waistband_pts = [(0, 0), (waist_q, 0), (waist_q, waistband_height), (0, waistband_height)]

    # --- SKIRT LEFT (pleats) ---
    skirt_left_pts = [(0, 0), (waist_q, 0), (hip_q, cm(9.0)), (bottom_q, skirt_len), (0, skirt_len)]
    pleat_positions = [pleat_dist_from_waist + i * pleat_to_pleat_dist for i in range(num_left_pleats)]

    # --- SKIRT RIGHT (overlap point) ---
    skirt_right_pts = [
        (0, 0), (waist_q, 0), (hip_q, cm(9.0)),
        (bottom_q, skirt_len - overlap_extra_bottom),
        (overlap_gap, skirt_len), (0, skirt_len),
    ]

    # --- SLEEVE (fitted, HJA method) ---
    cap_height = round(armhole_straight / 3 + cm(0.5), 3)
    sleeve_pts = [
        (0, cap_height), (bicep_half * 0.5, cap_height * 0.5), (bicep_half, 0),
        (bicep_half * 1.5, cap_height * 0.6), (bicep_half * 2, cap_height),
        (bicep_half * 2 - (bicep_half - sleeve_opening / 2), sleeve_length),
        (bicep_half - sleeve_opening / 2, sleeve_length),
    ]

    pieces = {
        "BACK_BODICE": {"points": back_bodice_pts, "on_fold": True, "cut": 1,
                        "label": f"BACK BODICE bust/4={bust_q}cm EXACT"},
        "BACK_SKIRT": {"points": back_skirt_pts, "on_fold": True, "cut": 1,
                       "label": f"BACK SKIRT +{num_waist_darts} waist darts ({dart_length}cm)"},
        "FRONT_LEFT_BODICE": {"points": front_left_pts, "on_fold": False, "cut": 1,
                              "label": "FRONT LEFT (under layer)"},
        "FRONT_RIGHT_BODICE": {"points": front_right_pts, "on_fold": False, "cut": 1,
                               "label": f"FRONT RIGHT (wrap over-layer +{overlap_ext}cm)"},
        "SHAWL_COLLAR": {"points": shawl_collar_pts, "on_fold": True, "cut": 1,
                         "label": f"SHAWL COLLAR CB height={cb_collar_height}cm"},
        "WAISTBAND": {"points": waistband_pts, "on_fold": False, "cut": 2,
                     "label": f"WAISTBAND height={waistband_height}cm exact"},
        "SKIRT_LEFT": {"points": skirt_left_pts, "on_fold": False, "cut": 1,
                       "label": f"SKIRT LEFT +{num_left_pleats} pleats ({pleat_depth}cm depth)"},
        "SKIRT_RIGHT": {"points": skirt_right_pts, "on_fold": False, "cut": 1,
                        "label": f"SKIRT RIGHT +overlap point (gap {overlap_gap}cm)"},
        "SLEEVE": {"points": sleeve_pts, "on_fold": False, "cut": 2,
                  "label": f"SLEEVE fitted bicep/2={bicep_half}cm"},
    }

    # Store markings as metadata for DXF/preview generators to draw
    pieces["_markings"] = {
        "dart_positions": dart_positions, "dart_length": dart_length,
        "pleat_positions": pleat_positions, "pleat_depth": pleat_depth,
        "num_buttons": num_buttons,
    }
    return pieces


# ============================================================
# MAIN - DISPATCHER
# ============================================================

GARMENT_DRAFTERS = {
    "tshirt_dress": draft_tshirt_dress,
    "tshirt": draft_tshirt_dress,
    "shirt": draft_shirt,
    "jacket": draft_jacket,
    "bomber": draft_jacket,
    "pants": draft_pants,
    "palazzo": draft_pants,
    "wrap_blazer_dress": draft_wrap_blazer_dress,
    "blazer_dress": draft_wrap_blazer_dress,
    "wrap_dress": draft_wrap_blazer_dress,
}

def generate_pattern(measurements: Dict[str, float], garment_type: str,
                     output_dir: str = "output", title: str = None) -> Dict[str, str]:
    """
    Main entry point: generate a complete pattern from measurements.
    Returns dict with paths to generated files.
    """
    garment_type = garment_type.lower().strip()
    if garment_type not in GARMENT_DRAFTERS:
        raise ValueError(f"Unknown garment type: {garment_type}. Available: {list(GARMENT_DRAFTERS.keys())}")

    if title is None:
        title = garment_type.replace("_", " ").title() + " Pattern"

    os.makedirs(output_dir, exist_ok=True)

    # Draft pieces
    drafter = GARMENT_DRAFTERS[garment_type]
    pieces = drafter(measurements)

    # Generate DXF
    dxf_path = os.path.join(output_dir, f"{garment_type}_pattern.dxf")
    dxf_ok = generate_dxf(pieces, dxf_path, title)

    # Generate preview
    svg_path = os.path.join(output_dir, f"{garment_type}_preview.svg")
    png_path = generate_svg(pieces, svg_path, title)

    # Save piece data
    json_path = os.path.join(output_dir, f"{garment_type}_pieces.json")
    serializable = {}
    for name, pd in pieces.items():
        if name == "_markings":
            serializable[name] = pd
            continue
        serializable[name] = {
            "points": pd["points"],
            "on_fold": pd.get("on_fold", False),
            "cut": pd.get("cut", 1),
            "label": pd.get("label", name),
        }
    with open(json_path, "w") as f:
        json.dump(serializable, f, indent=2)

    # Print summary
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}")
    print(f"  Garment type: {garment_type}")
    real_pieces_count = len({k:v for k,v in pieces.items() if k != "_markings"})
    print(f"  Pieces: {real_pieces_count}")
    for name, pd in pieces.items():
        if name == "_markings":
            continue
        pts = pd["points"]
        w = max(x for x, y in pts) - min(x for x, y in pts)
        h = max(y for x, y in pts) - min(y for x, y in pts)
        qty = pd.get("cut", 1)
        print(f"    {name:16s}: {w:6.2f}cm x {h:6.2f}cm  ({w/2.54:5.2f}in x {h/2.54:5.2f}in)  cut {qty}")
    print(f"{'='*50}")
    print(f"  DXF: {dxf_path} ({'OK' if dxf_ok else 'FAILED'})")
    print(f"  Preview: {png_path}")
    print(f"  Pieces JSON: {json_path}")
    print(f"{'='*50}")

    return {
        "dxf": dxf_path if dxf_ok else None,
        "preview": png_path,
        "pieces_json": json_path,
        "piece_count": len([k for k in pieces if k != "_markings"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="2D Garment Pattern Generator")
    parser.add_argument("--measurements", "-m", required=True,
                        help="Path to measurements JSON file")
    parser.add_argument("--garment-type", "-g", required=True,
                        help="Garment type: tshirt_dress, shirt, jacket, pants")
    parser.add_argument("--output", "-o", default="output",
                        help="Output directory (default: output)")
    parser.add_argument("--title", "-t", default=None,
                        help="Pattern title for DXF/preview")

    args = parser.parse_args()

    with open(args.measurements) as f:
        measurements = json.load(f)

    result = generate_pattern(measurements, args.garment_type, args.output, args.title)
    print(f"\nDone! Files in {args.output}/")
Extension
Extension Embed



Actions

Your Business

Settings

Help
Search Amazon

United States
Search Amazon

