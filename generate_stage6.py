#!/usr/bin/env python3
"""
Stage 6 – PFD Layout Realization (spine layout v3 — dense packing)
Input:  pfd_global_graph.json  [, fragments.json]
Output: pfd_layout_realized.json

Layout strategy: dense spine + row packing (landscape canvas).
  - Content-adaptive row heights (no fixed LEVEL_HEIGHT).
  - Pipe segments collapsed to minimal width (edges draw the pipe).
  - Variable equipment sizing based on node connectivity.
  - Single-region equipment clusters placed inline with their parent region.
  - Stitch-connected fragment chains placed consecutively in same row.
  - Remaining fragments packed left-to-right, wrapping to next row.
  - All coordinates snapped to 5mm grid.
"""
import argparse
import json

# ── Grid snap ────────────────────────────────────────────────────────────────
GRID = 5  # mm

def _snap(v):
    return round(v / GRID) * GRID

# ── Node sizes (compact — pipe segments minimized) ───────────────────────────
NODE_W = {
    "pipe_segment":          40,    # restored: pipe runs must be visible
    "equipment_block":       80,    # default; overridden per-node by degree
    "valve:isolation":       22,    # was 26
    "valve:control":         22,    # was 26
    "instrument:flow_meter": 22,    # was 26
    "tank":                  90,    # larger — vessel
    "pump":                  40,    # smaller — inline
    "inlet_outlet":          24,    # was 30
}
NODE_H = {
    "pipe_segment":          12,    # restored: readable height for pipe runs
    "equipment_block":       60,    # default; overridden per-node by degree
    "valve:isolation":       22,    # was 26
    "valve:control":         38,    # was 44
    "instrument:flow_meter": 22,    # was 26
    "tank":                  70,    # larger
    "pump":                  40,    # was 50
    "inlet_outlet":          16,    # was 20
}

# Variable equipment sizes based on node degree
EQUIP_SIZE_LARGE  = (100, 70)   # degree ≥ 3
EQUIP_SIZE_MEDIUM = (80, 60)    # degree 2
EQUIP_SIZE_SMALL  = (60, 45)    # degree 0-1

PADDING            = 3      # was 10 → 5 → 3
GAP                = 5      # was 15 → 8 → 5 — between nodes within a fragment
GAP_AFTER_PIPE     = 8      # gap after pipe_segment nodes (matches GAP for restored width)

# ── Spine layout constants ───────────────────────────────────────────────────
MARGIN             = 40     # canvas border margin (mm) — keep for title block
GAP_BETWEEN_FRAGS  = 5      # was 10 → 8 → 5
ROW_GAP            = 15     # was LEVEL_HEIGHT=280 → 30 → 15 — tighter rows
EFRAG_GAP          = 10     # was EFRAG_LEVEL_HEIGHT=160 → 20 → 10
MAX_ROW_WIDTH      = 3120   # usable row width (base; auto-reduced for small graphs)

SYMBOLS = {
    "pipe_segment":          "pipe_straight",
    "valve:isolation":       "valve_gate",
    "valve:control":         "valve_control",
    "instrument:flow_meter": "instrument_flow_meter",
    "equipment_block":       "equipment_generic",
    "tank":                  "tank_vertical",
    "pump":                  "pump_centrifugal",
    "inlet_outlet":          "inlet_outlet_flag",
}


# ── Helpers ──────────────────────────────────────────────────────────────────
def _nkey(node_data):
    """Return the size-lookup key for a node."""
    t = node_data["type"]
    s = node_data.get("subtype")
    return f"{t}:{s}" if s else t


# Module-level set: bottom-zone efrags rendered horizontally (set in _compute_all_origins)
_HORIZONTAL_EFRAGS: set = set()

def _orientation(fid):
    """Return layout orientation. Bottom-zone efrags use horizontal to reduce height."""
    if fid.startswith("efrag_"):
        if fid in _HORIZONTAL_EFRAGS:
            return "horizontal"
        return "vertical"
    return "horizontal"


def _fid_sort_key(fid):
    if fid.startswith("rfrag_region_"):
        return (0, int(fid.split("_")[2]))
    elif fid.startswith("cfrag_"):
        return (1, int(fid.split("_")[1]))
    else:  # efrag_
        return (2, int(fid.split("_")[1]))


def _group_nodes_by_fragment(global_nodes):
    groups = {}
    for gid, nd in global_nodes.items():
        fid = nd["fragment_id"]
        groups.setdefault(fid, []).append(gid)
    return groups


def _compute_node_degrees(all_edges):
    """Return dict: node_id → degree (undirected count)."""
    degree = {}
    for e in all_edges:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    return degree


def _equip_size(node_data, degree):
    """Return (w, h) for an equipment_block node based on its connectivity."""
    nk = _nkey(node_data)
    if nk == "tank":
        return (NODE_W["tank"], NODE_H["tank"])
    if nk == "pump":
        return (NODE_W["pump"], NODE_H["pump"])
    if nk != "equipment_block":
        return (NODE_W.get(nk, 30), NODE_H.get(nk, 15))
    if degree >= 3:
        return EQUIP_SIZE_LARGE
    elif degree >= 2:
        return EQUIP_SIZE_MEDIUM
    else:
        return EQUIP_SIZE_SMALL


def _node_size(gid, node_data, node_degrees):
    """Return (w, h) for any node, applying variable equipment sizing."""
    nk = _nkey(node_data)
    if nk in ("equipment_block", "tank", "pump"):
        deg = node_degrees.get(gid, 0)
        return _equip_size(node_data, deg)
    return (NODE_W.get(nk, 30), NODE_H.get(nk, 15))


def _gap_after(node_data):
    """Return the gap to insert after this node."""
    nk = _nkey(node_data)
    if nk == "pipe_segment":
        return GAP_AFTER_PIPE
    return GAP


def _fragment_dims(ordered_gids, node_data_map, orientation, node_degrees):
    """Return (width, height) of the fragment bounding box."""
    if orientation == "horizontal":
        max_h = max(_node_size(g, node_data_map[g], node_degrees)[1] for g in ordered_gids)
        total_w = sum(_node_size(g, node_data_map[g], node_degrees)[0] for g in ordered_gids)
        total_gap = sum(_gap_after(node_data_map[g]) for g in ordered_gids[:-1])
        return (PADDING + total_w + total_gap + PADDING,
                PADDING + max_h + PADDING)
    else:  # vertical
        max_w = max(_node_size(g, node_data_map[g], node_degrees)[0] for g in ordered_gids)
        total_h = sum(_node_size(g, node_data_map[g], node_degrees)[1] for g in ordered_gids)
        total_gap = sum(_gap_after(node_data_map[g]) for g in ordered_gids[:-1])
        return (PADDING + max_w + PADDING,
                PADDING + total_h + total_gap + PADDING)


# ── Spine layout: chain extraction + row packing ────────────────────────────

def _build_chains(all_edges):
    """
    Extract ordered stitch-connected fragment chains.
    Returns list of chains; each chain is [fid1, fid2, ...] in traversal order.
    """
    # Stitch edges are cross-fragment: the two node IDs belong to different fragments.
    # Node IDs are formatted as "<frag_id>:<node_key>"; compare the prefix before ":".
    def _frag_of(nid):
        return nid.rsplit(":n", 1)[0] if ":n" in nid else nid

    stitch_edges = [
        (e["from"], e["to"])
        for e in all_edges
        if _frag_of(e.get("from", "")) != _frag_of(e.get("to", ""))
        and _frag_of(e.get("from", "")) != ""
    ]
    if not stitch_edges:
        return []

    # Build undirected fragment adjacency from stitch edges
    frag_adj: dict[str, set] = {}
    for src, dst in stitch_edges:
        sf = src.rsplit(":n", 1)[0]
        df = dst.rsplit(":n", 1)[0]
        frag_adj.setdefault(sf, set()).add(df)
        frag_adj.setdefault(df, set()).add(sf)

    # Traverse connected components → ordered chains
    visited: set = set()
    chains = []

    for start in sorted(frag_adj.keys(), key=_fid_sort_key):
        if start in visited:
            continue

        # BFS to collect the component
        comp: set = set()
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in comp:
                continue
            comp.add(node)
            for nb in frag_adj.get(node, set()):
                if nb not in comp:
                    queue.append(nb)
        visited.update(comp)

        # Find endpoint(s) (degree 1 within this component)
        endpoints = [
            n for n in comp
            if len(frag_adj.get(n, set()) & comp) == 1
        ]
        if not endpoints:
            endpoints = [min(comp, key=_fid_sort_key)]

        # Walk from the lowest-index endpoint
        entry = min(endpoints, key=_fid_sort_key)
        chain = [entry]
        chain_set = {entry}
        prev, curr = None, entry
        while True:
            nbrs = [
                n for n in frag_adj.get(curr, set())
                if n != prev and n in comp and n not in chain_set
            ]
            if not nbrs:
                break
            nxt = nbrs[0]
            chain.append(nxt)
            chain_set.add(nxt)
            prev, curr = curr, nxt

        chains.append(chain)

    return chains


def _load_efrag_spanning(fragments_path):
    """Load efrag → list of spanning region names from fragments.json."""
    try:
        with open(fragments_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    result = {}
    for frag in data.get("fragments", []):
        fid = frag.get("fragment_id", "")
        if fid.startswith("efrag_"):
            result[fid] = frag.get("spanning_regions", [])
    return result


def _compute_all_origins(fragment_groups, node_data_map, all_edges,
                         node_degrees, efrag_spanning):
    """
    Compute (ox, oy) origin for every fragment using dense content-adaptive packing.

    Packing order:
      1. Stitch chains (rfrag + cfrag sequences) → packed consecutively, same row.
      2. Remaining unconnected rfrags/cfrags → packed row by row.
      3. Single-region efrags → attached inline to their parent region's row.
      4. Cross-region efrags → separate zone below.
    """
    # Pre-compute compact dimensions for every fragment
    frag_w: dict[str, float] = {}
    frag_h: dict[str, float] = {}
    for fid, gids in fragment_groups.items():
        ordered = sorted(gids, key=lambda g: int(g.split(":n")[1]))
        orient = _orientation(fid)
        fw, fh = _fragment_dims(ordered, node_data_map, orient, node_degrees)
        frag_w[fid] = fw
        frag_h[fid] = fh

    # Identify stitch chains and unconnected frags
    chains = _build_chains(all_edges)
    chain_frags = set(f for c in chains for f in c)

    rfrags = sorted(
        [f for f in fragment_groups if f.startswith("rfrag_")],
        key=_fid_sort_key,
    )
    cfrags = sorted(
        [f for f in fragment_groups if f.startswith("cfrag_")],
        key=_fid_sort_key,
    )
    efrags = sorted(
        [f for f in fragment_groups if f.startswith("efrag_")],
        key=_fid_sort_key,
    )
    unconnected = [f for f in rfrags + cfrags if f not in chain_frags]

    # Classify efrags: single-region + small (≤2 nodes) → inline;
    # large or cross-region → bottom zone
    inline_efrags: dict[str, list] = {}  # rfrag_fid → [efrag_ids]
    bottom_efrags: list = []
    for fid in efrags:
        spanning = efrag_spanning.get(fid, [])
        n_nodes = len(fragment_groups.get(fid, []))
        if len(spanning) == 1 and n_nodes <= 2:
            region_name = spanning[0]
            rfrag_fid = f"rfrag_{region_name}"
            if rfrag_fid in fragment_groups:
                inline_efrags.setdefault(rfrag_fid, []).append(fid)
            else:
                bottom_efrags.append(fid)
        else:
            bottom_efrags.append(fid)

    # Mark bottom-zone efrags as horizontal and recompute their dimensions
    global _HORIZONTAL_EFRAGS
    _HORIZONTAL_EFRAGS = set(bottom_efrags)
    for fid in bottom_efrags:
        gids = fragment_groups[fid]
        ordered = sorted(gids, key=lambda g: int(g.split(":n")[1]))
        fw, fh = _fragment_dims(ordered, node_data_map, "horizontal", node_degrees)
        frag_w[fid] = fw
        frag_h[fid] = fh

    # Adaptive MAX_ROW_WIDTH: ensure landscape aspect ratio.
    non_efrag_fids = [f for f in fragment_groups if not f.startswith("efrag_")]
    pipe_fids = non_efrag_fids if non_efrag_fids else list(fragment_groups.keys())
    total_pipe_w = sum(frag_w[f] for f in pipe_fids)
    avg_frag_h = (sum(frag_h[f] for f in pipe_fids) / len(pipe_fids)
                  if pipe_fids else 50)
    row_step = avg_frag_h + ROW_GAP

    # Floor: at least the widest chain (can't be broken across rows)
    widest_chain = 0.0
    for chain in chains:
        chain_w = (sum(frag_w[f] for f in chain)
                   + GAP_BETWEEN_FRAGS * max(0, len(chain) - 1))
        for f in chain:
            if f in inline_efrags:
                for ef in inline_efrags[f]:
                    chain_w += GAP_BETWEEN_FRAGS + frag_w[ef]
        widest_chain = max(widest_chain, chain_w)

    # Target landscape AR ≥ 2: W ≥ sqrt(3 × total_w × row_step)
    landscape_width = (3.0 * total_pipe_w * row_step) ** 0.5
    effective_max_width = _snap(min(MAX_ROW_WIDTH,
                                    max(800, widest_chain + 50, landscape_width)))

    origins: dict[str, tuple] = {}
    rows: list[list] = []        # list of rows; each row = list of fids
    current_row_fids: list = []
    current_x = 0.0

    def _row_width(fids):
        return (sum(frag_w[f] for f in fids)
                + GAP_BETWEEN_FRAGS * max(0, len(fids) - 1))

    def flush_row():
        nonlocal current_row_fids, current_x
        if current_row_fids:
            rows.append(current_row_fids)
            current_row_fids = []
            current_x = 0.0

    def add_group(fids):
        nonlocal current_x, current_row_fids
        group_w = (sum(frag_w[f] for f in fids)
                   + GAP_BETWEEN_FRAGS * max(0, len(fids) - 1))

        # Also account for inline efrags that attach to rfrags in this group
        inline_extra = 0.0
        for f in fids:
            if f in inline_efrags:
                for ef in inline_efrags[f]:
                    inline_extra += GAP_BETWEEN_FRAGS + frag_w[ef]

        total_w = group_w + inline_extra

        # Wrap to next row if this group doesn't fit
        if current_x > 0 and current_x + GAP_BETWEEN_FRAGS + total_w > effective_max_width:
            flush_row()

        for fid in fids:
            current_row_fids.append(fid)
            current_x += frag_w[fid] + GAP_BETWEEN_FRAGS

            # Attach inline efrags right after their parent rfrag
            if fid in inline_efrags:
                for ef in inline_efrags[fid]:
                    current_row_fids.append(ef)
                    current_x += frag_w[ef] + GAP_BETWEEN_FRAGS

    # 1. Chains first (stitch-linked members stay on same row)
    for chain in sorted(chains, key=lambda c: _fid_sort_key(c[0])):
        add_group(chain)

    # 2. Unconnected rfrags / cfrags
    for fid in unconnected:
        add_group([fid])

    flush_row()

    # 3. Assign y-coordinates using content-adaptive row heights
    y_cursor = MARGIN
    for row_fids in rows:
        row_h = max(frag_h[f] for f in row_fids)
        x_cursor = 0.0
        for fid in row_fids:
            origins[fid] = (_snap(MARGIN + x_cursor), _snap(y_cursor))
            x_cursor += frag_w[fid] + GAP_BETWEEN_FRAGS
        y_cursor += row_h + ROW_GAP

    main_y_end = y_cursor  # bottom of main content

    # 4. Bottom-zone efrags: packed in content-adaptive rows
    if bottom_efrags:
        efrag_rows: list[list] = []
        efrag_row_fids: list = []
        ex = 0.0
        for fid in bottom_efrags:
            if ex > 0 and ex + GAP_BETWEEN_FRAGS + frag_w[fid] > effective_max_width:
                efrag_rows.append(efrag_row_fids)
                efrag_row_fids = []
                ex = 0.0
            efrag_row_fids.append(fid)
            ex += frag_w[fid] + GAP_BETWEEN_FRAGS
        if efrag_row_fids:
            efrag_rows.append(efrag_row_fids)

        ey_cursor = main_y_end + EFRAG_GAP
        for erow in efrag_rows:
            erow_h = max(frag_h[f] for f in erow)
            ex_cursor = 0.0
            for fid in erow:
                origins[fid] = (_snap(MARGIN + ex_cursor), _snap(ey_cursor))
                ex_cursor += frag_w[fid] + GAP_BETWEEN_FRAGS
            ey_cursor += erow_h + EFRAG_GAP

    return origins


# ── Node/edge/group builders ─────────────────────────────────────────────────

def _layout_fragment_nodes(fid, ordered_gids, ox, oy, orientation,
                           node_data_map, node_degrees):
    """Assign absolute {x, y, w, h} to each node in the fragment."""
    geom = {}
    if orientation == "horizontal":
        max_h = max(_node_size(g, node_data_map[g], node_degrees)[1]
                    for g in ordered_gids)
        cy = oy + PADDING + max_h / 2
        x_cursor = ox + PADDING
        for gid in ordered_gids:
            nd = node_data_map[gid]
            w, h = _node_size(gid, nd, node_degrees)
            geom[gid] = {"x": _snap(x_cursor), "y": _snap(cy - h / 2),
                         "w": w, "h": h}
            x_cursor += w + _gap_after(nd)
    else:  # vertical
        max_w = max(_node_size(g, node_data_map[g], node_degrees)[0]
                    for g in ordered_gids)
        cx = ox + PADDING + max_w / 2
        y_cursor = oy + PADDING
        for gid in ordered_gids:
            nd = node_data_map[gid]
            w, h = _node_size(gid, nd, node_degrees)
            geom[gid] = {"x": _snap(cx - w / 2), "y": _snap(y_cursor),
                         "w": w, "h": h}
            y_cursor += h + _gap_after(nd)
    return geom


def _build_output_nodes(global_nodes, node_geom_flat):
    out = []
    for gid, nd in global_nodes.items():
        g = node_geom_flat[gid]
        orient = _orientation(nd["fragment_id"])
        w, h = g["w"], g["h"]

        if orient == "horizontal":
            ports = {"in": {"x": 0, "y": h / 2}, "out": {"x": w, "y": h / 2}}
        else:
            ports = {"in": {"x": w / 2, "y": 0}, "out": {"x": w / 2, "y": h}}

        t = nd["type"]
        if "subtype" in nd:
            t = f"{t}:{nd['subtype']}"

        out.append({
            "global_id":   gid,
            "type":        t,
            "symbol":      SYMBOLS.get(_nkey(nd), "generic"),
            "position":    {"x": g["x"], "y": g["y"]},
            "size":        {"width": w, "height": h},
            "orientation": orient,
            "ports":       ports,
        })
    return out


def _route_edges(all_edges, node_geom_flat, global_nodes):
    out = []
    for edge in all_edges:
        src, dst = edge["from"], edge["to"]
        sg, tg = node_geom_flat[src], node_geom_flat[dst]
        is_stitch = edge.get("stitch", False)

        src_vert = global_nodes[src]["fragment_id"].startswith("efrag_")
        dst_vert = global_nodes[dst]["fragment_id"].startswith("efrag_")

        from_x = sg["x"] + sg["w"] / 2 if src_vert else sg["x"] + sg["w"]
        from_y = sg["y"] + sg["h"]     if src_vert else sg["y"] + sg["h"] / 2
        to_x   = tg["x"] + tg["w"] / 2 if dst_vert else tg["x"]
        to_y   = tg["y"]               if dst_vert else tg["y"] + tg["h"] / 2

        # Snap edge endpoints
        from_x, from_y = _snap(from_x), _snap(from_y)
        to_x, to_y = _snap(to_x), _snap(to_y)

        if is_stitch:
            dy = abs(to_y - from_y)
            if dy < 20:
                # Same row — horizontal L-shape
                mid_x = _snap((from_x + to_x) / 2)
                path = [
                    {"x": from_x, "y": from_y},
                    {"x": mid_x,  "y": from_y},
                    {"x": mid_x,  "y": to_y},
                    {"x": to_x,   "y": to_y},
                ]
            else:
                # Cross-row — vertical routing with two 90° bends
                exit_x = _snap(from_x + 20)
                path = [
                    {"x": from_x, "y": from_y},
                    {"x": exit_x, "y": from_y},
                    {"x": exit_x, "y": to_y},
                    {"x": to_x,   "y": to_y},
                ]
        else:
            path = [{"x": from_x, "y": from_y}, {"x": to_x, "y": to_y}]

        out.append({
            "from":  src,
            "to":    dst,
            "type":  "pipe",
            "path":  path,
            "style": {"line_type": "solid", "arrow": "flow"},
        })
    return out


def _build_groups(fragment_groups, node_data_map, origins, node_degrees):
    out = []
    for fid in sorted(fragment_groups.keys(), key=_fid_sort_key):
        ordered = sorted(fragment_groups[fid], key=lambda g: int(g.split(":n")[1]))
        orient = _orientation(fid)
        ox, oy = origins[fid]
        fw, fh = _fragment_dims(ordered, node_data_map, orient, node_degrees)
        out.append({
            "fragment_id":  fid,
            "bounding_box": {"x": ox, "y": oy, "width": fw, "height": fh},
            "orientation":  "left_to_right" if orient == "horizontal" else "top_to_bottom",
        })
    return out


# ── Verification ─────────────────────────────────────────────────────────────

def _verify(output, original_node_count, original_edge_count):
    nodes = output["nodes"]
    edges = output["edges"]
    groups = output["groups"]

    assert len(nodes) == original_node_count, \
        f"Node count: {len(nodes)} != {original_node_count}"
    assert len(edges) == original_edge_count, \
        f"Edge count: {len(edges)} != {original_edge_count}"

    seen = {n["global_id"] for n in nodes}
    assert len(seen) == len(nodes), "Duplicate global_ids in output"

    for e in edges:
        assert e["from"] in seen, f"Edge references unknown source: {e['from']}"
        assert e["to"]   in seen, f"Edge references unknown target: {e['to']}"

    for n in nodes:
        for k in ("position", "size", "ports"):
            assert k in n, f"Missing '{k}' on {n['global_id']}"

    assert len(groups) >= 1, f"Group count: {len(groups)} < 1"

    stitch = sum(1 for e in edges if len(e["path"]) == 4)
    intra  = sum(1 for e in edges if len(e["path"]) == 2)

    print("All assertions passed.")
    print(f"  Nodes:   {len(nodes)}")
    print(f"  Edges:   {len(edges)}  ({stitch} stitch / {intra} intra)")
    print(f"  Groups:  {len(groups)}")
    print("  Node types:")
    dist = {}
    for n in nodes:
        t = n["type"].split(":")[0]
        dist[t] = dist.get(t, 0) + 1
    for t, c in sorted(dist.items()):
        print(f"    {t}: {c}")

    # Print canvas bounds + fill ratio
    xs = [n["position"]["x"] for n in nodes]
    ys = [n["position"]["y"] for n in nodes]
    x2 = [n["position"]["x"] + n["size"]["width"]  for n in nodes]
    y2 = [n["position"]["y"] + n["size"]["height"] for n in nodes]
    cw = max(x2) - min(xs) + 2 * MARGIN
    ch = max(y2) - min(ys) + 2 * MARGIN
    canvas_area = cw * ch
    node_area = sum(n["size"]["width"] * n["size"]["height"] for n in nodes)
    fill = node_area / canvas_area * 100 if canvas_area > 0 else 0
    print(f"  Canvas:  {cw:.0f} × {ch:.0f} mm  ({'landscape' if cw > ch else 'portrait'})")
    print(f"  Fill:    {fill:.1f}%")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stage 6: PFD Layout Realization")
    parser.add_argument("--input", default="pfd_global_graph.json")
    parser.add_argument("--input-fragments", default=None,
                        help="Optional fragments.json for inline equipment placement")
    parser.add_argument("--output", default="pfd_layout_realized.json")
    args = parser.parse_args()

    src = args.input
    dst = args.output

    with open(src) as f:
        data = json.load(f)

    global_nodes = data["global_graph"]["nodes"]
    all_edges    = data["global_graph"]["edges"]

    # Compute node degrees for variable equipment sizing
    node_degrees = _compute_node_degrees(all_edges)

    # Load efrag spanning regions if fragments.json available
    efrag_spanning = {}
    if args.input_fragments:
        efrag_spanning = _load_efrag_spanning(args.input_fragments)

    fragment_groups = _group_nodes_by_fragment(global_nodes)

    # Phase 6.1: compute all fragment origins via dense spine packing
    origins = _compute_all_origins(fragment_groups, global_nodes, all_edges,
                                   node_degrees, efrag_spanning)

    # Phase 6.2: lay out nodes within each fragment
    node_geom_flat = {}
    for fid, gids in fragment_groups.items():
        ordered = sorted(gids, key=lambda g: int(g.split(":n")[1]))
        ox, oy = origins[fid]
        orient = _orientation(fid)
        node_geom_flat.update(
            _layout_fragment_nodes(fid, ordered, ox, oy, orient,
                                   global_nodes, node_degrees)
        )

    # Phase 6.3: build outputs
    out_nodes  = _build_output_nodes(global_nodes, node_geom_flat)
    out_edges  = _route_edges(all_edges, node_geom_flat, global_nodes)
    out_groups = _build_groups(fragment_groups, global_nodes, origins, node_degrees)

    output = {
        "metadata": {
            "source_file":       src,
            "schema_version":    "1",
            "layout_engine":     "spine_v3",
            "coordinate_system": "cartesian",
            "units":             "mm",
            "patterns_expanded": False,
        },
        "symbols": SYMBOLS,
        "nodes":   out_nodes,
        "edges":   out_edges,
        "groups":  out_groups,
    }

    _verify(output, len(global_nodes), len(all_edges))

    with open(dst, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(out_nodes)}-node layout to {dst}")


if __name__ == "__main__":
    main()
