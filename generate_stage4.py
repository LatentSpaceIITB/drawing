"""
Stage 4: PFD Layout Primitive Expansion (table-driven, schema_version 2)

Reads pfd_macros.json + fragments.json + expansion_table.json
→ pfd_layout_primitives.json

Three explicit phases per macro entry:
  Phase 1 (_apply_multiplicity) — topology expansion driven by expansion_table
  Phase 2 (_apply_semantics)    — merge semantic_defaults onto expanded nodes
  Phase 3 (identity binding)    — deferred to Stage 5
"""

import json
from pathlib import Path
from collections import Counter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRIMITIVE_TYPES = {"pipe_segment", "valve", "instrument", "equipment_block"}


# ---------------------------------------------------------------------------
# Phase 1 helper: build sequential chain from (type, subtype|None) pairs
# ---------------------------------------------------------------------------

def _build_chain(type_pairs):
    """[(type, subtype|None)] → (nodes list, edges list)."""
    nodes = []
    for i, (t, s) in enumerate(type_pairs):
        node = {"id": f"n{i}", "type": t}
        if s is not None:
            node["subtype"] = s
        nodes.append(node)
    edges = [{"from": f"n{i}", "to": f"n{i+1}"} for i in range(len(nodes) - 1)]
    return nodes, edges


# ---------------------------------------------------------------------------
# Phase 1: Multiplicity Expansion
# ---------------------------------------------------------------------------

def _apply_multiplicity(template_spec, n_equipment):
    """Phase 1: expand canonical template by count.

    Returns (nodes, edges, interfaces).
    """
    exp_type = template_spec["expansion_type"]

    if exp_type == "none":
        return [], [], {}

    if exp_type == "repeat":
        # EQUIPMENT_CLUSTER: chain of n_equipment equipment_block nodes
        types = [("equipment_block", None)] * n_equipment
        nodes, edges = _build_chain(types)
        interfaces = {"entry": "n0", "exit": f"n{n_equipment - 1}"}
        return nodes, edges, interfaces

    # expansion_type == "linear"
    types = [(n["type"], n.get("subtype")) for n in template_spec["canonical_nodes"]]
    canonical_eq_count = template_spec["equipment_canonical_count"]
    insert_idx = template_spec["equipment_insertion_index"]

    if canonical_eq_count == 0:
        # Insert n_equipment blocks at insert_idx
        # (if insert_idx is None, n_equipment will always be 0 for those templates)
        for i in range(n_equipment):
            types.insert(insert_idx + i, ("equipment_block", None))
    else:
        # Canonical has >= 1 equipment_block; adjust to match n_equipment
        eq_positions = [i for i, (t, _) in enumerate(types) if t == "equipment_block"]
        if n_equipment == 0:
            types = [(t, s) for t, s in types if t != "equipment_block"]
        elif n_equipment > canonical_eq_count:
            # Duplicate: insert extra blocks after the last existing equipment_block
            last = max(eq_positions)
            for i in range(n_equipment - canonical_eq_count):
                types.insert(last + 1 + i, ("equipment_block", None))
        # n_equipment == canonical_eq_count → no change

    nodes, edges = _build_chain(types)
    interfaces = {"entry": "n0", "exit": f"n{len(nodes) - 1}"}
    return nodes, edges, interfaces


# ---------------------------------------------------------------------------
# Phase 2: Semantic Annotation
# ---------------------------------------------------------------------------

def _apply_semantics(nodes, template_spec):
    """Phase 2: merge semantic_defaults from canonical onto each expanded node (in-place)."""
    if not nodes:
        return
    # Build lookup: (type, subtype|None) → semantic_defaults (first match wins)
    canon_lookup = {}
    for cn in template_spec.get("canonical_nodes", []):
        key = (cn["type"], cn.get("subtype"))
        if key not in canon_lookup:
            canon_lookup[key] = cn.get("semantic_defaults", {})
    for node in nodes:
        key = (node["type"], node.get("subtype"))
        defaults = canon_lookup.get(key, {})
        node.update(defaults)


# ---------------------------------------------------------------------------
# Expansion entry-points
# ---------------------------------------------------------------------------

def _expand_region(macro_entry, table):
    """Expand a region_fragment macro into layout primitives."""
    spec = table[macro_entry["macro_template"]]
    n_eq = macro_entry["parameters"]["equipment_blocks"]
    nodes, edges, ifaces = _apply_multiplicity(spec, n_eq)
    _apply_semantics(nodes, spec)
    return {
        "fragment_id": macro_entry["fragment_id"],
        "fragment_type": macro_entry["fragment_type"],
        "macro_template": macro_entry["macro_template"],
        "layout_graph": {"nodes": nodes, "edges": edges},
        "interfaces": ifaces,
    }


def _expand_connectivity(macro_entry, table, region_to_fragment):
    """Expand a connectivity_fragment macro; no equipment injection."""
    spec = table[macro_entry["macro_template"]]
    nodes, edges, ifaces = _apply_multiplicity(spec, 0)
    _apply_semantics(nodes, spec)
    params = macro_entry["parameters"]
    via = "valve" if macro_entry["macro_template"] == "CONNECTIVITY_VALVE" else "instrument"
    return {
        "fragment_id": macro_entry["fragment_id"],
        "fragment_type": macro_entry["fragment_type"],
        "macro_template": macro_entry["macro_template"],
        "layout_graph": {"nodes": nodes, "edges": edges},
        "interfaces": ifaces,
        "cross_fragment_edge": {
            "from_fragment": region_to_fragment[params["from_region"]],
            "to_fragment": region_to_fragment[params["to_region"]],
            "via": via,
        },
    }


def _expand_cluster(macro_entry, table):
    """Expand an equipment_cluster_fragment: chain of equipment_block nodes."""
    spec = table["EQUIPMENT_CLUSTER"]
    cluster_size = macro_entry["parameters"]["cluster_size"]
    nodes, edges, ifaces = _apply_multiplicity(spec, cluster_size)
    _apply_semantics(nodes, spec)
    return {
        "fragment_id": macro_entry["fragment_id"],
        "fragment_type": macro_entry["fragment_type"],
        "macro_template": macro_entry["macro_template"],
        "layout_graph": {"nodes": nodes, "edges": edges},
        "interfaces": ifaces,
    }


def _expand_pattern(macro_entry):
    """Pattern fragments are placeholders — emit empty graph."""
    return {
        "fragment_id": macro_entry["fragment_id"],
        "fragment_type": macro_entry["fragment_type"],
        "macro_template": macro_entry["macro_template"],
        "layout_graph": {"nodes": [], "edges": []},
        "interfaces": {},
        "pattern_ref": macro_entry["pattern_ref"],
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify(primitives, region_to_fragment):
    rfrag_ids = set(region_to_fragment.values())

    # 1. Exactly 75 entries
    assert len(primitives) == 75, f"Expected 75, got {len(primitives)}"

    pattern_entries = [p for p in primitives if p["fragment_type"] == "pattern_fragment"]
    non_pattern     = [p for p in primitives if p["fragment_type"] != "pattern_fragment"]
    connectivity    = [p for p in primitives if p["fragment_type"] == "connectivity_fragment"]

    # 2. Pattern entries (7): nodes == [], pattern_ref present
    assert len(pattern_entries) == 7, f"Expected 7 pattern entries, got {len(pattern_entries)}"
    for p in pattern_entries:
        assert p["layout_graph"]["nodes"] == [], f"{p['fragment_id']}: expected empty nodes"
        assert "pattern_ref" in p, f"{p['fragment_id']}: missing pattern_ref"

    # 3. Non-pattern entries (68): at least 1 node, all node.type in PRIMITIVE_TYPES
    assert len(non_pattern) == 68, f"Expected 68 non-pattern, got {len(non_pattern)}"
    for p in non_pattern:
        nodes = p["layout_graph"]["nodes"]
        assert len(nodes) >= 1, f"{p['fragment_id']}: no nodes"
        for n in nodes:
            assert n["type"] in PRIMITIVE_TYPES, (
                f"{p['fragment_id']}: unknown type '{n['type']}'"
            )

    # 4. All edges reference valid node IDs in the same graph
    for p in primitives:
        node_ids = {n["id"] for n in p["layout_graph"]["nodes"]}
        for e in p["layout_graph"]["edges"]:
            assert e["from"] in node_ids, (
                f"{p['fragment_id']}: edge from '{e['from']}' not in nodes"
            )
            assert e["to"] in node_ids, (
                f"{p['fragment_id']}: edge to '{e['to']}' not in nodes"
            )

    # 5. Connectivity entries (4): cross_fragment_edge present, valid rfrag IDs
    assert len(connectivity) == 4, f"Expected 4 connectivity entries, got {len(connectivity)}"
    for p in connectivity:
        cfe = p.get("cross_fragment_edge")
        assert cfe is not None, f"{p['fragment_id']}: missing cross_fragment_edge"
        assert cfe["from_fragment"] in rfrag_ids, (
            f"{p['fragment_id']}: from_fragment '{cfe['from_fragment']}' not a valid rfrag"
        )
        assert cfe["to_fragment"] in rfrag_ids, (
            f"{p['fragment_id']}: to_fragment '{cfe['to_fragment']}' not a valid rfrag"
        )

    # 6. Equipment cluster entries (30): node counts checked in main

    # 7. Semantic field completeness: every node of a given type carries its defaults
    for p in primitives:
        for n in p["layout_graph"]["nodes"]:
            t = n.get("type")
            s = n.get("subtype")
            if t == "pipe_segment":
                assert "flow_direction" in n, (
                    f"{p['fragment_id']} {n['id']}: pipe_segment missing flow_direction"
                )
            elif t == "valve" and s == "isolation":
                assert "normally_open" in n, (
                    f"{p['fragment_id']} {n['id']}: valve:isolation missing normally_open"
                )
                assert "fail_position" in n, (
                    f"{p['fragment_id']} {n['id']}: valve:isolation missing fail_position"
                )
            elif t == "valve" and s == "control":
                assert "control_loop" in n, (
                    f"{p['fragment_id']} {n['id']}: valve:control missing control_loop"
                )
            elif t == "instrument" and s == "flow_meter":
                assert "measurement" in n, (
                    f"{p['fragment_id']} {n['id']}: instrument:flow_meter missing measurement"
                )

    print("All assertions passed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    base = Path(__file__).parent

    with open(base / "pfd_macros.json") as f:
        macros_data = json.load(f)
    with open(base / "fragments.json") as f:
        fragments_data = json.load(f)
    with open(base / "expansion_table.json") as f:
        expansion_table_data = json.load(f)

    table = expansion_table_data["templates"]

    # Build region_id → rfrag_id from fragments.json
    region_to_fragment = {}
    for frag in fragments_data["fragments"]:
        if frag["fragment_type"] == "region_fragment":
            for region_id in frag["source_regions"]:
                region_to_fragment[region_id] = frag["fragment_id"]

    layout_primitives = []
    cluster_size_map = {}  # fragment_id → cluster_size for assertion 6

    for macro_entry in macros_data["macros"]:
        ftype = macro_entry["fragment_type"]
        if ftype == "region_fragment":
            entry = _expand_region(macro_entry, table)
        elif ftype == "connectivity_fragment":
            entry = _expand_connectivity(macro_entry, table, region_to_fragment)
        elif ftype == "equipment_cluster_fragment":
            entry = _expand_cluster(macro_entry, table)
            cluster_size_map[macro_entry["fragment_id"]] = macro_entry["parameters"]["cluster_size"]
        elif ftype == "pattern_fragment":
            entry = _expand_pattern(macro_entry)
        else:
            raise ValueError(f"Unknown fragment_type: {ftype}")
        layout_primitives.append(entry)

    # Assertion 6: equipment cluster node counts == cluster_size
    for p in layout_primitives:
        if p["fragment_type"] == "equipment_cluster_fragment":
            expected = cluster_size_map[p["fragment_id"]]
            actual = len(p["layout_graph"]["nodes"])
            assert actual == expected, (
                f"{p['fragment_id']}: cluster node count {actual} != cluster_size {expected}"
            )

    _verify(layout_primitives, region_to_fragment)

    # Node-type distribution
    type_counter = Counter()
    for p in layout_primitives:
        for n in p["layout_graph"]["nodes"]:
            key = n["type"] if "subtype" not in n else f"{n['type']}:{n['subtype']}"
            type_counter[key] += 1

    print("\nNode-type distribution across all fragments:")
    for key, count in sorted(type_counter.items()):
        print(f"  {key}: {count}")

    output = {
        "metadata": {
            "source_file": "pfd_macros.json",
            "expansion_table_file": "expansion_table.json",
            "primitive_vocabulary": sorted(PRIMITIVE_TYPES),
            "generation_method": "deterministic",
            "schema_version": "2",
        },
        "layout_primitives": layout_primitives,
    }

    out_path = base / "pfd_layout_primitives.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {len(layout_primitives)} layout primitives to {out_path.name}")


if __name__ == "__main__":
    main()
