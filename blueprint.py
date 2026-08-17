"""Blueprint renderer for apparel pattern drafting Telegram bot.

Renders all pattern pieces onto a clean 16:9 white canvas as a professional
blueprint preview image (PNG).  matplotlib, PIL and numpy are lazy-imported
*inside* functions so the bot stays stable even when those packages are missing.
"""
from __future__ import annotations


def _piece_bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_size(bbox):
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _layout_pieces(pieces_data, canvas_w=16, canvas_h=9, padding=0.5, title_height=1.2):
    """Arrange pieces in a grid. Returns list of (piece_data, x_offset, y_offset, scale)."""
    avail_w, avail_h = canvas_w - 2 * padding, canvas_h - title_height - padding
    bboxes = [_piece_bbox(pd["points"]) if pd.get("points") else (0, 0, 1, 1)
              for pd in pieces_data]
    sizes = [_bbox_size(b) for b in bboxes]
    n = len(pieces_data)
    if n == 0:
        return []
    best, best_score = (1, n), float("inf")
    for cols in range(1, n + 1):
        rows = (n + cols - 1) // cols
        score = abs((avail_w / cols) / (avail_h / rows) - 16 / 9)
        if score < best_score:
            best_score, best = score, (cols, rows)
    cols, rows = best
    cell_w, cell_h = avail_w / cols, avail_h / rows
    margin, scale = 0.15, float("inf")
    slot_w, slot_h = cell_w * (1 - 2 * margin), cell_h * (1 - 2 * margin)
    for w, h in sizes:
        if w > 0:
            scale = min(scale, slot_w / w)
        if h > 0:
            scale = min(scale, slot_h / h)
    if scale == float("inf"):
        scale = 1.0
    layout = []
    for idx, pd in enumerate(pieces_data):
        col, row = idx % cols, idx // cols
        bbox = bboxes[idx]
        w, h = _bbox_size(bbox)
        cell_x, cell_y = padding + col * cell_w, title_height + row * cell_h
        x_off = cell_x + (cell_w - w * scale) / 2 - bbox[0] * scale
        y_off = cell_y + (cell_h - h * scale) / 2 - bbox[1] * scale
        layout.append((pd, x_off, y_off, scale))
    return layout


def _draw_curve(ax, curve, x_off, y_off, scale, color="black", lw=1.5):
    import numpy as np
    p0 = curve.get("start", curve.get("p0", (0, 0)))
    p1 = curve.get("cp1", curve.get("control1", (0, 0)))
    p2 = curve.get("cp2", curve.get("control2", (0, 0)))
    p3 = curve.get("end", curve.get("p3", (0, 0)))
    t = np.linspace(0, 1, 40)
    bx = (1 - t)**3 * p0[0] + 3 * (1 - t)**2 * t * p1[0] + 3 * (1 - t) * t**2 * p2[0] + t**3 * p3[0]
    by = (1 - t)**3 * p0[1] + 3 * (1 - t)**2 * t * p1[1] + 3 * (1 - t) * t**2 * p2[1] + t**3 * p3[1]
    ax.plot(x_off + bx * scale, y_off + by * scale, color=color, lw=lw, solid_capstyle="round")


def _draw_dart(ax, dart, x_off, y_off, scale):
    start, end = dart.get("start", (0, 0)), dart.get("end", (0, 0))
    width = dart.get("width", 1.0)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    seg = [(start[0] + nx, start[1] + ny), start, (start[0] - nx, start[1] - ny), end]
    ax.plot([x_off + p[0] * scale for p in seg], [y_off + p[1] * scale for p in seg],
            color="green", lw=1.2, ls="--", dash_capstyle="round")


def _draw_notch(ax, notch, x_off, y_off, scale):
    import numpy as np
    point, depth = notch.get("point", (0, 0)), notch.get("depth", 0.3)
    rad = np.radians(notch.get("angle", 0.0))
    dx, dy = depth * np.cos(rad), depth * np.sin(rad)
    px, py = x_off + point[0] * scale, y_off + point[1] * scale
    ax.plot([px, px + dx], [py, py + dy], color="red", lw=1.5, solid_capstyle="round")


def _draw_grainline(ax, grainline, x_off, y_off, scale):
    if not grainline or len(grainline) < 2:
        return
    p0, p1 = grainline[0], grainline[1]
    ax.annotate("", xy=(x_off + p0[0] * scale, y_off + p0[1] * scale),
                xytext=(x_off + p1[0] * scale, y_off + p1[1] * scale),
                arrowprops=dict(arrowstyle="<->", color="blue", lw=1.2))


def _draw_piece(ax, piece_data, x_off, y_off, scale):
    from matplotlib.patches import Polygon as MplPolygon
    pts = piece_data.get("points", [])
    if pts:
        xs = [x_off + p[0] * scale for p in pts]
        ys = [y_off + p[1] * scale for p in pts]
        if (xs[0], ys[0]) != (xs[-1], ys[-1]):
            xs.append(xs[0]); ys.append(ys[0])
        ax.plot(xs, ys, color="black", lw=1.5, solid_joinstyle="round", solid_capstyle="round")
        ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True, facecolor="white",
                                edgecolor="none", alpha=0.0, zorder=0))
    for curve in piece_data.get("curves", []):
        _draw_curve(ax, curve, x_off, y_off, scale)
    for dart in piece_data.get("darts", []):
        _draw_dart(ax, dart, x_off, y_off, scale)
    for notch in piece_data.get("notches", []):
        _draw_notch(ax, notch, x_off, y_off, scale)
    if piece_data.get("grainline"):
        _draw_grainline(ax, piece_data["grainline"], x_off, y_off, scale)
    bbox = _piece_bbox(pts) if pts else (0, 0, 1, 1)
    cx = x_off + (bbox[0] + bbox[2]) / 2 * scale
    name = piece_data.get("name", piece_data.get("label", "Piece"))
    ax.text(cx, y_off + bbox[3] * scale + 0.12, name, ha="center", va="bottom",
            fontsize=10, fontweight="bold", color="black")
    cut_label = piece_data.get("label", "")
    cut_text = f"CUT {piece_data.get('cut_quantity', 1)}" + (f" {cut_label}" if cut_label else "")
    ax.text(cx, y_off + bbox[1] * scale - 0.15, cut_text, ha="center", va="top",
            fontsize=8, color="dimgray", style="italic")
    key_meas = piece_data.get("measurements", {})
    if key_meas:
        ax.text(cx, y_off + (bbox[1] + bbox[3]) / 2 * scale,
                "\n".join(f"{k}: {v}" for k, v in list(key_meas.items())[:4]),
                ha="center", va="center", fontsize=6.5, color="gray", alpha=0.8)



    # Draw slash fold lines (radiating drape lines) if present.
    slash_lines = piece_data.get("slash_fold_lines", [])
    if slash_lines:
        for line in slash_lines:
            if len(line) >= 2:
                x0, y0 = line[0]
                x1, y1 = line[1]
                ax.plot([x_off + x0*scale, x_off + x1*scale],
                        [y_off + y0*scale, y_off + y1*scale],
                        color="#B0B0B0", lw=0.5, ls=":", zorder=3)
        # Draw pivot point marker
        first_line = slash_lines[0]
        px, py = first_line[0]
        ax.plot(x_off + px*scale, y_off + py*scale, 'o',
                color="#666666", markersize=3, zorder=5)

def _draw_title_banner(ax, md, canvas_w, canvas_h):
    from matplotlib.patches import FancyBboxPatch
    bar = FancyBboxPatch((0.3, canvas_h - 1.15), canvas_w - 0.6, 1.0,
                         boxstyle="round,pad=0.02", linewidth=1.2,
                         edgecolor="#333333", facecolor="#F5F5F5", zorder=5)
    ax.add_patch(bar)
    md = md or {}
    garment, silhouette = md.get("garment_type", "Pattern"), md.get("silhouette", "")
    title = f"{garment} — {silhouette}" if silhouette else garment
    ax.text(canvas_w / 2, canvas_h - 0.55, title, ha="center", va="center",
            fontsize=16, fontweight="bold", color="#222222", zorder=6)
    meas = md.get("measurements", {})
    parts = [f"{k.capitalize()} {v}" for k in ("bust", "waist", "hip")
             if (v := meas.get(k, ""))]
    sub = "  |  ".join(parts)
    size_label = md.get("size_label", "")
    if size_label:
        sub += f"  |  Size {size_label}" if sub else f"Size {size_label}"
    if sub:
        ax.text(canvas_w / 2, canvas_h - 0.9, sub, ha="center", va="center",
                fontsize=9, color="#555555", zorder=6)


def _draw_style_overlay(ax, layout):
    for pd, x_off, y_off, scale in layout:
        style = pd.get("style_details") or {}
        if not isinstance(style, dict):
            continue
        for gather in style.get("gathers", []):
            pt = gather.get("point", gather) if isinstance(gather, dict) else gather
            if pt:
                px, py = x_off + pt[0] * scale, y_off + pt[1] * scale
                ax.plot([px - 0.08, px + 0.08], [py, py], color="purple", lw=0.8, ls=":", zorder=4)
                ax.plot([px - 0.06, px + 0.06], [py + 0.04, py - 0.04], color="purple", lw=0.8, ls=":", zorder=4)
        for pleat in style.get("pleats", []):
            pt = pleat.get("point", pleat) if isinstance(pleat, dict) else pleat
            if pt:
                px, py = x_off + pt[0] * scale, y_off + pt[1] * scale
                ax.plot([px, px], [py - 0.1, py + 0.1], color="#0066CC", lw=1.0, ls="-.", zorder=4)
        if style.get("cowl") and pd.get("points"):
            bbox = _piece_bbox(pd["points"])
            cx = x_off + (bbox[0] + bbox[2]) / 2 * scale
            cy = y_off + bbox[3] * scale
            ax.text(cx, cy + 0.2, "COWL", ha="center", va="bottom",
                    fontsize=7, color="#8B4513", style="italic", zorder=6)


def render_blueprint(pieces, output_path, measurements_data=None, style=None):
    """Render all pattern pieces to a blueprint PNG.

    pieces: list of dicts with keys name, points, curves, darts, notches,
        grainline, label, cut_quantity, measurements, style_details.
    measurements_data: full parsed dict from Gemini (measurements, garment_type,
        silhouette, styling_details, size_label, measurements_table).
    style: optional StyleDetails dict or list of dicts (one per piece).
    Returns output_path on success; raises RuntimeError on failure.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np  # noqa: F401

    try:
        canvas_w, canvas_h = 16, 9
        fig = plt.figure(figsize=(canvas_w, canvas_h), dpi=150, facecolor="white")
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, canvas_w); ax.set_ylim(0, canvas_h)
        ax.set_aspect("equal"); ax.axis("off")
        # Subtle graph-paper grid.
        for gx in range(0, canvas_w + 1):
            ax.axvline(gx, color="#EEEEEE", lw=0.4, zorder=0)
        for gy in range(0, canvas_h + 1):
            ax.axhline(gy, color="#EEEEEE", lw=0.4, zorder=0)
        for gx in range(0, canvas_w * 2 + 1):
            ax.axvline(gx / 2, color="#F7F7F7", lw=0.2, zorder=0)
        for gy in range(0, canvas_h * 2 + 1):
            ax.axhline(gy / 2, color="#F7F7F7", lw=0.2, zorder=0)
        _draw_title_banner(ax, measurements_data, canvas_w, canvas_h)
        # Merge top-level style into pieces.
        if isinstance(style, dict):
            for p in pieces:
                if "style_details" not in p:
                    p["style_details"] = style
        elif isinstance(style, list):
            for p, s in zip(pieces, style):
                p["style_details"] = s
        layout = _layout_pieces(pieces, canvas_w, canvas_h, padding=0.5, title_height=1.2)
        for pd, x_off, y_off, scale in layout:
            _draw_piece(ax, pd, x_off, y_off, scale)
        _draw_style_overlay(ax, layout)
        fig.savefig(output_path, dpi=150, facecolor="white", pad_inches=0)
        plt.close(fig)
        return output_path
    except ImportError as exc:
        raise RuntimeError(f"Required rendering library unavailable: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Blueprint rendering failed: {exc}") from exc
