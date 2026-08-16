# generator.py

import json
import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import ezdxf
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon


# ============================================================
# DIRECTORIES / DATABASE
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "pattern_data"
DB_PATH = DATA_DIR / "patterns.db"

ORIGINAL_DXF_DIR = DATA_DIR / "original_dxf"
GENERATED_DXF_DIR = DATA_DIR / "generated_dxf"
PREVIEW_DIR = DATA_DIR / "previews"

for directory in [
    DATA_DIR,
    ORIGINAL_DXF_DIR,
    GENERATED_DXF_DIR,
    PREVIEW_DIR,
]:
    directory.mkdir(parents=True, exist_ok=True)


# ============================================================
# DATABASE
# ============================================================

def init_database():
    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_name TEXT,
            garment_type TEXT,
            size TEXT,
            unit TEXT,
            measurements_json TEXT,
            plan_json TEXT,
            original_dxf TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pattern_pieces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER,
            piece_name TEXT,
            piece_type TEXT,
            geometry_json TEXT,
            FOREIGN KEY(pattern_id)
            REFERENCES patterns(id)
        )
    """)

    conn.commit()
    conn.close()


init_database()


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=None):
    if value is None:
        return default

    try:
        if isinstance(value, str):
            value = value.strip()

            # fractions
            if "/" in value and " " in value:
                parts = value.split()

                whole = float(parts[0])
                a, b = parts[1].split("/")

                return whole + float(a) / float(b)

            if "/" in value:
                a, b = value.split("/")
                return float(a) / float(b)

        return float(value)

    except Exception:
        return default


def clean_name(value):
    return (
        str(value or "garment")
        .strip()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )


def midpoint(a, b):
    return (
        (a[0] + b[0]) / 2,
        (a[1] + b[1]) / 2,
    )


def distance(a, b):
    return math.sqrt(
        (a[0] - b[0]) ** 2 +
        (a[1] - b[1]) ** 2
    )


def bezier_quadratic(p0, p1, p2, steps=20):

    result = []

    for i in range(steps + 1):

        t = i / steps

        x = (
            (1 - t) ** 2 * p0[0]
            + 2 * (1 - t) * t * p1[0]
            + t ** 2 * p2[0]
        )

        y = (
            (1 - t) ** 2 * p0[1]
            + 2 * (1 - t) * t * p1[1]
            + t ** 2 * p2[1]
        )

        result.append((x, y))

    return result


def add_polygon(doc, name, points, layer="PATTERN"):

    if not points:
        return

    layer_name = layer.upper()

    if layer_name not in doc.layers:
        doc.layers.add(layer_name)

    msp = doc.modelspace()

    msp.add_lwpolyline(
        points,
        close=True,
        dxfattribs={
            "layer": layer_name
        }
    )


def add_line(doc, p1, p2, layer="INTERNAL"):

    if layer.upper() not in doc.layers:
        doc.layers.add(layer.upper())

    doc.modelspace().add_line(
        p1,
        p2,
        dxfattribs={
            "layer": layer.upper()
        }
    )


def add_text(doc, text, position, height=0.35):

    doc.modelspace().add_text(
        str(text),
        dxfattribs={
            "height": height
        }
    ).set_placement(position)


# ============================================================
# MEASUREMENT NORMALIZATION
# ============================================================

def normalize_measurements(spec):

    m = spec.get("measurements", {})

    # Current bot sends both nested and flat values,
    # but this also works with either format.

    def get(name, *aliases):

        value = m.get(name)

        if value is not None:
            return safe_float(value)

        for alias in aliases:

            value = m.get(alias)

            if value is not None:
                return safe_float(value)

        value = spec.get(name)

        if value is not None:
            return safe_float(value)

        for alias in aliases:

            value = spec.get(alias)

            if value is not None:
                return safe_float(value)

        return None

    chest = get("chest", "bust")
    waist = get("waist")
    hip = get("hip")
    length = get(
        "length",
        "length_from_hps"
    )
    shoulder = get("shoulder")
    armhole = get("armhole")
    sleeve = get(
        "sleeve_length",
        "sleeve_length_from_neck_seam"
    )

    # Conservative fallbacks.
    # These are drafting defaults, not claimed measurements.

    if chest is None:
        chest = 36.0

    if waist is None:
        waist = chest * 0.80

    if hip is None:
        hip = chest * 1.05

    if length is None:
        length = 28.0

    if shoulder is None:
        shoulder = chest * 0.36

    if armhole is None:
        armhole = chest * 0.18

    if sleeve is None:
        sleeve = 23.0

    return {
        "chest": chest,
        "waist": waist,
        "hip": hip,
        "length": length,
        "shoulder": shoulder,
        "armhole": armhole,
        "sleeve_length": sleeve,
    }


# ============================================================
# PATTERN PLANNER
# ============================================================

def build_pattern_plan(spec):

    garment = (
        str(
            spec.get(
                "garment_type",
                ""
            )
        )
        .lower()
    )

    styles = " ".join(
        spec.get(
            "style_details",
            []
        )
    ).lower()

    construction = " ".join(
        spec.get(
            "construction_details",
            []
        )
    ).lower()

    text = (
        garment
        + " "
        + styles
        + " "
        + construction
    )

    pieces = []

    if any(
        x in text
        for x in [
            "t-shirt",
            "tee",
            "tshirt"
        ]
    ):

        pieces = [
            "FRONT",
            "BACK",
            "SLEEVE",
            "NECKBAND",
        ]

        family = "tshirt"

    elif any(
        x in text
        for x in [
            "shirt",
            "button",
            "placket"
        ]
    ):

        pieces = [
            "FRONT",
            "BACK",
            "SLEEVE",
            "COLLAR",
            "COLLAR_STAND",
            "CUFF",
            "PLACKET",
        ]

        family = "shirt"

    elif any(
        x in text
        for x in [
            "dress",
            "gown"
        ]
    ):

        pieces = [
            "FRONT_BODICE",
            "BACK_BODICE",
            "SLEEVE",
        ]

        family = "dress"

    elif any(
        x in text
        for x in [
            "top",
            "blouse"
        ]
    ):

        pieces = [
            "FRONT",
            "BACK",
            "SLEEVE",
        ]

        family = "top"

    else:

        pieces = [
            "FRONT",
            "BACK",
            "SLEEVE",
        ]

        family = "basic"

    # Add special pieces only when evidence exists.

    if "collar" in text and "COLLAR" not in pieces:
        pieces.append("COLLAR")

    if "cuff" in text and "CUFF" not in pieces:
        pieces.append("CUFF")

    if "placket" in text and "PLACKET" not in pieces:
        pieces.append("PLACKET")

    if "neckband" in text and "NECKBAND" not in pieces:
        pieces.append("NECKBAND")

    if "pocket" in text:
        pieces.append("POCKET")

    if "facing" in text:
        pieces.append("FACING")

    return {
        "family": family,
        "pieces": pieces,
        "style_details": spec.get(
            "style_details",
            []
        ),
        "construction_details": spec.get(
            "construction_details",
            []
        ),
    }


# ============================================================
# BASIC BODICE
# ============================================================

def make_front(spec):

    m = normalize_measurements(spec)

    chest = m["chest"]
    waist = m["waist"]
    length = m["length"]
    shoulder = m["shoulder"]
    armhole = m["armhole"]

    quarter_chest = chest / 4
    quarter_waist = waist / 4

    shoulder_half = shoulder / 2

    neck_width = max(
        2.5,
        shoulder * 0.28
    )

    neck_depth = max(
        2.5,
        shoulder * 0.35
    )

    armhole_y = length - armhole

    points = []

    # Center front
    points.append((0, 0))

    # Hem
    points.append(
        (quarter_waist + 2.0, 0)
    )

    # Waist
    points.append(
        (quarter_waist + 1.5, length * 0.45)
    )

    # Armhole base
    points.append(
        (quarter_chest + 0.5, armhole_y)
    )

    # Armhole curve
    curve = bezier_quadratic(
        (
            quarter_chest + 0.5,
            armhole_y
        ),
        (
            shoulder_half + 1.0,
            armhole_y + 1.0
        ),
        (
            shoulder_half,
            length - 1.0
        ),
        18
    )

    points.extend(curve)

    # Shoulder / neckline

    points.append(
        (
            neck_width,
            length
        )
    )

    neck_curve = bezier_quadratic(
        (
            neck_width,
            length
        ),
        (
            neck_width * 0.6,
            length - neck_depth * 0.35
        ),
        (
            0,
            length - neck_depth
        ),
        12
    )

    points.extend(neck_curve)

    return points


def make_back(spec):

    m = normalize_measurements(spec)

    chest = m["chest"]
    waist = m["waist"]
    length = m["length"]
    shoulder = m["shoulder"]
    armhole = m["armhole"]

    quarter_chest = chest / 4
    quarter_waist = waist / 4

    shoulder_half = shoulder / 2

    neck_width = max(
        2.5,
        shoulder * 0.28
    )

    neck_depth = 1.0

    armhole_y = length - armhole

    points = [
        (0, 0),
        (quarter_waist + 2.0, 0),
        (
            quarter_waist + 1.5,
            length * 0.45
        ),
        (
            quarter_chest + 0.5,
            armhole_y
        )
    ]

    curve = bezier_quadratic(
        (
            quarter_chest + 0.5,
            armhole_y
        ),
        (
            shoulder_half + 1.0,
            armhole_y + 1.0
        ),
        (
            shoulder_half,
            length - 1.0
        ),
        18
    )

    points.extend(curve)

    points.append(
        (
            neck_width,
            length
        )
    )

    neck_curve = bezier_quadratic(
        (
            neck_width,
            length
        ),
        (
            neck_width * 0.6,
            length - 0.5
        ),
        (
            0,
            length - neck_depth
        ),
        10
    )

    points.extend(neck_curve)

    return points


# ============================================================
# SLEEVE
# ============================================================

def make_sleeve(spec):

    m = normalize_measurements(spec)

    sleeve_length = m["sleeve_length"]
    armhole = m["armhole"]
    chest = m["chest"]

    bicep = max(
        10.0,
        chest * 0.18
    )

    cap_height = max(
        4.0,
        armhole * 0.65
    )

    left = (0, 0)
    right = (bicep * 2, 0)

    left_cap = (
        0,
        sleeve_length - cap_height
    )

    top = (
        bicep,
        sleeve_length
    )

    right_cap = (
        bicep * 2,
        sleeve_length - cap_height
    )

    points = [
        left,
        left_cap
    ]

    curve1 = bezier_quadratic(
        left_cap,
        (
            bicep * 0.25,
            sleeve_length
        ),
        top,
        18
    )

    curve2 = bezier_quadratic(
        top,
        (
            bicep * 1.75,
            sleeve_length
        ),
        right_cap,
        18
    )

    points.extend(curve1[1:])
    points.extend(curve2[1:])

    points.extend([
        right_cap,
        right
    ])

    return points


# ============================================================
# COLLAR
# ============================================================

def make_collar(spec):

    m = normalize_measurements(spec)

    neck = max(
        12.0,
        m["shoulder"] * 1.8
    )

    width = 3.0

    return [
        (0, 0),
        (neck, 0),
        (neck - 1.0, width),
        (1.0, width)
    ]


# ============================================================
# COLLAR STAND
# ============================================================

def make_collar_stand(spec):

    m = normalize_measurements(spec)

    neck = max(
        12.0,
        m["shoulder"] * 1.8
    )

    height = 1.25

    return [
        (0, 0),
        (neck, 0),
        (neck - 0.5, height),
        (0.5, height)
    ]


# ============================================================
# CUFF
# ============================================================

def make_cuff(spec):

    m = normalize_measurements(spec)

    sleeve = m["sleeve_length"]

    width = 5.0
    length = max(
        5.0,
        sleeve * 0.25
    )

    return [
        (0, 0),
        (length, 0),
        (length, width),
        (0, width)
    ]


# ============================================================
# PLACKET
# ============================================================

def make_placket(spec):

    m = normalize_measurements(spec)

    length = m["length"]

    width = 2.5

    return [
        (0, 0),
        (length * 0.55, 0),
        (length * 0.55, width),
        (0, width)
    ]


# ============================================================
# NECKBAND
# ============================================================

def make_neckband(spec):

    m = normalize_measurements(spec)

    neck = max(
        12.0,
        m["shoulder"] * 1.8
    )

    width = 1.5

    return [
        (0, 0),
        (neck, 0),
        (neck, width),
        (0, width)
    ]


# ============================================================
# POCKET
# ============================================================

def make_pocket(spec):

    width = 6.0
    height = 7.0

    return [
        (0, 0),
        (width, 0),
        (width, height),
        (0, height)
    ]


# ============================================================
# BUILD PIECES
# ============================================================

def build_pieces(spec, plan):

    pieces = {}

    family = plan.get(
        "family",
        "basic"
    )

    requested = plan.get(
        "pieces",
        []
    )

    for piece in requested:

        name = piece.upper()

        if name in [
            "FRONT",
            "FRONT_BODICE"
        ]:

            pieces[name] = make_front(spec)

        elif name in [
            "BACK",
            "BACK_BODICE"
        ]:

            pieces[name] = make_back(spec)

        elif name == "SLEEVE":

            pieces[name] = make_sleeve(spec)

        elif name == "COLLAR":

            pieces[name] = make_collar(spec)

        elif name == "COLLAR_STAND":

            pieces[name] = make_collar_stand(spec)

        elif name == "CUFF":

            pieces[name] = make_cuff(spec)

        elif name == "PLACKET":

            pieces[name] = make_placket(spec)

        elif name == "NECKBAND":

            pieces[name] = make_neckband(spec)

        elif name == "POCKET":

            pieces[name] = make_pocket(spec)

        elif name == "FACING":

            pieces["FACING"] = make_front(spec)

    return pieces


# ============================================================
# ADD SEAM ALLOWANCE
# ============================================================

def offset_polygon(points, allowance=0.5):

    # Simple radial approximation.
    # Production-grade nested offset should later use
    # shapely.buffer for complex curves.

    if not points:
        return points

    cx = sum(
        p[0] for p in points
    ) / len(points)

    cy = sum(
        p[1] for p in points
    ) / len(points)

    result = []

    for x, y in points:

        dx = x - cx
        dy = y - cy

        length = math.sqrt(
            dx * dx + dy * dy
        )

        if length == 0:

            result.append(
                (x, y)
            )

        else:

            result.append(
                (
                    x + allowance * dx / length,
                    y + allowance * dy / length
                )
            )

    return result


# ============================================================
# GRAINLINE
# ============================================================

def add_grainline(
    doc,
    points,
    x_offset,
    layer="GRAINLINE"
):

    ys = [
        p[1]
        for p in points
    ]

    xs = [
        p[0]
        for p in points
    ]

    if not ys:
        return

    min_y = min(ys)
    max_y = max(ys)

    center_x = (
        min(xs) + max(xs)
    ) / 2

    p1 = (
        center_x + x_offset,
        min_y + 2
    )

    p2 = (
        center_x + x_offset,
        max_y - 2
    )

    add_line(
        doc,
        p1,
        p2,
        layer
    )


# ============================================================
# NOTCHES
# ============================================================

def add_notch(
    doc,
    point,
    direction=(0, 1)
):

    x, y = point

    length = 0.3

    dx, dy = direction

    end = (
        x + dx * length,
        y + dy * length
    )

    add_line(
        doc,
        point,
        end,
        "NOTCHES"
    )


# ============================================================
# DXF EXPORT
# ============================================================

def export_dxf(
    pieces,
    spec,
    output_path
):

    doc = ezdxf.new(
        "R2018"
    )

    # Layers

    for layer in [
        "PATTERN",
        "SEAM_ALLOWANCE",
        "GRAINLINE",
        "NOTCHES",
        "INTERNAL",
        "LABELS"
    ]:

        if layer not in doc.layers:

            doc.layers.add(
                layer
            )

    current_x = 0.0
    spacing = 8.0

    piece_positions = {}

    for name, points in pieces.items():

        if not points:
            continue

        xs = [
            p[0]
            for p in points
        ]

        width = (
            max(xs) - min(xs)
        )

        shifted = [
            (
                p[0] + current_x,
                p[1]
            )
            for p in points
        ]

        piece_positions[name] = (
            current_x,
            shifted
        )

        add_polygon(
            doc,
            name,
            shifted,
            "PATTERN"
        )

        # Seam allowance

        allowance = offset_polygon(
            points,
            0.5
        )

        allowance = [
            (
                p[0] + current_x,
                p[1]
            )
            for p in allowance
        ]

        add_polygon(
            doc,
            name,
            allowance,
            "SEAM_ALLOWANCE"
        )

        # Grainline

        add_grainline(
            doc,
            points,
            current_x
        )

        # Label

        cx = (
            min(
                p[0]
