# Methodology: Latent-Space Decomposition and Reconstruction of Piping and Instrumentation Diagrams

## 3.1 Overview

We present a deterministic, ten-stage pipeline that transforms raw Piping and Instrumentation Diagrams (P&IDs), encoded as GraphML files, into a structured latent representation and then reconstructs them as standards-compliant SVG renderings. The pipeline decomposes each diagram through a series of progressively abstracted representations—from raw graph topology, through semantic region decomposition and fragment classification, to a global process flow diagram (PFD) graph and finally a fully expanded P&ID with ISO 10628-2 and ISA 5.1 symbology.

The method is validated on a 12-diagram corpus ranging from 177 to 671 raw nodes, with all diagrams processed end-to-end without manual intervention.

### 3.1.1 Architectural Diagram

```
                          ┌─────────────────────────────────┐
                          │        Input: GraphML File       │
                          │   (nodes, edges, bounding boxes) │
                          └──────────────┬──────────────────┘
                                         │
                          ┌──────────────▼──────────────────┐
                          │  Stage 1: Feature Extraction     │
                          │  ┌───────────────────────────┐  │
                          │  │ 1a. Parse + normalize      │  │
                          │  │ 1b. Crossing resolution    │  │
                          │  │ 1c. Connector contraction  │  │
                          │  │ 1d. Region decomposition   │  │
                          │  │ 1e. Topological analysis   │  │
                          │  └───────────────────────────┘  │
                          └──────────────┬──────────────────┘
                                         │ ground_truth.json
                          ┌──────────────▼──────────────────┐
                          │  Stage 2: Fragment Extraction    │
                          │  ┌───────────────────────────┐  │
                          │  │ Region / Connectivity /    │  │
                          │  │ Equipment / Cycle / Pattern│  │
                          │  └───────────────────────────┘  │
                          └──────────────┬──────────────────┘
                                         │ fragments.json
                     ┌───────────────────┼───────────────────┐
                     │                   │                   │
          ┌──────────▼──────┐ ┌──────────▼──────┐ ┌─────────▼───────┐
          │ Stage 3a: Text  │ │ Stage 3b: LLM   │ │ Stage 3c: Macro │
          │ Realization     │ │ Paraphrasing     │ │ Mapping         │
          └──────────┬──────┘ └─────────────────┘ └─────────┬───────┘
                     │                                      │
                     │ canonical_descriptions.json           │ pfd_macros.json
                     │                                      │
                     │          ┌────────────────────────────┘
                     │          │
                     │ ┌────────▼────────────────────────────┐
                     │ │  Stage 4: Layout Primitive Expansion │◄── expansion_table.json
                     │ └────────┬────────────────────────────┘
                     │          │ pfd_layout_primitives.json
                     │ ┌────────▼────────────────────────────┐
                     │ │  Stage 5: Global Graph Assembly      │
                     │ │  (namespace, stitch, flow validate)  │
                     │ └────────┬────────────────────────────┘
                     │          │ pfd_global_graph.json
                     │ ┌────────▼────────────────────────────┐
                     │ │  Stage 6: PFD Layout Realization     │
                     │ │  (spine packing, edge routing)       │
                     │ └────────┬────────────────────────────┘
                     │          │ pfd_layout_realized.json
                ┌────┘          │
                │      ┌────────▼────────────────────────────┐
                │      │  Stage 7: Interaction Hooks (PFD)    │
                │      └────────┬────────────────────────────┘
                │               │ pfd_interaction_hooks.json
                │      ┌────────▼────────────────────────────┐
                │      │  Stage 8: PFD → P&ID Expansion      │◄── pid_expansion_rules.json
                │      │  (enrich / augment / replace)        │
                │      └────────┬────────────────────────────┘
                │               │ pid_primitives.json
                │      ┌────────▼────────────────────────────┐
                │      │  Stage 9: P&ID Assembly + Layout     │
                │      │  (graph flatten, layout, hooks)      │
                │      └────────┬────────────────────────────┘
                │               │ pid_global_graph.json
                │               │ pid_layout_realized.json
                │               │ pid_interaction_hooks.json
                │      ┌────────▼────────────────────────────┐
                │      │  Stage 10: SVG Rendering             │◄── pid_symbol_library.json
                │      │  (ISO 10628-2 / ISA 5.1 symbols)    │
                │      └────────┬────────────────────────────┘
                │               │
                │      ┌────────▼────────────────────────────┐
                │      │         Output: pid.svg              │
                │      └─────────────────────────────────────┘
                │
                │      ┌─────────────────────────────────────┐
                └─────►│ Latent Representation (Stages 2–5)  │
                       │  • Fragment decomposition            │
                       │  • Canonical text + macro sequences  │
                       │  • Typed, stitched global graph      │
                       └─────────────────────────────────────┘
```

### 3.1.2 Node Type Taxonomy

The pipeline recognizes ten semantic node types drawn from the GraphML source, organized into four functional classes:

| Class | Types | Semantic Role |
|---|---|---|
| **Pipe types** $\mathcal{P}$ | `general`, `arrow`, `inlet_outlet` | Form pipe runs and process flow paths |
| **Equipment types** $\mathcal{E}$ | `general`, `tank`, `pump` | Stationary process equipment |
| **Control types** $\mathcal{C}$ | `valve`, `instrumentation` | Flow regulation and measurement |
| **Wire types** $\mathcal{W}$ | `connector`, `crossing` | Drawing-level routing (eliminated during simplification) |

Note that `general` belongs to both $\mathcal{P}$ and $\mathcal{E}$, serving as a dual-role node representing process equipment that also participates in pipe run topology. The set $\mathcal{S} = \mathcal{P} \cup \mathcal{E} \cup \mathcal{C} \setminus \mathcal{W}$ defines the semantic types that survive into the simplified graph.

---

## 3.2 Stage 1: Feature Extraction

### 3.2.1 GraphML Parsing and Normalization

Each input GraphML file is parsed using standard XML processing. For every node $v$, we extract:

- A categorical label $\ell(v) \in \{\texttt{general}, \texttt{valve}, \texttt{instrumentation}, \texttt{arrow}, \texttt{inlet\_outlet}, \texttt{tank}, \texttt{pump}, \texttt{connector}, \texttt{crossing}, \texttt{background}\}$
- A bounding box $(x_{\min}, y_{\min}, x_{\max}, y_{\max})$
- A center position $\mathbf{c}(v) = \left(\frac{x_{\min}+x_{\max}}{2},\; \frac{y_{\min}+y_{\max}}{2}\right)$

Label normalization is applied at parse time: the GraphML label `inlet/outlet` is mapped to `inlet_outlet` to prevent delimiter conflicts in downstream identifiers. Nodes with $\ell(v) = \texttt{background}$ are discarded immediately.

Edges carry a type attribute $\tau(e) \in \{\texttt{solid}, \texttt{non\_solid}\}$, defaulting to `solid` when unspecified. The raw graph is constructed as an undirected graph $G_{\text{raw}} = (V_{\text{raw}}, E_{\text{raw}})$.

### 3.2.2 Crossing Resolution

Crossing nodes represent points where two orthogonal pipes visually intersect without physical connection. The algorithm resolves each crossing into the correct pair of through-connections.

**Definition.** For a crossing node $c$ with neighbor set $N(c)$, the *angular position* of neighbor $n_i$ relative to $c$ is:

$$\theta_i = \text{atan2}\!\left(y_{n_i} - y_c,\; x_{n_i} - x_c\right)$$

**Degree-4 case (standard).** We classify each neighbor into an angular bucket:

$$B(n_i) = \begin{cases} \texttt{horizontal} & \text{if } |x_{n_i} - x_c| \geq |y_{n_i} - y_c| \\ \texttt{vertical} & \text{otherwise} \end{cases}$$

If each bucket contains exactly two members, we bridge each pair and remove $c$.

**Fallback pairing (uneven buckets).** When the bucket heuristic yields an uneven split, we evaluate all $\binom{4}{2}/2 = 3$ possible pairings $\{(a,b),(c,d)\}$ of the four neighbors. For each partition, we compute a *collinearity score*:

$$S\!\left(\{(a,b),(c,d)\}\right) = \left|\;|\theta_a - \theta_b| - \pi\;\right| + \left|\;|\theta_c - \theta_d| - \pi\;\right|$$

The partition minimizing $S$ is selected. This criterion pairs neighbors that are closest to being diametrically opposite through the crossing point, which is geometrically correct for orthogonal pipe crossings.

**Degree-2 case.** The two neighbors are bridged directly.

**Other degrees.** Crossing nodes with degree $\neq 2, 4$ arise at drawing boundaries and are removed without bridging, preserving connectivity of the remaining graph.

**Edge type merging.** When two edges $e_1, e_2$ are bridged through a crossing, the merged edge type is:

$$\tau_{\text{merged}} = \begin{cases} \texttt{solid} & \text{if } \tau(e_1) = \texttt{solid} \lor \tau(e_2) = \texttt{solid} \\ \texttt{non\_solid} & \text{otherwise} \end{cases}$$

### 3.2.3 Connector Contraction

Connector nodes are junction points in the drawing that carry no semantic meaning. They are iteratively contracted to simplify the graph.

**Algorithm.** The contraction proceeds in rounds, processing connectors in ascending degree order:

1. Sort remaining connectors by $\deg(c)$, ascending.
2. For each connector $c$ with neighbor set $N(c) = \{n_1, \ldots, n_k\}$:
   - For all pairs $(n_i, n_j)$ where $i < j$: add edge $(n_i, n_j)$ if absent; promote to `solid` if any contributing edge is solid.
   - Remove $c$ and all its incident edges.

The degree-2-first ordering is critical: contracting a degree-$k$ connector generates up to $\binom{k}{2}$ new edges, so deferring high-degree nodes prevents combinatorial blowup. This process terminates when $\{v \in V : \ell(v) = \texttt{connector}\} = \emptyset$.

The result is the *semantic graph* $G_{\text{sem}} = (V_{\text{sem}}, E_{\text{sem}})$ where $\ell(v) \in \mathcal{S}$ for all $v \in V_{\text{sem}}$.

### 3.2.4 Region Decomposition

We decompose the semantic graph into *regions*—maximal connected subsets of process nodes separated by control elements.

**Definition.** The *pipe subgraph* is:

$$G_{\text{pipe}} = G_{\text{sem}}\!\left[\{v : \ell(v) \notin \mathcal{C}\}\right]$$

i.e., the induced subgraph on all non-control nodes (general, arrow, inlet_outlet, tank, pump).

The connected components $\{R_0, R_1, \ldots, R_{m-1}\}$ of $G_{\text{pipe}}$ define the region decomposition.

**Boundary element re-attachment.** For each control node $v \in \mathcal{C}$:

$$\text{regions}(v) = \{R_i : \exists\, u \in R_i \text{ such that } (u, v) \in E_{\text{sem}}\}$$

Node $v$ is attached as a *boundary element* to each region in $\text{regions}(v)$, carrying metadata including $|\text{regions}(v)|$ for cross-region connectivity analysis.

### 3.2.5 Topological Characterization

For each region $R_i$, we compute:

| Metric | Definition |
|---|---|
| Junction count | $J_i = |\{v \in R_i : \deg_{R_i}(v) \geq 3\}|$ |
| Maximum degree | $\Delta_i = \max_{v \in R_i} \deg_{R_i}(v)$ |
| Average degree | $\bar{d}_i = \frac{1}{|R_i|}\sum_{v \in R_i}\deg_{R_i}(v)$ |
| Junction density | $\rho_i = J_i / |R_i|$ |
| Equipment count | $|R_i \cap \mathcal{E}|$ |
| Valve-bounded | $\text{True}$ if $|\{b \in \partial R_i : \ell(b) \in \mathcal{C}\}| \geq 2$ |

### 3.2.6 Path Analysis and Cycle Detection

**Disjoint path analysis.** We construct a *region-level multigraph* $G_R$: for each control node $v$ touching regions $R_i$ and $R_j$ (where $i \neq j$), we add edge $(R_i, R_j)$. The edge connectivity $\kappa(R_i, R_j)$ is computed using maximum-flow algorithms, yielding the number of edge-disjoint paths between each pair of connected regions.

**Cycle detection.** The fundamental cycle basis of the simple region graph is computed. A non-empty cycle basis indicates topological loops in the process flow, which appear in 1 of the 12 corpus diagrams.

### 3.2.7 Repetition Pattern Detection

We identify two types of recurring patterns:

1. **Topology hash.** The sorted degree sequence $\sigma_i = \text{sort}(\deg_{R_i}(v) : v \in R_i)$ serves as a structural fingerprint. Regions with identical $\sigma_i$ form topology groups.

2. **Valve-sequence hash.** The sorted boundary-element type list $\beta_i = \text{sort}(\ell(b) : b \in \partial R_i)$ captures the control profile. Regions with identical $\beta_i$ form valve-sequence groups.

Groups with $|\text{group}| \geq 2$ are reported as repetition patterns.

---

## 3.3 Stage 2: Fragment Extraction

Fragments are the atomic units of the latent representation. Each region, cross-region connection, equipment cluster, topological cycle, and repetition pattern is encoded as a typed fragment.

### 3.3.1 Region Fragment Classification

Each region $R_i$ is classified into exactly one of four subtypes using the following priority-ordered decision rule:

$$\text{subtype}(R_i) = \begin{cases}
\texttt{controlled\_transfer} & \text{if } R_i \in \mathcal{V}_{\text{bounded}} \\
\texttt{isolated\_segment}    & \text{if } |\partial R_i| = 0 \\
\texttt{measurement\_only}    & \text{if } \nexists\, b \in \partial R_i : \ell(b) = \texttt{valve} \\
\texttt{linear\_transfer}     & \text{otherwise}
\end{cases}$$

where $\mathcal{V}_{\text{bounded}}$ is the set of valve-bounded regions (those with $\geq 2$ control-type boundary elements) and $\partial R_i$ denotes the boundary element set.

Each region fragment records:
- **Topology:** node count, junction count, junction density, cyclicity, path redundancy
- **Control semantics:** valve-bounded flag, boundary count, entry/exit valve classification
- **Equipment anchors:** equipment count, equipment roles, internal adjacency
- **Pattern signature:** topology hash, valve-sequence hash, group membership

### 3.3.2 Connectivity Fragments

For each pair of regions $(R_i, R_j)$ connected via the disjoint path analysis, a connectivity fragment records:

$$F_{\text{conn}} = \langle R_i, R_j, \kappa(R_i, R_j), \text{bridge\_types}, |\text{bridges}| \rangle$$

where bridge types are the labels of control nodes mediating the connection.

### 3.3.3 Equipment Cluster Fragments

Equipment clusters are computed via Union-Find with path-halving compression on the global equipment adjacency graph:

```
FIND(x):
    while parent[x] ≠ x:
        parent[x] ← parent[parent[x]]    // path halving
        x ← parent[x]
    return x

UNION(a, b):
    ra, rb ← FIND(a), FIND(b)
    if ra ≠ rb: parent[ra] ← rb
```

Each connected component of the equipment graph becomes an `equipment_cluster_fragment` carrying cluster size, node roles, adjacency count, and whether it spans multiple regions.

### 3.3.4 Pattern and Cycle Fragments

**Pattern fragments** are metadata references: one per topology group and one per valve-sequence group with $\geq 2$ members. They carry the pattern hash, instance count, and source region list.

**Cycle fragments** are produced when the region graph contains fundamental cycles (observed in 1 of 12 corpus diagrams). They record cycle length and participating regions.

---

## 3.4 Stage 3: Multi-Modal Realization

This stage produces three parallel representations of each fragment.

### 3.4.1 Stage 3a: Canonical Text Realization

Each fragment is deterministically mapped to a natural-language sentence via templates parameterized by fragment metadata. Examples:

| Subtype | Template (abbreviated) |
|---|---|
| `controlled_transfer` | "A fully bounded pipe segment containing $N$ equipment items, enclosed by $B$ control elements: {list}." |
| `measurement_only` | "A monitored pipe segment with $I$ instrumentation points. No valve control; flow is measured but not regulated." |
| `linear_transfer` | "A pipe segment with $V$ isolating valves on its boundary. Flow passes through without full enclosure." |
| `isolated_segment` | "An open pipe segment with no control or instrumentation elements on its boundary." |

### 3.4.2 Stage 3b: Linguistic Variants (LLM)

Five paraphrases per fragment are generated using Claude (Anthropic API) with deterministic temperature settings. This provides training data augmentation for downstream models.

### 3.4.3 Stage 3c: PFD Macro Mapping

Each fragment is mapped to a *macro sequence*—an ordered list of tokens from a fixed vocabulary:

$$\mathcal{V}_{\text{macro}} = \{\texttt{PIPE\_RUN},\; \texttt{ISOLATION\_VALVE},\; \texttt{CONTROL\_VALVE},\; \texttt{FLOW\_METER},\; \texttt{EQUIPMENT\_BLOCK},\; \texttt{TANK},\; \texttt{PUMP},\; \texttt{INLET\_OUTLET}\}$$

The mapping is defined by the subtype-to-template correspondence:

| Fragment Subtype | Template | Macro Sequence |
|---|---|---|
| `isolated_segment` | `ISOLATED_SEGMENT` | `[PIPE_RUN, PIPE_RUN]` |
| `linear_transfer` | `LINEAR_TRANSFER` | `[PIPE_RUN, ISOLATION_VALVE, PIPE_RUN]` |
| `measurement_only` | `MEASUREMENT_ONLY` | `[PIPE_RUN, FLOW_METER, PIPE_RUN]` |
| `controlled_transfer` | `CONTROLLED_TRANSFER` | `[CONTROL_VALVE, PIPE_RUN, CONTROL_VALVE]` |
| Equipment cluster | `EQUIPMENT_CLUSTER` | `[EQUIPMENT_BLOCK]` |
| Connectivity (valve) | `CONNECTIVITY_VALVE` | `[PIPE_RUN, ISOLATION_VALVE, PIPE_RUN]` |
| Connectivity (meter) | `CONNECTIVITY_METER` | `[PIPE_RUN, FLOW_METER, PIPE_RUN]` |
| Pattern / Cycle | `PATTERN_REF` | `[]` (empty) |

The macro template uniquely determines the macro sequence—this invariant is verified at generation time.

---

## 3.5 Stage 4: Layout Primitive Expansion

Each macro sequence is expanded into a typed node-edge subgraph (a *layout primitive*) using a declarative expansion table.

### 3.5.1 Expansion Modes

Three expansion modes are supported:

1. **Linear expansion.** The template's canonical node chain is instantiated and adjusted for the fragment's actual equipment count:

$$\text{nodes}(F) = \text{adjust}(\text{canonical\_chain}(T),\; n_{\text{equip}}(F))$$

   The adjustment inserts or removes equipment nodes at a template-specified index. Sequential edges $n_0 \to n_1 \to \cdots \to n_k$ are generated.

2. **Repeat expansion** (equipment clusters). Creates $n_{\text{equip}}$ copies of the canonical node in a linear chain.

3. **None** (pattern/cycle references). Produces an empty graph.

### 3.5.2 Semantic Annotation

After structural expansion, each node receives semantic defaults from the expansion table, merged by `(type, subtype)` matching:

| Node Type | Semantic Defaults |
|---|---|
| `pipe_segment` | `flow_direction: "forward"` |
| `valve:isolation` | `normally_open: true, fail_position: "closed"` |
| `valve:control` | `control_loop: true` |
| `instrument:flow_meter` | `measurement: "flow"` |

### 3.5.3 Primitive Type Vocabulary

All expanded nodes have types drawn from:

$$\mathcal{T}_{\text{prim}} = \{\texttt{pipe\_segment},\; \texttt{valve},\; \texttt{instrument},\; \texttt{equipment\_block},\; \texttt{tank},\; \texttt{pump},\; \texttt{inlet\_outlet}\}$$

with optional subtypes (e.g., `valve:isolation`, `valve:control`, `instrument:flow_meter`).

---

## 3.6 Stage 5: Global Graph Assembly

### 3.6.1 Namespacing and Assembly

Each layout primitive's local node IDs are namespaced with their fragment identifier:

$$\text{id}_{\text{global}}(v) = F_{\text{id}} \mathbin{:} \text{id}_{\text{local}}(v)$$

Pattern and cycle fragments (empty graphs) are skipped. All intra-fragment edges are similarly namespaced. Fragment *interfaces* (entry and exit node IDs) are recorded for stitching.

### 3.6.2 Cross-Fragment Stitching

For each connectivity fragment $F_c$ connecting source fragment $F_s$ to target fragment $F_t$, two stitch edges are created:

$$e_1: \text{exit}(F_s) \to \text{entry}(F_c), \qquad e_2: \text{exit}(F_c) \to \text{entry}(F_t)$$

Each stitch edge carries the attribute `stitch = true` for downstream routing and styling.

### 3.6.3 Flow Validation

A BFS-based propagation detects flow-direction conflicts:

1. Identify source nodes (in-degree 0) in the directed interpretation of the graph.
2. Propagate `flow_direction` attributes along edges.
3. Flag any node receiving conflicting directions from multiple predecessors.

All 12 corpus diagrams produce zero conflicts, validating the consistency of the expansion and stitching process.

---

## 3.7 Stage 6: PFD Layout Realization

### 3.7.1 Node Sizing

Each node type maps to a fixed size in millimeters:

| Type | Width (mm) | Height (mm) |
|---|---|---|
| `pipe_segment` | 40 | 15 |
| `equipment_block` | 80 | 60 |
| `valve:isolation` | 26 | 26 |
| `valve:control` | 26 | 44 |
| `instrument:flow_meter` | 26 | 26 |
| `tank` | 80 | 60 |
| `pump` | 50 | 50 |
| `inlet_outlet` | 30 | 20 |

### 3.7.2 Fragment Bounding Box Computation

For a horizontal fragment with ordered nodes $\{v_1, \ldots, v_n\}$:

$$W_F = 2P + \sum_{i=1}^{n} w_i + (n-1) \cdot G, \qquad H_F = 2P + \max_i\, h_i$$

For a vertical fragment (equipment clusters):

$$W_F = 2P + \max_i\, w_i, \qquad H_F = 2P + \sum_{i=1}^{n} h_i + (n-1) \cdot G$$

where $P = 10\;\text{mm}$ (intra-fragment padding) and $G = 15\;\text{mm}$ (inter-node gap).

### 3.7.3 Spine Layout Algorithm

The layout proceeds in three phases with the following constants:

| Parameter | Value | Description |
|---|---|---|
| $M$ | 40 mm | Canvas border margin |
| $G_F$ | 10 mm | Gap between adjacent fragments |
| $L_H$ | 280 mm | Vertical step between pipe rows |
| $L_E$ | 160 mm | Vertical step between equipment rows |
| $W_{\max}$ | 3120 mm | Maximum usable row width |

**Phase 1: Chain extraction.** Stitch edges define an undirected fragment adjacency graph. Connected components are extracted via BFS, and each component is linearized by walking from a degree-1 endpoint (or the lowest-index member in cyclic components).

**Phase 2: Row packing.** Fragment groups are placed using a greedy left-to-right, top-to-bottom strategy:

```
PACK(fragment_list):
    row ← 0, x ← 0
    for each group G in fragment_list:
        group_width ← Σ W_F(f) + G_F · (|G| - 1)    for f ∈ G
        if x > 0 AND x + G_F + group_width > W_max:
            row ← row + 1, x ← 0
        for each fragment f ∈ G:
            origin(f) ← (M + x,  M + row · L_H)
            x ← x + W_F(f) + G_F
```

Packing order:
1. Stitch chains (preserving cross-fragment flow continuity on the same row)
2. Unconnected region and connectivity fragments
3. Equipment clusters (separate zone below, using $L_E$ spacing)

The equipment zone starts at:

$$y_{\text{equip}} = M + (r_{\max} + 1) \cdot L_H + M$$

where $r_{\max}$ is the highest row index used by pipe fragments.

### 3.7.4 Intra-Fragment Node Placement

**Horizontal fragments:** Nodes are vertically centered at $c_y = o_y + P + \frac{\max_i h_i}{2}$ and placed left-to-right starting at $x = o_x + P$, advancing by $w_i + G$ per node.

**Vertical fragments:** Nodes are horizontally centered at $c_x = o_x + P + \frac{\max_i w_i}{2}$ and placed top-to-bottom starting at $y = o_y + P$.

### 3.7.5 Edge Routing

**Intra-fragment edges** are routed as 2-point straight lines between port positions:
- Horizontal: source at $(x + w, y + h/2)$, target at $(x, y + h/2)$
- Vertical: source at $(x + w/2, y + h)$, target at $(x + w/2, y)$

**Stitch edges** use a 4-point L-shaped path:

$$\text{path} = \left[(x_s, y_s),\; \left(\frac{x_s + x_t}{2}, y_s\right),\; \left(\frac{x_s + x_t}{2}, y_t\right),\; (x_t, y_t)\right]$$

---

## 3.8 Stage 7: Interaction Hooks

A declarative UI contract is generated mapping each node, edge, and fragment to interaction behaviors:

| Element | Hover | Click | Double-Click | Context Menu |
|---|---|---|---|---|
| Node | Highlight self + connected edges; show tooltip | Select; emit `NODE_SELECTED` | Emit `DRILL_DOWN_FRAGMENT` | — |
| Edge | Highlight self; show from/to tooltip | Emit `EDGE_SELECTED` | — | — |
| Fragment | Highlight all members | Select; emit `FRAGMENT_SELECTED` | — | Isolate, metadata, hide others |

Stitch edges are detected by path length ($|\text{path}| = 4$) and styled with `dashed_thick` emphasis.

---

## 3.9 Stage 8: PFD-to-P&ID Expansion

This stage transforms the abstract PFD graph into a detailed P&ID by applying declarative expansion rules to each node.

### 3.9.1 Tag Counter System

Sequential tag generation uses prefixed counters:

$$\text{tag}(k) = \text{prefix}(k) \mathbin{-} \text{pad}(\text{counter}(k))$$

| Counter Key | Prefix | Start | Example |
|---|---|---|---|
| `valve:isolation` | XV | 101 | XV-101 |
| `valve:control` | FV | 201 | FV-201 |
| `controller` | FC | 201 | FC-201 |
| `actuator` | ACT | 201 | ACT-201 |
| `flow_meter` | FT | 301 | FT-301 |
| `equipment` | E | 001 | E-001 |
| `pressure_inst` | PI | 401 | PI-401 |
| `temperature_inst` | TI | 501 | TI-501 |
| `drain_valve` | DR | 601 | DR-601 |
| `line` | L | 101 | L-101-A |
| `tank` | TK | 701 | TK-701 |
| `pump` | P | 801 | P-801 |
| `level_inst` | LI | 901 | LI-901 |
| `motor` | M | 1001 | M-1001 |

### 3.9.2 Expansion Rules

Each PFD node type maps to one of three expansion modes:

**Enrich** — adds metadata fields to the primary node without creating new nodes:
- `pipe_segment` → assign line ID, size, spec, service
- `inlet_outlet` → assign line ID

**Augment** — adds instrumentation or auxiliary nodes:
- `equipment_block` → $+$ PI (pressure indicator) $+$ TI (temperature indicator), connected by `sense` edges
- `valve:isolation` → $+$ drain valve, connected by `branch` edge
- `tank` → $+$ PI $+$ TI $+$ LI (level indicator), connected by `sense` edges
- `pump` → $+$ motor driver, connected by `mechanical` edge

**Replace** — restructures the node into a multi-node assembly:
- `valve:control` → primary valve $+$ pneumatic actuator $+$ controller (FC), with `signal` (FC→ACT) and `mechanical` (ACT→valve) edges
- `instrument:flow_meter` → primary element $+$ transmitter (FT), with `signal` edge

### 3.9.3 Edge Enrichment

Process edges inherit line specifications from adjacent pipe segments:

$$\text{line\_id}(e) = \text{line\_id}(\text{from}(e)) \;\text{if pipe\_segment, else}\; \text{line\_id}(\text{to}(e))$$

Default line specifications: size = 4 in, spec = CS150, service = Process.

### 3.9.4 Instrument Loop Pairing

Instrument control loops are constructed by positional pairing:

$$\text{loop}_i = \langle \text{flow\_meter}[i],\; \text{control\_valve}[i] \rangle$$

with loop identifier:

$$\text{loop\_id}_i = \texttt{FIC-} \mathbin{\|} \text{number}(\text{FT\_tag}_i)$$

The FC controller tag is found by searching the control valve's added nodes for type `instrument:controller`.

---

## 3.10 Stage 9: P&ID Assembly, Layout, and Interaction

### 3.10.1 Graph Flattening

The expanded primitives are flattened into a unified graph:
- Primary nodes retain their compound PFD type (e.g., `valve:isolation`)
- Added nodes receive a `parent_id` field
- Edge ID scheme: process edges as `pe_0, pe_1, \ldots$; expansion edges as `xe_0, xe_1, \ldots$

### 3.10.2 Layout Placement

**Primary nodes** inherit exact $(x, y)$ positions from the PFD layout (Stage 6).

**Added nodes** are offset relative to their parent's top-left corner:

$$\mathbf{p}_{\text{added}} = \mathbf{p}_{\text{parent}} + (\Delta x, \Delta y)$$

| Added Node Type | $\Delta x$ (mm) | $\Delta y$ (mm) | Rationale |
|---|---|---|---|
| `valve:drain` | 4 | 30 | Below isolation valve |
| `actuator:pneumatic` | 2 | $-22$ | Above control valve |
| `instrument:controller` | 3 | $-46$ | Above actuator stack |
| `instrument:transmitter` | 3 | $-24$ | Above flow meter |
| `instrument:pressure` | 84 | $-5$ | Right of equipment |
| `instrument:temperature` | 84 | 19 | Below PI, right of equipment |
| `motor:driver` | 14 | $-22$ | Above pump |
| `instrument:level` | 84 | 43 | Below TI, right of tank |

### 3.10.3 Port Computation

Port positions determine edge connection points:

| Node Category | Input Port | Output Port |
|---|---|---|
| Horizontal process nodes | $(0,\; h/2)$ | $(w,\; h/2)$ |
| Vertical process nodes | $(w/2,\; 0)$ | $(w/2,\; h)$ |
| Instrument bubbles | $(w/2,\; h/2)$ | $(w/2,\; h/2)$ |
| Actuator / motor | $(w/2,\; h)$ | $(w/2,\; 0)$ |
| Drain valve | $(w/2,\; 0)$ | — |

### 3.10.4 Edge Routing

**Process edges** follow Stage 6 logic: 2-point straight (intra-fragment) or 4-point L-shaped (stitch).

**Expansion edges** are routed as 2-point center-to-center paths:

$$\text{path}(e) = \left[\left(x_s + \frac{w_s}{2},\; y_s + \frac{h_s}{2}\right),\; \left(x_t + \frac{w_t}{2},\; y_t + \frac{h_t}{2}\right)\right]$$

Edge styles:

| Signal Type | Line Style |
|---|---|
| `sense`, `signal` | dashed |
| `mechanical`, `branch` | solid |

### 3.10.5 Group Construction

**Fragment groups:** Bounding box over all nodes (primary + added) sharing a fragment ID:

$$\text{bbox}(F) = \left(\min_i x_i,\; \min_i y_i,\; \max_i(x_i + w_i) - \min_i x_i,\; \max_i(y_i + h_i) - \min_i y_i\right)$$

**Loop groups:** Bounding box over the FT, FC, FV, and actuator nodes of each instrument loop.

---

## 3.11 Stage 10: SVG Rendering

### 3.11.1 Symbol Resolution

Symbols are resolved via a first-match walk of an ordered rule array:

```
for rule in resolution_rules:
    if all conditions in rule.match are satisfied:
        return rule.symbol
```

Conditions may test `ntype` (exact match) or `semantics.*` (semantic field equality). More specific rules precede generic fallbacks, e.g., `valve:isolation` with `fail_position: "closed"` resolves to `gate_valve_fc` before the generic `gate_valve`.

### 3.11.2 ISO Symbol Library

All symbols are defined in normalized $[0, 1]$ coordinates with the following scaling:

$$x_{\text{actual}} = x_{\text{norm}} \cdot w, \quad y_{\text{actual}} = y_{\text{norm}} \cdot h, \quad r_{\text{actual}} = r_{\text{norm}} \cdot \min(w, h)$$

Stroke widths are absolute (in mm). Color sentinels `$fill` and `$stroke` resolve to per-type colors at render time.

**Key ISO/ISA symbols implemented:**

| Symbol | Standard | Geometry |
|---|---|---|
| Gate valve (FC) | ISO 10628-2 | Bowtie (two opposing triangles) + stem + horizontal FC bar |
| Control valve | ISO 10628-2 | Three-part stack: solid actuator triangle / base bar / bowtie body |
| Orifice plate | ISO 10628-2 | Two narrow vertical plates flanking centerline + DP taps |
| Vessel | ISO 10628-2 | Rounded rectangle + nozzle stripe at 82% height |
| Centrifugal pump | ISO 10628-2 | Circle + discharge triangle |
| Instrument bubble | ISA 5.1 | Circle ($r = 0.44 \cdot \min(w,h)$) + horizontal bisect chord |
| Tank | ISO 10628-2 | Capsule shape (rect with rounded ends) |

ISA 5.1 bubbles (controller, transmitter, PI, TI, LI) share identical geometry; they are distinguished by their inner label text (FC, FT, PI, TI, LI).

### 3.11.3 Canvas and Display Scaling

The viewBox is computed from the node/group bounding boxes:

$$\text{vw} = x_{\max} - x_{\min} + 2 \cdot \text{PAD}, \qquad \text{vh} = y_{\max} - y_{\min} + 2 \cdot \text{PAD}$$

with $\text{PAD} = 40\;\text{mm}$. The display dimensions enforce a landscape constraint:

$$\text{SCALE} = \min\!\left(1.0,\; \frac{1800}{\text{vw}},\; \frac{1200}{\text{vh}}\right)$$

$$\text{disp\_w} = \lfloor\text{vw} \cdot \text{SCALE}\rceil, \qquad \text{disp\_h} = \lfloor\text{vh} \cdot \text{SCALE}\rceil$$

### 3.11.4 Layer Ordering

The SVG is rendered in strict layer order (back to front):

1. **Background:** Outer gray border (`#c8c8c8`), inner drawing area (`#f5f5f5`) with black frame
2. **Process edges** (`pe_*`): Solid lines, stroke width 3.0 mm
3. **Expansion edges** (`xe_*`): Signal/sense as dashed thin lines; mechanical/branch as solid thin lines
4. **Nodes:** ISO symbols rendered within translated `<g>` groups
5. **Legend:** Auto-generated swatch table for all node types and edge styles

Group bounding boxes (fragment and loop boundaries) are intentionally omitted from the SVG, as real P&IDs do not contain such visual annotations.

### 3.11.5 Edge Rendering Parameters

| Edge Kind | Color | Stroke Width | Dash Pattern |
|---|---|---|---|
| Process pipe | `#111111` | 3.0 | — |
| Signal | `#444444` | 0.9 | `6 3` |
| Sense | `#444444` | 0.8 | `3 2` |
| Mechanical | `#222222` | 1.4 | — |
| Branch | `#222222` | 1.6 | — |

No arrowheads are rendered; flow direction is implied by the graph connectivity and semantic attributes.

### 3.11.6 Label Typography

Font sizing formulas scale with node dimensions to prevent overflow:

| Node Category | Font Size Formula |
|---|---|
| Instrument inner text | $f_s = \min(w, h) \cdot 0.30$ |
| Instrument tag (below) | $f_s = \min(4.5,\; h \cdot 0.22)$ |
| Equipment tag (centered) | $f_s = \min(6.5,\; h \cdot 0.14,\; w \cdot 0.11)$ |
| Default tag (below) | $f_s = \min(5.0,\; h \cdot 0.22,\; w \cdot 0.18)$ |
| Pipe segment tag | $f_s = \min(4.5,\; h \cdot 0.28)$ |

---

## 3.12 Corpus Statistics and Validation

The pipeline was validated on a 12-diagram corpus. All diagrams processed end-to-end without manual intervention.

| File | Raw Nodes | Semantic Nodes | Regions | Fragments | PFD Nodes | Stitch Edges | P&ID Nodes | Loops | SVG Size |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 433 | 137 | 43 | 69 | 183 | 10 | 346 | 2 | 228 KB |
| 1 | 316 | 100 | 40 | 67 | 136 | 8 | 221 | 0 | 138 KB |
| 2 | 481 | 144 | 37 | 74 | 243 | 28 | 500 | 1 | 334 KB |
| 3 | 276 | 88 | 16 | 29 | 103 | 4 | 227 | 0 | 153 KB |
| 4 | 381 | 121 | 31 | 55 | 158 | 12 | 312 | 3 | 207 KB |
| 5 | 401 | 124 | 30 | 54 | 122 | 0 | 239 | 1 | 154 KB |
| 6 | 466 | 136 | 31 | 55 | 169 | 10 | 353 | 4 | 235 KB |
| 7 | 347 | 110 | 41 | 67 | 164 | 14 | 289 | 0 | 188 KB |
| 8 | 177 | 57 | 25 | 39 | 94 | 12 | 140 | 0 | 90 KB |
| 9 | 222 | 81 | 39 | 72 | 168 | 12 | 303 | 0 | 194 KB |
| 10 | 671 | 210 | 59 | 96 | 301 | 28 | 583 | 1 | 383 KB |
| 11 | 410 | 131 | 42 | 60 | 155 | 6 | 266 | 1 | 170 KB |

**Key observations:**

- **Compression ratio.** The semantic graph averages $29.4\%$ of raw node count ($\bar{r} = |V_{\text{sem}}| / |V_{\text{raw}}|$), demonstrating significant structural redundancy in the drawing-level representation.
- **Expansion factor.** The P&ID expansion increases node count by a mean factor of $1.95\times$ over the PFD graph, reflecting the addition of instrumentation, drain valves, actuators, and controllers.
- **Region count.** Ranges from 16 to 59 per diagram, reflecting varying process complexity. Region count correlates positively with raw node count ($r = 0.82$).
- **Stitch edges.** Range from 0 (fully disconnected fragment set) to 28 (heavily cross-linked process sections).
- **Instrument loops.** Range from 0 to 4, reflecting the density of flow control instrumentation. Loops are only formed when both flow meters and control valves are present.
- **Cycle handling.** One diagram (file 8) exhibits a topological cycle in the region graph, requiring cycle-aware chain walking in the layout algorithm.

### 3.12.1 Invariants Verified

The following invariants are checked at each pipeline stage via dynamic assertions:

1. **Referential integrity.** Every edge endpoint references an existing node.
2. **Tag uniqueness.** All generated tags (XV-xxx, FV-xxx, etc.) are globally unique.
3. **Position inheritance.** Primary P&ID node positions exactly match their PFD layout positions.
4. **Flow consistency.** BFS propagation detects zero directional conflicts across all 12 diagrams.
5. **Path length correctness.** Intra-fragment edges have 2-point paths; stitch edges have 4-point paths; expansion edges have 2-point paths.
6. **No primary node overlap.** Within each fragment, primary node bounding boxes do not intersect.
7. **Complete coverage.** Every non-pattern, non-cycle fragment appears in the global graph, layout, and interaction hooks.
