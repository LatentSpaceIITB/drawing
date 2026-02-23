"""
Stage 2: Fragment Schema Definition & Extraction
Reads ground_truth.json, emits fragments.json.
No LLM — all classification is deterministic.
"""

import json
from collections import defaultdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def node_type(node_id: str) -> str:
    """Extract type prefix from a node ID string (e.g. 'valve269' -> 'valve')."""
    for prefix in ("instrumentation", "general", "valve", "arrow"):
        if node_id.startswith(prefix):
            return prefix
    raise ValueError(f"Unknown node type for id: {node_id!r}")


def load_ground_truth(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Region fragments
# ---------------------------------------------------------------------------

def classify_region_subtype(region: dict, valve_bounded_set: set) -> str:
    """Deterministic subtype, in priority order."""
    rid = region["id"]
    if rid in valve_bounded_set:
        return "controlled_transfer"
    be_types = {b["type"] for b in region["boundary_elements"]}
    if not be_types:
        return "isolated_segment"
    if "valve" not in be_types:
        # only instrumentation (or other non-valve) boundary
        return "measurement_only"
    return "linear_transfer"


def build_region_fragment(region: dict, gt: dict,
                          topo_hash_to_gid: dict,
                          vseq_hash_to_gid: dict,
                          valve_bounded_set: set) -> dict:
    rid = region["id"]
    subtype = classify_region_subtype(region, valve_bounded_set)

    # ---- topology ----
    topo = {
        "internal_node_count": len(region["nodes"]),
        "junction_count": region["junction_count"],
        "junction_density": region["junction_density"],
        "is_cyclic": False,           # 0 cycles globally
        "path_redundancy": 1,
    }

    # ---- control semantics ----
    be = region["boundary_elements"]
    be_types = [b["type"] for b in be]
    entry_controls = [node_type(v) for v in region["entry_valves"]]
    exit_controls  = [node_type(v) for v in region["exit_valves"]]
    control_sequence = sorted(set(be_types))  # sorted unique types

    ctrl = {
        "valve_bounded":          region["valve_bounded"],
        "valve_touched":          region["valve_touched"],
        "boundary_count":         len(be),
        "valve_count":            sum(1 for t in be_types if t == "valve"),
        "instrumentation_count":  sum(1 for t in be_types if t == "instrumentation"),
        "control_sequence":       control_sequence,
        "entry_controls":         entry_controls,
        "exit_controls":          exit_controls,
    }

    # ---- equipment anchors ----
    eq_data   = gt["equipment_anchors"]["equipment_per_region"].get(rid, {})
    eq_nodes  = eq_data.get("equipment_nodes", [])
    eq_adj    = eq_data.get("adjacency", [])
    eq_roles  = [node_type(en["node_id"]) for en in eq_nodes]
    equip = {
        "has_equipment":            len(eq_nodes) > 0,
        "equipment_count":          len(eq_nodes),
        "equipment_roles":          eq_roles,
        "internal_adjacency_count": len(eq_adj),
    }

    # ---- pattern signature ----
    # Find which topology group this region belongs to
    topo_hash = None
    for pg in gt["repetition_patterns"]["pattern_groups"]:
        if rid in pg["regions"]:
            topo_hash = pg["pattern_hash"]
            break

    vseq_hash = None
    for vp in gt["repetition_patterns"]["valve_sequence_patterns"]:
        if rid in vp["regions"]:
            vseq_hash = vp["valve_sequence"]
            break

    pattern_sig = {
        "topology_hash":      topo_hash,
        "valve_sequence_hash": vseq_hash,
        "topology_group_id":  topo_hash_to_gid.get(topo_hash),
        "valve_seq_group_id": vseq_hash_to_gid.get(vseq_hash),
    }

    # ---- region linkage ----
    # Boundary elements that connect to OTHER regions
    connected_regions = []
    bridge_control_roles = []
    for b in be:
        others = [r for r in b["connected_regions"] if r != rid]
        if others:
            connected_regions.extend(others)
            bridge_control_roles.append(b["type"])

    linkage = {
        "connected_regions":    sorted(set(connected_regions)),
        "bridge_control_roles": bridge_control_roles,
    }

    return {
        "fragment_id":      f"rfrag_{rid}",
        "fragment_type":    "region_fragment",
        "fragment_subtype": subtype,
        "source_regions":   [rid],
        "topology":         topo,
        "control_semantics": ctrl,
        "equipment_anchors": equip,
        "pattern_signature": pattern_sig,
        "region_linkage":   linkage,
    }


# ---------------------------------------------------------------------------
# Connectivity fragments
# ---------------------------------------------------------------------------

def build_connectivity_fragments(gt: dict) -> list:
    """One fragment per disjoint-path entry."""
    # Build a lookup: frozenset({r1,r2}) -> list of bridge boundary elements
    regions = gt["topology"]["regions"]
    bridge_map = defaultdict(list)  # frozenset -> [(type, node_id)]
    for region in regions:
        for b in region["boundary_elements"]:
            if len(b["connected_regions"]) >= 2:
                key = frozenset(b["connected_regions"])
                bridge_map[key].append(b["type"])

    fragments = []
    for idx, dp in enumerate(gt["path_analysis"]["disjoint_paths"]):
        fr, tr = dp["from_region"], dp["to_region"]
        pc     = dp["path_count"]
        key    = frozenset([fr, tr])
        bridge_types = list(dict.fromkeys(bridge_map.get(key, [])))  # deduplicated, ordered

        fragments.append({
            "fragment_id":    f"cfrag_{idx}",
            "fragment_type":  "connectivity_fragment",
            "source_regions": [fr, tr],
            "topology": {
                "path_count":   pc,
                "is_redundant": pc > 1,
            },
            "control_semantics": {
                "bridge_control_roles": bridge_types,
                "bridge_count":         len(bridge_types),
            },
            "region_linkage": {
                "from_region": fr,
                "to_region":   tr,
            },
        })
    return fragments


# ---------------------------------------------------------------------------
# Equipment cluster fragments
# ---------------------------------------------------------------------------

def build_equipment_cluster_fragments(gt: dict) -> list:
    """One fragment per connected component of global_equipment_graph."""
    edges = gt["equipment_anchors"]["global_equipment_graph"]
    eq_per_region = gt["equipment_anchors"]["equipment_per_region"]

    # Collect all equipment nodes (including instrumentation in global graph)
    all_nodes: set = set()
    for region_id, data in eq_per_region.items():
        for en in data["equipment_nodes"]:
            all_nodes.add(en["node_id"])
    for e in edges:
        all_nodes.add(e["source"])
        all_nodes.add(e["target"])

    # node -> region (only for nodes that appear in equipment_per_region)
    node_to_region: dict = {}
    for region_id, data in eq_per_region.items():
        for en in data["equipment_nodes"]:
            node_to_region[en["node_id"]] = region_id

    # Union-Find
    parent = {n: n for n in all_nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in edges:
        union(e["source"], e["target"])

    # Group nodes by component root; count adjacency edges per component
    comp_nodes: dict = defaultdict(list)
    for n in all_nodes:
        comp_nodes[find(n)].append(n)

    comp_adj: dict = defaultdict(int)
    for e in edges:
        comp_adj[find(e["source"])] += 1

    # Sort components for stable ordering (by smallest node id lexicographically)
    sorted_comps = sorted(comp_nodes.items(), key=lambda kv: sorted(kv[1])[0])

    fragments = []
    for idx, (root, nodes) in enumerate(sorted_comps):
        node_roles = sorted([node_type(n) for n in nodes])
        spanning = sorted({node_to_region[n] for n in nodes if n in node_to_region})
        fragments.append({
            "fragment_id":    f"efrag_{idx}",
            "fragment_type":  "equipment_cluster_fragment",
            "cluster_size":   len(nodes),
            "node_roles":     node_roles,
            "adjacency_count": comp_adj[root],
            "spanning_regions": spanning,
            "is_cross_region":  len(spanning) > 1,
            "equipment_refs":  sorted(nodes),
        })
    return fragments


# ---------------------------------------------------------------------------
# Cycle fragments (0 instances for current data — schema only)
# ---------------------------------------------------------------------------

def build_cycle_fragments(gt: dict) -> list:
    fragments = []
    for idx, cycle in enumerate(gt["cycle_analysis"]["cycles"]):
        # cycle structure TBD when data has cycles
        fragments.append({
            "fragment_id":              f"lfrag_{idx}",
            "fragment_type":            "cycle_fragment",
            "source_regions":           cycle.get("regions", []),
            "cycle_length":             cycle.get("length", 0),
            "control_elements_involved": cycle.get("control_types", []),
            "is_recirculation":          cycle.get("is_recirculation", False),
        })
    return fragments


# ---------------------------------------------------------------------------
# Pattern fragments
# ---------------------------------------------------------------------------

def build_pattern_fragments(gt: dict,
                             topo_hash_to_gid: dict,
                             vseq_hash_to_gid: dict) -> list:
    fragments = []

    for pg in gt["repetition_patterns"]["pattern_groups"]:
        h   = pg["pattern_hash"]
        gid = topo_hash_to_gid[h]
        fragments.append({
            "fragment_id":    f"pfrag_{gid}",
            "fragment_type":  "pattern_fragment",
            "pattern_kind":   "topology",
            "pattern_hash":   h,
            "instance_count": len(pg["regions"]),
            "source_regions": pg["regions"],
        })

    for vp in gt["repetition_patterns"]["valve_sequence_patterns"]:
        h   = vp["valve_sequence"]
        gid = vseq_hash_to_gid[h]
        fragments.append({
            "fragment_id":    f"pfrag_{gid}",
            "fragment_type":  "pattern_fragment",
            "pattern_kind":   "valve_sequence",
            "pattern_hash":   h,
            "instance_count": len(vp["regions"]),
            "source_regions": vp["regions"],
        })

    return fragments


# ---------------------------------------------------------------------------
# Assemble & write output
# ---------------------------------------------------------------------------

def assemble_output(fragments: list, source_file: str) -> dict:
    type_counts: dict = defaultdict(int)
    subtype_counts: dict = defaultdict(int)

    for frag in fragments:
        ftype = frag["fragment_type"]
        type_counts[ftype] += 1
        if ftype == "region_fragment":
            subtype_counts[frag["fragment_subtype"]] += 1

    return {
        "metadata": {
            "source_file":          source_file,
            "total_fragments":      len(fragments),
            "fragment_type_counts": dict(type_counts),
            "region_subtype_counts": dict(subtype_counts),
        },
        "fragments": fragments,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    SOURCE = "ground_truth.json"
    OUTPUT = "fragments.json"

    gt = load_ground_truth(SOURCE)

    # Build stable ID maps for pattern groups
    topo_hash_to_gid: dict = {}
    for i, pg in enumerate(gt["repetition_patterns"]["pattern_groups"]):
        topo_hash_to_gid[pg["pattern_hash"]] = f"topo_group_{i}"

    vseq_hash_to_gid: dict = {}
    for i, vp in enumerate(gt["repetition_patterns"]["valve_sequence_patterns"]):
        vseq_hash_to_gid[vp["valve_sequence"]] = f"valve_seq_{i}"

    valve_bounded_set = set(gt["control_awareness"]["valve_bounded_regions"])

    # ---- Build all fragment types ----
    region_frags = [
        build_region_fragment(r, gt, topo_hash_to_gid, vseq_hash_to_gid, valve_bounded_set)
        for r in gt["topology"]["regions"]
    ]

    connectivity_frags = build_connectivity_fragments(gt)
    equipment_frags    = build_equipment_cluster_fragments(gt)
    cycle_frags        = build_cycle_fragments(gt)
    pattern_frags      = build_pattern_fragments(gt, topo_hash_to_gid, vseq_hash_to_gid)

    all_fragments = (region_frags + connectivity_frags +
                     equipment_frags + cycle_frags + pattern_frags)

    # ---- Assertions ----
    n_region = len(region_frags)
    n_conn   = len(connectivity_frags)
    n_equip  = len(equipment_frags)
    n_cycle  = len(cycle_frags)
    n_patt   = len(pattern_frags)

    assert n_region == 34,  f"Expected 34 region fragments, got {n_region}"
    assert n_conn   == 4,   f"Expected 4 connectivity fragments, got {n_conn}"
    assert n_cycle  == 0,   f"Expected 0 cycle fragments, got {n_cycle}"
    assert n_patt   == 7,   f"Expected 7 pattern fragments (2 topo + 5 vseq), got {n_patt}"

    subtype_dist: dict = defaultdict(int)
    for f in region_frags:
        subtype_dist[f["fragment_subtype"]] += 1
    assert subtype_dist["controlled_transfer"] == 6,  f"controlled_transfer={subtype_dist['controlled_transfer']}"
    assert subtype_dist["measurement_only"]    == 5,  f"measurement_only={subtype_dist['measurement_only']}"
    assert subtype_dist["linear_transfer"]     == 17, f"linear_transfer={subtype_dist['linear_transfer']}"
    assert subtype_dist["isolated_segment"]    == 6,  f"isolated_segment={subtype_dist['isolated_segment']}"
    assert sum(subtype_dist.values()) == 34, "region subtype counts must sum to 34"

    # All 34 region IDs appear in exactly 1 region fragment
    all_rids = [gt["topology"]["regions"][i]["id"] for i in range(34)]
    frag_rids = [f["source_regions"][0] for f in region_frags]
    assert sorted(frag_rids) == sorted(all_rids), "Region ID mismatch in fragments"

    # All 4 disjoint paths have a connectivity fragment
    dp_pairs = {(d["from_region"], d["to_region"])
                for d in gt["path_analysis"]["disjoint_paths"]}
    cf_pairs = {(f["region_linkage"]["from_region"], f["region_linkage"]["to_region"])
                for f in connectivity_frags}
    assert dp_pairs == cf_pairs, f"Disjoint path mismatch: {dp_pairs} vs {cf_pairs}"

    # ---- Assemble & write ----
    output = assemble_output(all_fragments, SOURCE)

    with open(OUTPUT, "w") as fh:
        json.dump(output, fh, indent=2)

    # Reload check
    with open(OUTPUT) as fh:
        reloaded = json.load(fh)
    assert reloaded["metadata"]["total_fragments"] == len(all_fragments)

    # ---- Print summary ----
    print("=" * 56)
    print("fragments.json written successfully")
    print("=" * 56)
    print(f"{'Fragment type':<35}  {'Count':>5}")
    print("-" * 56)
    for ftype, cnt in sorted(output["metadata"]["fragment_type_counts"].items()):
        print(f"  {ftype:<33}  {cnt:>5}")
    print(f"  {'TOTAL':<33}  {output['metadata']['total_fragments']:>5}")
    print()
    print(f"{'Region subtype':<35}  {'Count':>5}")
    print("-" * 56)
    for stype, cnt in sorted(output["metadata"]["region_subtype_counts"].items()):
        print(f"  {stype:<33}  {cnt:>5}")
    print()
    print(f"Equipment cluster fragments: {n_equip}")
    print(f"  Cross-region clusters: "
          f"{sum(1 for f in equipment_frags if f['is_cross_region'])}")
    print("All assertions passed.")


if __name__ == "__main__":
    main()
