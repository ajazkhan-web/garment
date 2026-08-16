import os
import ezdxf
import matplotlib.pyplot as plt
import numpy as np

def generate_curve(p0, p1, p2, num_pts=15):
    """Quadratic Bézier curve generator for CAD armholes & sleeve caps"""
    t = np.linspace(0, 1, num_pts)
    curve = [(1-ti)**2 * np.array(p0) + 2*(1-ti)*ti * np.array(p1) + ti**2 * np.array(p2) for ti in t]
    return [(float(pt[0]), float(pt[1])) for pt in curve]

def generate_technical_draft_and_dxf(spec_data, out_dxf="drafted_pattern.dxf", out_png="blueprint.png"):
    gtype = spec_data.get('garment_type', 'dress').lower()
    chest = float(spec_data.get('chest', 36.0) or 36.0)
    length = float(spec_data.get('length', 34.0) or 34.0)
    shoulder = float(spec_data.get('shoulder', 13.5) or 13.5)
    sleeve = float(spec_data.get('sleeve_length', 20.0) or 20.0)
    waist = float(spec_data.get('waist', 29.0) or 28.0)
    armhole = float(spec_data.get('armhole', 7.5) or 8.0)
    pleat_count = int(spec_data.get('pleats', 4) if "pleat" in str(spec_data) else 0)

    half_ch = chest / 4.0
    half_w = waist / 4.0
    half_sh = shoulder / 2.0
    sleeve_bicep = (chest * 0.35) / 2.0

    # 1. Front Body Contours (Wrap / V-Neck + Armhole Curve)
    front_hem = [(0, 0), (half_w + 3.5, 0)]
    front_side = [(half_w + 1.5, length * 0.45), (half_ch + 0.5, length - armhole)]
    
    # Armhole Curve
    ah_p0 = (half_ch + 0.5, length - armhole)
    ah_ctrl = (half_sh - 0.5, length - armhole + 1.5)
    ah_p1 = (half_sh, length - 1.5)
    front_ah = generate_curve(ah_p0, ah_ctrl, ah_p1, 12)
    
    front_neck = [(3.25, length - 0.5), (0, length - 7.5)]
    front_pts = front_hem + front_side + front_ah + front_neck

    # 2. Back Body Contours
    back_hem = [(0, 0), (half_w + 2.0, 0)]
    back_side = [(half_w + 1.0, length * 0.45), (half_ch, length - armhole)]
    back_ah = generate_curve((half_ch, length - armhole), (half_sh, length - armhole + 1.8), (half_sh, length - 1.5), 12)
    back_neck = [(3.25, length - 0.5), (0, length - 1.2)]
    back_pts = back_hem + back_side + back_ah + back_neck

    # 3. Professional Sleeve with Curved Crown Cap
    cap_height = armhole * 0.65
    s_wrist = 4.5
    sleeve_l_under = (0, 0)
    sleeve_l_bicep = (0, sleeve - cap_height)
    
    # S-Curve Sleeve Cap
    cap_left = generate_curve(sleeve_l_bicep, (sleeve_bicep * 0.4, sleeve - cap_height + 2.0), (sleeve_bicep, sleeve), 12)
    cap_right = generate_curve((sleeve_bicep, sleeve), (sleeve_bicep * 1.6, sleeve - cap_height + 2.0), (sleeve_bicep * 2, sleeve - cap_height), 12)
    sleeve_r_under = (sleeve_bicep * 2, 0)
    sleeve_pts = [(0, 0)] + cap_left + cap_right + [sleeve_r_under, (s_wrist * 2, 0)]

    # 4. Collar / Facing
    collar_pts = [(0, 0), (half_sh * 2.2, 0), (half_sh * 2.2, 2.5), (0, 2.5)]

    pieces = {
        "FRONT_WRAP_PANEL": front_pts,
        "BACK_PANEL": back_pts,
        "SLEEVE_CROWN": sleeve_pts,
        "COLLAR_FACING": collar_pts
    }

    # DXF & Matplotlib Setup
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    fig, ax = plt.subplots(figsize=(18, 8), facecolor='#FFFFFF')
    ax.set_facecolor('#FFFFFF')

    current_x = 0.0
    for name, pts in pieces.items():
        arr = np.array(pts, dtype=float)
        w = np.max(arr[:, 0]) - np.min(arr[:, 0])
        arr[:, 0] += current_x

        # Write DXF Polyline
        poly = [(float(p[0]), float(p[1])) for p in arr]
        msp.add_lwpolyline(poly, close=True, dxfattribs={'layer': name})

        # Draw technical outlines
        patch = plt.Polygon(arr, closed=True, facecolor='none', edgecolor='#1F3A24', linewidth=1.6)
        ax.add_patch(patch)

        cx, cy = np.mean(arr[:, 0]), np.mean(arr[:, 1])
        ax.text(cx, cy, name, color='#1F3A24', weight='bold', fontsize=8, ha='center', va='center')

        # Grainline
        ax.annotate('', xy=(cx, cy + 3.5), xytext=(cx, cy - 3.5), arrowprops=dict(arrowstyle='<->', color='#777', lw=1.0))
        ax.text(cx + 0.4, cy, "GRAINLINE", rotation=90, fontsize=6, color='#777', va='center')

        # Pleats & Notch marks on Front Panel
        if "FRONT" in name and pleat_count > 0:
            for p_i in range(pleat_count):
                py = (length * 0.25) + (p_i * 1.25)
                px = np.min(arr[:, 0]) + (half_w * 0.4)
                ax.plot([px, px + 1.8], [py, py + 0.4], color='#D9381E', lw=1.2, linestyle='--')
                ax.text(px + 2.0, py, f"Notch {p_i+1}", fontsize=6, color='#D9381E')
                # Add Notch line to DXF
                msp.add_line((float(px), float(py)), (float(px + 1.8), float(py + 0.4)), dxfattribs={'layer': 'NOTCHES'})

        current_x += w + 6.0

    doc.saveas(out_dxf)

    # Tech Spec CAD Information Panel
    info_str = (
        f"CAD TECHNICAL DRAFT SPECIFICATION\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Garment: {spec_data.get('garment_type', 'Tailored Dress')}\n"
        f"• Size: {spec_data.get('size', 'S')}\n"
        f"• Length: {length}\" | Bust: {chest}\"\n"
        f"• Waist: {waist}\" | Shoulder: {shoulder}\"\n"
        f"• Sleeve: {sleeve}\" | Armhole: {armhole}\"\n"
        f"• Pleats: {pleat_count} Pleats / Notches Marked\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Standards: 0.5\" Seam Allowance\n"
        f"• Output: Optitex AAMA Polyline (.DXF)"
    )
    ax.text(current_x + 1.0, length * 0.4, info_str, fontsize=9, family='monospace', bbox=dict(boxstyle='round,pad=0.8', facecolor='#F6F8FA', edgecolor='#CBD5E1'))

    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title("Technical 2D Pattern Draft Layout & CAD Blueprints", fontsize=13, weight='bold', pad=15, color='#0F172A')
    plt.tight_layout()
    plt.savefig(out_png, dpi=250, bbox_inches='tight')
    plt.close(fig)

    return out_dxf, out_png
