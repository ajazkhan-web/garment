import os
import ezdxf
import matplotlib.pyplot as plt
import numpy as np

TEMPLATE_DIR = "saved_templates"
os.makedirs(TEMPLATE_DIR, exist_ok=True)

def parse_all_pieces_from_dxf(dxf_path):
    """DXF se saare distinct contours aur lines extract karta hai"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    pieces = []

    # 1. LWPOLYLINE
    for e in msp.query('LWPOLYLINE'):
        pts = [(p[0], p[1]) for p in e.get_points()]
        if len(pts) > 2:
            layer = e.dxf.layer if hasattr(e.dxf, 'layer') else f"PIECE_{len(pieces)+1}"
            pieces.append((layer, pts))

    # 2. POLYLINE
    for e in msp.query('POLYLINE'):
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        if len(pts) > 2:
            layer = e.dxf.layer if hasattr(e.dxf, 'layer') else f"PIECE_{len(pieces)+1}"
            pieces.append((layer, pts))

    return pieces

def build_spaced_dxf_and_image(pieces, scale_x, scale_y, out_dxf, out_png, title_info=""):
    """Har piece ko space out karke DXF aur Image dono mein alag banata hai"""
    new_doc = ezdxf.new('R2010')
    new_msp = new_doc.modelspace()

    fig, ax = plt.subplots(figsize=(20, 7), facecolor='#F7F9F6')
    ax.set_facecolor('#F7F9F6')

    colors = ['#4E674F', '#5B7A5D', '#688E6A', '#7A9A7B', '#8DA68E', '#9FB3A0']
    current_x = 0.0

    piece_names = ["FRONT", "BACK", "SLEEVE", "NECK_RIB", "BOTTOM_RIB", "POCKET"]

    for idx, (raw_layer, pts) in enumerate(pieces):
        arr = np.array(pts, dtype=float)
        
        # Scaling apply karein
        arr[:, 0] *= scale_x
        arr[:, 1] *= scale_y

        min_x, max_x = np.min(arr[:, 0]), np.max(arr[:, 0])
        min_y, max_y = np.min(arr[:, 1]), np.max(arr[:, 1])
        width = max_x - min_x

        # Normalize to (0, 0)
        arr[:, 0] -= min_x
        arr[:, 1] -= min_y

        # Shift along X axis (Piece separation)
        arr[:, 0] += current_x

        layer_name = piece_names[idx] if idx < len(piece_names) else f"PIECE_{idx+1}"

        # 1. Write to DXF with separate Layer and shifted coordinates
        poly_pts = [(float(p[0]), float(p[1])) for p in arr]
        new_msp.add_lwpolyline(poly_pts, close=True, dxfattribs={'layer': layer_name})

        # 2. Draw on Review Image
        col = colors[idx % len(colors)]
        poly_patch = plt.Polygon(arr, closed=True, facecolor=col, edgecolor='#233324', linewidth=1.6, alpha=0.92)
        ax.add_patch(poly_patch)
        
        cx, cy = np.mean(arr[:, 0]), np.mean(arr[:, 1])
        ax.text(cx, cy, layer_name.replace('_', ' '), color='white', weight='bold', fontsize=9, ha='center', va='center')

        # Gap between pieces (8 inches gap so Optitex treats them as separate pieces)
        current_x += width + 8.0

    new_doc.saveas(out_dxf)

    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title(f"{title_info} ({len(pieces)} Separate Pieces Generated)", fontsize=13, weight='bold', color='#233324', pad=18)
    plt.tight_layout()
    plt.savefig(out_png, dpi=250, bbox_inches='tight')
    plt.close()

    return out_dxf, out_png, len(pieces)

def save_master_template(source_dxf_path, template_name="master.dxf"):
    target_path = os.path.join(TEMPLATE_DIR, template_name)
    pieces = parse_all_pieces_from_dxf(source_dxf_path)
    
    # Save base template spaced out
    out_dxf, out_png, count = build_spaced_dxf_and_image(pieces, 1.0, 1.0, target_path, "master_preview.png", "Master Template Saved")
    return target_path, out_png, count

def morph_saved_pattern(new_data, template_name="master.dxf", out_dxf="morphed_pattern.dxf", out_png="preview.png"):
    template_path = os.path.join(TEMPLATE_DIR, template_name)
    if not os.path.exists(template_path):
        template_path = "garment_pattern.dxf"

    pieces = parse_all_pieces_from_dxf(template_path)

    target_len = float(new_data.get('length', 28.5))
    target_chest = float(new_data.get('chest', 46.0))
    base_len = 28.5
    base_chest = 46.0

    scale_y = target_len / base_len if base_len else 1.0
    scale_x = target_chest / base_chest if base_chest else 1.0

    title_str = f"Pattern (Size: {new_data.get('size', 'L')}) — Chest: {target_chest}\", Length: {target_len}\""
    out_dxf, out_png, count = build_spaced_dxf_and_image(pieces, scale_x, scale_y, out_dxf, out_png, title_str)
    return out_dxf, out_png