"""
extract_features.py
Reads 0.graphml (P&ID) and writes ground_truth.json.

Steps:
  0 - Parse GraphML
  1 - Build raw NetworkX graph
  2 - Resolve crossings (collinearity)
  3 - Contract connectors
  4 - Region decomposition
  5 - Valve-bounded analysis
  6 - Disjoint path analysis
  7 - Cycle detection
  8 - Degree / junction characterization
  9 - Equipment anchors
 10 - Line-type distribution
 11 - Repetition patterns
"""

import argparse
import json
import math
import collections
import os
import xml.etree.ElementTree as ET

import networkx as nx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"

SEMANTIC_TYPES  = {"valve", "general", "instrumentation", "arrow", "inlet_outlet", "tank", "pump"}
CONTROL_TYPES   = {"valve", "instrumentation"}
EQUIPMENT_TYPES = {"general", "tank", "pump"}
PIPE_TYPES      = {"general", "arrow", "inlet_outlet"}
WIRE_TYPES      = {"connector", "crossing"}
IGNORE_TYPES    = {"background"}


# ---------------------------------------------------------------------------
# Step 0 — Parse GraphML
# ---------------------------------------------------------------------------
def parse_graphml(path: str):
    tree = ET.parse(path)
    root = tree.getroot()

    ns = {"g": GRAPHML_NS}

    # Build key map: key_id -> attr_name
    key_map = {}
    for key_el in root.findall("g:key", ns):
        key_map[key_el.get("id")] = key_el.get("attr.name")

    raw_nodes = {}
    raw_edges = []

    graph_el = root.find("g:graph", ns)
    if graph_el is None:
        raise RuntimeError("No <graph> element found in GraphML")

    for node_el in graph_el.findall("g:node", ns):
        node_id = node_el.get("id")
        attrs = {}
        for data_el in node_el.findall("g:data", ns):
            k = key_map.get(data_el.get("key"), data_el.get("key"))
            attrs[k] = data_el.text

        label = attrs.get("label", "")
        # Normalize labels with slashes
        label = label.replace("/", "_")

        # Determine bounding box
        # Integer keys d1-d4: xmin, ymin, xmax, ymax
        # Float keys  d5-d8: xmin, ymin, xmax, ymax
        bbox = None
        if "xmin" in attrs and attrs["xmin"] is not None:
            try:
                xmin = float(attrs["xmin"])
                ymin = float(attrs["ymin"])
                xmax = float(attrs["xmax"])
                ymax = float(attrs["ymax"])
                bbox = (xmin, ymin, xmax, ymax)
            except (TypeError, ValueError):
                pass

        if label in IGNORE_TYPES:
            continue  # drop background immediately

        center = None
        if bbox:
            center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)

        raw_nodes[node_id] = {
            "id": node_id,
            "label": label,
            "bbox": list(bbox) if bbox else None,
            "center": list(center) if center else None,
        }

    for edge_el in graph_el.findall("g:edge", ns):
        src = edge_el.get("source")
        tgt = edge_el.get("target")
        edge_label = "solid"
        for data_el in edge_el.findall("g:data", ns):
            k = key_map.get(data_el.get("key"), data_el.get("key"))
            if k == "edge_label" and data_el.text:
                edge_label = data_el.text.strip()
        raw_edges.append({"source": src, "target": tgt, "edge_type": edge_label})

    return raw_nodes, raw_edges


# ---------------------------------------------------------------------------
# Step 1 — Build Raw NetworkX Graph
# ---------------------------------------------------------------------------
def build_raw_graph(nodes: dict, edges: list) -> nx.Graph:
    G = nx.Graph()
    for nid, ndata in nodes.items():
        G.add_node(nid, **ndata)
    for e in edges:
        src, tgt = e["source"], e["target"]
        # Only add edge if both endpoints exist (background already dropped)
        if src in nodes and tgt in nodes:
            G.add_edge(src, tgt, edge_type=e["edge_type"])
    return G


# ---------------------------------------------------------------------------
# Step 2 — Resolve Crossings
# ---------------------------------------------------------------------------
def _angle_bucket(cx, cy, nx_, ny):
    """Return 'horizontal' or 'vertical' based on vector direction."""
    dx = nx_ - cx
    dy = ny - cy
    return "horizontal" if abs(dx) >= abs(dy) else "vertical"


def resolve_crossings(G_raw: nx.Graph) -> nx.Graph:
    G = G_raw.copy()
    crossings = [n for n, d in G.nodes(data=True) if d.get("label") == "crossing"]

    for c in crossings:
        if c not in G:
            continue
        neighbors = list(G.neighbors(c))
        cx, cy = G.nodes[c]["center"]

        if len(neighbors) != 4:
            # Edge case: log and handle gracefully
            print(f"  [WARN] crossing {c} has degree {len(neighbors)} (expected 4)")
            # For degree 2: just bridge the two neighbors
            if len(neighbors) == 2:
                n1, n2 = neighbors
                et = _merged_edge_type(G, c, n1, n2)
                if not G.has_edge(n1, n2):
                    G.add_edge(n1, n2, edge_type=et)
            G.remove_node(c)
            continue

        # Group neighbors by direction relative to crossing center
        groups = {"horizontal": [], "vertical": []}
        for nb in neighbors:
            nbx, nby = G.nodes[nb]["center"]
            bucket = _angle_bucket(cx, cy, nbx, nby)
            groups[bucket].append(nb)

        # If grouping is uneven (e.g. 3+1 or 4+0), fall back: pick closest two pairs by angle
        if len(groups["horizontal"]) != 2 or len(groups["vertical"]) != 2:
            groups = _fallback_pairing(cx, cy, neighbors, G)

        for pair in [groups["horizontal"], groups["vertical"]]:
            if len(pair) == 2:
                n1, n2 = pair
                et = _merged_edge_type(G, c, n1, n2)
                if not G.has_edge(n1, n2):
                    G.add_edge(n1, n2, edge_type=et)

        G.remove_node(c)

    return G


def _merged_edge_type(G, c, n1, n2):
    """Return 'solid' if any of the two incident edges is solid, else 'non-solid'."""
    t1 = G.edges[c, n1].get("edge_type", "solid") if G.has_edge(c, n1) else "solid"
    t2 = G.edges[c, n2].get("edge_type", "solid") if G.has_edge(c, n2) else "solid"
    return "solid" if "solid" in (t1, t2) else "non-solid"


def _fallback_pairing(cx, cy, neighbors, G):
    """Pair 4 neighbors into 2 collinear pairs by minimizing angular distance to opposite."""
    import itertools
    angles = []
    for nb in neighbors:
        nbx, nby = G.nodes[nb]["center"]
        angles.append(math.atan2(nby - cy, nbx - cx))

    # Try all 3 ways to split 4 into 2+2 and pick the one with most collinear pairs
    idx = list(range(4))
    best_score = float("inf")
    best_groups = {"horizontal": [neighbors[0], neighbors[1]], "vertical": [neighbors[2], neighbors[3]]}

    for a, b in [(0,1),(0,2),(0,3)]:
        rest = [i for i in idx if i not in (a, b)]
        c_, d_ = rest
        score = (abs(abs(angles[a] - angles[b]) - math.pi) +
                 abs(abs(angles[c_] - angles[d_]) - math.pi))
        if score < best_score:
            best_score = score
            best_groups = {
                "horizontal": [neighbors[a], neighbors[b]],
                "vertical":   [neighbors[c_], neighbors[d_]],
            }
    return best_groups


# ---------------------------------------------------------------------------
# Step 3 — Contract Connectors
# ---------------------------------------------------------------------------
def contract_connectors(G_no_cross: nx.Graph) -> nx.Graph:
    G = G_no_cross.copy()

    # Iteratively remove connectors, processing degree-2 first
    while True:
        connectors = [n for n, d in G.nodes(data=True) if d.get("label") == "connector"]
        if not connectors:
            break

        # Sort by degree (low first) to avoid O(k^2) blowup
        connectors.sort(key=lambda n: G.degree(n))

        progress = False
        for c in connectors:
            if c not in G:
                continue
            neighbors = list(G.neighbors(c))
            # Gather edge types
            edge_types = [G.edges[c, nb].get("edge_type", "solid") for nb in neighbors]
            merged_et = "solid" if "solid" in edge_types else "non-solid"

            # Connect all pairs of neighbors
            for i in range(len(neighbors)):
                for j in range(i + 1, len(neighbors)):
                    n1, n2 = neighbors[i], neighbors[j]
                    if not G.has_edge(n1, n2):
                        G.add_edge(n1, n2, edge_type=merged_et)
                    else:
                        # Promote to solid if either is solid
                        existing = G.edges[n1, n2].get("edge_type", "solid")
                        if merged_et == "solid" or existing == "solid":
                            G.edges[n1, n2]["edge_type"] = "solid"

            G.remove_node(c)
            progress = True

        if not progress:
            break  # shouldn't happen, but safety exit

    return G


# ---------------------------------------------------------------------------
# Step 4 — Region Decomposition
# ---------------------------------------------------------------------------
def decompose_regions(G_semantic: nx.Graph):
    """
    Returns (regions, region_of_node) where:
      regions = list of region dicts
      region_of_node = {node_id: region_id}
    """
    # G_pipes: pipe-like nodes (general, arrow, inlet_outlet, tank, pump)
    # Excludes control types (valve, instrumentation)
    pipe_nodes = [n for n, d in G_semantic.nodes(data=True)
                  if d.get("label") not in CONTROL_TYPES]
    G_pipes = G_semantic.subgraph(pipe_nodes).copy()

    components = list(nx.connected_components(G_pipes))

    region_of_node = {}
    regions = []

    for idx, comp in enumerate(components):
        rid = f"region_{idx}"
        for n in comp:
            region_of_node[n] = rid

    # Re-attach boundary control elements
    control_nodes = [n for n, d in G_semantic.nodes(data=True)
                     if d.get("label") in CONTROL_TYPES]

    # Build region boundary map
    region_boundary = collections.defaultdict(list)
    for cn in control_nodes:
        cn_data = G_semantic.nodes[cn]
        connected_regions = set()
        for nb in G_semantic.neighbors(cn):
            if nb in region_of_node:
                connected_regions.add(region_of_node[nb])
        for rid in connected_regions:
            region_boundary[rid].append({
                "node_id": cn,
                "type": cn_data.get("label"),
                "connected_regions": sorted(connected_regions),
            })

    for idx, comp in enumerate(components):
        rid = f"region_{idx}"
        regions.append({
            "id": rid,
            "nodes": sorted(comp),
            "boundary_elements": region_boundary.get(rid, []),
        })

    return regions, region_of_node


# ---------------------------------------------------------------------------
# Step 5 — Valve-Bounded Analysis
# ---------------------------------------------------------------------------
def valve_bounded_analysis(G_semantic: nx.Graph, regions: list, region_of_node: dict):
    """Returns control_awareness dict."""
    valve_bounded_regions = []
    region_valve_map = {}

    # Arrow nodes for direction hints
    arrow_nodes = {n for n, d in G_semantic.nodes(data=True) if d.get("label") == "arrow"}

    for region in regions:
        rid = region["id"]
        boundary = region["boundary_elements"]
        valves = [b["node_id"] for b in boundary if b["type"] == "valve"]
        instruments = [b["node_id"] for b in boundary if b["type"] == "instrumentation"]
        all_control = valves + instruments

        region_valve_map[rid] = all_control

        valve_touched = len(all_control) >= 1
        region["valve_touched"] = valve_touched

        # "valve_bounded": control nodes on ≥ 2 distinct sides
        # Proxy: ≥ 2 distinct control nodes (simplification for topology purposes)
        valve_bounded = len(all_control) >= 2
        region["valve_bounded"] = valve_bounded
        if valve_bounded:
            valve_bounded_regions.append(rid)

        # Entry/exit valves from arrow adjacency
        entry_valves, exit_valves = [], []
        region_nodes_set = set(region["nodes"])
        for cv in all_control:
            for nb in G_semantic.neighbors(cv):
                if nb in arrow_nodes:
                    # Arrow adjacent to control node → flow hint
                    arrow_nb_regions = {region_of_node[n2]
                                        for n2 in G_semantic.neighbors(nb)
                                        if n2 in region_of_node}
                    if rid in arrow_nb_regions:
                        exit_valves.append(cv)
                    else:
                        entry_valves.append(cv)

        region["entry_valves"] = list(set(entry_valves))
        region["exit_valves"]  = list(set(exit_valves))

    return {
        "valve_bounded_regions": valve_bounded_regions,
        "region_valve_map": region_valve_map,
    }


# ---------------------------------------------------------------------------
# Step 6 — Disjoint Path Analysis
# ---------------------------------------------------------------------------
def disjoint_paths(G_semantic: nx.Graph, regions: list, region_of_node: dict):
    """
    Build a region graph via control nodes, compute edge connectivity per pair.
    """
    # Build region-level graph
    G_region = nx.MultiGraph()
    region_ids = [r["id"] for r in regions]
    G_region.add_nodes_from(region_ids)

    control_nodes = [n for n, d in G_semantic.nodes(data=True)
                     if d.get("label") in CONTROL_TYPES]

    for cn in control_nodes:
        touching = set()
        for nb in G_semantic.neighbors(cn):
            if nb in region_of_node:
                touching.add(region_of_node[nb])
        touching = list(touching)
        for i in range(len(touching)):
            for j in range(i + 1, len(touching)):
                G_region.add_edge(touching[i], touching[j], via=cn)

    # Collapse to simple graph for connectivity analysis
    G_simple = nx.Graph(G_region)
    results = []
    for r1, r2 in nx.edges(G_simple):
        try:
            conn = nx.edge_connectivity(G_simple, r1, r2)
        except Exception:
            conn = 1
        results.append({"from_region": r1, "to_region": r2, "path_count": conn})

    return {"disjoint_paths": results}


# ---------------------------------------------------------------------------
# Step 7 — Cycle Detection
# ---------------------------------------------------------------------------
def detect_cycles(regions: list, region_of_node: dict, G_semantic: nx.Graph):
    G_region = nx.Graph()
    region_ids = [r["id"] for r in regions]
    G_region.add_nodes_from(region_ids)

    control_nodes = [n for n, d in G_semantic.nodes(data=True)
                     if d.get("label") in CONTROL_TYPES]

    for cn in control_nodes:
        touching = set()
        for nb in G_semantic.neighbors(cn):
            if nb in region_of_node:
                touching.add(region_of_node[nb])
        touching = list(touching)
        for i in range(len(touching)):
            for j in range(i + 1, len(touching)):
                if not G_region.has_edge(touching[i], touching[j]):
                    G_region.add_edge(touching[i], touching[j])

    cycles = nx.cycle_basis(G_region)
    return {
        "cycle_count": len(cycles),
        "cycles": [{"id": f"cycle_{i}", "regions": c} for i, c in enumerate(cycles)],
    }


# ---------------------------------------------------------------------------
# Step 8 — Degree & Junction Characterization
# ---------------------------------------------------------------------------
def degree_characterization(G_semantic: nx.Graph, regions: list):
    for region in regions:
        nodes = region["nodes"]
        sub = G_semantic.subgraph(nodes)
        degrees = [sub.degree(n) for n in nodes]
        if not degrees:
            region.update({
                "junction_count": 0, "max_degree": 0,
                "avg_degree": 0.0, "junction_density": 0.0,
            })
            continue
        junctions = sum(1 for d in degrees if d >= 3)
        region["junction_count"]   = junctions
        region["max_degree"]       = max(degrees)
        region["avg_degree"]       = round(sum(degrees) / len(degrees), 4)
        region["junction_density"] = round(junctions / len(nodes), 4)


# ---------------------------------------------------------------------------
# Step 9 — Equipment Anchors
# ---------------------------------------------------------------------------
def equipment_anchors(G_semantic: nx.Graph, regions: list, region_of_node: dict):
    equip_types = EQUIPMENT_TYPES | {"instrumentation"}

    per_region = {}
    global_equip_edges = []

    all_equip_nodes = {n for n, d in G_semantic.nodes(data=True) if d.get("label") in equip_types}

    # Per-region equipment
    for region in regions:
        rid = region["id"]
        equip_in_region = [n for n in region["nodes"] if n in all_equip_nodes]
        equip_details = []
        for n in equip_in_region:
            nd = G_semantic.nodes[n]
            equip_details.append({
                "node_id": n,
                "type": nd.get("label"),
                "center": nd.get("center"),
                "bbox":   nd.get("bbox"),
            })
        region["equipment_nodes"] = equip_details

        # Equipment adjacency within region
        adj = []
        sub = G_semantic.subgraph(region["nodes"])
        for n in equip_in_region:
            for nb in sub.neighbors(n):
                if nb in all_equip_nodes and n < nb:  # avoid duplicates
                    adj.append({"source": n, "target": nb,
                                "edge_type": sub.edges[n, nb].get("edge_type", "solid")})
        per_region[rid] = {"equipment_nodes": equip_details, "adjacency": adj}

    # Global equipment graph (cross-region + intra-region)
    for n in all_equip_nodes:
        for nb in G_semantic.neighbors(n):
            if nb in all_equip_nodes and n < nb:
                global_equip_edges.append({
                    "source": n, "target": nb,
                    "edge_type": G_semantic.edges[n, nb].get("edge_type", "solid"),
                })

    return {
        "global_equipment_graph": global_equip_edges,
        "equipment_per_region": per_region,
    }


# ---------------------------------------------------------------------------
# Step 10 — Line-Type Distribution
# ---------------------------------------------------------------------------
def line_type_distribution(G_semantic: nx.Graph, regions: list):
    global_hist = {"solid": 0, "non_solid": 0}
    per_region = {}

    for _, _, edata in G_semantic.edges(data=True):
        et = edata.get("edge_type", "solid")
        key = "non_solid" if "non" in et else "solid"
        global_hist[key] += 1

    for region in regions:
        rid = region["id"]
        hist = {"solid": 0, "non_solid": 0}
        sub = G_semantic.subgraph(region["nodes"])
        for _, _, edata in sub.edges(data=True):
            et = edata.get("edge_type", "solid")
            key = "non_solid" if "non" in et else "solid"
            hist[key] += 1
        region["edge_type_histogram"] = hist
        region["signal_dominated"]    = hist["non_solid"] > hist["solid"]
        per_region[rid] = hist

    return {"global": global_hist, "per_region": per_region}


# ---------------------------------------------------------------------------
# Step 11 — Repetition Patterns
# ---------------------------------------------------------------------------
def find_repetition_patterns(G_semantic: nx.Graph, regions: list):
    hash_to_regions = collections.defaultdict(list)

    for region in regions:
        nodes = region["nodes"]
        sub = G_semantic.subgraph(nodes)
        degree_seq = tuple(sorted(sub.degree(n) for n in nodes))
        h = str(degree_seq)
        hash_to_regions[h].append(region["id"])

    pattern_groups = [
        {"pattern_hash": h, "regions": rlist}
        for h, rlist in hash_to_regions.items()
        if len(rlist) >= 2
    ]

    # Valve-sequence pattern: boundary control node types per region, sorted
    seq_to_regions = collections.defaultdict(list)
    for region in regions:
        boundary = region.get("boundary_elements", [])
        seq = tuple(sorted(b["type"] for b in boundary))
        seq_to_regions[str(seq)].append(region["id"])

    valve_seq_patterns = [
        {"valve_sequence": s, "regions": rlist}
        for s, rlist in seq_to_regions.items()
        if len(rlist) >= 2
    ]

    return {
        "pattern_groups": pattern_groups,
        "valve_sequence_patterns": valve_seq_patterns,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Extract features from a P&ID GraphML file")
    parser.add_argument("--input", default="0.graphml", help="Path to input GraphML file")
    parser.add_argument("--output", default="ground_truth.json", help="Path to output JSON file")
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output
    source_file = os.path.basename(input_path)

    print(f"Parsing {input_path} ...")
    raw_nodes, raw_edges = parse_graphml(input_path)

    # Raw counts (before any transformation)
    raw_counts = collections.Counter(n["label"] for n in raw_nodes.values())
    print(f"  Raw node counts: {dict(raw_counts)}")

    edge_counts_raw = collections.Counter(e["edge_type"] for e in raw_edges)
    print(f"  Raw edge counts: {dict(edge_counts_raw)}")

    # Coordinate bounds
    all_bboxes = [n["bbox"] for n in raw_nodes.values() if n["bbox"]]
    coord_bounds = {
        "xmin": min(b[0] for b in all_bboxes),
        "ymin": min(b[1] for b in all_bboxes),
        "xmax": max(b[2] for b in all_bboxes),
        "ymax": max(b[3] for b in all_bboxes),
    } if all_bboxes else {}

    # Step 1
    print("Building raw graph ...")
    G_raw = build_raw_graph(raw_nodes, raw_edges)
    print(f"  G_raw: {G_raw.number_of_nodes()} nodes, {G_raw.number_of_edges()} edges")

    # Step 2
    print("Resolving crossings ...")
    G_no_cross = resolve_crossings(G_raw)
    remaining_cross = sum(1 for _, d in G_no_cross.nodes(data=True) if d.get("label") == "crossing")
    print(f"  G_no_cross: {G_no_cross.number_of_nodes()} nodes, "
          f"{G_no_cross.number_of_edges()} edges  (crossings left: {remaining_cross})")

    # Step 3
    print("Contracting connectors ...")
    G_semantic = contract_connectors(G_no_cross)
    remaining_conn = sum(1 for _, d in G_semantic.nodes(data=True) if d.get("label") == "connector")
    print(f"  G_semantic: {G_semantic.number_of_nodes()} nodes, "
          f"{G_semantic.number_of_edges()} edges  (connectors left: {remaining_conn})")

    # Sanity check
    semantic_counts = collections.Counter(d["label"] for _, d in G_semantic.nodes(data=True))
    actual   = G_semantic.number_of_nodes()
    non_semantic = sum(v for k, v in semantic_counts.items() if k not in SEMANTIC_TYPES)
    assert non_semantic == 0, f"Unexpected non-semantic labels after contraction: {dict(semantic_counts)}"
    print(f"  Semantic node counts: {dict(semantic_counts)} (total {actual})")

    # Build normalized graph lists
    norm_nodes = []
    for nid, nd in G_semantic.nodes(data=True):
        norm_nodes.append({
            "id":     nid,
            "type":   nd.get("label"),
            "center": nd.get("center"),
            "bbox":   nd.get("bbox"),
        })
    norm_edges = []
    for src, tgt, edata in G_semantic.edges(data=True):
        norm_edges.append({
            "source":    src,
            "target":    tgt,
            "edge_type": edata.get("edge_type", "solid"),
        })

    # Step 4
    print("Decomposing regions ...")
    regions, region_of_node = decompose_regions(G_semantic)
    print(f"  {len(regions)} regions found")

    # Step 5
    print("Valve-bounded analysis ...")
    control_awareness = valve_bounded_analysis(G_semantic, regions, region_of_node)

    # Step 8 (degree characterization — needs to come before we enrich regions)
    print("Degree characterization ...")
    degree_characterization(G_semantic, regions)

    # Step 9
    print("Equipment anchors ...")
    equip_data = equipment_anchors(G_semantic, regions, region_of_node)

    # Step 10
    print("Line-type distribution ...")
    line_dist = line_type_distribution(G_semantic, regions)

    # Step 6
    print("Disjoint path analysis ...")
    path_analysis = disjoint_paths(G_semantic, regions, region_of_node)

    # Step 7
    print("Cycle detection ...")
    cycle_data = detect_cycles(regions, region_of_node, G_semantic)
    print(f"  {cycle_data['cycle_count']} fundamental cycles detected")

    # Step 11
    print("Repetition patterns ...")
    repetition = find_repetition_patterns(G_semantic, regions)
    print(f"  {len(repetition['pattern_groups'])} repeating topology groups, "
          f"{len(repetition['valve_sequence_patterns'])} valve-sequence patterns")

    # Assemble JSON
    ground_truth = {
        "metadata": {
            "source_file":        source_file,
            "raw_node_counts":    dict(raw_counts),
            "semantic_node_counts": dict(semantic_counts),
            "edge_counts": {
                "solid":     edge_counts_raw.get("solid", 0),
                "non_solid": edge_counts_raw.get("non-solid", 0),
            },
            "coordinate_bounds": coord_bounds,
        },
        "normalized_graph": {
            "nodes": norm_nodes,
            "edges": norm_edges,
        },
        "topology": {
            "total_regions": len(regions),
            "regions":       regions,
        },
        "control_awareness": control_awareness,
        "path_analysis":     path_analysis,
        "cycle_analysis":    cycle_data,
        "equipment_anchors": equip_data,
        "line_type_distribution": line_dist,
        "repetition_patterns": repetition,
    }

    # Write output
    print(f"\nWriting {output_path} ...")
    with open(output_path, "w") as f:
        json.dump(ground_truth, f, indent=2)

    # Final sanity checks
    print("\n--- Sanity Checks ---")
    print(f"  semantic nodes : {actual}")
    print(f"  crossings left : {remaining_cross}  (expected 0)")
    print(f"  connectors left: {remaining_conn}  (expected 0)")
    total_region_nodes = sum(len(r["nodes"]) for r in regions)
    print(f"  sum(region node counts): {total_region_nodes}")
    print(f"  regions: {len(regions)}")
    # Reload check
    with open(output_path) as f:
        reloaded = json.load(f)
    assert reloaded["metadata"]["source_file"] == source_file, "Reload check failed"
    print("  JSON reload: OK")
    print("\nDone.")


if __name__ == "__main__":
    main()
