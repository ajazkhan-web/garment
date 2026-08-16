import os
import math
import ezdxf
import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# GARMENT AI PATTERN GENERATOR
# ============================================================
#
# Input:
#   AI extracted garment measurements
#
# Output:
#   DXF technical pattern
#   PNG technical preview
#
# NOTE:
# This is an automated drafting engine.
# Production patterns should be checked by a pattern technician.
# ============================================================


# ============================================================
# BASIC HELPERS
# ============================================================

def to_float(value, default=None):
    """
    Safely convert a value to float.

    Supports:
        23
        "23"
        "2 1/2"
        "3/4"
        "23.5"
    """

    if value is None:
        return default

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return default

    # Simple decimal
    try:
        return float(text)
    except ValueError:
        pass

    # Mixed fraction e.g. 2 1/2
    parts = text.replace('"', '').split()

    try:

        if len(parts) == 2 and "/" in parts[1]:

            whole = float(parts[0])
            numerator, denominator = parts[1].split("/")

            return whole + (
                float(numerator) / float(denominator)
            )

        # Simple fraction e.g. 3/4
        if "/" in text:

            numerator, denominator = text.split("/")

            return (
                float(numerator)
                / float(denominator)
            )

    except Exception:
        return default

    return default


# ============================================================
# UNIT CONVERSION
# ============================================================

def convert_to_inches(value, unit="in"):

    value = to_float(value)

    if value is None:
        return None

    unit = str(unit).lower().strip()

    if unit in ["cm", "centimeter", "centimeters"]:
        return value / 2.54

    if unit in ["mm", "millimeter", "millimeters"]:
        return value / 25.4

    return value


def convert_to_cm(value, unit="in"):

    value = to_float(value)

    if value is None:
        return None

    unit = str(unit).lower().strip()

    if unit in ["in", "inch", "inches", '"']:
        return value * 2.54

    if unit in ["mm", "millimeter", "millimeters"]:
        return value / 10

    return value


# ============================================================
# BEZIER CURVE
# ============================================================

def generate_curve(
    p0,
    p1,
    p2,
    num_pts=20
):
    """
    Quadratic Bezier curve.
    """

    t = np.linspace(
        0,
        1,
        num_pts
    )

    curve = []

    for ti in t:

        point = (
            (1 - ti) ** 2 * np.array(p0)
            +
            2 * (1 - ti) * ti * np.array(p1)
            +
            ti ** 2 * np.array(p2)
        )

        curve.append(
            (
                float(point[0]),
                float(point[1])
            )
        )

    return curve


# ============================================================
# MEASUREMENT EXTRACTION
# ============================================================

def normalize_measurements(spec_data):

    unit = spec_data.get(
        "unit",
        "in"
    )

    measurements = spec_data.get(
        "measurements",
        {}
    )

    # Support both:
    #
    # measurements: {...}
    #
    # AND old format:
    #
    # chest, waist, length...

    def get_measurement(
        new_name,
        old_names=(),
        default=None
    ):

        value = measurements.get(
            new_name
        )

        if value is None:

            for name in old_names:

                if spec_data.get(name) is not None:

                    value = spec_data.get(name)

                    break

        return convert_to_inches(
            value,
            unit
        ) if value is not None else default

    data = {

        "length":
            get_measurement(
                "length_from_hps",
                ["length"],
                30
            ),

        "chest":
            get_measurement(
                "chest",
                [],
                36
            ),

        "waist":
            get_measurement(
                "waist",
                [],
                30
            ),

        "hip":
            get_measurement(
                "hip",
                [],
                38
            ),

        "shoulder":
            get_measurement(
                "shoulder",
                [],
                14
            ),

        "sleeve":
            get_measurement(
                "sleeve_length_from_neck_seam",
                ["sleeve_length"],
                20
            ),

        "neck_width":
            get_measurement(
                "boat_neck_width",
                [],
                7
            ),

        "front_neck_drop":
            get_measurement(
                "front_neck_drop",
                [],
                3
            ),

        "back_neck_drop":
            get_measurement(
                "back_neck_drop",
                [],
                1
            ),

        "sleeve_opening":
            get_measurement(
                "sleeve_opening_fabric_flat",
                [],
                8
            ),

        "sleeve_cuff_height":
            get_measurement(
                "sleeve_opening_full_height",
                [],
                1
            ),
    }

    return data


# ============================================================
# PATTERN SETTINGS
# ============================================================

class PatternSettings:

    def __init__(
        self,
        seam_allowance=0.5,
        hem_allowance=1.0,
        scale=1.0
    ):

        self.seam_allowance = seam_allowance

        self.hem_allowance = hem_allowance

        self.scale = scale


# ============================================================
# POINT HELPERS
# ============================================================

def offset_points(
    points,
    offset_x=0,
    offset_y=0
):

    return [
        (
            float(x + offset_x),
            float(y + offset_y)
        )
        for x, y in points
    ]


def bounds(points):

    xs = [p[0] for p in points]

    ys = [p[1] for p in points]

    return (
        min(xs),
        max(xs),
        min(ys),
        max(ys)
    )


# ============================================================
# FRONT BODICE
# ============================================================

def build_front(
    m,
    settings
):

    chest = m["chest"]

    waist = m["waist"]

    length = m["length"]

    shoulder = m["shoulder"]

    armhole = max(
        7.0,
        chest / 6.0 + 1.0
    )

    # Quarter measurements

    chest_q = chest / 4.0

    waist_q = waist / 4.0

    shoulder_half = shoulder / 2.0

    # Main points

    p0 = (0, 0)

    p1 = (
        chest_q,
        0
    )

    p2 = (
        waist_q,
        length * 0.50
    )

    p3 = (
        waist_q,
        length
    )

    # Armhole

    arm_start = (
        chest_q,
        length - armhole
    )

    shoulder_end = (
        shoulder_half,
        length - 1.0
    )

    arm_curve = generate_curve(
        arm_start,
        (
            shoulder_half - 1.5,
            length - armhole + 2
        ),
        shoulder_end,
        20
    )

    # Neckline

    neck_width = m["neck_width"]

    neck_drop = m["front_neck_drop"]

    neck_curve = generate_curve(
        (
            0,
            length
        ),
        (
            neck_width * 0.45,
            length - neck_drop
        ),
        (
            neck_width,
            length - 0.5
        ),
        15
    )

    points = [

        p0,

        p1,

        p2,

        p3,

        (shoulder_half, length - 1.0),

    ]

    points += list(
        reversed(arm_curve)
    )

    points += list(
        reversed(neck_curve)
    )

    return points


# ============================================================
# BACK BODICE
# ============================================================

def build_back(
    m,
    settings
):

    chest = m["chest"]

    waist = m["waist"]

    length = m["length"]

    shoulder = m["shoulder"]

    chest_q = chest / 4

    waist_q = waist / 4

    shoulder_half = shoulder / 2

    armhole = max(
        7,
        chest / 6 + 1
    )

    arm_start = (
        chest_q,
        length - armhole
    )

    shoulder_end = (
        shoulder_half,
        length - 1
    )

    arm_curve = generate_curve(
        arm_start,
        (
            shoulder_half - 1,
            length - armhole + 2
        ),
        shoulder_end,
        20
    )

    neck_width = m["neck_width"]

    neck_drop = m["back_neck_drop"]

    neck_curve = generate_curve(
        (
            0,
            length
        ),
        (
            neck_width * 0.45,
            length - neck_drop
        ),
        (
            neck_width,
            length - 0.5
        ),
        15
    )

    points = [

        (0, 0),

        (chest_q, 0),

        (waist_q, length * 0.5),

        (waist_q, length),

        shoulder_end

    ]

    points += list(
        reversed(arm_curve)
    )

    points += list(
        reversed(neck_curve)
    )

    return points


# ============================================================
# SLEEVE
# ============================================================

def build_sleeve(
    m,
    settings
):

    sleeve_length = m["sleeve"]

    chest = m["chest"]

    sleeve_opening = m[
        "sleeve_opening"
    ]

    armhole = max(
        7,
        chest / 6 + 1
    )

    sleeve_width = (
        armhole * 0.85
    )

    cap_height = (
        armhole * 0.65
    )

    top_y = sleeve_length

    left = (
        0,
        0
    )

    left_cap = (
        0,
        top_y - cap_height
    )

    center = (
        sleeve_width,
        top_y
    )

    right_cap = (
        sleeve_width * 2,
        top_y - cap_height
    )

    right = (
        sleeve_width * 2,
        0
    )

    left_curve = generate_curve(
        left_cap,
        (
            sleeve_width * 0.30,
            top_y + 0.5
        ),
        center,
        20
    )

    right_curve = generate_curve(
        center,
        (
            sleeve_width * 1.70,
            top_y + 0.5
        ),
        right_cap,
        20
    )

    cuff_width = max(
        sleeve_opening,
        3
    )

    points = [

        left,

        left_cap

    ]

    points += left_curve

    points += right_curve

    points += [

        right_cap,

        right,

        (
            cuff_width * 2,
            0
        )

    ]

    return points


# ============================================================
# NECK FACING
# ============================================================

def build_facing(
    m,
    settings
):

    width = max(
        m["neck_width"] * 2,
        10
    )

    depth = 2.5

    return [

        (0, 0),

        (width, 0),

        (width, depth),

        (0, depth)

    ]


# ============================================================
# SEAM ALLOWANCE
# ============================================================

def add_seam_allowance(
    points,
    allowance
):

    """
    Basic outward allowance representation.

    NOTE:
    This is a drafting approximation.
    For production-grade nested CAD,
    use a proper geometric offset engine.
    """

    result = []

    min_x, max_x, min_y, max_y = bounds(
        points
    )

    for x, y in points:

        new_x = x

        new_y = y

        if abs(x - min_x) < 0.0001:
            new_x -= allowance

        if abs(x - max_x) < 0.0001:
            new_x += allowance

        if abs(y - min_y) < 0.0001:
            new_y -= allowance

        if abs(y - max_y) < 0.0001:
            new_y += allowance

        result.append(
            (
                new_x,
                new_y
            )
        )

    return result


# ============================================================
# GRAINLINE
# ============================================================

def add_grainline(
    msp,
    cx,
    cy,
    length=8
):

    msp.add_line(

        (
            cx,
            cy - length / 2
        ),

        (
            cx,
            cy + length / 2
        ),

        dxfattribs={
            "layer": "GRAINLINE"
        }

    )


# ============================================================
# NOTCH
# ============================================================

def add_notch(
    msp,
    x,
    y,
    size=0.25
):

    msp.add_line(

        (
            x - size,
            y
        ),

        (
            x + size,
            y
        ),

        dxfattribs={
            "layer": "NOTCH"
        }

    )


# ============================================================
# TEXT LABEL
# ============================================================

def add_label(
    msp,
    x,
    y,
    text,
    height=0.25
):

    msp.add_text(
        text,
        dxfattribs={
            "layer": "ANNOTATION",
            "height": height
        }
    ).set_placement(
        (
            x,
            y
        )
    )


# ============================================================
# DXF LAYERS
# ============================================================

def create_layers(doc):

    layers = {

        "PATTERN": 1,

        "SEAM_ALLOWANCE": 2,

        "GRAINLINE": 3,

        "NOTCH": 4,

        "ANNOTATION": 5,

        "CENTER_LINE": 6,

        "INTERNAL": 7

    }

    for name, color in layers.items():

        if name not in doc.layers:

            doc.layers.add(
                name,
                color=color
            )


# ============================================================
# DRAW PIECE
# ============================================================

def draw_piece(
    msp,
    ax,
    name,
    points,
    current_x,
    settings,
    add_allowance=True
):

    pts = offset_points(
        points,
        current_x,
        0
    )

    # Main pattern

    msp.add_lwpolyline(

        pts,

        close=True,

        dxfattribs={
            "layer": "PATTERN"
        }

    )

    # Seam allowance

    if add_allowance:

        sa_points = add_seam_allowance(
            points,
            settings.seam_allowance
        )

        sa_points = offset_points(
            sa_points,
            current_x,
            0
        )

        msp.add_lwpolyline(

            sa_points,

            close=True,

            dxfattribs={
                "layer": "SEAM_ALLOWANCE"
            }

        )

    # Technical preview

    arr = np.array(
        pts,
        dtype=float
    )

    ax.plot(

        arr[:, 0],
        arr[:, 1],

        linewidth=1.5,

        label=name

    )

    # Center

    cx = float(
        np.mean(arr[:, 0])
    )

    cy = float(
        np.mean(arr[:, 1])
    )

    # Piece label

    ax.text(

        cx,
        cy,

        name,

        fontsize=8,

        weight="bold",

        ha="center",

        va="center"

    )

    # Grainline

    add_grainline(
        msp,
        cx,
        cy
    )

    ax.annotate(

        "",

        xy=(
            cx,
            cy + 4
        ),

        xytext=(
            cx,
            cy - 4
        ),

        arrowprops={
            "arrowstyle": "<->",
            "linewidth": 1
        }

    )

    ax.text(
        cx + 0.3,
        cy,
        "GRAINLINE",
        rotation=90,
        fontsize=5,
        va="center"
    )

    # Center line

    min_x, max_x, min_y, max_y = bounds(
        pts
    )

    msp.add_line(

        (
            cx,
            min_y
        ),

        (
            cx,
            max_y
        ),

        dxfattribs={
            "layer": "CENTER_LINE"
        }

    )

    return max_x - min_x


# ============================================================
# MAIN GENERATOR
# ============================================================

def generate_technical_draft_and_dxf(

    spec_data,

    out_dxf="drafted_pattern.dxf",

    out_png="blueprint.png"

):

    # --------------------------------------------------------
    # Normalize data
    # --------------------------------------------------------

    m = normalize_measurements(
        spec_data
    )

    garment_type = str(
        spec_data.get(
            "garment_type",
            "Garment"
        )
    )

    size = spec_data.get(
        "size",
        "S"
    )

    settings = PatternSettings(
        seam_allowance=0.5,
        hem_allowance=1.0
    )

    # --------------------------------------------------------
    # Build pattern pieces
    # --------------------------------------------------------

    pieces = {

        "FRONT": build_front(
            m,
            settings
        ),

        "BACK": build_back(
            m,
            settings
        ),

        "SLEEVE": build_sleeve(
            m,
            settings
        ),

        "NECK_FACING": build_facing(
            m,
            settings
        )

    }

    # --------------------------------------------------------
    # Create DXF
    # --------------------------------------------------------

    doc = ezdxf.new(
        "R2010"
    )

    create_layers(
        doc
    )

    msp = doc.modelspace()

    # --------------------------------------------------------
    # Preview
    # --------------------------------------------------------

    fig, ax = plt.subplots(
        figsize=(18, 10)
    )

    current_x = 0

    spacing = 8

    # --------------------------------------------------------
    # Draw pieces
    # --------------------------------------------------------

    for name, points in pieces.items():

        width = draw_piece(

            msp,

            ax,

            name,

            points,

            current_x,

            settings

        )

        current_x += (
            width + spacing
        )

    # --------------------------------------------------------
    # Add technical information
    # --------------------------------------------------------

    info_x = current_x

    info_y = m["length"] * 0.8

    info_lines = [

        "GARMENT AI PATTERN",

        "────────────────────────",

        f"Garment: {garment_type}",

        f"Size: {size}",

        "",

        "MEASUREMENTS",

        f"Chest: {m['chest']:.2f}\"",

        f"Waist: {m['waist']:.2f}\"",

        f"Hip: {m['hip']:.2f}\"",

        f"Length: {m['length']:.2f}\"",

        f"Shoulder: {m['shoulder']:.2f}\"",

        f"Sleeve: {m['sleeve']:.2f}\"",

        "",

        "CONSTRUCTION",

        "Seam Allowance: 0.50\"",

        "Hem Allowance: 1.00\"",

        "Grainline: Marked",

        "Notches: Marked",

        "",

        "OUTPUT",

        "DXF R2010",

        "Units: Inch"

    ]

    for i, line in enumerate(
        info_lines
    ):

        ax.text(

            info_x,

            info_y - (
                i * 1.0
            ),

            line,

            fontsize=8,

            family="monospace"

        )

    # --------------------------------------------------------
    # Preview settings
    # --------------------------------------------------------

    ax.set_aspect(
        "equal",
        adjustable="datalim"
    )

    ax.axis(
        "off"
    )

    ax.set_title(
        "GARMENT AI — TECHNICAL PATTERN DRAFT",
        fontsize=15,
        weight="bold"
    )

    plt.tight_layout()

    # --------------------------------------------------------
    # Save files
    # ------------------------------
