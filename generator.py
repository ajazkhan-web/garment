from pathlib import Path
from typing import Dict
import ezdxf
from ezdxf import colors

MM_PER_INCH = 25.4


def inch(value: float) -> float:
    return float(value) * MM_PER_INCH


def add_polyline(msp, points, layer="CUT", closed=True):
    msp.add_lwpolyline(
        points,
        close=closed,
        dxfattribs={"layer": layer},
    )


def add_text(msp, text, x, y, layer="ANNOTATION", height=7):
    entity = msp.add_text(
        str(text),
        dxfattribs={
            "layer": layer,
            "height": height,
        },
    )
    entity.set_placement((x, y))


def make_dress(measurements: Dict[str, float], output_file: str):
    m = measurements

    length = inch(m["length"])
    bust = inch((m["bust"] + m.get("bust_ease", 5)) / 4)
    hip = inch((m["hip"] + m.get("hip_ease", 6)) / 4)
    bottom = inch(m.get("bottom_opening", 48) / 2)
    shoulder = inch(m["shoulder"] / 2)
    armhole = inch(m["armhole"])
    side_slit = inch(m.get("side_slit", 16))
    neck_width = inch(m.get("neck_width", 7))

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    layers = {
        "CUT": colors.WHITE,
        "STITCH": colors.YELLOW,
        "GRAIN": colors.GREEN,
        "NOTCH": colors.RED,
        "ANNOTATION": colors.CYAN,
    }

    for name, color in layers.items():
        if name not in doc.layers:
            doc.layers.new(name, dxfattribs={"color": color})

    # Front body, half pattern, center front on fold
    front_x = 0
    front = [
        (front_x, 0),
        (front_x + neck_width, 0),
        (front_x + shoulder, armhole * 0.55),
        (front_x + bust, armhole * 1.30),
        (front_x + hip, length * 0.58),
        (front_x + bottom, length),
        (front_x, length),
    ]

    add_polyline(msp, front, "CUT")
    add_text(msp, "FRONT - CUT 1 ON FOLD", front_x, length + 25)

    msp.add_line(
        (front_x - 20, 0),
        (front_x - 20, length),
        dxfattribs={"layer": "GRAIN"},
    )

    add_text(msp, "FOLD", front_x - 65, length / 2, "GRAIN", 6)

    # Side slit mark
    slit_y = length - side_slit
    msp.add_line(
        (front_x + hip, slit_y),
        (front_x + bottom, slit_y),
        dxfattribs={"layer": "NOTCH"},
    )
    add_text(msp, "SIDE SLIT START", front_x + hip, slit_y + 15, "NOTCH", 6)

    # Back body
    back_x = max(500, bottom + 200)

    back = [
        (back_x, 0),
        (back_x + neck_width, 0),
        (back_x + shoulder, armhole * 0.55),
        (back_x + bust, armhole * 1.30),
        (back_x + hip, length * 0.58),
        (back_x + bottom, length),
        (back_x, length),
    ]

    add_polyline(msp, back, "CUT")
    add_text(msp, "BACK - CUT 1 ON FOLD", back_x, length + 25)

    msp.add_line(
        (back_x - 20, 0),
        (back_x - 20, length),
        dxfattribs={"layer": "GRAIN"},
    )

    # Sleeve
    sleeve_x = 0
    sleeve_y = -250
    sleeve_length = inch(m.get("sleeve_length", 9))
    sleeve_opening = inch(m.get("sleeve_opening", 13) / 2)

    sleeve = [
        (sleeve_x, sleeve_y),
        (sleeve_x + shoulder * 0.75, sleeve_y + armhole * 0.40),
        (sleeve_x + shoulder, sleeve_y + sleeve_length * 0.25),
        (sleeve_x + sleeve_opening, sleeve_y + sleeve_length),
        (sleeve_x, sleeve_y + sleeve_length),
    ]

    add_polyline(msp, sleeve, "CUT")
    add_text(msp, "SLEEVE - CUT 2", sleeve_x, sleeve_y + sleeve_length + 25)

    msp.add_line(
        (sleeve_x + shoulder / 2, sleeve_y),
        (sleeve_x + shoulder / 2, sleeve_y + sleeve_length),
        dxfattribs={"layer": "GRAIN"},
    )

    # Neckband
    neck_x = 550
    neck_y = -250
    neckband_length = inch(m.get("neck_width", 7) * 2.8)
    neckband_width = inch(2.5)

    neckband = [
        (neck_x, neck_y),
        (neck_x + neckband_length, neck_y),
        (neck_x + neckband_length, neck_y + neckband_width),
        (neck_x, neck_y + neckband_width),
    ]

    add_polyline(msp, neckband, "CUT")
    add_text(msp, "NECKBAND - CUT 1", neck_x, neck_y + neckband_width + 25)

    add_text(
        msp,
        "ALL DIMENSIONS IN MILLIMETERS",
        0,
        -370,
        "ANNOTATION",
        7,
    )

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output_file)
    return output_file
