"""
Stage 3A — Canonical Text Realization (deterministic)
Reads fragments.json, writes canonical_descriptions.json.
No external dependencies.
"""

import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Natural-language helpers
# ---------------------------------------------------------------------------

def _ordinal_word(n: int) -> str:
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    return words.get(n, str(n))


def ctrl_str(valve_count: int, instr_count: int) -> str:
    """Return a natural-language description of control elements."""
    parts = []
    if valve_count > 0:
        v = _ordinal_word(valve_count)
        noun = "control valve" if valve_count == 1 else "control valves"
        parts.append(f"{v} {noun}")
    if instr_count > 0:
        i = _ordinal_word(instr_count)
        noun = "instrumentation point" if instr_count == 1 else "instrumentation points"
        parts.append(f"{i} {noun}")
    if len(parts) == 0:
        return "no control elements"
    if len(parts) == 1:
        return parts[0]
    return " and ".join(parts)


def _pluralise(n: int, singular: str, plural: str | None = None) -> str:
    if plural is None:
        plural = singular + "s"
    return singular if n == 1 else plural


# ---------------------------------------------------------------------------
# Per-type/subtype template functions
# ---------------------------------------------------------------------------

def _describe_region(frag: dict) -> str:
    subtype = frag["fragment_subtype"]
    cs = frag["control_semantics"]
    ea = frag["equipment_anchors"]
    N = ea["equipment_count"]
    V = cs["valve_count"]
    I = cs["instrumentation_count"]
    B = cs["boundary_count"]

    if subtype == "controlled_transfer":
        boundary_macros = cs["control_sequence"]
        # Determine unique boundary types
        unique_types = list(dict.fromkeys(boundary_macros))  # preserve order, deduplicate
        if len(unique_types) >= 2:
            c_str = ctrl_str(V, I)
        else:
            c_str = ctrl_str(V, I)
        item_word = _pluralise(N, "item")
        elem_word = _pluralise(B, "element")
        return (
            f"A fully bounded pipe segment containing {N} process equipment {item_word}. "
            f"The segment is enclosed by {B} control {elem_word}: {c_str}, "
            f"preventing unrestricted flow in either direction."
        )

    elif subtype == "measurement_only":
        item_word = _pluralise(N, "item")
        pt_word = _pluralise(I, "point")
        return (
            f"A monitored pipe segment containing {N} process equipment {item_word}, "
            f"fitted with {I} instrumentation {pt_word}. "
            f"No valve control is present; flow is measured but not regulated."
        )

    elif subtype == "linear_transfer":
        item_word = _pluralise(N, "item")
        valve_word = _pluralise(V, "valve")
        return (
            f"A pipe segment containing {N} process equipment {item_word}, "
            f"with {V} isolating {valve_word} on its boundary. "
            f"Flow passes through without full enclosure."
        )

    elif subtype == "isolated_segment":
        item_word = _pluralise(N, "item")
        return (
            f"An open pipe segment containing {N} process equipment {item_word}, "
            f"with no control or instrumentation elements on its boundary."
        )

    else:
        return f"A pipe segment of subtype '{subtype}'."


def _describe_connectivity(frag: dict) -> str:
    cs = frag["control_semantics"]
    rl = frag["region_linkage"]
    roles = cs["bridge_control_roles"]
    role = roles[0] if roles else "unknown"
    role_label = "control valve" if role == "valve" else "instrumentation point"
    article = "An" if role_label[0] in "aeiouAEIOU" else "A"
    from_r = rl["from_region"]
    to_r = rl["to_region"]
    return (
        f"{article} {role_label} connects two process regions ({from_r} and {to_r}) "
        f"via a single, non-redundant path."
    )


def _describe_equipment_cluster(frag: dict) -> str:
    S = frag["cluster_size"]
    roles = frag["node_roles"]
    is_cross = frag["is_cross_region"]
    spanning = frag["spanning_regions"]
    R = len(spanning)

    # Build roles_str
    role_counts: dict[str, int] = {}
    for r in roles:
        role_counts[r] = role_counts.get(r, 0) + 1
    role_parts = []
    for role, count in role_counts.items():
        if role == "general":
            label = "general equipment item" if count == 1 else f"{count} general equipment items"
        elif role == "instrumentation":
            label = "instrumentation point" if count == 1 else f"{count} instrumentation points"
        else:
            label = role if count == 1 else f"{count} {role}s"
        role_parts.append(label)
    roles_str = ", ".join(role_parts)

    if is_cross:
        item_word = _pluralise(S, "item")
        region_word = _pluralise(R, "region")
        return (
            f"A cross-region equipment cluster of {S} interconnected {item_word} ({roles_str}), "
            f"spanning {R} adjacent process {region_word}."
        )
    else:
        if S == 1:
            return "A standalone process equipment item within a single process region."
        item_word = _pluralise(S, "item")
        return (
            f"A cluster of {S} interconnected equipment {item_word} ({roles_str}) "
            f"within a single process region."
        )


def _hash_to_human_topology(h: str) -> str:
    """Convert topology_hash string to human description."""
    mapping = {
        "(0,)": "single-node",
        "(1, 1)": "two-node",
    }
    return mapping.get(h, h)


def _hash_to_human_valve_seq(h: str) -> str:
    """Convert valve_sequence_hash string to human description."""
    mapping = {
        "('valve',)": "single valve",
        "('instrumentation',)": "single instrumentation point",
        "('instrumentation', 'valve')": "instrumentation followed by valve",
        "('valve', 'valve')": "two valves",
        "()": "no control elements",
    }
    return mapping.get(h, h)


def _describe_pattern(frag: dict) -> str:
    kind = frag["pattern_kind"]
    h = frag["pattern_hash"]
    N = frag["instance_count"]
    region_word = _pluralise(N, "region")

    if kind == "topology":
        node_desc = _hash_to_human_topology(h)
        # Extract node_count from hash for template
        try:
            tup = eval(h)  # safe: only contains ints/tuples
            node_count = len(tup) + tup.count(1) if isinstance(tup, tuple) else 1
        except Exception:
            node_count = "unknown"
        return (
            f"A recurring structural topology pattern present in {N} {region_word}, "
            f"characterized by a {node_desc} internal structure."
        )

    elif kind == "valve_sequence":
        human_hash = _hash_to_human_valve_seq(h)
        return (
            f"A recurring valve-sequence pattern ({human_hash}) shared by {N} {region_word}."
        )

    else:
        return f"A recurring pattern of kind '{kind}' with hash {h}."


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def describe_fragment(frag: dict) -> str:
    ftype = frag["fragment_type"]
    if ftype == "region_fragment":
        return _describe_region(frag)
    elif ftype == "connectivity_fragment":
        return _describe_connectivity(frag)
    elif ftype == "equipment_cluster_fragment":
        return _describe_equipment_cluster(frag)
    elif ftype == "pattern_fragment":
        return _describe_pattern(frag)
    else:
        return f"Unknown fragment type: {ftype}."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    src = Path("fragments.json")
    dst = Path("canonical_descriptions.json")

    with open(src) as f:
        data = json.load(f)

    fragments = data["fragments"]
    total = len(fragments)

    descriptions = []
    for frag in fragments:
        fid = frag["fragment_id"]
        ftype = frag["fragment_type"]
        fsubtype = frag.get("fragment_subtype") or frag.get("pattern_kind")
        text = describe_fragment(frag)

        entry = {
            "fragment_id": fid,
            "fragment_type": ftype,
            "fragment_subtype": fsubtype,
            "canonical_text": text,
        }
        descriptions.append(entry)

    output = {
        "metadata": {
            "source_file": "fragments.json",
            "total_fragments": total,
            "generation_method": "deterministic",
        },
        "descriptions": descriptions,
    }

    with open(dst, "w") as f:
        json.dump(output, f, indent=2)

    # Verification
    assert len(descriptions) == total, f"Expected {total} descriptions, got {len(descriptions)}"
    ids_seen = {d["fragment_id"] for d in descriptions}
    ids_expected = {frag["fragment_id"] for frag in fragments}
    missing = ids_expected - ids_seen
    assert not missing, f"Missing fragment IDs: {missing}"
    empty = [d["fragment_id"] for d in descriptions if not d["canonical_text"].strip()]
    assert not empty, f"Empty canonical_text for: {empty}"

    print(f"[3A] Wrote {dst} — {total} descriptions, all verified.")

    # Summary by type
    from collections import Counter
    type_counts = Counter(d["fragment_type"] for d in descriptions)
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
