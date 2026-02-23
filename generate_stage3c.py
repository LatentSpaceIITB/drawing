"""
Stage 3C — PFD Macro Mapping (deterministic, schema v2)
Reads fragments.json, writes pfd_macros.json.
No external dependencies.

Key invariant: macro_template uniquely determines macro_sequence.
All variable quantities are encoded in parameters or pattern_ref.
"""

import json
from collections import Counter
from pathlib import Path

VOCAB = ["PIPE_RUN", "ISOLATION_VALVE", "CONTROL_VALVE", "FLOW_METER", "EQUIPMENT_BLOCK"]

TEMPLATE_SEQUENCES = {
    "ISOLATED_SEGMENT":    ["PIPE_RUN", "PIPE_RUN"],
    "LINEAR_TRANSFER":     ["PIPE_RUN", "ISOLATION_VALVE", "PIPE_RUN"],
    "MEASUREMENT_ONLY":    ["PIPE_RUN", "FLOW_METER", "PIPE_RUN"],
    "CONTROLLED_TRANSFER": ["CONTROL_VALVE", "PIPE_RUN", "CONTROL_VALVE"],
    "EQUIPMENT_CLUSTER":   ["EQUIPMENT_BLOCK"],
    "PATTERN_REF":         [],
    # CONNECTIVITY sequences are derived from bridge role
    "CONNECTIVITY_VALVE":  ["PIPE_RUN", "ISOLATION_VALVE", "PIPE_RUN"],
    "CONNECTIVITY_METER":  ["PIPE_RUN", "FLOW_METER", "PIPE_RUN"],
}


def subtype_to_template(subtype: str) -> str:
    mapping = {
        "isolated_segment":    "ISOLATED_SEGMENT",
        "linear_transfer":     "LINEAR_TRANSFER",
        "measurement_only":    "MEASUREMENT_ONLY",
        "controlled_transfer": "CONTROLLED_TRANSFER",
    }
    return mapping[subtype]


def _region_macro(frag: dict) -> tuple[str, list[str], dict]:
    subtype = frag["fragment_subtype"]
    cs = frag["control_semantics"]
    ea = frag["equipment_anchors"]

    template = subtype_to_template(subtype)
    sequence = TEMPLATE_SEQUENCES[template]

    if subtype == "isolated_segment":
        parameters = {
            "equipment_blocks": ea["equipment_count"],
            "isolation_valves": 0,
            "control_valves": 0,
            "flow_meters": 0,
        }
    elif subtype == "linear_transfer":
        parameters = {
            "equipment_blocks": ea["equipment_count"],
            "isolation_valves": cs["valve_count"],
            "control_valves": 0,
            "flow_meters": cs["instrumentation_count"],
        }
    elif subtype == "measurement_only":
        parameters = {
            "equipment_blocks": ea["equipment_count"],
            "isolation_valves": 0,
            "control_valves": 0,
            "flow_meters": cs["instrumentation_count"],
        }
    elif subtype == "controlled_transfer":
        parameters = {
            "equipment_blocks": ea["equipment_count"],
            "isolation_valves": 0,
            "control_valves": cs["valve_count"],
            "flow_meters": cs["instrumentation_count"],
        }
    else:
        raise ValueError(f"Unknown region subtype: {subtype!r}")

    return template, sequence, parameters


def _connectivity_macro(frag: dict) -> tuple[str, list[str], dict]:
    cs = frag["control_semantics"]
    roles = cs["bridge_control_roles"]
    role = roles[0] if roles else "valve"

    if role == "instrumentation":
        template = "CONNECTIVITY_METER"
    else:
        template = "CONNECTIVITY_VALVE"

    sequence = TEMPLATE_SEQUENCES[template]
    rl = frag["region_linkage"]
    parameters = {
        "from_region": rl["from_region"],
        "to_region": rl["to_region"],
    }
    return template, sequence, parameters


def _cluster_macro(frag: dict) -> tuple[str, list[str], dict]:
    template = "EQUIPMENT_CLUSTER"
    sequence = TEMPLATE_SEQUENCES[template]
    parameters = {
        "cluster_size": frag["cluster_size"],
        "is_cross_region": frag["is_cross_region"],
    }
    return template, sequence, parameters


def _pattern_macro(frag: dict) -> tuple[str, list[str], None]:
    return "PATTERN_REF", [], None


def build_macro_entry(frag: dict) -> dict:
    fid = frag["fragment_id"]
    ftype = frag["fragment_type"]

    if ftype == "region_fragment":
        template, sequence, parameters = _region_macro(frag)
        return {
            "fragment_id": fid,
            "fragment_type": ftype,
            "macro_template": template,
            "macro_sequence": sequence,
            "parameters": parameters,
        }
    elif ftype == "connectivity_fragment":
        template, sequence, parameters = _connectivity_macro(frag)
        return {
            "fragment_id": fid,
            "fragment_type": ftype,
            "macro_template": template,
            "macro_sequence": sequence,
            "parameters": parameters,
        }
    elif ftype == "equipment_cluster_fragment":
        template, sequence, parameters = _cluster_macro(frag)
        return {
            "fragment_id": fid,
            "fragment_type": ftype,
            "macro_template": template,
            "macro_sequence": sequence,
            "parameters": parameters,
        }
    elif ftype == "pattern_fragment":
        return {
            "fragment_id": fid,
            "fragment_type": ftype,
            "macro_template": "PATTERN_REF",
            "macro_sequence": [],
            "pattern_ref": fid,
        }
    else:
        raise ValueError(f"Unknown fragment_type: {ftype!r}")


def main():
    src = Path("fragments.json")
    dst = Path("pfd_macros.json")

    with open(src) as f:
        data = json.load(f)

    fragments = data["fragments"]

    macros_list = [build_macro_entry(frag) for frag in fragments]

    output = {
        "metadata": {
            "source_file": "fragments.json",
            "macro_vocabulary": VOCAB,
            "generation_method": "deterministic",
            "schema_version": "2",
        },
        "macros": macros_list,
    }

    with open(dst, "w") as f:
        json.dump(output, f, indent=2)

    # --- Verification ---

    total = len(macros_list)
    assert total == 75, f"Expected 75 macros, got {total}"

    pattern_entries = [m for m in macros_list if m["macro_template"] == "PATTERN_REF"]
    non_pattern_entries = [m for m in macros_list if m["macro_template"] != "PATTERN_REF"]

    # Pattern fragments: empty sequence, pattern_ref present
    for m in pattern_entries:
        assert m["macro_sequence"] == [], f"{m['fragment_id']}: pattern sequence not empty"
        assert "pattern_ref" in m, f"{m['fragment_id']}: missing pattern_ref"

    # Non-pattern fragments: non-empty sequence, all tokens in VOCAB
    for m in non_pattern_entries:
        assert m["macro_sequence"], f"{m['fragment_id']}: empty macro_sequence"
        bad = [tok for tok in m["macro_sequence"] if tok not in VOCAB]
        assert not bad, f"{m['fragment_id']}: invalid tokens {bad}"

    # Key invariant: macro_template uniquely determines macro_sequence
    template_sequences: dict[str, list] = {}
    for m in macros_list:
        tmpl = m["macro_template"]
        seq = m["macro_sequence"]
        if tmpl not in template_sequences:
            template_sequences[tmpl] = seq
        else:
            assert template_sequences[tmpl] == seq, (
                f"Invariant violated for {tmpl}: "
                f"{template_sequences[tmpl]} vs {seq} in {m['fragment_id']}"
            )

    print(f"[3C] Wrote {dst} — {total} macro entries, all invariants verified.")

    # Distribution by macro_template
    print("\nTemplate distribution (macro_template → sequence × count):")
    by_template: dict[str, list] = {}
    for m in macros_list:
        by_template.setdefault(m["macro_template"], []).append(m["fragment_id"])

    for tmpl in sorted(by_template):
        fids = by_template[tmpl]
        seq = template_sequences[tmpl]
        print(f"  {tmpl} ×{len(fids):2d}  →  {seq}")


if __name__ == "__main__":
    main()
