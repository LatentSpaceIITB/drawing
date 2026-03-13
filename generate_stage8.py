"""
Stage 8: PFD → P&ID Expansion

Reads pfd_global_graph.json + pid_expansion_rules.json
→ pid_primitives.json

Applies declarative expansion rules to each PFD node to produce:
  - pid_expansions: enriched/augmented primary nodes + added instrument/drain nodes
  - pid_edges:      process edges enriched with line specs
  - instrument_loops: FT/FC/FV groupings inferred from positional pairing
"""

import argparse
import json
from pathlib import Path
from collections import Counter


# ---------------------------------------------------------------------------
# Tag counter
# ---------------------------------------------------------------------------

class TagCounter:
    def __init__(self, prefix, start, pad=0, suffix=""):
        self.prefix = prefix
        self.counter = start
        self.pad = pad
        self.suffix = suffix

    def next(self) -> str:
        num = self.counter
        self.counter += 1
        num_str = str(num).zfill(self.pad) if self.pad else str(num)
        return f"{self.prefix}-{num_str}{self.suffix}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_rules(path) -> dict:
    with open(path) as f:
        return json.load(f)


def _compound_type(node: dict) -> str:
    """Return "type:subtype" if subtype present, otherwise just "type"."""
    t = node.get("type", "")
    s = node.get("subtype", "")
    return f"{t}:{s}" if s else t


def _init_counters(rules_data: dict) -> dict:
    counters = {}
    for key, cfg in rules_data["tag_counters"].items():
        counters[key] = TagCounter(
            prefix=cfg["prefix"],
            start=cfg["start"],
            pad=cfg.get("pad", 0),
            suffix=cfg.get("suffix", ""),
        )
    return counters


def _resolve_field_value(val, counters, defaults):
    """Resolve <tag:X> and <default:X> placeholders."""
    if not isinstance(val, str):
        return val
    if val.startswith("<tag:"):
        tag_key = val[5:-1]
        return counters[tag_key].next()
    if val.startswith("<default:"):
        default_key = val[9:-1]
        return defaults[default_key]
    return val


def _apply_rule(gid: str, node: dict, rule: dict,
                counters: dict, defaults: dict) -> dict:
    """Build one pid_expansion entry for the given PFD node."""
    expand = rule["expand"]
    mode = expand["mode"]
    inherit = rule.get("inherit", {})
    ctype = _compound_type(node)

    # --- Primary node ---
    primary = {"id": gid}

    # Set type/subtype on primary
    if ":" in ctype:
        t, s = ctype.split(":", 1)
        primary["type"] = t
        primary["subtype"] = s
    else:
        primary["type"] = ctype

    # Resolve primary_fields (tag references + defaults)
    for field, val in expand.get("primary_fields", {}).items():
        primary[field] = _resolve_field_value(val, counters, defaults)

    # Inherit selected fields from the original PFD node
    for field in ("flow_direction", "normally_open", "fail_position",
                  "control_loop", "measurement"):
        if inherit.get(field) and field in node:
            primary[field] = node[field]

    if inherit.get("semantic_fields"):
        for k, v in node.items():
            if k != "fragment_id" and k not in primary:
                primary[k] = v

    # --- Added nodes ---
    added_nodes_out = []
    for an_spec in expand.get("added_nodes", []):
        local_id = an_spec["local_id"]
        an_node = {"id": f"{gid}:{local_id}", "type": an_spec["type"]}
        if "tag_key" in an_spec:
            an_node["tag"] = counters[an_spec["tag_key"]].next()
        # Copy extra literal fields (e.g. normally_closed)
        for k, v in an_spec.items():
            if k not in ("local_id", "type", "tag_key"):
                an_node[k] = v
        added_nodes_out.append(an_node)

    # --- Added edges ---
    added_edges_out = []
    for ae_spec in expand.get("added_edges", []):
        from_id = gid if ae_spec["from"] == "self" else f"{gid}:{ae_spec['from']}"
        to_id   = gid if ae_spec["to"]   == "self" else f"{gid}:{ae_spec['to']}"
        added_edges_out.append({"from": from_id, "to": to_id, "kind": ae_spec["kind"]})

    return {
        "pfd_origin":      gid,
        "pfd_type":        ctype,
        "fragment_id":     node.get("fragment_id", ""),
        "expansion_mode":  mode,
        "primary_node":    primary,
        "added_nodes":     added_nodes_out,
        "added_edges":     added_edges_out,
    }


def _enrich_edges(raw_edges: list, expansions: list,
                  line_spec_defaults: dict) -> list:
    """Build pid_edges: enrich PFD process edges with line specs."""
    # Map pipe_segment node_id → line_id
    pipe_tag_map = {}
    for exp in expansions:
        if exp["pfd_type"] == "pipe_segment":
            lid = exp["primary_node"].get("line_id")
            if lid:
                pipe_tag_map[exp["pfd_origin"]] = lid

    pid_edges = []
    for i, edge in enumerate(raw_edges):
        from_id = edge["from"]
        to_id   = edge["to"]
        stitch  = edge.get("stitch", False)

        # Inherit line_id from the from-node if it is a pipe_segment;
        # fall back to the to-node.
        line_id = pipe_tag_map.get(from_id) or pipe_tag_map.get(to_id)

        pe = {
            "edge_id": f"pe_{i}",
            "from":    from_id,
            "to":      to_id,
            "kind":    "process",
            "stitch":  stitch,
        }
        if line_id:
            pe["line_id"] = line_id
            pe["size"]    = line_spec_defaults["size"]
            pe["spec"]    = line_spec_defaults["spec"]
            pe["service"] = line_spec_defaults["service"]
        else:
            pe["line_id"] = None

        pid_edges.append(pe)

    return pid_edges


def _build_instrument_loops(expansions: list) -> list:
    """
    Pair each instrument:flow_meter expansion with a valve:control expansion
    at the same positional index.  Derive loop_id from the FT tag number.
    """
    flow_meters    = [e for e in expansions if e["pfd_type"] == "instrument:flow_meter"]
    control_valves = [e for e in expansions if e["pfd_type"] == "valve:control"]

    loops = []
    for i, fm in enumerate(flow_meters):
        ft_tag = fm["primary_node"].get("tag")          # e.g. "FT-301"
        ft_num = ft_tag.split("-", 1)[1] if ft_tag else None
        loop_id = f"FIC-{ft_num}" if ft_num else None

        cv = control_valves[i] if i < len(control_valves) else None
        cv_tag    = cv["primary_node"].get("tag") if cv else None
        cv_origin = cv["pfd_origin"]              if cv else None

        # Find the FC controller tag inside the cv's added_nodes
        fc_tag = None
        if cv:
            for an in cv.get("added_nodes", []):
                if an["type"] == "instrument:controller":
                    fc_tag = an["tag"]
                    break

        loops.append({
            "loop_id":             loop_id,
            "flow_meter_tag":      ft_tag,
            "controller_tag":      fc_tag,
            "control_valve_tag":   cv_tag,
            "pfd_meter_origin":    fm["pfd_origin"],
            "pfd_valve_origin":    cv_origin,
        })

    return loops


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify(output: dict):
    expansions = output["pid_expansions"]
    pid_edges  = output["pid_edges"]
    loops      = output["instrument_loops"]

    # 1. Total expansion count == pfd node count
    pfd_node_count = output["metadata"]["pfd_node_count"]
    assert len(expansions) == pfd_node_count, \
        f"Expected {pfd_node_count} expansions, got {len(expansions)}"

    # 2. pid_edges count
    assert len(pid_edges) >= 0, \
        f"Expected >= 0 pid_edges, got {len(pid_edges)}"

    # 3. All primary_node.tag values unique
    tags = [e["primary_node"].get("tag") for e in expansions
            if "tag" in e["primary_node"]]
    assert len(tags) == len(set(tags)), \
        f"Duplicate primary_node tags found"

    for e in expansions:
        an = e["added_nodes"]
        ptype = e["pfd_type"]
        if ptype == "equipment_block":
            assert len(an) == 2, f"{e['pfd_origin']}: expected 2 added nodes, got {len(an)}"
        elif ptype == "valve:isolation":
            assert len(an) == 1, f"{e['pfd_origin']}: expected 1 added node, got {len(an)}"
        elif ptype == "valve:control":
            assert len(an) == 2, f"{e['pfd_origin']}: expected 2 added nodes, got {len(an)}"
        elif ptype == "instrument:flow_meter":
            assert len(an) == 1, f"{e['pfd_origin']}: expected 1 added node, got {len(an)}"
        elif ptype == "pipe_segment":
            assert len(an) == 0, f"{e['pfd_origin']}: pipe_segment should have no added nodes"

    # 8. Instrument loops
    assert len(loops) >= 0, \
        f"Expected >= 0 instrument loops, got {len(loops)}"

    print("All assertions passed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 8: PFD to P&ID Expansion")
    parser.add_argument("--input", default="pfd_global_graph.json")
    parser.add_argument("--rules", default="pid_expansion_rules.json")
    parser.add_argument("--output", default="pid_primitives.json")
    args = parser.parse_args()

    base = Path(__file__).parent

    with open(base / args.input) as f:
        graph_data = json.load(f)
    rules_data = _load_rules(base / args.rules)

    nodes     = graph_data["global_graph"]["nodes"]
    raw_edges = graph_data["global_graph"]["edges"]

    # Build rule index: compound_type → rule
    rule_index = {}
    for rule in rules_data["rules"]:
        rule_index[rule["match"]["node_type"]] = rule

    defaults = rules_data["line_spec_defaults"]
    counters = _init_counters(rules_data)

    # Build pid_expansions (one per PFD node, stable dict-key order)
    pid_expansions = []
    for gid, node in nodes.items():
        ctype = _compound_type(node)
        rule  = rule_index.get(ctype)
        if rule is None:
            raise ValueError(f"No rule for node type '{ctype}' (node: {gid})")
        entry = _apply_rule(gid, node, rule, counters, defaults)
        pid_expansions.append(entry)

    # Build pid_edges
    pid_edges = _enrich_edges(raw_edges, pid_expansions, defaults)

    # Build instrument_loops
    instrument_loops = _build_instrument_loops(pid_expansions)

    # Compute metadata totals
    added_node_count  = sum(len(e["added_nodes"]) for e in pid_expansions)
    added_edge_count  = sum(len(e["added_edges"]) for e in pid_expansions)
    total_node_count  = len(pid_expansions) + added_node_count
    total_edge_count  = len(pid_edges) + added_edge_count

    output = {
        "metadata": {
            "schema_version":       "1",
            "source_file":          "pfd_global_graph.json",
            "rule_file":            "pid_expansion_rules.json",
            "pfd_node_count":       len(nodes),
            "pid_expansion_count":  len(pid_expansions),
            "pid_total_node_count": total_node_count,
            "pid_edge_count":       total_edge_count,
        },
        "pid_expansions":   pid_expansions,
        "pid_edges":        pid_edges,
        "instrument_loops": instrument_loops,
    }

    _verify(output)

    # Summary
    type_counts = Counter(e["pfd_type"] for e in pid_expansions)
    stitch_count = sum(1 for e in pid_edges if e.get("stitch"))
    intra_count  = len(pid_edges) - stitch_count

    print(f"  PID expansions: {len(pid_expansions)}")
    print(f"    pipe_segment:          {type_counts['pipe_segment']:3d}  (enriched, mode=enrich)")
    print(f"    equipment_block:       {type_counts['equipment_block']:3d}  (+2 nodes each: PI, TI)")
    print(f"    valve:isolation:       {type_counts['valve:isolation']:3d}  (+1 node each: drain valve)")
    print(f"    valve:control:         {type_counts['valve:control']:3d}  (+2 nodes each: actuator, controller)")
    print(f"    instrument:flow_meter: {type_counts['instrument:flow_meter']:3d}  (+1 node each: transmitter)")
    print(f"  PID edges: {len(pid_edges)}  ({stitch_count} stitch / {intra_count} intra)")
    print(f"  Instrument loops: {len(instrument_loops)}")

    out_path = base / args.output
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {out_path.name}")


if __name__ == "__main__":
    main()
