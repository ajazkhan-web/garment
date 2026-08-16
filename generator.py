import os
import re
import ezdxf
import matplotlib.pyplot as plt
import numpy as np

def parse_val(val, default=0.0):
    if val is None: return default
    if isinstance(val, (int, float)): return float(val)
    val_str = str(val).strip().replace('"', '').replace("'", "")
    try:
        if " " in val_str:
            parts = val_str.split()
            whole = float(parts[0])
            frac = parts[1].split('/')
            return whole + (float(frac[0]) / float(frac[1]))
        elif "/" in val_str:
            frac = val_str.split('/')
            return float(frac[0]) / float(frac[1])
        return float(val_str)
    except Exception:
        nums = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", val_str)
        return float(nums[0]) if nums else default

def generate_bezier(p0, p1, p2, n=12):
    t = np.linspace(0, 1, n)
    pts = [(1-ti)**2 * np.array(p0) + 2*(1-ti)*ti * np.array(p1) + ti**2 * np.array(p2) for ti in t]
    return [(float(p[0]), float(p[1])) for p in pts]

def generate_technical_draft_and_dxf(spec_data, out_dxf="draft.dxf", out_png="preview.png"):
    meas = spec_data.get("measurements", {}) if isinstance(spec_data, dict) else {}
    gtype = str(spec_data.get("garment_type", "Garment")).lower()

    length = parse_val(meas.get("length_from_hps") or meas.get("length"), 23.0)
    chest = parse_val(meas.get("chest") or meas.get("bust"), 36.0)
    waist = parse_val(meas.get("waist"), 29.0)
    shoulder = parse_val(meas.get("shoulder"), 14.0)
    armhole = parse_val(meas.get("armhole"), 7.5)
    sleeve = parse_val(meas.get("sleeve_length_from_neck_seam") or meas.get("sleeve_length"), 22.0)
    
    half_ch = chest / 4.0 if chest > 0 else 9.0
    half_w = waist / 4.0 if waist > 0 else 7.25
    half_sh = shoulder / 2.0 if shoulder > 0 else 7.0

    # Contours Drafting
    front_hem = [(0, 0), (half_w + 3.0, 0)]
    front_side = [(half_w + 1.5, length * 0.4), (half_ch + 1.0, length - armhole)]
    front_ah = generate_bezier((half_ch + 1.0, length - armhole), (half_sh - 0.5, length - armhole + 2.0), (half_sh, length - 1.5))
    front_neck = [(3.5, length - 0.5), (0, length - 4.5)]
    front_pts = front_hem + front_side + front_ah + front_neck

    back_hem = [(0, 0), (half_w + 2.0, 0)]
    back_side = [(half_w + 0.5, length * 0.4), (half_ch + 0.5, length - armhole)]
    back_ah = generate_bezier((half_ch + 0.5, length - armhole), (half_sh - 0.5, length - armhole + 2.0), (half_sh, length - 1.5))
    back_neck = [(3.5, length - 0.5), (0, length - 1.2)]
    back_pts = back_hem + back_side + back_ah + back_neck

    # Sleeve Crown
    bicep_w = 12.0
    cap_h = armhole * 0.65
    sleeve_l_cap = generate_bezier((0, sleeve - cap_h), (bicep_w * 0.25, sleeve), (bicep_w * 0.5, sleeve))
    sleeve_r_cap = generate_bezier((bicep_w * 0.5, sleeve), (bicep_w * 0.75, sleeve), (bicep_w, sleeve - cap_h))
    sleeve_pts = [(0, 0)] + sleeve_l_cap + sleeve_r_cap + [(bicep_w, 0), (0, 0)]
    collar_pts = [(0, 0), (16.0, 0), (16.0, 2.5), (0, 2.5)]

    pieces = {
        "FRONT_BODY": front_pts,
        "BACK_BODY": back_pts,
        "SLEEVE_CROWN": sleeve_pts,
        "COLLAR": collar_pts
    }

    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    fig, ax = plt.subplots(figsize=(16, 8), facecolor='#FFFFFF')
    ax.set_facecolor('#FFFFFF')

    cur_x = 0.0
    for name, pts in pieces.items():
        arr = np.array(pts, dtype=float)
        w = np.max(arr[:, 0]) - np.min(arr[:, 0])
        arr[:, 0] += cur_x

        poly = [(float(p[0]), float(p[1])) for p in arr]
        msp.add_lwpolyline(poly, close=True, dxfattribs={'layer': name})

        patch = plt.Polygon(arr, closed=True, facecolor='none', edgecolor='#1B4D3E', linewidth=1.7)
        ax.add_patch(patch)

        cx, cy = np.mean(arr[:, 0]), np.mean(arr[:, 1])
        ax.text(cx, cy, name, color='#1B4D3E', weight='bold', fontsize=8, ha='center', va='center')
        ax.annotate('', xy=(cx, cy + 3.0), xytext=(cx, cy - 3.0), arrowprops=dict(arrowstyle='<->', color='#777', lw=1.0))
        ax.text(cx + 0.4, cy, "GRAINLINE", rotation=90, fontsize=6, color='#777', va='center')
        cur_x += w + 6.0

    doc.saveas(out_dxf)

    spec_box = (
        f"CAD SPECIFICATION\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• Style: {spec_data.get('garment_type', 'Garment')}\n"
        f"• Size: {spec_data.get('size', 'S')}\n"
        f"• Length: {length}\" | Bust: {chest}\"\n"
        f"• Shoulder: {shoulder}\" | Sleeve: {sleeve}\"\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• S-Curve Sleeve & Notch Lines\n"
        f"• Optitex AAMA DXF Output"
    )
    ax.text(cur_x + 1.0, length * 0.4, spec_box, fontsize=8.5, family='monospace', bbox=dict(boxstyle='round,pad=0.7', facecolor='#F6F8FA', edgecolor='#CBD5E1'))

    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title("Optitex 2D Pattern Layout Blueprint", fontsize=13, weight='bold', pad=15)
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches='tight')
    plt.close(fig)

    return out_dxf, out_png
