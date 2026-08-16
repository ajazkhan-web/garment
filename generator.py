import os
import ezdxf
import matplotlib.pyplot as plt
import numpy as np

def generate_garment_pattern(spec_data, out_dxf="morphed_pattern_all_pieces.dxf", out_png="pattern_preview.png"):
    garment = spec_data.get('garment_type', 'jacket').lower()
    chest = float(spec_data.get('chest', 46.0))
    length = float(spec_data.get('length', 28.5))
    shoulder = float(spec_data.get('shoulder', 23.0))
    sleeve = float(spec_data.get('sleeve_length', 23.0))
    
    half_chest = chest / 2.0
    half_sh = shoulder / 2.0
    
    # Coordinates based on garment category
    if "poncho" in garment or "top" in garment or "women" in garment:
        front = [(0, 0), (half_chest + 2, 0), (half_chest + 4, length - 6), (half_sh, length - 1.5), (4.5, length - 0.5), (0, length - 4.0)]
        back = [(0, 0), (half_chest + 2, 0), (half_chest + 4, length - 6), (half_sh, length - 1.5), (4.5, length - 0.5), (0, length - 1.2)]
        sleeve_p = [(0, 0), (12.0, 0), (10.0, sleeve), (2.0, sleeve), (0, 0)]
        neck_p = [(0, 0), (18.0, 0), (18.0, 2.0), (0, 2.0)]
    else: # Jacket / Pullover / T-shirt
        front = [(0, 0), (half_chest, 0), (half_chest, length - 10.0), (half_sh, length - 1.5), (4.0, length - 0.5), (0, length - 4.5)]
        back = [(0, 0), (half_chest, 0), (half_chest, length - 10.0), (half_sh, length - 1.5), (4.0, length - 0.5), (0, length - 1.0)]
        sleeve_p = [(0, 0), (9.5, 0), (9.5, sleeve - 4.0), (4.75, sleeve), (0, sleeve - 4.0)]
        neck_p = [(0, 0), (16.0, 0), (16.0, 2.5), (0, 2.5)]

    pieces = {
        "FRONT_BODY": front,
        "BACK_BODY": back,
        "SLEEVE": sleeve_p,
        "COLLAR_RIB": neck_p
    }

    # DXF Generation
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    fig, ax = plt.subplots(figsize=(16, 7), facecolor='#FAFAFA')
    ax.set_facecolor('#FAFAFA')
    
    current_x = 0.0
    colors = ['#557A57', '#6A8E6C', '#7F9F81', '#94B096']
    
    for idx, (name, pts) in enumerate(pieces.items()):
        arr = np.array(pts, dtype=float)
        w = np.max(arr[:, 0]) - np.min(arr[:, 0])
        arr[:, 0] += current_x
        
        # Add to DXF Layer
        poly_pts = [(float(p[0]), float(p[1])) for p in arr]
        msp.add_lwpolyline(poly_pts, close=True, dxfattribs={'layer': name})
        
        # Plot Polygon
        patch = plt.Polygon(arr, closed=True, facecolor=colors[idx % len(colors)], edgecolor='#223322', linewidth=1.5, alpha=0.9)
        ax.add_patch(patch)
        
        cx, cy = np.mean(arr[:, 0]), np.mean(arr[:, 1])
        ax.text(cx, cy, name.replace('_', ' '), color='white', weight='bold', fontsize=9, ha='center', va='center')
        
        current_x += w + 6.0

    doc.saveas(out_dxf)

    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title(f"Technical Pattern Draft ({garment.upper()} - Size: {spec_data.get('size', 'L')})", fontsize=12, weight='bold', pad=15)
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches='tight')
    plt.close(fig) # Cleanly close figure to prevent corrupt file

    return out_dxf, out_png
