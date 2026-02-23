"""
Stage 5 — Global Graph Assembly and Semantic Enrichment

Reads:  pfd_layout_primitives.json  (Stage 4 output, schema_version 2)
        fragments.json               (source_regions lookup)
Writes: pfd_global_graph.json        (renderer-ready global graph, schema_version 1)

Phases:
  5.1  Assemble global graph  — namespace node IDs, collect intra-fragment edges
  5.2  Stitch fragments       — 8 cross-fragment edges via 4 connectivity fragments
  5.3  Pattern expansion      — skip (empty layout_graph)
  5.4  Flow validation        — BFS conflict check (expected: none)
  5.5  Layout hints           — one hint block per non-pattern fragment
"""

import json
from collections import defaultdict, deque

PRIMITIVES_FILE = "pfd_layout_primitives.json"
FRAGMENTS_FILE  = "fragments.json"
OUTPUT_FILE     = "pfd_global_graph.json"


# ---------------------------------------------------------------------------
# Phase 5.1
# ---------------------------------------------------------------------------

def _assemble_global_graph(primitives):
    """Namespace all node IDs; collect intra-fragment edges and interfaces."""
    global_nodes        = {}
    global_edges        = []
    fragment_interfaces = {}

    for p in primitives:
        fid   = p["fragment_id"]
        ftype = p["fragment_type"]

        if ftype == "pattern_fragment":
            continue

        layout     = p.get("layout_graph", {})
        nodes      = layout.get("nodes", [])
        edges      = layout.get("edges", [])
        interfaces = p.get("interfaces", {})

        # Namespace and store nodes
        for node in nodes:
            local_id  = node["id"]
            global_id = f"{fid}:{local_id}"
            node_data = {k: v for k, v in node.items() if k != "id"}
            node_data["fragment_id"] = fid
            global_nodes[global_id] = node_data

        # Namespace intra-fragment edges
        for edge in edges:
            global_edges.append({
                "from": f"{fid}:{edge['from']}",
                "to":   f"{fid}:{edge['to']}",
            })

        # Build fragment interface map
        if interfaces and "entry" in interfaces and "exit" in interfaces:
            fragment_interfaces[fid] = {
                "entry": f"{fid}:{interfaces['entry']}",
                "exit":  f"{fid}:{interfaces['exit']}",
            }

    return global_nodes, global_edges, fragment_interfaces


# ---------------------------------------------------------------------------
# Phase 5.2
# ---------------------------------------------------------------------------

def _stitch_fragments(primitives, fragment_interfaces, global_edges):
    """Append 8 stitch edges: src_exit→cfrag_entry and cfrag_exit→tgt_entry."""
    stitch_edges = []

    for p in primitives:
        if p["fragment_type"] != "connectivity_fragment":
            continue

        cfid = p["fragment_id"]
        cfe  = p.get("cross_fragment_edge")
        if not cfe:
            continue

        src = cfe["from_fragment"]   # e.g. "rfrag_region_0"
        tgt = cfe["to_fragment"]     # e.g. "rfrag_region_28"

        stitch_edges.append({
            "from":   fragment_interfaces[src]["exit"],
            "to":     fragment_interfaces[cfid]["entry"],
            "stitch": True,
        })
        stitch_edges.append({
            "from":   fragment_interfaces[cfid]["exit"],
            "to":     fragment_interfaces[tgt]["entry"],
            "stitch": True,
        })

    global_edges.extend(stitch_edges)
    return stitch_edges


# ---------------------------------------------------------------------------
# Phase 5.4
# ---------------------------------------------------------------------------

def _validate_flow(global_nodes, global_edges):
    """BFS: propagate flow_direction along edges; return list of conflict dicts."""
    adj       = defaultdict(list)
    in_degree = defaultdict(int)

    for edge in global_edges:
        adj[edge["from"]].append(edge["to"])
        in_degree[edge["to"]] += 1

    all_node_ids = set(global_nodes)
    sources      = [n for n in all_node_ids if in_degree[n] == 0]

    # node_dirs[n] = set of flow_direction values that propagate into node n
    node_dirs = defaultdict(set)
    visited   = set()
    queue     = deque(sources)

    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)

        fd = global_nodes[node].get("flow_direction")
        if fd:
            for succ in adj[node]:
                node_dirs[succ].add(fd)

        for succ in adj[node]:
            if succ not in visited:
                queue.append(succ)

    conflicts = [
        {"node": nid, "conflicting_directions": sorted(dirs)}
        for nid, dirs in node_dirs.items()
        if len(dirs) > 1
    ]
    return conflicts


# ---------------------------------------------------------------------------
# Phase 5.5
# ---------------------------------------------------------------------------

def _build_layout_hints(primitives, fragment_source_regions):
    """Return one layout hint dict per non-pattern fragment."""
    ORIENTATION = {
        "LINEAR_TRANSFER":     "horizontal",
        "MEASUREMENT_ONLY":    "horizontal",
        "CONTROLLED_TRANSFER": "horizontal",
        "ISOLATED_SEGMENT":    "horizontal",
        "CONNECTIVITY_VALVE":  "horizontal",
        "CONNECTIVITY_METER":  "horizontal",
        "EQUIPMENT_CLUSTER":   "vertical",
    }
    SPACING = {
        "LINEAR_TRANSFER":     "medium",
        "MEASUREMENT_ONLY":    "medium",
        "CONTROLLED_TRANSFER": "medium",
        "ISOLATED_SEGMENT":    "medium",
        "CONNECTIVITY_VALVE":  "medium",
        "CONNECTIVITY_METER":  "medium",
        "EQUIPMENT_CLUSTER":   "compact",
    }

    layout_hints = {}

    for p in primitives:
        fid      = p["fragment_id"]
        ftype    = p["fragment_type"]
        template = p.get("macro_template", "")

        if ftype == "pattern_fragment":
            continue

        orientation = ORIENTATION.get(template, "horizontal")
        spacing     = SPACING.get(template, "medium")

        if ftype == "equipment_cluster_fragment":
            group = fid
        elif ftype == "connectivity_fragment":
            cfe = p.get("cross_fragment_edge")
            if cfe:
                # "rfrag_region_0" → "region_0"
                group = cfe["from_fragment"].replace("rfrag_", "")
            else:
                src   = fragment_source_regions.get(fid, [])
                group = src[0] if src else fid
        else:
            # region_fragment
            src   = fragment_source_regions.get(fid, [])
            group = src[0] if src else fid

        layout_hints[fid] = {
            "orientation":       orientation,
            "preferred_spacing": spacing,
            "group":             group,
        }

    return layout_hints


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify(global_graph, fragment_interfaces, layout_hints, flow_analysis,
            primitives, stitch_edges):
    """Assert correctness and print node-type distribution."""
    global_nodes = global_graph["nodes"]
    global_edges = global_graph["edges"]

    # 1. Global node count
    assert len(global_nodes) == 185, (
        f"Expected 185 nodes, got {len(global_nodes)}"
    )

    # 2. Edge referential integrity
    for edge in global_edges:
        assert edge["from"] in global_nodes, (
            f"Edge 'from' not in nodes: {edge['from']!r}"
        )
        assert edge["to"] in global_nodes, (
            f"Edge 'to' not in nodes: {edge['to']!r}"
        )

    # 3. Exactly 8 stitch edges
    assert len(stitch_edges) == 8, (
        f"Expected 8 stitch edges, got {len(stitch_edges)}"
    )

    # 4. Every non-pattern fragment has a fragment_interfaces entry
    for p in primitives:
        if p["fragment_type"] == "pattern_fragment":
            continue
        fid = p["fragment_id"]
        assert fid in fragment_interfaces, (
            f"fragment_interfaces missing entry for {fid!r}"
        )

    # 5. Every non-pattern fragment has a layout_hints entry
    for p in primitives:
        if p["fragment_type"] == "pattern_fragment":
            continue
        fid = p["fragment_id"]
        assert fid in layout_hints, (
            f"layout_hints missing entry for {fid!r}"
        )

    # 6. No flow conflicts
    assert flow_analysis["conflicts"] == [], (
        f"Unexpected flow conflicts: {flow_analysis['conflicts']}"
    )

    # 7. Print node-type distribution
    dist = defaultdict(int)
    for ndata in global_nodes.values():
        t   = ndata["type"]
        s   = ndata.get("subtype")
        key = f"{t}:{s}" if s else t
        dist[key] += 1

    print("All assertions passed.")
    print("Global node-type distribution:")
    for k in sorted(dist):
        print(f"  {k}: {dist[k]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    with open(PRIMITIVES_FILE) as f:
        primitives_data = json.load(f)
    with open(FRAGMENTS_FILE) as f:
        fragments_data = json.load(f)

    primitives = primitives_data["layout_primitives"]

    # source_regions lookup (used by region and connectivity fragments)
    fragment_source_regions = {}
    for frag in fragments_data["fragments"]:
        fid = frag["fragment_id"]
        if "source_regions" in frag:
            fragment_source_regions[fid] = frag["source_regions"]

    # Phase 5.1 — assemble
    global_nodes, global_edges, fragment_interfaces = _assemble_global_graph(primitives)

    # Phase 5.2 — stitch
    stitch_edges = _stitch_fragments(primitives, fragment_interfaces, global_edges)

    # Phase 5.3 — count skipped pattern fragments
    pattern_skipped = sum(
        1 for p in primitives if p["fragment_type"] == "pattern_fragment"
    )

    # Phase 5.4 — flow validation
    conflicts    = _validate_flow(global_nodes, global_edges)
    flow_analysis = {"conflicts": conflicts}

    # Phase 5.5 — layout hints
    layout_hints = _build_layout_hints(primitives, fragment_source_regions)

    # Assemble output
    global_graph = {
        "nodes": global_nodes,
        "edges": global_edges,
    }

    output = {
        "metadata": {
            "source_file":              PRIMITIVES_FILE,
            "schema_version":           "1",
            "node_count":               len(global_nodes),
            "edge_count":               len(global_edges),
            "stitch_edge_count":        len(stitch_edges),
            "pattern_fragments_skipped": pattern_skipped,
        },
        "global_graph":        global_graph,
        "fragment_interfaces": fragment_interfaces,
        "layout_hints":        layout_hints,
        "flow_analysis":       flow_analysis,
    }

    # Verify before writing
    _verify(global_graph, fragment_interfaces, layout_hints, flow_analysis,
            primitives, stitch_edges)

    print(f"Stitch edges added: {len(stitch_edges)}")
    print(f"Flow conflicts: {len(conflicts)}")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(global_nodes)}-node global graph to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
