#!/usr/bin/env python3
"""
Visualise pfd_layout_realized.json → pfd_layout_realized.svg
Open the SVG in any browser; use browser zoom/scroll to navigate.
"""
import json

# ── Colours ────────────────────────────────────────────────────────────────────
NODE_FILL = {
    "pipe_segment":          "#c8c8c8",
    "equipment_block":       "#9ec4e8",
    "valve:isolation":       "#f5c97f",
    "valve:control":         "#f5956a",
    "instrument:flow_meter": "#8fd4a0",
}
NODE_LABEL = {
    "pipe_segment":          "pipe",
    "equipment_block":       "equip",
    "valve:isolation":       "V-iso",
    "valve:control":         "V-ctl",
    "instrument:flow_meter": "FM",
}
GROUP_STYLE = {
    "rfrag": ("#eef0ff", "#7777cc"),
    "cfrag": ("#fff0f0", "#cc7777"),
    "efrag": ("#f0fff0", "#77aa77"),
}


def _frag_kind(fid):
    return fid.split("_")[0]


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    with open("pfd_layout_realized.json") as f:
        data = json.load(f)

    nodes  = data["nodes"]
    edges  = data["edges"]
    groups = data["groups"]

    # ── Compute canvas bounds ─────────────────────────────────────────────────
    xs, ys = [], []
    for n in nodes:
        xs += [n["position"]["x"], n["position"]["x"] + n["size"]["width"]]
        ys += [n["position"]["y"], n["position"]["y"] + n["size"]["height"]]
    for g in groups:
        bb = g["bounding_box"]
        xs += [bb["x"], bb["x"] + bb["width"]]
        ys += [bb["y"], bb["y"] + bb["height"]]

    PAD = 40
    x0, y0 = min(xs) - PAD, min(ys) - PAD
    vw, vh = max(xs) - min(xs) + 2 * PAD, max(ys) - min(ys) + 2 * PAD

    def tx(x): return x - x0
    def ty(y): return y - y0

    # Display size: scale so width ≤ 1800 px (browser scales the rest)
    SCALE  = min(1.0, 1800 / vw)
    disp_w = round(vw * SCALE)
    disp_h = round(vh * SCALE)

    out = []

    # ── SVG header ────────────────────────────────────────────────────────────
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{disp_w}" height="{disp_h}" '
        f'viewBox="0 0 {vw:.1f} {vh:.1f}">'
    )

    # ── Defs ──────────────────────────────────────────────────────────────────
    out.append("""  <defs>
    <marker id="arr"  markerWidth="7" markerHeight="5" refX="6" refY="2.5" orient="auto">
      <polygon points="0 0, 7 2.5, 0 5" fill="#555"/>
    </marker>
    <marker id="arrs" markerWidth="7" markerHeight="5" refX="6" refY="2.5" orient="auto">
      <polygon points="0 0, 7 2.5, 0 5" fill="#c03030"/>
    </marker>
    <style>text { font-family: monospace; pointer-events: none; }</style>
  </defs>""")

    # ── Group bounding boxes ───────────────────────────────────────────────────
    out.append("  <!-- groups -->")
    for g in sorted(groups, key=lambda g: g["fragment_id"]):
        fid  = g["fragment_id"]
        kind = _frag_kind(fid)
        fill, stroke = GROUP_STYLE.get(kind, ("#f8f8f8", "#888"))
        bb = g["bounding_box"]
        x, y, w, h = tx(bb["x"]), ty(bb["y"]), bb["width"], bb["height"]
        out.append(
            f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="0.7" '
            f'stroke-dasharray="5 3" rx="3"/>'
        )
        out.append(
            f'  <text x="{x+3:.1f}" y="{y+9:.1f}" font-size="6" '
            f'fill="{stroke}" opacity="0.8">{_esc(fid)}</text>'
        )

    # ── Edges ─────────────────────────────────────────────────────────────────
    out.append("  <!-- edges -->")
    for e in edges:
        is_stitch = len(e["path"]) == 4
        pts   = " ".join(f"{tx(p['x']):.1f},{ty(p['y']):.1f}" for p in e["path"])
        col   = "#c03030" if is_stitch else "#444"
        sw    = "1.8" if is_stitch else "1.0"
        dash  = 'stroke-dasharray="7 3"' if is_stitch else ""
        arrow = "arrs" if is_stitch else "arr"
        out.append(
            f'  <polyline points="{pts}" fill="none" stroke="{col}" '
            f'stroke-width="{sw}" {dash} marker-end="url(#{arrow})"/>'
        )

    # ── Nodes ─────────────────────────────────────────────────────────────────
    out.append("  <!-- nodes -->")
    for n in nodes:
        ntype  = n["type"]
        x      = tx(n["position"]["x"])
        y      = ty(n["position"]["y"])
        w, h   = n["size"]["width"], n["size"]["height"]
        fill   = NODE_FILL.get(ntype, "#ddd")
        abbr   = NODE_LABEL.get(ntype, ntype)
        cx, cy = x + w / 2, y + h / 2
        fs     = min(6.5, h * 0.42, w * 0.17)
        gid    = _esc(n["global_id"])
        out.append(
            f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="{fill}" stroke="#333" stroke-width="0.5" rx="2">'
            f'<title>{gid} ({_esc(ntype)})</title></rect>'
        )
        out.append(
            f'  <text x="{cx:.1f}" y="{cy + fs * 0.38:.1f}" '
            f'font-size="{fs:.1f}" text-anchor="middle" fill="#111">'
            f'{_esc(abbr)}</text>'
        )

    # ── Legend ────────────────────────────────────────────────────────────────
    lx, ly, lw, lh = 8, vh - 95, 138, 90
    out.append(
        f'  <rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" '
        f'fill="white" stroke="#999" stroke-width="0.8" rx="4" opacity="0.9"/>'
    )
    out.append(
        f'  <text x="{lx+5}" y="{ly+11}" font-size="7.5" '
        f'font-weight="bold" fill="#222">Legend</text>'
    )
    entries = list(NODE_FILL.items()) + [
        ("stitch edge", "#c03030"),
        ("intra-frag edge", "#444"),
    ]
    for i, (label, col) in enumerate(entries):
        ry = ly + 18 + i * 11
        if "edge" in label:
            out.append(
                f'  <line x1="{lx+5}" y1="{ry+4}" x2="{lx+18}" y2="{ry+4}" '
                f'stroke="{col}" stroke-width="2"/>'
            )
        else:
            out.append(
                f'  <rect x="{lx+5}" y="{ry}" width="13" height="8" '
                f'fill="{col}" stroke="#333" stroke-width="0.5" rx="1"/>'
            )
        out.append(
            f'  <text x="{lx+22}" y="{ry+7}" font-size="6" fill="#222">'
            f'{_esc(label)}</text>'
        )

    out.append("</svg>")

    svg_path = "pfd_layout_realized.svg"
    with open(svg_path, "w") as f:
        f.write("\n".join(out))

    print(f"Wrote {svg_path}")
    print(f"  Canvas : {vw:.0f} × {vh:.0f} mm")
    print(f"  Display: {disp_w} × {disp_h} px")
    print(f"  Open with: open {svg_path}")


if __name__ == "__main__":
    main()
