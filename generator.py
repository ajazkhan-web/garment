import os
import ezdxf
import matplotlib.pyplot as plt
import numpy as np

def generate_technical_draft_and_dxf(spec_data, out_dxf="drafted_pattern.dxf", out_png="technical_draft.png"):
    chest = float(spec_data.get('chest', 46.0))
    length = float(spec_data.get('length', 28.5))
    shoulder = float(spec_data.get('shoulder', 23.0))
    sleeve_len = float(spec_data.get('sleeve_length', 23.0))
    neck_w = float(spec_data.get('neck_width', 7.75))
    armhole = float(spec_data.get('armhole', 10.0))
    
    # 1. Coordinate Drafting Engine for all 4 Main Pieces
    # Half-body / Piece coordinates calculation
    half_chest = chest / 2.0
    half_sh = shoulder / 2.0
    
    front_pts = [
        (0, 0), (half_chest, 0), (half_chest, length - armhole),
        (half_sh, length - 1.5), (neck_w / 2.0, length - 0.5), (0, length - 4.5)
    ]
    
    back_pts = [
        (0, 0), (half_chest, 0), (half_chest, length - armhole),
        (half_sh, length - 1.5), (neck_w / 2.0, length - 0.5), (0, length - 1.0)
    ]
    
    sleeve_pts = [
        (0, 0), (9.25, 0), (9.25, sleeve_len - 5.0),
        (4.62, sleeve_len), (0, sleeve_len - 5.0)
    ]
    
    collar_pts = [(0, 0), (neck_w * 2, 0), (neck_w * 2, 2.75), (0, 2.75)]
    
    pieces_dict = {
        "FRONT_BODY": front_pts,
        "BACK_BODY": back_pts,
        "SLEEVE": sleeve_pts,
        "NECK_COLLAR": collar_pts
    }

    # 2. Build Multi-Piece DXF with Spacing
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    fig, ax = plt.subplots(figsize=(16, 8), facecolor='#FDFDFD')
    ax.set_facecolor('#FDFDFD')
    
    current_x = 0.0
    for name, pts in pieces_dict.items():
        arr = np.array(pts, dtype=float)
        width = np.max(arr[:, 0]) - np.min(arr[:, 0])
        arr[:, 0] += current_x
        
        # Add DXF Polyline
        poly_pts = [(float(p[0]), float(p[1])) for p in arr]
        msp.add_lwpolyline(poly_pts, close=True, dxfattribs={'layer': name})
        
        # Draw Visual Schematic
        patch = plt.Polygon(arr, closed=True, facecolor='#EDF4ED', edgecolor='#2B4C2D', linewidth=1.8)
        ax.add_patch(patch)
        
        cx, cy = np.mean(arr[:, 0]), np.mean(arr[:, 1])
        ax.text(cx, cy, name, color='#2B4C2D', weight='bold', fontsize=10, ha='center')
        
        current_x += width + 6.0

    doc.saveas(out_dxf)

    # 3. Add Tech Spec Annotation on the Image
    spec_text = (
        f"DRAFTING SPECIFICATIONS:\n"
        f"• Style: {spec_data.get('garment_type', 'Quarter Zip')}\n"
        f"• Size: {spec_data.get('size', 'L')}\n"
        f"• Chest: {chest}\"\n"
        f"• Total Length: {length}\"\n"
        f"• Shoulder: {shoulder}\"\n"
        f"• Sleeve: {sleeve_len}\""
    )
    ax.text(current_x + 2, length / 2.0, spec_text, fontsize=10, bbox=dict(facecolor='#EFEFEF', alpha=0.8, edgecolor='#999'))

    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title("2D Garment Pattern Layout & Measurement Blueprint", fontsize=14, weight='bold', pad=15)
    plt.tight_layout()
    plt.savefig(out_png, dpi=250)
    plt.close()

    return out_dxf, out_png
