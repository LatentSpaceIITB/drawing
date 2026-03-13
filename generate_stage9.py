#!/usr/bin/env python3
"""
Stage 9 – PID Graph Assembly + Layout + Interaction
Inputs:  pid_primitives.json, pfd_layout_realized.json
Outputs: pid_global_graph.json, pid_layout_realized.json, pid_interaction_hooks.json
"""

import argparse
import json
from collections import defaultdict

# ── Constants ──────────────────────────────────────────────────────────────────

SIZES = {
    "pipe_segment":           (40, 12),     # restored — pipe runs must be visible
    "equipment_block":        (80, 60),     # default; overridden per-node from PFD
    "valve:isolation":        (22, 22),     # was (26, 26)
    "valve:control":          (22, 38),     # was (26, 44)
    "valve:drain":            (14, 14),     # was (18, 18)
    "instrument:flow_meter":  (22, 22),     # was (26, 26)
    "actuator:pneumatic":     (18, 14),     # was (22, 18)
    "instrument:controller":  (18, 18),     # was (20, 20)
    "instrument:transmitter": (18, 18),     # was (20, 20)
    "instrument:pressure":    (18, 18),     # was (20, 20)
    "instrument:temperature": (18, 18),     # was (20, 20)
    "tank":                   (90, 70),     # was (80, 60)
    "pump":                   (40, 40),     # was (50, 50)
    "inlet_outlet":           (24, 16),     # was (30, 20)
    "motor:driver":           (18, 14),     # was (22, 18)
    "instrument:level":       (18, 18),     # was (20, 20)
}

# Added-node offsets from parent top-left (dx, dy) in mm
# Tighter clustering for compact layout v3
OFFSETS = {
    # drain: horizontally centred under isolation valve (w=22), below valve bottom
    "valve:drain":            (4,  24),   # was (4, 30) — closer
    # actuator: centred above control valve (w=22, h=38), above valve top
    "actuator:pneumatic":     (2, -16),   # was (2, -22) — closer
    # controller: above actuator
    "instrument:controller":  (1, -36),   # was (3, -46) — closer
    # transmitter: centred above flow_meter (w=22, h=22)
    "instrument:transmitter": (2, -20),   # was (3, -24)
    # PI/TI: right of equipment_block, vertically offset
    "instrument:pressure":    (64,  -3),  # was (84, -5) — closer to equip
    "instrument:temperature": (64,  17),  # was (84, 19)
    "motor:driver":           (11, -16),  # was (14, -22)
    "instrument:level":       (64,  37),  # was (84, 43)
}

SYMBOLS = {
    "pipe_segment":           "pipe_straight",
    "equipment_block":        "vessel",
    "valve:isolation":        "gate_valve",
    "valve:control":          "control_valve",
    "valve:drain":            "drain_valve",
    "instrument:flow_meter":  "flow_element",
    "actuator:pneumatic":     "diaphragm_actuator",
    "instrument:controller":  "controller_bubble",
    "instrument:transmitter": "transmitter_bubble",
    "instrument:pressure":    "pressure_indicator",
    "instrument:temperature": "temperature_indicator",
    "tank":                   "tank_horizontal",
    "pump":                   "pump_centrifugal",
    "inlet_outlet":           "inlet_outlet_connector",
    "motor:driver":           "motor_driver",
    "instrument:level":       "level_indicator",
}

TOOLTIP_TITLE = {
    "pipe_segment":           "Pipe Segment",
    "equipment_block":        "Equipment",
    "valve:isolation":        "Isolation Valve",
    "valve:drain":            "Drain Valve",
    "valve:control":          "Control Valve",
    "actuator:pneumatic":     "Pneumatic Actuator",
    "instrument:controller":  "Controller",
    "instrument:flow_meter":  "Flow Meter",
    "instrument:transmitter": "Transmitter",
    "instrument:pressure":    "Pressure Indicator",
    "instrument:temperature": "Temperature Indicator",
    "tank":                   "Tank",
    "pump":                   "Pump",
    "inlet_outlet":           "Inlet/Outlet",
    "motor:driver":           "Motor Driver",
    "instrument:level":       "Level Indicator",
}

TOOLTIP_FIELDS = {
    "pipe_segment":           ["line_id", "size", "spec", "service"],
    "equipment_block":        ["tag", "equipment_type", "duty"],
    "valve:isolation":        ["tag", "normally_open", "fail_position", "valve_type"],
    "valve:drain":            ["tag", "normally_closed"],
    "valve:control":          ["tag", "fail_position", "control_loop"],
    "actuator:pneumatic":     ["tag"],
    "instrument:controller":  ["tag"],
    "instrument:flow_meter":  ["tag", "measurement"],
    "instrument:transmitter": ["tag"],
    "instrument:pressure":    ["tag"],
    "instrument:temperature": ["tag"],
    "tank":                   ["tag", "equipment_type", "contents"],
    "pump":                   ["tag", "equipment_type", "duty"],
    "inlet_outlet":           ["tag", "direction"],
    "motor:driver":           ["tag"],
    "instrument:level":       ["tag"],
}


# ── Phase 1: Graph Assembly ────────────────────────────────────────────────────

def _flatten_graph(primitives):
    """Flatten pid_primitives into a nodes dict and edges list."""
    nodes = {}
    expansion_edges = []
    xe_counter = 0

    for exp in primitives["pid_expansions"]:
        fid = exp["fragment_id"]
        pn = exp["primary_node"]
        pid = pn["id"]
        ctype = exp["pfd_type"]  # compound type e.g. "valve:isolation"

        # Primary node: use compound pfd_type, drop internal subtype key
        node_entry = dict(pn)
        node_entry["type"] = ctype
        node_entry.pop("subtype", None)
        node_entry["fragment_id"] = fid
        nodes[pid] = node_entry

        # Added nodes
        for an in exp["added_nodes"]:
            aid = an["id"]
            entry = dict(an)
            entry["fragment_id"] = fid
            entry["parent_id"] = pid
            nodes[aid] = entry

        # Expansion edges (xe_)
        for ae in exp["added_edges"]:
            expansion_edges.append({
                "edge_id": f"xe_{xe_counter}",
                "from": ae["from"],
                "to": ae["to"],
                "kind": ae["kind"],
            })
            xe_counter += 1

    # Process edges (pe_) — pass-through from pid_primitives
    process_edges = []
    for pe in primitives["pid_edges"]:
        process_edges.append({
            "edge_id": pe["edge_id"],
            "from": pe["from"],
            "to": pe["to"],
            "kind": "process",
            "stitch": pe.get("stitch", False),
            "line_id": pe.get("line_id"),
        })

    # Process edges first, then expansion edges
    edges = process_edges + expansion_edges
    return nodes, edges


def _verify_graph(graph_output):
    nodes = graph_output["global_graph"]["nodes"]
    edges = graph_output["global_graph"]["edges"]

    assert len(nodes) >= 1, f"Node count: {len(nodes)} < 1"
    assert len(edges) >= 0, f"Edge count: {len(edges)} < 0"

    node_ids = set(nodes.keys())
    assert len(node_ids) == len(nodes), "Duplicate node IDs"

    for e in edges:
        assert e["from"] in node_ids, f"Edge from unknown node: {e['from']}"
        assert e["to"] in node_ids, f"Edge to unknown node: {e['to']}"

    edge_ids = [e["edge_id"] for e in edges]
    assert len(set(edge_ids)) == len(edges), "Duplicate edge IDs"


# ── Phase 2: Layout ────────────────────────────────────────────────────────────

def _build_pfd_pos_lookup(pfd_layout):
    """node_id → {x, y, w, h, orientation}"""
    lookup = {}
    for nd in pfd_layout["nodes"]:
        gid = nd["global_id"]
        lookup[gid] = {
            "x": nd["position"]["x"],
            "y": nd["position"]["y"],
            "w": nd["size"]["width"],
            "h": nd["size"]["height"],
            "orientation": nd["orientation"],
        }
    return lookup


def _size_for(ctype):
    return SIZES.get(ctype, (30, 30))


def _symbol_for(ctype):
    return SYMBOLS.get(ctype, "generic")


def _ports_for(ctype, w, h, orientation):
    """Compute relative port positions for a node."""
    # Instrument bubbles and controllers: center only
    if ctype in ("instrument:pressure", "instrument:temperature",
                 "instrument:transmitter", "instrument:controller",
                 "instrument:level"):
        return {"center": {"x": w / 2, "y": h / 2}}
    # Actuator / motor: in=bottom (connects to parent), out=top
    elif ctype in ("actuator:pneumatic", "motor:driver"):
        return {"in": {"x": w / 2, "y": h}, "out": {"x": w / 2, "y": 0}}
    # Drain: in=top (branches from parent valve)
    elif ctype == "valve:drain":
        return {"in": {"x": w / 2, "y": 0}}
    # Primary process nodes
    elif orientation == "vertical":
        return {"in": {"x": w / 2, "y": 0}, "out": {"x": w / 2, "y": h}}
    else:  # horizontal
        return {"in": {"x": 0, "y": h / 2}, "out": {"x": w, "y": h / 2}}


def _layout_nodes(primitives, pfd_pos):
    """
    Build layout node dicts and a node_map for routing.
    Returns (layout_nodes_list, node_map).
    node_map: node_id → {x, y, w, h, orientation, fragment_id}
    """
    result = []
    node_map = {}

    for exp in primitives["pid_expansions"]:
        fid = exp["fragment_id"]
        pn = exp["primary_node"]
        pid = pn["id"]
        ctype = exp["pfd_type"]

        # Position from PFD lookup; inherit PFD size when available
        pos = pfd_pos[pid]
        pfd_w, pfd_h = pos["w"], pos["h"]
        # Use PFD size for primary nodes (Stage 6 computed variable sizes)
        w, h = pfd_w, pfd_h
        orient = pos["orientation"]
        ports = _ports_for(ctype, w, h, orient)

        # Semantics: all primary_node fields except internal ids/type keys
        semantics = {k: v for k, v in pn.items()
                     if k not in ("id", "type", "subtype")}

        tag_val = pn.get("tag", pn.get("line_id", ""))

        primary_layout = {
            "global_id": pid,
            "type": ctype,
            "symbol": _symbol_for(ctype),
            "tag": tag_val,
            "pfd_origin": pid,
            "is_added_node": False,
            "fragment_id": fid,
            "position": {"x": pos["x"], "y": pos["y"]},
            "size": {"width": w, "height": h},
            "orientation": orient,
            "ports": ports,
            "semantics": semantics,
        }
        result.append(primary_layout)
        node_map[pid] = {
            "x": pos["x"], "y": pos["y"],
            "w": w, "h": h,
            "orientation": orient,
            "fragment_id": fid,
        }

        # Added nodes
        for an in exp["added_nodes"]:
            aid = an["id"]
            atype = an["type"]
            dx, dy = OFFSETS.get(atype, (0, 0))
            # Dynamic offset: PI/TI/LI reference parent width (varies per equipment)
            if atype in ("instrument:pressure", "instrument:temperature",
                         "instrument:level"):
                dx = w + 4  # 4mm gap right of equipment, regardless of size
                # Keep dy from OFFSETS (vertical stacking)
            ax = pos["x"] + dx
            ay = pos["y"] + dy
            aw, ah = _size_for(atype)
            aorient = "vertical"
            aports = _ports_for(atype, aw, ah, aorient)
            asem = {k: v for k, v in an.items() if k not in ("id", "type")}

            added_layout = {
                "global_id": aid,
                "type": atype,
                "symbol": _symbol_for(atype),
                "tag": an.get("tag", ""),
                "pfd_origin": pid,
                "is_added_node": True,
                "parent_id": pid,
                "fragment_id": fid,
                "position": {"x": ax, "y": ay},
                "size": {"width": aw, "height": ah},
                "orientation": aorient,
                "ports": aports,
                "semantics": asem,
            }
            result.append(added_layout)
            node_map[aid] = {
                "x": ax, "y": ay,
                "w": aw, "h": ah,
                "orientation": aorient,
                "fragment_id": fid,
            }

    return result, node_map


def _route_process_edges(pid_edges, node_map):
    """
    Route process edges using Stage 6 logic verbatim.
    Intra-fragment → 2-pt straight; stitch edges → 4-pt L-shaped.
    """
    result = []
    for pe in pid_edges:
        src, dst = pe["from"], pe["to"]
        sg = node_map[src]
        tg = node_map[dst]
        is_stitch = pe.get("stitch", False)

        src_vert = sg["fragment_id"].startswith("efrag_")
        dst_vert = tg["fragment_id"].startswith("efrag_")

        from_x = sg["x"] + sg["w"] / 2 if src_vert else sg["x"] + sg["w"]
        from_y = sg["y"] + sg["h"]     if src_vert else sg["y"] + sg["h"] / 2
        to_x   = tg["x"] + tg["w"] / 2 if dst_vert else tg["x"]
        to_y   = tg["y"]               if dst_vert else tg["y"] + tg["h"] / 2

        if is_stitch:
            mid_x = (from_x + to_x) / 2
            path = [
                {"x": from_x, "y": from_y},
                {"x": mid_x,  "y": from_y},
                {"x": mid_x,  "y": to_y},
                {"x": to_x,   "y": to_y},
            ]
        else:
            path = [{"x": from_x, "y": from_y}, {"x": to_x, "y": to_y}]

        result.append({
            "edge_id": pe["edge_id"],
            "from": src,
            "to": dst,
            "kind": "process",
            "style": "solid",
            "line_id": pe.get("line_id"),
            "stitch": is_stitch,
            "path": path,
        })

    return result


def _route_expansion_edges(primitives, node_map):
    """
    Route expansion edges as 2-pt center-to-center paths.
    xe_0 … xe_203, preserving expansion iteration order.
    """
    STYLE_MAP = {
        "sense": "dashed", "signal": "dashed",
        "mechanical": "solid", "branch": "solid",
    }

    result = []
    xe_counter = 0

    for exp in primitives["pid_expansions"]:
        for ae in exp["added_edges"]:
            src, dst = ae["from"], ae["to"]
            sg = node_map[src]
            tg = node_map[dst]
            kind = ae["kind"]

            from_x = sg["x"] + sg["w"] / 2
            from_y = sg["y"] + sg["h"] / 2
            to_x   = tg["x"] + tg["w"] / 2
            to_y   = tg["y"] + tg["h"] / 2

            result.append({
                "edge_id": f"xe_{xe_counter}",
                "from": src,
                "to": dst,
                "kind": kind,
                "style": STYLE_MAP.get(kind, "solid"),
                "signal_type": kind,
                "path": [{"x": from_x, "y": from_y}, {"x": to_x, "y": to_y}],
            })
            xe_counter += 1

    return result


def _fid_sort_key(fid):
    if fid.startswith("rfrag_region_"):
        return (0, int(fid.split("_")[2]))
    elif fid.startswith("cfrag_"):
        return (1, int(fid.split("_")[1]))
    else:  # efrag_
        return (2, int(fid.split("_")[1]))


def _build_groups(primitives, node_map, loops):
    """Build fragment groups + loop groups."""

    # Collect all node ids per fragment (primary + added)
    frag_nodes = defaultdict(list)
    for exp in primitives["pid_expansions"]:
        fid = exp["fragment_id"]
        frag_nodes[fid].append(exp["primary_node"]["id"])
        for an in exp["added_nodes"]:
            frag_nodes[fid].append(an["id"])

    def _bbox(nids):
        xs  = [node_map[n]["x"]              for n in nids]
        ys  = [node_map[n]["y"]              for n in nids]
        x2s = [node_map[n]["x"] + node_map[n]["w"] for n in nids]
        y2s = [node_map[n]["y"] + node_map[n]["h"] for n in nids]
        x0, y0 = min(xs), min(ys)
        x1, y1 = max(x2s), max(y2s)
        return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}

    fragment_groups = []
    for fid in sorted(frag_nodes.keys(), key=_fid_sort_key):
        if fid.startswith("rfrag_"):
            gtype = "region_fragment"
        elif fid.startswith("cfrag_"):
            gtype = "connectivity_fragment"
        else:
            gtype = "equipment_cluster"
        fragment_groups.append({
            "group_id": fid,
            "group_type": gtype,
            "bounding_box": _bbox(frag_nodes[fid]),
        })

    # Build tag → node_id map for loop group bboxes
    tag_to_id = {}
    for exp in primitives["pid_expansions"]:
        pn = exp["primary_node"]
        if "tag" in pn:
            tag_to_id[pn["tag"]] = pn["id"]
        for an in exp["added_nodes"]:
            if "tag" in an:
                tag_to_id[an["tag"]] = an["id"]

    loop_groups = []
    for loop in loops:
        lid = loop["loop_id"]
        ft_tag = loop["flow_meter_tag"]
        fc_tag = loop["controller_tag"]
        fv_tag = loop["control_valve_tag"]

        member_ids = []
        for tag in (ft_tag, fc_tag, fv_tag):
            nid = tag_to_id.get(tag)
            if nid and nid in node_map:
                member_ids.append(nid)

        # Also include the actuator node for bbox coverage
        act_id = f"{loop['pfd_valve_origin']}:act"
        if act_id in node_map:
            member_ids.append(act_id)

        if not member_ids:
            continue

        loop_groups.append({
            "group_id": lid,
            "group_type": "instrument_loop",
            "loop_members": [ft_tag, fc_tag, fv_tag],
            "bounding_box": _bbox(member_ids),
        })

    return fragment_groups + loop_groups


def _verify_layout(layout_output, pfd_pos, expected_nodes, expected_edges):
    nodes  = layout_output["nodes"]
    edges  = layout_output["edges"]
    groups = layout_output["groups"]

    assert len(nodes) == expected_nodes, f"Layout node count: {len(nodes)} != {expected_nodes}"
    assert len(edges) == expected_edges, f"Layout edge count: {len(edges)} != {expected_edges}"

    # Primary node positions must match PFD lookup
    for nd in nodes:
        if not nd["is_added_node"]:
            gid = nd["global_id"]
            pos = pfd_pos.get(gid)
            if pos:
                assert nd["position"]["x"] == pos["x"], \
                    f"x mismatch for {gid}: {nd['position']['x']} vs {pos['x']}"
                assert nd["position"]["y"] == pos["y"], \
                    f"y mismatch for {gid}: {nd['position']['y']} vs {pos['y']}"

    # Every node has required keys
    for nd in nodes:
        for k in ("position", "size", "symbol", "ports"):
            assert k in nd, f"Missing '{k}' on {nd['global_id']}"

    # Primary nodes within same fragment must not overlap
    frag_primary = defaultdict(list)
    for nd in nodes:
        if not nd["is_added_node"]:
            frag_primary[nd["fragment_id"]].append(nd)

    for fid, fnodes in frag_primary.items():
        for i in range(len(fnodes)):
            for j in range(i + 1, len(fnodes)):
                a, b = fnodes[i], fnodes[j]
                ax1 = a["position"]["x"]
                ay1 = a["position"]["y"]
                ax2 = ax1 + a["size"]["width"]
                ay2 = ay1 + a["size"]["height"]
                bx1 = b["position"]["x"]
                by1 = b["position"]["y"]
                bx2 = bx1 + b["size"]["width"]
                by2 = by1 + b["size"]["height"]
                overlap = (ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1)
                assert not overlap, \
                    f"Overlap in {fid}: {a['global_id']} vs {b['global_id']}"

    # Edge path lengths
    stitch_count = 0
    for e in edges:
        eid = e["edge_id"]
        if eid.startswith("pe_"):
            if e.get("stitch"):
                assert len(e["path"]) == 4, \
                    f"Stitch edge {eid} path length != 4"
                stitch_count += 1
            else:
                assert len(e["path"]) == 2, \
                    f"Intra edge {eid} path length != 2"
        elif eid.startswith("xe_"):
            assert len(e["path"]) == 2, \
                f"Expansion edge {eid} path length != 2"

    assert len(groups) >= 1, f"Group count: {len(groups)} < 1"


# ── Phase 3: Interaction Hooks ─────────────────────────────────────────────────

def _build_node_hooks(layout_nodes, loop_member_map):
    """Build node hooks (one per layout node)."""
    result = []
    for nd in layout_nodes:
        gid   = nd["global_id"]
        ntype = nd["type"]
        fid   = nd["fragment_id"]

        fields = TOOLTIP_FIELDS.get(ntype, ["tag"])
        title  = TOOLTIP_TITLE.get(ntype, ntype)

        # Properties: fragment_id + semantic fields
        props = {"fragment_id": fid}
        sem = nd.get("semantics", {})
        for f in fields:
            if f == "fragment_id":
                continue
            if f in sem:
                props[f] = sem[f]
            elif f in nd:
                props[f] = nd[f]

        # Loop membership
        loop_id = loop_member_map.get(gid)
        if loop_id:
            props["loop_id"] = loop_id

        highlight = ["self", "connected_edges"]
        if loop_id:
            highlight.append("loop_members")

        result.append({
            "global_id": gid,
            "type": ntype,
            "fragment_id": fid,
            "properties": props,
            "hooks": {
                "on_hover": {
                    "highlight": highlight,
                    "tooltip": {"title": title, "fields": fields},
                },
                "on_click": {"select": True, "emit_event": "NODE_SELECTED"},
                "on_double_click": {"emit_event": "DRILL_DOWN_FRAGMENT"},
            },
        })
    return result


def _build_edge_hooks(layout_edges):
    """Build edge hooks (one per layout edge)."""
    result = []
    for e in layout_edges:
        eid = e["edge_id"]

        if eid.startswith("pe_"):
            is_stitch = e.get("stitch", False)
            emphasis  = "dashed_thick" if is_stitch else "normal"
        else:  # xe_
            sig_type = e.get("signal_type", e.get("kind", ""))
            emphasis  = "dashed_thin" if sig_type in ("sense", "signal") else "solid_thin"

        result.append({
            "edge_id": eid,
            "from": e["from"],
            "to":   e["to"],
            "stitch": e.get("stitch", False),
            "hooks": {
                "on_hover": {
                    "highlight": ["self"],
                    "tooltip": {"fields": ["from", "to"]},
                    "emphasis": emphasis,
                },
                "on_click": {"emit_event": "EDGE_SELECTED"},
            },
        })
    return result


def _build_loop_hooks(loops):
    """Build loop hooks (one per instrument loop)."""
    result = []
    for loop in loops:
        result.append({
            "loop_id": loop["loop_id"],
            "members": {
                "flow_meter_tag":   loop["flow_meter_tag"],
                "controller_tag":   loop["controller_tag"],
                "control_valve_tag": loop["control_valve_tag"],
            },
            "hooks": {
                "on_hover": {
                    "highlight": ["loop_members"],
                    "emphasis": "loop_highlight",
                },
                "on_click": {"select": True, "emit_event": "LOOP_SELECTED"},
                "on_context_menu": ["ISOLATE_LOOP", "SHOW_LOOP_METADATA"],
            },
        })
    return result


def _build_fragment_hooks(layout_nodes):
    """Build fragment hooks (one per unique fragment_id of primary nodes)."""
    frag_ids = sorted(
        set(nd["fragment_id"] for nd in layout_nodes if not nd["is_added_node"]),
        key=_fid_sort_key,
    )
    result = []
    for fid in frag_ids:
        if fid.startswith("rfrag_"):
            ftype = "region_fragment"
        elif fid.startswith("cfrag_"):
            ftype = "connectivity_fragment"
        else:
            ftype = "equipment_cluster"
        result.append({
            "fragment_id": fid,
            "fragment_type": ftype,
            "hooks": {
                "on_hover": {"highlight": ["fragment"]},
                "on_click": {"select": True, "emit_event": "FRAGMENT_SELECTED"},
                "on_context_menu": ["ISOLATE_FRAGMENT", "SHOW_METADATA", "HIDE_OTHERS"],
            },
        })
    return result


def _verify_hooks(hooks_output, loop_tags, expected_nodes, expected_edges):
    nh = hooks_output["node_hooks"]
    eh = hooks_output["edge_hooks"]

    assert len(nh) == expected_nodes, f"Expected {expected_nodes} node hooks, got {len(nh)}"
    assert len(eh) == expected_edges, f"Expected {expected_edges} edge hooks, got {len(eh)}"

    # All loop member tags must appear in node hook properties
    hook_node_tags = set()
    for nd in nh:
        tag = nd["properties"].get("tag") or nd["properties"].get("line_id")
        if tag:
            hook_node_tags.add(tag)
    for tag in loop_tags:
        assert tag in hook_node_tags, \
            f"Loop member tag '{tag}' not found in node hook properties"

    # Edge IDs must be unique
    hook_eids = [e["edge_id"] for e in eh]
    assert len(set(hook_eids)) == len(eh), "Duplicate edge IDs in hooks"


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Stage 9 – PID Graph Assembly + Layout + Interaction")
    ap.add_argument("--input-primitives", default="pid_primitives.json")
    ap.add_argument("--input-layout", default="pfd_layout_realized.json")
    ap.add_argument("--output-graph", default="pid_global_graph.json")
    ap.add_argument("--output-layout", default="pid_layout_realized.json")
    ap.add_argument("--output-hooks", default="pid_interaction_hooks.json")
    args = ap.parse_args()

    with open(args.input_primitives) as f:
        primitives = json.load(f)

    with open(args.input_layout) as f:
        pfd_layout = json.load(f)

    # ── Phase 1: Graph Assembly ───────────────────────────────────────────────
    nodes_dict, edges_list = _flatten_graph(primitives)

    graph_output = {
        "metadata": {
            "schema_version": "1",
            "source_file": "pid_primitives.json",
            "node_count": len(nodes_dict),
            "edge_count": len(edges_list),
            "stitch_edge_count": sum(1 for e in edges_list if e.get("stitch", False)),
            "expansion_node_count": sum(
                len(exp["added_nodes"]) for exp in primitives["pid_expansions"]
            ),
        },
        "global_graph": {
            "nodes": nodes_dict,
            "edges": edges_list,
        },
        "instrument_loops": primitives["instrument_loops"],
    }

    _verify_graph(graph_output)

    with open(args.output_graph, "w") as f:
        json.dump(graph_output, f, indent=2)

    # ── Phase 2: Layout ───────────────────────────────────────────────────────
    pfd_pos = _build_pfd_pos_lookup(pfd_layout)

    layout_nodes, node_map = _layout_nodes(primitives, pfd_pos)
    process_edges_routed   = _route_process_edges(primitives["pid_edges"], node_map)
    expansion_edges_routed = _route_expansion_edges(primitives, node_map)

    # Process edges first (pe_), then expansion edges (xe_)
    all_layout_edges = process_edges_routed + expansion_edges_routed

    groups = _build_groups(primitives, node_map, primitives["instrument_loops"])

    layout_output = {
        "metadata": {
            "schema_version": "1",
            "source_files": ["pid_primitives.json", "pfd_layout_realized.json"],
            "layout_engine": "deterministic_v2",
            "units": "mm",
            "coordinate_system": "top_left_origin",
            "total_nodes": len(layout_nodes),
            "total_edges": len(all_layout_edges),
            "groups": len(groups),
        },
        "symbols": SYMBOLS,
        "nodes": layout_nodes,
        "edges": all_layout_edges,
        "groups": groups,
    }

    _verify_layout(layout_output, pfd_pos, len(layout_nodes), len(all_layout_edges))

    with open(args.output_layout, "w") as f:
        json.dump(layout_output, f, indent=2)

    # ── Phase 3: Interaction Hooks ────────────────────────────────────────────
    loops = primitives["instrument_loops"]

    # Build tag → node_id map for loop membership
    tag_to_id = {}
    for exp in primitives["pid_expansions"]:
        pn = exp["primary_node"]
        if "tag" in pn:
            tag_to_id[pn["tag"]] = pn["id"]
        for an in exp["added_nodes"]:
            if "tag" in an:
                tag_to_id[an["tag"]] = an["id"]

    # node_id → loop_id for the 3 tagged members of each loop
    loop_member_map = {}
    for loop in loops:
        for tag in (loop["flow_meter_tag"],
                    loop["controller_tag"],
                    loop["control_valve_tag"]):
            nid = tag_to_id.get(tag)
            if nid:
                loop_member_map[nid] = loop["loop_id"]

    node_hooks     = _build_node_hooks(layout_nodes, loop_member_map)
    edge_hooks     = _build_edge_hooks(all_layout_edges)
    loop_hooks     = _build_loop_hooks(loops)
    fragment_hooks = _build_fragment_hooks(layout_nodes)

    # All loop member tags for assertion
    all_loop_tags = []
    for loop in loops:
        all_loop_tags.extend([
            loop["flow_meter_tag"],
            loop["controller_tag"],
            loop["control_valve_tag"],
        ])

    hooks_output = {
        "metadata": {
            "schema_version": "1",
            "source_files": ["pid_layout_realized.json", "pid_global_graph.json"],
            "hook_version": "2",
        },
        "events": {
            "LOOP_SELECTED":       "Instrument control loop selected",
            "NODE_SELECTED":       "Graph node selected",
            "EDGE_SELECTED":       "Graph edge selected",
            "FRAGMENT_SELECTED":   "Fragment selected",
            "DRILL_DOWN_FRAGMENT": "Drill down into fragment",
            "ISOLATE_FRAGMENT":    "Isolate fragment",
            "SHOW_METADATA":       "Show metadata",
        },
        "node_hooks":     node_hooks,
        "edge_hooks":     edge_hooks,
        "loop_hooks":     loop_hooks,
        "fragment_hooks": fragment_hooks,
    }

    _verify_hooks(hooks_output, all_loop_tags, len(layout_nodes), len(all_layout_edges))

    with open(args.output_hooks, "w") as f:
        json.dump(hooks_output, f, indent=2)

    # ── Summary ───────────────────────────────────────────────────────────────
    stitch_in_layout = sum(1 for e in all_layout_edges if e.get("stitch", False))
    frag_groups = [g for g in groups if g["group_type"] != "instrument_loop"]
    loop_groups = [g for g in groups if g["group_type"] == "instrument_loop"]

    print("All assertions passed.")
    print(f"  pid_global_graph:    {len(nodes_dict)} nodes  {len(edges_list)} edges"
          f"  ({graph_output['metadata']['stitch_edge_count']} stitch)")
    print(f"  pid_layout:          {len(layout_nodes)} nodes  {len(all_layout_edges)} edges"
          f"  {len(groups)} groups  ({len(frag_groups)} fragment / {len(loop_groups)} loop)")
    print(f"  pid_hooks:           {len(node_hooks)} node_hooks  {len(edge_hooks)} edge_hooks"
          f"  {len(fragment_hooks)} fragment_hooks  {len(loop_hooks)} loop_hooks")
    print(f"Wrote {args.output_graph}")
    print(f"Wrote {args.output_layout}")
    print(f"Wrote {args.output_hooks}")


if __name__ == "__main__":
    main()
