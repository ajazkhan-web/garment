"""
blueprint.py — 2D Blueprint Preview Renderer
==============================================
Renders a labelled technical drawing (blueprint-style) PNG preview of the
drafted pattern pieces, alongside a measurement table.

All heavy imports (matplotlib, PIL) are done LAZILY inside functions so that
the bot can start and operate normally even if these packages are missing —
the blueprint is simply skipped and only the DXF is sent.

Author: EJAJ KHAN
"""
from __future__ import annotations

import math
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import config
except ImportError:
    class _C:
        OUTPUT_DIR = os.path.join(os.getcwd(), "output")
    config = _C()


def _pt(p):
    """Normalise Point-or-tuple to (x, y)."""
    return (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])


def _piece_bounds(piece):
    xs, ys = [], []
    for p in piece.outline:
        x, y = _pt(p)
        xs.append(x); ys.append(y)
    for seg_pts in piece.outline_curves.values():
        for p in seg_pts:
            x, y = _pt(p)
            xs.append(x); ys.append(y)
    if not xs:
        return (0, 0, 1, 1)
    return (min(xs), min(ys), max(xs), max(ys))


def _smooth_outline_xy(piece):
    """Same curve-expansion logic as the DXF exporter, for plotting."""
    xs, ys = [], []
    n = len(piece.outline)
    for i in range(n):
        x, y = _pt(piece.outline[i])
        xs.append(x); ys.append(y)
        if i in piece.outline_curves:
            for p in piece.outline_curves[i]:
                px, py = _pt(p)
                xs.append(px); ys.append(py)
    if xs:
        xs.append(xs[0]); ys.append(ys[0])
    return xs, ys


def _draw_piece_ax(ax, piece, title_suffix: str = ""):
    """Draw one pattern piece onto a matplotlib Axes."""
    xs, ys = _smooth_outline_xy(piece)
    if not xs:
        ax.set_visible(False)
        return

    # Cut line
    ax.plot(xs, ys, color="#1a1a1a", linewidth=1.6, solid_capstyle="round", zorder=3)

    # Seam line (dashed)
    if piece.seam_line:
        sxs, sys_ = [], []
        for p in piece.seam_line:
            x, y = _pt(p)
            sxs.append(x); sys_.append(y)
        sxs.append(sxs[0]); sys_.append(sys_[0])
        ax.plot(sxs, sys_, color="#4a4a4a", linewidth=0.8, linestyle=(0, (4, 3)), zorder=2)

    # Darts
    for dart in piece.darts:
        s, e, a = dart.get("start"), dart.get("end"), dart.get("apex")
        if s and e and a:
            sx, sy = _pt(s); ex, ey = _pt(e); ax_, ay = _pt(a)
            ax.plot([sx, ax_], [sy, ay], color="#1a1a1a", linewidth=0.9, zorder=3)
            ax.plot([ex, ax_], [ey, ay], color="#1a1a1a", linewidth=0.9, zorder=3)

    # Gather guides
    for gg in piece.gather_guides:
        pts = gg.get("points", [])
        if len(pts) >= 2:
            p1x, p1y = _pt(pts[0]); p2x, p2y = _pt(pts[1])
            ax.plot([p1x, p2x], [p1y, p2y], color="#8a8a8a", linewidth=0.6,
                    linestyle=(0, (2, 2)), zorder=1)

    # Pleat guides
    for pg in piece.pleat_guides:
        pts = pg.get("points", [])
        if len(pts) >= 2:
            p1x, p1y = _pt(pts[0]); p2x, p2y = _pt(pts[1])
            ax.plot([p1x, p2x], [p1y, p2y], color="#666666", linewidth=0.8,
                    linestyle=(0, (5, 2, 1, 2)), zorder=1)

    # Fold lines
    for fl in piece.fold_lines:
        pts = fl.get("points", [])
        if len(pts) >= 2:
            p1x, p1y = _pt(pts[0]); p2x, p2y = _pt(pts[1])
            ax.plot([p1x, p2x], [p1y, p2y], color="#999999", linewidth=0.8,
                    linestyle="-.", zorder=1)

    # Mirror axis
    if piece.mirror_axis:
        s, e = piece.mirror_axis.get("start"), piece.mirror_axis.get("end")
        if s and e:
            sx, sy = _pt(s); ex, ey = _pt(e)
            ax.plot([sx, ex], [sy, ey], color="#aaaaaa", linewidth=0.8, linestyle="-.", zorder=1)

    # Grainline with arrow — import locally to avoid module-level matplotlib dep
    from matplotlib.patches import FancyArrowPatch
    if piece.grainline:
        s, e = piece.grainline.get("start"), piece.grainline.get("end")
        if s and e:
            sx, sy = _pt(s); ex, ey = _pt(e)
            arrow = FancyArrowPatch((sx, sy), (ex, ey), arrowstyle="<->",
                                    mutation_scale=10, color="#2a6fdb", linewidth=1.1, zorder=4)
            ax.add_patch(arrow)

    # Notches
    for notch in piece.notches:
        nx, ny = _pt(notch)
        ax.plot(nx, ny, marker="+", color="#c0392b", markersize=6, markeredgewidth=1.2, zorder=5)

    # Annotations
    for ann in piece.annotations:
        px, py = _pt(ann.get("pos", (0, 0)))
        ax.text(px, py, ann.get("text", ""), fontsize=6.5, color="#1a1a1a",
                ha="center", va="bottom", zorder=6)

    # Piece title
    minx, miny, maxx, maxy = _piece_bounds(piece)
    title = f"{piece.name.upper()}\nCUT {piece.cut_qty}{title_suffix}"
    ax.text((minx + maxx) / 2, maxy + 3, title, fontsize=7.5, fontweight="bold",
            ha="center", va="bottom", color="#111111")

    ax.set_aspect("equal")
    pad = 4
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad + 6)
    ax.axis("off")


def render_blueprint(pieces: list, measurements_table: dict,
                     garment_type: str, size_label: str = "",
                     style_notes: str = "", output_path: Optional[str] = None) -> str:
    """
    Renders a multi-panel blueprint PNG: one subplot per pattern piece,
    plus a measurement table and legend.

    All matplotlib/PIL imports happen HERE (inside the function) so that
    the bot can import blueprint.py at module level without needing
    matplotlib installed — the import only fails if render_blueprint is
    actually called, and callers should catch ImportError.
    """
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend, no display needed
    import matplotlib.pyplot as plt

    n_pieces = len(pieces)
    n_cols = min(3, max(n_pieces, 1))
    n_rows = math.ceil(n_pieces / n_cols) if n_pieces else 1

    fig = plt.figure(figsize=(6 * n_cols + 4, 5.5 * n_rows), dpi=150)
    fig.patch.set_facecolor("white")

    grid_cols = n_cols + 1
    gs = fig.add_gridspec(n_rows, grid_cols, width_ratios=[1] * n_cols + [0.9])

    for i, piece in enumerate(pieces):
        r, c = divmod(i, n_cols)
        ax = fig.add_subplot(gs[r, c])
        _draw_piece_ax(ax, piece)

    # Measurement table + legend panel
    ax_info = fig.add_subplot(gs[:, n_cols])
    ax_info.axis("off")

    title = f"GARMENT MEASUREMENT CHART — {garment_type.upper()}"
    if size_label:
        title += f" (SIZE {size_label})"
    ax_info.text(0, 1.0, title, fontsize=10, fontweight="bold", va="top", transform=ax_info.transAxes)

    y = 0.94
    if measurements_table:
        ax_info.add_patch(plt.Rectangle((0, y - 0.02), 1, 0.03, transform=ax_info.transAxes,
                                        color="#f4d03f", zorder=1))
        ax_info.text(0.02, y - 0.005, "MEASUREMENT", fontsize=7.5, fontweight="bold",
                    va="center", transform=ax_info.transAxes)
        ax_info.text(0.78, y - 0.005, "VALUE", fontsize=7.5, fontweight="bold",
                    va="center", transform=ax_info.transAxes)
        y -= 0.045
        for label, value in measurements_table.items():
            if y < 0.35:
                break
            ax_info.text(0.02, y, str(label), fontsize=7, va="center", transform=ax_info.transAxes)
            ax_info.text(0.8, y, str(value), fontsize=7, va="center", transform=ax_info.transAxes)
            y -= 0.028
    else:
        ax_info.text(0, y, "(no raw measurement table parsed)", fontsize=7.5,
                    style="italic", color="#888888", va="top", transform=ax_info.transAxes)
        y -= 0.06

    y -= 0.03
    ax_info.text(0, y, "STANDARD MARKINGS", fontsize=8.5, fontweight="bold",
                va="top", transform=ax_info.transAxes)
    y -= 0.04
    legend_items = [
        ("—— ——", "CUT LINE"),
        ("- - -", "SEAM LINE"),
        ("<-->", "GRAINLINE"),
        ("+", "NOTCH (MATCH POINT)"),
        ("-·-·-", "FOLD LINE"),
        ("· · ·", "GATHER / EASE GUIDE"),
    ]
    for symbol, label in legend_items:
        ax_info.text(0.0, y, symbol, fontsize=7.5, family="monospace",
                    va="center", transform=ax_info.transAxes)
        ax_info.text(0.22, y, label, fontsize=7.5, va="center", transform=ax_info.transAxes)
        y -= 0.032

    y -= 0.03
    ax_info.text(0, y, "PATTERN PIECES & CUT QUANTITIES", fontsize=8.5, fontweight="bold",
                va="top", transform=ax_info.transAxes)
    y -= 0.04
    for piece in pieces:
        if y < 0.03:
            break
        ax_info.text(0, y, f"• {piece.name} — CUT {piece.cut_qty}", fontsize=7,
                    va="center", transform=ax_info.transAxes)
        y -= 0.028

    if style_notes:
        y -= 0.03
        ax_info.text(0, y, "STYLE NOTES:", fontsize=8, fontweight="bold",
                    va="top", transform=ax_info.transAxes)
        y -= 0.035
        ax_info.text(0, y, style_notes, fontsize=6.8, va="top", wrap=True,
                    transform=ax_info.transAxes)

    fig.suptitle(
        "DRAFTING VISUALISATION ONLY — Preview render, not a certified production pattern",
        fontsize=8, color="#888888", y=0.005,
    )

    plt.tight_layout(rect=[0, 0.01, 1, 0.98])

    if output_path is None:
        output_path = os.path.join(config.OUTPUT_DIR, f"{garment_type}_blueprint.png")
    fig.savefig(output_path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    import generator as gen
    m = gen.Measurements(bust=92, waist=72, hip=96, shoulder_width=38,
                         back_length=40, sleeve_length=58, armhole_depth=22,
                         skirt_length=60, dress_length=100)
    style = gen.StyleDetails(silhouette="wrap", has_cowl=True, has_gathers=True,
                             gather_locations=["front neckline"], asymmetric_hem=True,
                             size_label="S")
    pieces, engine = gen.draft_pieces(m, "dress", style=style)
    table = {"Bust": "92 cm", "Waist": "72 cm", "Hip": "96 cm", "Sleeve Length": "58 cm"}
    path = render_blueprint(pieces, table, "dress", size_label="S",
                            style_notes="Cowl drape front, asymmetric hem, gathered neckline.",
                            output_path="test_blueprint.png")
    print(f"Blueprint saved to: {path}")
