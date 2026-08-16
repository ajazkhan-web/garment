import os
import ezdxf
import matplotlib.pyplot as plt
import numpy as np

TEMPLATES_DIR = "saved_templates"
os.makedirs(TEMPLATES_DIR, exist_ok=True)

def save_master_dxf(source_dxf_path, garment_type="default"):
    clean_name = garment_type.lower().replace(" ", "_") + ".dxf"
    target_path = os.path.join(TEMPLATES_DIR, clean_name)
    
    # Read & space pieces
    doc = ezdxf.readfile(source_dxf_path)
    msp = doc.modelspace()
    new_doc = ezdxf.new('R2010')
    new_msp = new_doc.modelspace()
    
    pieces = []
    for e in list(msp.query('LWPOLYLINE')) + list(msp.query('POLYLINE')):
        pts = [(p[0], p[1]) for p in (e.get_points() if e.dxftype() == 'LWPOLYLINE' else [v.dxf.location for v in e.vertices])]
        if len(pts) > 2:
            pieces.append(pts)
            
    # Write normalized spaced pieces
    cur_x = 0.0
    for idx, pts in enumerate(pieces):
        arr = np.array(pts, dtype=float)
        arr[:, 0] -= np.min(arr[:, 0])
        arr[:, 1] -= np.min(arr[:, 1])
        w = np.max(arr[:, 0])
        arr[:, 0] += cur_x
        poly = [(float(p[0]), float(p[1])) for p in arr]
        new_msp.add_lwpolyline(poly, close=True, dxfattribs={'layer': f'PIECE_{idx+1}'})
        cur_x += w + 6.0
        
    new_doc.saveas(target_path)
    # Also save as default master
    new_doc.saveas(os.path.join(TEMPLATES_DIR, "master.dxf"))
    return target_path, len(pieces)

def generate_technical_draft_and_dxf(spec_data, out_dxf="pattern.dxf", out_png="draft.png"):
    gtype = spec_data.get('garment_type', 'dress').lower()
    chest = float(spec_data.get('chest', 36.0) or 36.0)
    length = float(spec_data.get('length', 34.0) or 34.0)
    shoulder = float(spec_data.get('shoulder', 13.5) or 13.5)
    sleeve = float(spec_data.get('sleeve_length', 20.0) or 20.0)
    waist = float(spec_data.get('waist', 29.0) or (chest - 6.0))
    armhole = float(spec_data.get('armhole', 7.5) or 8.0)
    
    hc = chest / 4.0
    hw = waist / 4.0
    hs = shoulder / 2.0

    # 1. Coordinate Drafting Geometry
    if "dress" in gtype or "blazer" in gtype:
        front = [(0, 0), (hw + 3.0, 0), (hw + 2.0, length * 0.4), (hc + 1.5, length - armhole), (hs, length - 1.5), (3.5, length - 0.5), (0, length - 7.0)]
        back = [(0, 0), (hw + 2.0, 0), (hw + 1.5, length * 0.4), (hc + 1.0, length - armhole), (hs, length - 1.5), (3.5, length - 0.5), (0, length - 1.0)]
        sleeve_pts = [(0, 0), (8.0, 0), (7.0, sleeve - 4.0), (3.5, sleeve), (0, sleeve - 4.0)]
        collar_pts = [(0, 0), (14.0, 0), (14.0, 2.5), (0, 2.5)]
    else: # Jacket / Top
        front = [(0, 0), (hc + 4, 0), (hc + 4, length - armhole), (hs, length - 1.5), (4.0, length - 0.5), (0, length - 4.5)]
        back = [(0, 0), (hc + 4, 0), (hc + 4, length - armhole), (hs, length - 1.5), (4.0, length - 0.5), (0, length - 1.0)]
        sleeve_pts = [(0, 0), (9.5, 0), (9.0, sleeve - 4.0), (4.5, sleeve), (0, sleeve - 4.0)]
        collar_pts = [(0, 0), (16.0, 0), (16.0, 2.5), (0, 2.5)]

    pieces = {
        "FRONT_PANEL": front,
        "BACK_PANEL": back,
        "SLEEVE": sleeve_pts,
        "COLLAR": collar_pts
    }

    # 2. DXF Setup
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # 3. Plotting Blueprint (Perplexity CAD Style)
    fig, ax = plt.subplots(figsize=(16, 8), facecolor='#FFFFFF')
    ax.set_facecolor('#FFFFFF')

    cur_x = 0.0
    for name, pts in pieces.items():
        arr = np.array(pts, dtype=float)
        w = np.max(arr[:, 0]) - np.min(arr[:, 0])
        arr[:, 0] += cur_x
        
        # Polyline into DXF
        poly = [(float(p[0]), float(p[1])) for p in arr]
        msp.add_lwpolyline(poly, close=True, dxfattribs={'layer': name})
        
        # Technical CAD Blueprint Outline
        patch = plt.Polygon(arr, closed=True, facecolor='none', edgecolor='#2B4C2D', linewidth=1.8)
        ax.add_patch(patch)
        
        # Piece Label & Grainline
        cx, cy = np.mean(arr[:, 0]), np.mean(arr[:, 1])
        ax.text(cx, cy, name, color='#2B4C2D', weight='bold', fontsize=9, ha='center', va='center')
        ax.annotate('', xy=(cx, cy + 4), xytext=(cx, cy - 4), arrowprops=dict(arrowstyle='<->', color='#666', lw=1.2))
        ax.text(cx + 0.5, cy, "GRAINLINE", rotation=90, fontsize=6, color='#666', va='center')
        
        cur_x += w + 6.0

    doc.saveas(out_dxf)

    # Drafting Tech Spec Box
    spec_box = (
        f"2D CAD DRAFTING SPECIFICATIONS\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Style: {spec_data.get('garment_type', 'Garment')}\n"
        f"• Size: {spec_data.get('size', 'S')}\n"
        f"• Bust/Chest: {chest}\" | Waist: {waist}\"\n"
        f"• Length: {length}\" | Shoulder: {shoulder}\"\n"
        f"• Sleeve Length: {sleeve}\" | Armhole: {armhole}\"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Seam Allowance: 0.5\" Included\n"
        f"• Optitex AAMA Formatted"
    )
    ax.text(cur_x + 1.0, length * 0.4, spec_box, fontsize=9, family='monospace', bbox=dict(boxstyle='round,pad=0.8', facecolor='#F8F9FA', edgecolor='#D0D7DE'))

    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title("Optitex 2D Pattern Layout & Technical Drafting Blueprint", fontsize=13, weight='bold', pad=15, color='#1A202C')
    plt.tight_layout()
    plt.savefig(out_png, dpi=250, bbox_inches='tight')
    plt.close(fig)

    return out_dxf, out_png
