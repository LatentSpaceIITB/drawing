#!/usr/bin/env python3
"""
Stage 6 – PFD Layout Realization
Input:  pfd_global_graph.json
Output: pfd_layout_realized.json
"""
import json

# ── Constants ─────────────────────────────────────────────────────────────────
NODE_W = {
    "pipe_segment":          30,
    "equipment_block":       100,
    "valve:isolation":       50,
    "valve:control":         50,
    "instrument:flow_meter": 50,
}
NODE_H = {
    "pipe_segment":          15,
    "equipment_block":       70,
    "valve:isolation":       50,
    "valve:control":         50,
    "instrument:flow_meter": 50,
}
PADDING = 15
GAP     = 25

SYMBOLS = {
    "pipe_segment":          "pipe_straight",
    "valve:isolation":       "valve_gate",
    "valve:control":         "valve_control",
    "instrument:flow_meter": "instrument_flow_meter",
    "equipment_block":       "equipment_generic",
}

RFRAG_COL_W  = 500
RFRAG_ROW_H  = 150
CFRAG_CELL_W = 250
CFRAG_Y      = 980
EFRAG_COL_W  = 200
EFRAG_ROW_H  = 500
EFRAG_Y      = 1230


# ── Helpers ───────────────────────────────────────────────────────────────────
def _nkey(node_data):
    """Return the size-lookup key for a node: 'type:subtype' or 'type'."""
    t = node_data["type"]
    s = node_data.get("subtype")
    return f"{t}:{s}" if s else t


def _orientation(fid):
    return "vertical" if fid.startswith("efrag_") else "horizontal"


def _fid_sort_key(fid):
    """Sort order: rfrag (0), cfrag (1), efrag (2), then by numeric index."""
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


def _fragment_origin(fid):
    if fid.startswith("rfrag_region_"):
        N = int(fid.split("_")[2])
        return (N % 6 * RFRAG_COL_W, N // 6 * RFRAG_ROW_H)
    elif fid.startswith("cfrag_"):
        N = int(fid.split("_")[1])
        return (N * CFRAG_CELL_W, CFRAG_Y)
    else:  # efrag_
        N = int(fid.split("_")[1])
        return (N % 6 * EFRAG_COL_W, EFRAG_Y + N // 6 * EFRAG_ROW_H)


def _fragment_dims(ordered_gids, node_data_map, orientation):
    """Return (width, height) of the fragment bounding box."""
    if orientation == "horizontal":
        max_h   = max(NODE_H[_nkey(node_data_map[g])] for g in ordered_gids)
        total_w = sum(NODE_W[_nkey(node_data_map[g])] for g in ordered_gids)
        n = len(ordered_gids)
        return (PADDING + total_w + GAP * (n - 1) + PADDING,
                PADDING + max_h + PADDING)
    else:  # vertical
        max_w   = max(NODE_W[_nkey(node_data_map[g])] for g in ordered_gids)
        total_h = sum(NODE_H[_nkey(node_data_map[g])] for g in ordered_gids)
        n = len(ordered_gids)
        return (PADDING + max_w + PADDING,
                PADDING + total_h + GAP * (n - 1) + PADDING)


def _layout_fragment_nodes(fid, ordered_gids, ox, oy, orientation, node_data_map):
    """Assign absolute {x, y, w, h} to each node in the fragment."""
    geom = {}
    if orientation == "horizontal":
        max_h    = max(NODE_H[_nkey(node_data_map[g])] for g in ordered_gids)
        cy       = oy + PADDING + max_h / 2
        x_cursor = ox + PADDING
        for gid in ordered_gids:
            nd = node_data_map[gid]
            w  = NODE_W[_nkey(nd)]
            h  = NODE_H[_nkey(nd)]
            geom[gid] = {"x": x_cursor, "y": cy - h / 2, "w": w, "h": h}
            x_cursor += w + GAP
    else:  # vertical
        max_w    = max(NODE_W[_nkey(node_data_map[g])] for g in ordered_gids)
        cx       = ox + PADDING + max_w / 2
        y_cursor = oy + PADDING
        for gid in ordered_gids:
            nd = node_data_map[gid]
            w  = NODE_W[_nkey(nd)]
            h  = NODE_H[_nkey(nd)]
            geom[gid] = {"x": cx - w / 2, "y": y_cursor, "w": w, "h": h}
            y_cursor += h + GAP
    return geom


def _build_output_nodes(global_nodes, node_geom_flat):
    out = []
    for gid, nd in global_nodes.items():
        g      = node_geom_flat[gid]
        orient = _orientation(nd["fragment_id"])
        w, h   = g["w"], g["h"]

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
            "symbol":      SYMBOLS[_nkey(nd)],
            "position":    {"x": g["x"], "y": g["y"]},
            "size":        {"width": w, "height": h},
            "orientation": orient,
            "ports":       ports,
        })
    return out


def _route_edges(all_edges, node_geom_flat, global_nodes):
    out = []
    for edge in all_edges:
        src, dst  = edge["from"], edge["to"]
        sg, tg    = node_geom_flat[src], node_geom_flat[dst]
        is_stitch = edge.get("stitch", False)

        src_vert = global_nodes[src]["fragment_id"].startswith("efrag_")
        dst_vert = global_nodes[dst]["fragment_id"].startswith("efrag_")

        from_x = sg["x"] + sg["w"] / 2 if src_vert else sg["x"] + sg["w"]
        from_y = sg["y"] + sg["h"]     if src_vert else sg["y"] + sg["h"] / 2
        to_x   = tg["x"] + tg["w"] / 2 if dst_vert else tg["x"]
        to_y   = tg["y"]               if dst_vert else tg["y"] + tg["h"] / 2

        if is_stitch:
            mid_x = (from_x + to_x) / 2
            path  = [
                {"x": from_x, "y": from_y},
                {"x": mid_x,  "y": from_y},
                {"x": mid_x,  "y": to_y},
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


def _build_groups(fragment_groups, node_data_map):
    out = []
    for fid in sorted(fragment_groups.keys(), key=_fid_sort_key):
        ordered = sorted(fragment_groups[fid], key=lambda g: int(g.split(":n")[1]))
        orient  = _orientation(fid)
        ox, oy  = _fragment_origin(fid)
        fw, fh  = _fragment_dims(ordered, node_data_map, orient)
        out.append({
            "fragment_id":  fid,
            "bounding_box": {"x": ox, "y": oy, "width": fw, "height": fh},
            "orientation":  "left_to_right" if orient == "horizontal" else "top_to_bottom",
        })
    return out


def _verify(output, original_node_count, original_edge_count):
    nodes  = output["nodes"]
    edges  = output["edges"]
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

    assert len(groups) == 68, f"Group count: {len(groups)} != 68"

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


def main():
    src = "pfd_global_graph.json"
    dst = "pfd_layout_realized.json"

    with open(src) as f:
        data = json.load(f)

    global_nodes = data["global_graph"]["nodes"]
    all_edges    = data["global_graph"]["edges"]

    fragment_groups = _group_nodes_by_fragment(global_nodes)

    # Phase 6.1 + 6.2: place fragments and lay out nodes within each
    node_geom_flat = {}
    for fid, gids in fragment_groups.items():
        ordered = sorted(gids, key=lambda g: int(g.split(":n")[1]))
        ox, oy  = _fragment_origin(fid)
        orient  = _orientation(fid)
        node_geom_flat.update(
            _layout_fragment_nodes(fid, ordered, ox, oy, orient, global_nodes)
        )

    # Phase 6.3: route edges
    out_nodes  = _build_output_nodes(global_nodes, node_geom_flat)
    out_edges  = _route_edges(all_edges, node_geom_flat, global_nodes)
    out_groups = _build_groups(fragment_groups, global_nodes)

    output = {
        "metadata": {
            "source_file":       src,
            "schema_version":    "1",
            "layout_engine":     "deterministic_v1",
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
