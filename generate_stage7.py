"""
Stage 7: Interactive PFD Viewer Hooks
Builds pfd_interaction_hooks.json — a declarative UI contract for any viewer.
Inputs:  pfd_layout_realized.json, pfd_global_graph.json
Output:  pfd_interaction_hooks.json
"""

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOLTIP_TITLE = {
    "pipe_segment":        "Pipe Segment",
    "equipment_block":     "Equipment",
    "valve:isolation":     "Isolation Valve",
    "valve:control":       "Control Valve",
    "instrument:flow_meter": "Flow Meter",
    "tank":                "Tank",
    "pump":                "Pump",
    "inlet_outlet":        "Inlet / Outlet",
}

TOOLTIP_FIELDS = {
    "pipe_segment":        ["fragment_id", "flow_direction"],
    "equipment_block":     ["fragment_id"],
    "valve:isolation":     ["fragment_id", "normally_open", "fail_position"],
    "valve:control":       ["fragment_id", "control_loop"],
    "instrument:flow_meter": ["fragment_id", "measurement"],
    "tank":                ["fragment_id", "contents", "capacity"],
    "pump":                ["fragment_id", "driver", "flow_direction"],
    "inlet_outlet":        ["fragment_id", "flow_direction"],
}

SEMANTIC_KEYS = [
    "flow_direction", "normally_open", "fail_position",
    "control_loop", "measurement",
]

EVENTS = [
    "NODE_SELECTED", "EDGE_SELECTED", "FRAGMENT_SELECTED",
    "DRILL_DOWN_FRAGMENT", "ISOLATE_FRAGMENT", "SHOW_METADATA",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ntype(nd_layout: dict) -> str:
    """Return the compound node type string from a layout node.

    The layout file already stores the compound form (e.g. 'valve:isolation'),
    so we just return it directly.
    """
    return nd_layout["type"]


def _semantic_props(gid: str, nd_layout: dict, nd_semantic: dict) -> dict:
    """Build the properties dict embedded in a node hook."""
    ntype = _ntype(nd_layout)
    fields = TOOLTIP_FIELDS.get(ntype, ["fragment_id"])

    # fragment_id comes from the layout node (always present)
    fid = gid.split(":")[0]

    props: dict = {"fragment_id": fid}
    for key in fields:
        if key == "fragment_id":
            continue  # already added
        val = nd_semantic.get(key)
        if val is not None:
            props[key] = val

    return props


def _node_hook(gid: str, nd_layout: dict, nd_semantic: dict) -> dict:
    ntype = _ntype(nd_layout)
    fid = gid.split(":")[0]
    props = _semantic_props(gid, nd_layout, nd_semantic)
    fields = TOOLTIP_FIELDS.get(ntype, ["fragment_id"])
    title = TOOLTIP_TITLE.get(ntype, ntype)

    return {
        "global_id": gid,
        "type": ntype,
        "fragment_id": fid,
        "properties": props,
        "hooks": {
            "on_hover": {
                "highlight": ["self", "connected_edges"],
                "tooltip": {
                    "title": title,
                    "fields": fields,
                },
            },
            "on_click": {"select": True, "emit_event": "NODE_SELECTED"},
            "on_double_click": {"emit_event": "DRILL_DOWN_FRAGMENT"},
        },
    }


def _edge_hook(idx: int, edge: dict) -> dict:
    """Build an edge hook entry.

    Stitch edges are detected by path length == 4 (two intermediate waypoints
    added during stitching); intra-fragment edges have path length == 2.
    """
    is_stitch = len(edge.get("path", [])) == 4
    emphasis = "dashed_thick" if is_stitch else "normal"

    return {
        "edge_id": f"e_{idx}",
        "from": edge["from"],
        "to": edge["to"],
        "stitch": is_stitch,
        "hooks": {
            "on_hover": {
                "highlight": ["self"],
                "tooltip": {"fields": ["from", "to"]},
                "emphasis": emphasis,
            },
            "on_click": {"emit_event": "EDGE_SELECTED"},
        },
    }


def _fragment_hook(fid: str) -> dict:
    if fid.startswith("rfrag_"):
        ftype = "region_fragment"
    elif fid.startswith("cfrag_"):
        ftype = "connectivity_fragment"
    else:
        ftype = "equipment_cluster"

    return {
        "fragment_id": fid,
        "fragment_type": ftype,
        "hooks": {
            "on_hover": {"highlight": ["fragment"]},
            "on_click": {"select": True, "emit_event": "FRAGMENT_SELECTED"},
            "on_context_menu": ["ISOLATE_FRAGMENT", "SHOW_METADATA", "HIDE_OTHERS"],
        },
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify(output: dict, layout_nodes: list, layout_edges: list) -> None:
    nh = output["node_hooks"]
    eh = output["edge_hooks"]
    fh = output["fragment_hooks"]

    assert len(nh) == len(layout_nodes), f"Expected {len(layout_nodes)} node hooks, got {len(nh)}"
    assert len(eh) == len(layout_edges), f"Expected {len(layout_edges)} edge hooks, got {len(eh)}"
    groups = sorted(set(n["global_id"].split(":")[0] for n in layout_nodes))
    assert len(fh) == len(groups), f"Expected {len(groups)} fragment hooks, got {len(fh)}"

    # All global_ids match
    layout_gids = {n["global_id"] for n in layout_nodes}
    hook_gids   = {n["global_id"] for n in nh}
    assert layout_gids == hook_gids, "global_id mismatch between layout nodes and node_hooks"

    # Edge IDs unique and sequential
    edge_ids = [e["edge_id"] for e in eh]
    assert len(set(edge_ids)) == len(eh), "Duplicate edge IDs"

    # Fragment IDs match the 68 groups derived from layout nodes
    layout_fids = sorted(set(n["global_id"].split(":")[0] for n in layout_nodes))
    hook_fids   = sorted(fh_["fragment_id"] for fh_ in fh)
    assert layout_fids == hook_fids, "Fragment ID mismatch"

    # Stitch edge count
    stitch_count = sum(1 for e in eh if e["stitch"])

    # Count fragment types
    rfrag_count = sum(1 for f in fh if f["fragment_type"] == "region_fragment")
    cfrag_count = sum(1 for f in fh if f["fragment_type"] == "connectivity_fragment")
    efrag_count = sum(1 for f in fh if f["fragment_type"] == "equipment_cluster")

    print("All assertions passed.")
    print(f"  Node hooks:     {len(nh)}")
    print(f"  Edge hooks:     {len(eh)}  ({stitch_count} stitch / {len(eh) - stitch_count} intra)")
    print(f"  Fragment hooks: {len(fh)}   ({rfrag_count} region / {cfrag_count} connectivity / {efrag_count} equipment)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 7: Interactive PFD Viewer Hooks")
    parser.add_argument("--input-layout", default="pfd_layout_realized.json")
    parser.add_argument("--input-graph", default="pfd_global_graph.json")
    parser.add_argument("--output", default="pfd_interaction_hooks.json")
    args = parser.parse_args()

    with open(args.input_layout) as f:
        layout = json.load(f)

    with open(args.input_graph) as f:
        global_graph = json.load(f)

    layout_nodes: list = layout["nodes"]
    layout_edges: list = layout["edges"]
    semantic_nodes: dict = global_graph["global_graph"]["nodes"]

    # Build node hooks
    node_hooks = []
    for nd in layout_nodes:
        gid = nd["global_id"]
        nd_sem = semantic_nodes.get(gid, {})
        node_hooks.append(_node_hook(gid, nd, nd_sem))

    # Build edge hooks
    edge_hooks = [_edge_hook(i, e) for i, e in enumerate(layout_edges)]

    # Build fragment hooks (stable sorted order)
    frag_ids = sorted(set(nd["global_id"].split(":")[0] for nd in layout_nodes))
    fragment_hooks = [_fragment_hook(fid) for fid in frag_ids]

    output = {
        "metadata": {
            "source_files": ["pfd_layout_realized.json", "pfd_global_graph.json"],
            "schema_version": "1",
            "hook_version": "1",
        },
        "events": EVENTS,
        "node_hooks": node_hooks,
        "edge_hooks": edge_hooks,
        "fragment_hooks": fragment_hooks,
    }

    _verify(output, layout_nodes, layout_edges)

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
