#!/usr/bin/env python3
"""
Stage 10 — P&ID SVG renderer (v3 — industrial-density)
Inputs:  pid_layout_realized.json, pid_interaction_hooks.json
Output:  pid.svg

Additions over v2:
  - ISA-standard title block (lower-right)
  - Notes section (right margin)
  - Double-line border with zone labels
  - Flow direction arrows on process edges
  - Pipe segment symbol suppressed (edges draw the pipe)
  - Improved font sizing (min 4pt, max 6pt)
"""
import argparse
import json
import os
import random
import re

# ── Constants ────────────────────────────────────────────────────────────────
NODE_FILL = {
    "pipe_segment":             "#ffffff",
    "equipment_block":          "#ffffff",
    "valve:isolation":          "#ffffff",
    "valve:control":            "#ffffff",
    "valve:drain":              "#ffffff",
    "instrument:flow_meter":    "#ffffff",
    "actuator:pneumatic":       "#111111",
    "instrument:controller":    "#ffffff",
    "instrument:transmitter":   "#ffffff",
    "instrument:pressure":      "#ffffff",
    "instrument:temperature":   "#ffffff",
    "tank":                     "#ffffff",
    "pump":                     "#ffffff",
    "inlet_outlet":             "#ffffff",
    "motor:driver":             "#111111",
    "instrument:level":         "#ffffff",
}
NODE_STROKE = {
    "pipe_segment":             "#111111",
    "equipment_block":          "#111111",
    "valve:isolation":          "#111111",
    "valve:control":            "#111111",
    "valve:drain":              "#111111",
    "instrument:flow_meter":    "#111111",
    "actuator:pneumatic":       "#111111",
    "instrument:controller":    "#111111",
    "instrument:transmitter":   "#111111",
    "instrument:pressure":      "#111111",
    "instrument:temperature":   "#111111",
    "tank":                     "#111111",
    "pump":                     "#111111",
    "inlet_outlet":             "#111111",
    "motor:driver":             "#111111",
    "instrument:level":         "#111111",
}

# Font size constraints
MIN_FONT = 4.0   # pt
MAX_FONT = 6.0   # pt

# Industrial notes (typical P&ID general notes)
GENERAL_NOTES = [
    "ALL PIPING AND VALVES SHALL BE SIZED PER PROCESS REQUIREMENTS.",
    "TEMPERATURE AND PRESSURE ARE AT NORMAL OPERATING CONDITIONS.",
    "ALL INSTRUMENTS SHALL CONFORM TO ISA 5.1 STANDARDS.",
    "ELEVATIONS ARE TO CENTER-LINE OF PIPE UNLESS NOTED.",
    "ALL CONTROL VALVES FAIL-SAFE AS SHOWN.",
    "DRAIN AND VENT VALVES SHALL BE PROVIDED WHERE REQUIRED.",
    "ALL PIPE CONNECTIONS SHALL BE FLANGED UNLESS NOTED.",
    "RELIEF VALVES SHALL BE SET PER PROCESS DATA SHEETS.",
    "LINE DESIGNATIONS PER PIPING SPEC INDEX.",
    "ALL WELDING SHALL CONFORM TO ASME B31.3.",
    "INSTRUMENT AIR SUPPLY AT 100 PSIG MINIMUM.",
    "SEE P&ID LEGEND FOR SYMBOL DEFINITIONS.",
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _esc(s):
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def load_symbol_library(path):
    """Load pid_symbol_library.json; return dict or None on missing/malformed file."""
    try:
        with open(path) as f:
            lib = json.load(f)
        if "symbols" not in lib or "resolution_rules" not in lib:
            return None
        return lib
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _resolve_symbol(ntype, semantics, resolution_rules):
    """First-match walk of resolution_rules; return symbol key or None."""
    for rule in resolution_rules:
        match = rule["match"]
        ok = True
        for k, v in match.items():
            if k == "ntype":
                if ntype != v:
                    ok = False
                    break
            elif k.startswith("semantics."):
                sem_key = k[len("semantics."):]
                if semantics.get(sem_key) != v:
                    ok = False
                    break
        if ok:
            return rule["symbol"]
    return None


def _sym_iso(symbol_key, sym_def, w, h, semantics, fill, stroke):
    """Render one ISO symbol from library definition into a list of SVG element strings."""
    def col(c):
        if c == "$fill":   return fill
        if c == "$stroke": return stroke
        return c

    elems = []
    for el in sym_def["elements"]:
        t  = el["type"]
        sw = el.get("stroke_width", 1.0)

        if t == "line":
            x1 = el["x1"] * w
            y1 = el["y1"] * h
            x2 = el["x2"] * w
            y2 = el["y2"] * h
            sc = col(el.get("stroke", "$stroke"))
            elems.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="{sc}" stroke-width="{sw}"/>'
            )

        elif t == "polygon":
            pts = " ".join(f"{p[0]*w:.2f},{p[1]*h:.2f}" for p in el["points"])
            fc  = col(el.get("fill",   "$fill"))
            sc  = col(el.get("stroke", "$stroke"))
            elems.append(
                f'<polygon points="{pts}" fill="{fc}" stroke="{sc}" stroke-width="{sw}"/>'
            )

        elif t == "circle":
            cx_a = el["cx"] * w
            cy_a = el["cy"] * h
            r_a  = el["r_norm"] * min(w, h)
            fc   = col(el.get("fill",   "$fill"))
            sc   = col(el.get("stroke", "$stroke"))
            elems.append(
                f'<circle cx="{cx_a:.2f}" cy="{cy_a:.2f}" r="{r_a:.2f}" '
                f'fill="{fc}" stroke="{sc}" stroke-width="{sw}"/>'
            )
            if el.get("bisect"):
                bsw = el.get("bisect_stroke_width", round(sw * 0.7, 2))
                x1_b = cx_a - r_a
                x2_b = cx_a + r_a
                elems.append(
                    f'<line x1="{x1_b:.2f}" y1="{cy_a:.2f}" x2="{x2_b:.2f}" y2="{cy_a:.2f}" '
                    f'stroke="{sc}" stroke-width="{bsw}"/>'
                )

        elif t == "rect":
            rx_a  = el["x"] * w
            ry_a  = el["y"] * h
            rw_a  = el["w"] * w
            rh_a  = el["h"] * h
            fc    = col(el.get("fill",   "$fill"))
            sc    = col(el.get("stroke", "$stroke"))
            rx_c  = el.get("rx_norm", 0) * w
            ry_c  = el.get("ry_norm", 0) * h
            attrs = (
                f'x="{rx_a:.2f}" y="{ry_a:.2f}" '
                f'width="{rw_a:.2f}" height="{rh_a:.2f}" '
                f'fill="{fc}" stroke="{sc}" stroke-width="{sw}"'
            )
            if rx_c or ry_c:
                attrs += f' rx="{rx_c:.2f}" ry="{ry_c:.2f}"'
            elems.append(f'<rect {attrs}/>')

    return elems


_EQUIP_SYM_CYCLE = ["vessel", "heat_exchanger", "column_tower", "pump_centrifugal", "compressor"]

def _equip_sym_key(tag: str) -> str:
    """Return symbol key for an equipment_block node based on its tag number."""
    m = re.search(r'\d+', tag or "")
    n = int(m.group()) if m else 0
    return _EQUIP_SYM_CYCLE[n % len(_EQUIP_SYM_CYCLE)]


def _sym(ntype, w, h):
    """Return list of SVG element strings for node symbol in local (0,0)-(w,h) frame."""
    fill   = NODE_FILL.get(ntype,   "#dddddd")
    stroke = NODE_STROKE.get(ntype, "#444444")

    if ntype == "pipe_segment":
        # Suppressed — process edges draw the pipe
        return []

    if ntype == "equipment_block":
        return [
            f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" rx="5" ry="5"/>',
        ]

    if ntype == "valve:isolation":
        cx, cy = w / 2, h / 2
        return [
            f'<polygon points="0,0 {cx:.2f},{cy:.2f} 0,{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
            f'<polygon points="{w:.2f},0 {cx:.2f},{cy:.2f} {w:.2f},{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
        ]

    if ntype == "valve:control":
        cx, cy = w / 2, h / 2
        r = min(w, h) * 0.13
        return [
            f'<polygon points="0,0 {cx:.2f},{cy:.2f} 0,{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
            f'<polygon points="{w:.2f},0 {cx:.2f},{cy:.2f} {w:.2f},{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
            f'<circle cx="{cx:.2f}" cy="{h * 0.18:.2f}" r="{r:.2f}" '
            f'fill="{stroke}"/>',
        ]

    if ntype == "valve:drain":
        cx = w / 2
        return [
            f'<polygon points="{w*0.15:.2f},0 {w*0.85:.2f},0 {cx:.2f},{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
        ]

    if ntype == "instrument:flow_meter":
        cx, cy = w / 2, h / 2
        return [
            f'<polygon points="{cx:.2f},0 {w:.2f},{cy:.2f} {cx:.2f},{h:.2f} 0,{cy:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
        ]

    if ntype == "actuator:pneumatic":
        return [
            f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1" rx="4" ry="4"/>',
            f'<line x1="0" y1="{h/2:.2f}" x2="{w:.2f}" y2="{h/2:.2f}" '
            f'stroke="{stroke}" stroke-width="0.8"/>',
        ]

    if ntype in ("instrument:controller", "instrument:transmitter",
                 "instrument:pressure", "instrument:temperature"):
        r = min(w, h) / 2 - 1.0
        cx, cy = w / 2, h / 2
        return [
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>',
        ]

    if ntype == "tank":
        rx = w * 0.12
        ry = h * 0.5
        return [
            f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" '
            f'rx="{rx:.2f}" ry="{ry:.2f}"/>',
        ]

    if ntype == "pump":
        cx, cy = w / 2, h / 2
        r = min(w, h) * 0.42
        return [
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
            f'<polygon points="{cx + r * 0.5:.2f},{cy - r:.2f} '
            f'{cx + r * 1.3:.2f},{cy:.2f} {cx + r * 0.5:.2f},{cy + r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
        ]

    if ntype == "inlet_outlet":
        cx, cy = w / 2, h / 2
        return [
            f'<polygon points="{cx:.2f},0 {w:.2f},{cy:.2f} '
            f'{cx:.2f},{h:.2f} 0,{cy:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
        ]

    if ntype == "motor:driver":
        cx, cy = w / 2, h / 2
        r = min(w, h) * 0.42
        fs = r * 0.9
        return [
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
            f'<text x="{cx:.2f}" y="{cy + fs * 0.35:.2f}" '
            f'font-size="{fs:.2f}" text-anchor="middle" fill="{stroke}">M</text>',
        ]

    if ntype == "instrument:level":
        r = min(w, h) / 2 - 1.0
        cx, cy = w / 2, h / 2
        return [
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>',
            f'<line x1="{cx - r:.2f}" y1="{cy:.2f}" x2="{cx + r:.2f}" y2="{cy:.2f}" '
            f'stroke="{stroke}" stroke-width="0.7"/>',
        ]

    # fallback rectangle
    return [
        f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" '
        f'fill="#dddddd" stroke="#444" stroke-width="0.8" rx="2"/>',
    ]


def _node_svg(nd, css_classes, tooltip_text, x0, y0):
    """Return list of SVG element strings for one node."""
    gid   = nd["global_id"]
    ntype = nd["type"]
    x     = nd["position"]["x"] - x0
    y     = nd["position"]["y"] - y0
    w     = nd["size"]["width"]
    h     = nd["size"]["height"]
    tag   = nd.get("tag", "")
    fid   = nd.get("fragment_id", "")

    # Derive loop_id for data attribute
    loop_id = next(
        (c[5:] for c in css_classes if c.startswith("loop-") and c != "loop-member"),
        ""
    )
    css = " ".join(["node-g"] + css_classes)

    out = []
    out.append(
        f'  <g transform="translate({x:.2f},{y:.2f})" id="{_esc(gid)}" '
        f'class="{_esc(css)}" '
        f'data-loop-id="{_esc(loop_id)}" '
        f'data-fragment-id="{_esc(fid)}">'
    )
    out.append(f'    <title>{_esc(tooltip_text)}</title>')

    # Pipe segments: only render the line-ID tag label above the pipe centerline
    if ntype == "pipe_segment":
        if tag:
            fs_pipe = 4.5
            # Label sits just above the horizontal centerline of the node
            out.append(
                f'    <text x="{w/2:.2f}" y="{max(h/2 - 1.5, 3.0):.2f}" '
                f'font-size="{fs_pipe:.2f}" text-anchor="middle" fill="#444">'
                f'{_esc(tag)}</text>'
            )
        out.append('  </g>')
        return out

    # ISA symbol — prefer ISO library; fall back to built-in _sym()
    sym_elems = None
    if _SYM_LIB is not None:
        if ntype == "equipment_block":
            # Cycle through 5 distinct equipment symbols based on tag number
            sym_key = _equip_sym_key(tag)
        else:
            sym_key = _resolve_symbol(ntype, nd.get("semantics", {}),
                                      _SYM_LIB["resolution_rules"])
        if sym_key and sym_key in _SYM_LIB["symbols"]:
            fill   = NODE_FILL.get(ntype, "#dddddd")
            stroke = NODE_STROKE.get(ntype, "#444444")
            sym_elems = _sym_iso(sym_key, _SYM_LIB["symbols"][sym_key],
                                 w, h, nd.get("semantics", {}), fill, stroke)
    if sym_elems is None:
        sym_elems = _sym(ntype, w, h)
    for elem in sym_elems:
        out.append(f'    {elem}')

    # Labels with improved font sizing
    sk = NODE_STROKE.get(ntype, "#333")
    if ntype in ("instrument:controller", "instrument:transmitter",
                 "instrument:pressure", "instrument:temperature",
                 "instrument:level"):
        if ntype == "instrument:pressure":
            inner = "PI"
        elif ntype == "instrument:temperature":
            inner = "TI"
        elif ntype == "instrument:level":
            inner = "LI"
        else:
            inner = tag.split("-")[0] if tag else ntype[:2].upper()
        fs_inner = min(MAX_FONT, max(MIN_FONT, h * 0.35))
        cx, cy = w / 2, h / 2
        out.append(
            f'    <text x="{cx:.2f}" y="{cy + fs_inner * 0.38:.2f}" '
            f'font-size="{fs_inner:.2f}" text-anchor="middle" fill="{sk}">'
            f'{_esc(inner)}</text>'
        )
        # full tag below the circle
        fs_tag = min(MAX_FONT, max(MIN_FONT, h * 0.22))
        out.append(
            f'    <text x="{cx:.2f}" y="{h + fs_tag + 1.5:.2f}" '
            f'font-size="{fs_tag:.2f}" text-anchor="middle" fill="#222">'
            f'{_esc(tag)}</text>'
        )
    elif ntype in ("equipment_block", "tank"):
        fs_tag = min(MAX_FONT, max(MIN_FONT, min(h * 0.15, w * 0.11)))
        out.append(
            f'    <text x="{w/2:.2f}" y="{h/2 + fs_tag * 0.38:.2f}" '
            f'font-size="{fs_tag:.2f}" text-anchor="middle" '
            f'fill="{sk}" font-weight="bold">{_esc(tag)}</text>'
        )
    else:
        # Default: tag below node
        fs_tag = min(MAX_FONT, max(MIN_FONT, min(h * 0.22, w * 0.18)))
        out.append(
            f'    <text x="{w/2:.2f}" y="{h + fs_tag + 1.5:.2f}" '
            f'font-size="{fs_tag:.2f}" text-anchor="middle" fill="#222">'
            f'{_esc(tag)}</text>'
        )

    out.append('  </g>')
    return out


def _edge_svg(e, x0, y0):
    """Return SVG string for one edge (may include a label line)."""
    kind        = e.get("kind", "process")
    signal_type = e.get("signal_type", kind)
    line_id     = e.get("line_id")
    pts         = e["path"]

    # Style: thick for process pipes with flow arrows, thin dashed for instrument
    if kind == "process":
        col, sw, dash, marker = "#111111", "2.5", "", "arrow"
    elif signal_type == "signal":
        col, sw, dash, marker = "#444444", "0.9", "6 3", None
    elif signal_type == "sense":
        col, sw, dash, marker = "#444444", "0.8", "3 2", None
    elif signal_type == "mechanical":
        col, sw, dash, marker = "#222222", "1.4", "",    None
    elif signal_type == "branch":
        col, sw, dash, marker = "#222222", "1.6", "",    None
    else:
        col, sw, dash, marker = "#555555", "1.0", "",    None

    pts_str    = " ".join(f"{p['x'] - x0:.2f},{p['y'] - y0:.2f}" for p in pts)
    dash_attr  = f' stroke-dasharray="{dash}"' if dash else ""
    marker_end = f' marker-end="url(#{marker})"' if marker else ""

    lines = [
        f'  <polyline points="{pts_str}" fill="none" stroke="{col}" '
        f'stroke-width="{sw}"{dash_attr}{marker_end}/>'
    ]

    # Line-ID label at midpoint of solid process edges
    if kind == "process" and line_id:
        n = len(pts)
        if n >= 2:
            mid = max(1, n // 2)
            p0, p1 = pts[mid - 1], pts[mid] if mid < n else pts[-1]
        else:
            p0 = p1 = pts[0]
        mx = (p0["x"] + p1["x"]) / 2 - x0
        my = (p0["y"] + p1["y"]) / 2 - y0
        lines.append(
            f'  <text x="{mx:.2f}" y="{my - 2.5:.2f}" font-size="4.5" '
            f'text-anchor="middle" fill="#333">{_esc(line_id)}</text>'
        )

    return "\n".join(lines)


# ── Title Block (ISA standard) ───────────────────────────────────────────────

def _title_block_svg(vw, vh, metadata):
    """Return SVG lines for ISA-standard title block in lower-right corner."""
    tb_w, tb_h = 250, 120
    tx = vw - tb_w - 15
    ty = vh - tb_h - 15
    out = []

    # Outer frame
    out.append(
        f'  <rect x="{tx:.1f}" y="{ty:.1f}" width="{tb_w}" height="{tb_h}" '
        f'fill="#f5f5f5" stroke="#111111" stroke-width="1.5"/>'
    )

    # Horizontal dividers
    row_h = tb_h / 5
    for i in range(1, 5):
        y = ty + i * row_h
        sw = "1.0" if i == 2 else "0.5"
        out.append(
            f'  <line x1="{tx:.1f}" y1="{y:.1f}" x2="{tx + tb_w:.1f}" y2="{y:.1f}" '
            f'stroke="#111111" stroke-width="{sw}"/>'
        )

    # Vertical divider (splits bottom 3 rows into 2 columns)
    mid_x = tx + tb_w / 2
    out.append(
        f'  <line x1="{mid_x:.1f}" y1="{ty + 2 * row_h:.1f}" '
        f'x2="{mid_x:.1f}" y2="{ty + tb_h:.1f}" '
        f'stroke="#111111" stroke-width="0.5"/>'
    )

    fs_title = 7.0
    fs_body = 5.0
    fs_label = 4.0
    pad = 5

    # Derive metadata from source filename
    source = metadata.get("source_file", "")
    basename = os.path.splitext(os.path.basename(source))[0] if source else "UNKNOWN"
    project_name = f"PROJECT-{basename.upper()}"
    drawing_name = f"P&ID-{basename.upper()}"

    # Row 1: Company / Title
    out.append(
        f'  <text x="{tx + tb_w/2:.1f}" y="{ty + row_h * 0.65:.1f}" '
        f'font-size="{fs_title}" text-anchor="middle" fill="#111" '
        f'font-weight="bold">SYNTHETIC PROCESS &amp; INSTRUMENTATION DIAGRAM</text>'
    )

    # Row 2: Drawing name
    out.append(
        f'  <text x="{tx + tb_w/2:.1f}" y="{ty + row_h * 1.65:.1f}" '
        f'font-size="{fs_body}" text-anchor="middle" fill="#222">'
        f'{_esc(drawing_name)}</text>'
    )

    # Row 3-5: detail fields
    fields = [
        (f"PROJECT: {project_name}", f"CLIENT: SAMPLE"),
        (f"DWG NO: {basename}-PID-001", f"SCALE: NTS"),
        (f"REV: A", f"SHEET: 1 OF 1"),
    ]
    for i, (left, right) in enumerate(fields):
        ry = ty + (2 + i) * row_h
        # Labels
        out.append(
            f'  <text x="{tx + pad:.1f}" y="{ry + row_h * 0.45:.1f}" '
            f'font-size="{fs_label}" fill="#444">{_esc(left)}</text>'
        )
        out.append(
            f'  <text x="{mid_x + pad:.1f}" y="{ry + row_h * 0.45:.1f}" '
            f'font-size="{fs_label}" fill="#444">{_esc(right)}</text>'
        )

    return out


# ── Notes Section ────────────────────────────────────────────────────────────

def _notes_svg(vw, vh, tb_h=120):
    """Return SVG lines for notes section above title block on right margin."""
    notes_w = 250
    notes_x = vw - notes_w - 15
    notes_y = 25
    notes_h = vh - tb_h - 60  # fill space above title block

    out = []
    # Border
    out.append(
        f'  <rect x="{notes_x:.1f}" y="{notes_y:.1f}" '
        f'width="{notes_w}" height="{notes_h:.1f}" '
        f'fill="none" stroke="#888888" stroke-width="0.5"/>'
    )

    # Title
    out.append(
        f'  <text x="{notes_x + 5:.1f}" y="{notes_y + 10:.1f}" '
        f'font-size="6" font-weight="bold" fill="#222">GENERAL NOTES</text>'
    )

    # Notes text
    line_h = 8.5
    max_lines = int((notes_h - 20) / line_h)
    notes_to_show = GENERAL_NOTES[:min(len(GENERAL_NOTES), max_lines)]

    for i, note in enumerate(notes_to_show):
        ny = notes_y + 22 + i * line_h
        if ny + line_h > notes_y + notes_h:
            break
        # Truncate long notes
        display = note if len(note) <= 50 else note[:47] + "..."
        out.append(
            f'  <text x="{notes_x + 5:.1f}" y="{ny:.1f}" '
            f'font-size="4.5" fill="#333">{i+1}. {_esc(display)}</text>'
        )

    return out


# ── Enhanced Border ──────────────────────────────────────────────────────────

def _border_svg(vw, vh):
    """Return SVG lines for double-line border with zone labels and fold marks."""
    out = []

    # Outer border
    _BRD_OUTER = 8
    out.append(
        f'  <rect x="{_BRD_OUTER}" y="{_BRD_OUTER}" '
        f'width="{vw - 2*_BRD_OUTER:.1f}" height="{vh - 2*_BRD_OUTER:.1f}" '
        f'fill="none" stroke="#111111" stroke-width="2"/>'
    )

    # Inner border (5mm gap)
    _BRD_INNER = 13
    out.append(
        f'  <rect x="{_BRD_INNER}" y="{_BRD_INNER}" '
        f'width="{vw - 2*_BRD_INNER:.1f}" height="{vh - 2*_BRD_INNER:.1f}" '
        f'fill="none" stroke="#111111" stroke-width="0.8"/>'
    )

    # Zone labels across top (A, B, C, ...)
    n_cols = max(1, int(vw / 400))
    col_w = (vw - 2 * _BRD_INNER) / n_cols
    for i in range(n_cols):
        cx = _BRD_INNER + (i + 0.5) * col_w
        label = chr(65 + i)  # A, B, C, ...
        # Top label
        out.append(
            f'  <text x="{cx:.1f}" y="{_BRD_INNER - 1:.1f}" '
            f'font-size="5" text-anchor="middle" fill="#555">{label}</text>'
        )
        # Bottom label
        out.append(
            f'  <text x="{cx:.1f}" y="{vh - _BRD_OUTER + 5:.1f}" '
            f'font-size="5" text-anchor="middle" fill="#555">{label}</text>'
        )
        # Tick marks
        if i > 0:
            tick_x = _BRD_INNER + i * col_w
            out.append(
                f'  <line x1="{tick_x:.1f}" y1="{_BRD_OUTER:.1f}" '
                f'x2="{tick_x:.1f}" y2="{_BRD_INNER:.1f}" '
                f'stroke="#888" stroke-width="0.5"/>'
            )
            out.append(
                f'  <line x1="{tick_x:.1f}" y1="{vh - _BRD_INNER:.1f}" '
                f'x2="{tick_x:.1f}" y2="{vh - _BRD_OUTER:.1f}" '
                f'stroke="#888" stroke-width="0.5"/>'
            )

    # Zone labels down side (1, 2, 3, ...)
    n_rows = max(1, int(vh / 300))
    row_h = (vh - 2 * _BRD_INNER) / n_rows
    for i in range(n_rows):
        cy = _BRD_INNER + (i + 0.5) * row_h
        label = str(i + 1)
        # Left label
        out.append(
            f'  <text x="{_BRD_INNER - 2:.1f}" y="{cy + 2:.1f}" '
            f'font-size="5" text-anchor="end" fill="#555">{label}</text>'
        )
        # Right label
        out.append(
            f'  <text x="{vw - _BRD_INNER + 3:.1f}" y="{cy + 2:.1f}" '
            f'font-size="5" text-anchor="start" fill="#555">{label}</text>'
        )
        # Tick marks
        if i > 0:
            tick_y = _BRD_INNER + i * row_h
            out.append(
                f'  <line x1="{_BRD_OUTER:.1f}" y1="{tick_y:.1f}" '
                f'x2="{_BRD_INNER:.1f}" y2="{tick_y:.1f}" '
                f'stroke="#888" stroke-width="0.5"/>'
            )
            out.append(
                f'  <line x1="{vw - _BRD_INNER:.1f}" y1="{tick_y:.1f}" '
                f'x2="{vw - _BRD_OUTER:.1f}" y2="{tick_y:.1f}" '
                f'stroke="#888" stroke-width="0.5"/>'
            )

    # Fold marks at quarter points
    for frac in (0.25, 0.5, 0.75):
        fx = vw * frac
        fy = vh * frac
        # Top/bottom fold marks
        out.append(
            f'  <line x1="{fx:.1f}" y1="0" x2="{fx:.1f}" y2="4" '
            f'stroke="#999" stroke-width="0.3"/>'
        )
        out.append(
            f'  <line x1="{fx:.1f}" y1="{vh - 4:.1f}" x2="{fx:.1f}" y2="{vh:.1f}" '
            f'stroke="#999" stroke-width="0.3"/>'
        )
        # Left/right fold marks
        out.append(
            f'  <line x1="0" y1="{fy:.1f}" x2="4" y2="{fy:.1f}" '
            f'stroke="#999" stroke-width="0.3"/>'
        )
        out.append(
            f'  <line x1="{vw - 4:.1f}" y1="{fy:.1f}" x2="{vw:.1f}" y2="{fy:.1f}" '
            f'stroke="#999" stroke-width="0.3"/>'
        )

    return out


# ── Module-level ISO symbol library (loaded once in main) ────────────────────
_SYM_LIB = None


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Stage 10 — P&ID SVG renderer")
    ap.add_argument("--input-layout", default="pid_layout_realized.json")
    ap.add_argument("--input-hooks", default="pid_interaction_hooks.json")
    ap.add_argument("--input-symbols", default="pid_symbol_library.json")
    ap.add_argument("--output", default="pid.svg")
    args = ap.parse_args()

    global _SYM_LIB
    _SYM_LIB = load_symbol_library(args.input_symbols)
    if _SYM_LIB is None:
        print(f"  [warn] {args.input_symbols} not found — using built-in _sym()")
    else:
        print(f"  [info] ISO symbol library: {len(_SYM_LIB['symbols'])} symbols")

    with open(args.input_layout) as f:
        layout = json.load(f)
    with open(args.input_hooks) as f:
        hooks = json.load(f)

    nodes  = layout["nodes"]
    edges  = layout["edges"]
    groups = layout["groups"]
    metadata = layout.get("metadata", {})

    # ── Build tag → loop_id map from loop_hooks ──────────────────────────────
    tag_to_loop: dict[str, str] = {}
    for lh in hooks.get("loop_hooks", []):
        loop_id = lh["loop_id"]
        for tag in lh.get("members", {}).values():
            tag_to_loop[tag] = loop_id

    # Build global_id → loop_id (via tag)
    gid_to_loop: dict[str, str] = {}
    for nd in nodes:
        tag = nd.get("tag", "")
        if tag and tag in tag_to_loop:
            gid_to_loop[nd["global_id"]] = tag_to_loop[tag]

    # ── Canvas bounds ────────────────────────────────────────────────────────
    xs: list[float] = []
    ys: list[float] = []
    for n in nodes:
        xs += [n["position"]["x"], n["position"]["x"] + n["size"]["width"]]
        ys += [n["position"]["y"], n["position"]["y"] + n["size"]["height"]]
    for g in groups:
        bb = g["bounding_box"]
        xs += [bb["x"], bb["x"] + bb["w"]]
        ys += [bb["y"], bb["y"] + bb["h"]]

    PAD = 40
    x0 = min(xs) - PAD
    y0 = min(ys) - PAD
    # Add extra right margin for notes/title block
    content_w = max(xs) - min(xs) + 2 * PAD
    content_h = max(ys) - min(ys) + 2 * PAD
    vw = content_w + 270   # 270mm right margin for notes + title block
    vh = max(content_h, 300)  # minimum height for title block

    SCALE  = min(1.0, 1800 / vw, 1200 / vh)
    disp_w = round(vw * SCALE)
    disp_h = round(vh * SCALE)

    out: list[str] = []

    # ── SVG header ───────────────────────────────────────────────────────────
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{disp_w}" height="{disp_h}" '
        f'viewBox="0 0 {vw:.1f} {vh:.1f}">'
    )

    # ── Defs + CSS (including arrow marker) ──────────────────────────────────
    out.append("""\
  <defs>
    <marker id="arrow" viewBox="0 0 6 4" refX="5" refY="2"
            markerWidth="6" markerHeight="4" orient="auto">
      <path d="M0,0 L6,2 L0,4 Z" fill="#111"/>
    </marker>
    <style>
      text { font-family: monospace; pointer-events: none; }
      .loop-group { transition: opacity 0.15s; }
      .loop-group:hover { opacity: 0.85 !important; }
      .node-g { cursor: pointer; }
      .node-g:hover rect,
      .node-g:hover circle,
      .node-g:hover polygon,
      .node-g:hover path { filter: brightness(0.88); }
    </style>
  </defs>""")

    # ── Drawing background ───────────────────────────────────────────────────
    out.append(f'  <rect x="0" y="0" width="{vw:.1f}" height="{vh:.1f}" fill="#c8c8c8"/>')
    # White drawing area (inside border)
    out.append(
        f'  <rect x="8" y="8" '
        f'width="{vw - 16:.1f}" height="{vh - 16:.1f}" '
        f'fill="#f5f5f5"/>'
    )

    # ── Layer 1: Border frame with zone labels ───────────────────────────────
    out.extend(_border_svg(vw, vh))

    # ── Layer 2: Notes section (right margin, above title block) ─────────────
    out.append("  <!-- notes section -->")
    out.extend(_notes_svg(vw, vh))

    # ── Layer 3: Title block (lower-right) ───────────────────────────────────
    out.append("  <!-- title block -->")
    out.extend(_title_block_svg(vw, vh, metadata))

    # ── Layer 4: process edges (pe_*) ────────────────────────────────────────
    out.append("  <!-- process edges -->")
    for e in edges:
        if e["edge_id"].startswith("pe_"):
            out.append(_edge_svg(e, x0, y0))

    # ── Layer 5: expansion edges (xe_*) ──────────────────────────────────────
    out.append("  <!-- expansion edges -->")
    for e in edges:
        if e["edge_id"].startswith("xe_"):
            out.append(_edge_svg(e, x0, y0))

    # ── Layer 6: nodes (top) ─────────────────────────────────────────────────
    out.append("  <!-- nodes -->")
    for nd in nodes:
        gid   = nd["global_id"]
        ntype = nd["type"]
        tag   = nd.get("tag", "")
        fid   = nd.get("fragment_id", "")
        sem   = nd.get("semantics", {})

        # CSS classes
        css_classes: list[str] = []
        loop_id = gid_to_loop.get(gid)
        if loop_id:
            css_classes.append("loop-member")
            css_classes.append(f"loop-{loop_id}")
        fkind = fid.split("_")[0] if "_" in fid else fid
        if fkind:
            css_classes.append(f"fragment-{fkind}")

        # Tooltip: tag + all semantics fields
        lines = [f"{tag} ({ntype})"]
        for k, v in sem.items():
            if v is not None:
                lines.append(f"{k}: {v}")
        tooltip_text = "\n".join(lines)

        out.extend(_node_svg(nd, css_classes, tooltip_text, x0, y0))

    out.append("</svg>")

    svg_path = args.output
    with open(svg_path, "w") as f:
        f.write("\n".join(out))

    n_proc = sum(1 for e in edges if e["edge_id"].startswith("pe_"))
    n_exp  = sum(1 for e in edges if e["edge_id"].startswith("xe_"))
    n_loop = sum(1 for g in groups if g.get("group_type") == "instrument_loop")
    n_frag = len(groups) - n_loop

    print(f"Wrote {svg_path}")
    print(f"  Canvas:  {vw:.0f} × {vh:.0f} mm")
    print(f"  Display: {disp_w} × {disp_h} px")
    print(f"  Nodes:   {len(nodes)}   Edges: {len(edges)} "
          f"({n_proc} process + {n_exp} expansion)   Groups: {len(groups)} "
          f"({n_frag} frag + {n_loop} loop)")
    print(f"  Open with: open {svg_path}")


if __name__ == "__main__":
    main()
