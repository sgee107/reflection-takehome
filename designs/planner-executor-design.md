# Planner-Executor Architecture — Design Document

**Date**: 2026-04-23
**Author**: Claude Code
**Status**: Draft
**Predecessor**: `research/planner-executor-sketch.md`

---

## Problem Statement

The current agent processes shortfalls sequentially — one component at a time — with a single ReAct loop that handles both strategic reasoning (which suppliers, how to split, what tradeoffs) and mechanical execution (calling `place_order`). This causes four documented failure classes:

| Failure | Root Cause | Models Affected |
|---------|-----------|-----------------|
| F1: Magnet concentration violations | No cross-component planning — agent commits to one supplier before considering the split | All (Sonnet 4/6, Haiku 2/6, Qwen 3/6) |
| F2: Duplicate orders | Agent loses track of what it already placed | Sonnet only |
| F3: Early loop termination | Cognitive load too high — small models quit mid-loop | Qwen 7B (1/6 completeness) |
| F4: Missing deadline alerts | Doesn't reason about lead time vs deadline until mid-execution | Qwen 7B |

Step 1 (enriched tools, in progress) addresses F1 via concentration guardrails and F4 via delivery-vs-deadline warnings. But the core architectural issue remains: **the agent sees one component at a time and can't reason about cross-component interactions, shared supplier dependencies, or plan-wide constraint satisfaction.**

### What Changed Since the Original Sketch

The original planner-executor sketch (`research/planner-executor-sketch.md`) was written before:

1. **Step 1 enriched tools are implemented** — `get_eligible_suppliers` now returns annotated output with delivery feasibility, concentration limits, max quantities, and constraint notes. The enrichment layer from the sketch is largely done.

2. **Constraint extraction agent is mid-implementation** (Steps 1-6 complete, Steps 7-10 remain) — constraints are now extracted via a LangGraph subgraph with DB grounding tools and a mutable working set. The constraint pipeline is richer and more reliable than when the sketch was written.

3. **`place_order` enforces hard constraints** — concentration limits, duplicate detection, delivery warnings, MOQ rounding, and budget threshold alerts are now deterministic. The executor doesn't need to reason about these — it gets errors back if it violates them.

These changes shift the design: the enrichment layer is no longer "new" — it exists. The planner can rely on richer tool output. The executor can be simpler because more guardrails are in code.

---

## Goals

1. **Deterministic default plan** — an algorithm produces a greedy allocation covering all gaps, using fitness-scored suppliers, concentration-aware splits, and constraint checks — no LLM required
2. **Explicit conflict detection** — the algorithm identifies every conflict (infeasible deadlines, forced concentration splits, budget threshold crossings, shared supplier overload) as structured objects the LLM must resolve
3. **LLM as reviewer, not author** — the LLM sees the default plan + conflicts and only intervenes where genuine judgment is needed
4. **Append-only decision log** — every decision (algorithmic or LLM) is recorded with rationale, creating a full audit trail
5. **Deterministic execution** — the executor follows the finalized plan mechanically
6. **Human review checkpoint** — optional `--plan-only` to inspect before execution

## Non-Goals

- Multi-period / rolling horizon planning (future phase)
- Changing the scenario DB schema
- Replacing the constraint extraction agent (it feeds into this)
- Building a general-purpose planning framework

---

## Architecture

### Current Flow

```
load_scenario → gap_analysis → extract_constraints → build_agent → ReAct loop
                                                                      │
                                                          ┌───────────┴───────────┐
                                                          │  For each shortfall:  │
                                                          │  get_eligible_suppliers│
                                                          │  decide supplier/qty  │
                                                          │  place_order          │
                                                          │  (repeat)             │
                                                          └───────────────────────┘
```

### Proposed Flow

```
load_scenario → gap_analysis → extract_constraints
                                       │
                                       ▼
                         ┌─────────────────────────────┐
                         │  ENRICHMENT (deterministic)  │
                         │  build_option_matrix()        │
                         │  ↓                            │
                         │  GREEDY PLANNER (deterministic)│
                         │  greedy_allocate()             │
                         │  ↓                            │
                         │  CONFLICT DETECTION (determ.) │
                         │  detect_conflicts()            │
                         │  ↓                            │
                         │  Output: DefaultPlan +        │
                         │    Conflict[] + DecisionLog   │
                         └──────────────┬──────────────┘
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │  LLM REVIEWER (only if       │
                         │  conflicts exist)             │
                         │                              │
                         │  Input: DefaultPlan +        │
                         │    Conflict[] + DecisionLog  │
                         │                              │
                         │  Tools:                      │
                         │   resolve_conflict            │
                         │   override_allocation         │
                         │   simulate_plan               │
                         │   accept_plan                 │
                         │                              │
                         │  Output: FinalPlan +         │
                         │    updated DecisionLog       │
                         └──────────────┬──────────────┘
                                        │
                              (optional: --plan-only)
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │  EXECUTOR (deterministic)    │
                         │  for alloc in plan:          │
                         │    place_order(...)           │
                         │  for alert in plan.alerts:   │
                         │    create_alert(...)          │
                         └─────────────────────────────┘
```

**Key insight**: Soft-preference tradeoffs (domestic vs cost, strategic loyalty, sustainability, shared supplier load) are flagged whenever the conditions exist — no threshold gating. This means the LLM reviewer will almost always be invoked, since most scenarios have at least one domestic/international supplier pair. But many flags will be triaged as no-ops. The value is in the decision log: even a "no change needed" entry with rationale is auditable and informative. The LLM earns its cost by *interpreting* tradeoffs, not by doing arithmetic.

---

## Data Structures

### SupplierOption

Pre-computed for each (component, eligible supplier) pair:

```python
@dataclass
class SupplierOption:
    component_id: str
    supplier_id: str
    supplier_name: str
    unit_price: float
    lead_time_days: int
    delivery_date: str          # current_date + lead_time
    deadline: str               # earliest_needed_by from gap_df
    days_late: int              # 0 if on time, positive if late
    air_freight_delivery: str | None
    air_freight_days_late: int | None
    is_domestic: bool
    sustainability_rating: str
    relationship_tier: str
    certifications: list[str]
    moq: int
    gap_quantity: int           # total gap for this component
    max_concentration_qty: int | None  # max from this supplier before hitting limit
    fitness_score: float        # composite 0-1
```

### OptionMatrix

```python
@dataclass
class OptionMatrix:
    options: dict[str, list[SupplierOption]]  # component_id → sorted by fitness
    shared_suppliers: dict[str, list[str]]    # supplier_id → [component_ids]
    component_priorities: list[str]           # sorted by deadline urgency
```

### AllocationPlan

```python
@dataclass
class Allocation:
    component_id: str
    supplier_id: str
    quantity: int
    rationale: str
    expedite: bool = False

@dataclass
class PlannedAlert:
    description: str
    component_id: str | None = None

@dataclass
class AllocationPlan:
    allocations: list[Allocation]
    alerts: list[PlannedAlert]
```

### Conflict

Structured representation of a problem the greedy algorithm couldn't resolve cleanly:

```python
class ConflictType(Enum):
    # Hard conflicts (arithmetic — always detectable)
    INFEASIBLE_DEADLINE = "INFEASIBLE_DEADLINE"
    CONCENTRATION_SPLIT = "CONCENTRATION_SPLIT"
    BUDGET_THRESHOLD = "BUDGET_THRESHOLD"
    MOQ_OVERBUY = "MOQ_OVERBUY"
    NO_ELIGIBLE_SUPPLIER = "NO_ELIGIBLE_SUPPLIER"

    # Soft-preference tradeoffs (always flagged — LLM triages)
    DOMESTIC_VS_COST = "DOMESTIC_VS_COST"             # domestic + international both available
    STRATEGIC_LOYALTY = "STRATEGIC_LOYALTY"            # non-strategic chosen over strategic
    SUSTAINABILITY_TRADEOFF = "SUSTAINABILITY_TRADEOFF"  # lower-rated chosen over higher
    SHARED_SUPPLIER_LOAD = "SHARED_SUPPLIER_LOAD"     # supplier allocated across 2+ components

@dataclass
class Conflict:
    type: ConflictType
    component_id: str
    description: str            # human-readable explanation
    options: list[dict]         # possible resolutions the LLM can pick from
    greedy_choice: dict | None  # what the algorithm defaulted to (if it made a choice)
    data: dict                  # type-specific details (prices, dates, percentages)
```

### DecisionLog

Append-only log tracking every allocation decision:

```python
@dataclass
class Decision:
    component_id: str
    action: str                 # "allocate", "split", "alert", "override", "resolve_conflict"
    details: dict               # what was decided
    rationale: str              # why
    source: str                 # "greedy_algorithm" or "llm_reviewer"
    timestamp: str              # ISO timestamp
    conflict_id: str | None     # if this resolved a conflict

@dataclass
class DecisionLog:
    entries: list[Decision]

    def append(self, decision: Decision) -> None: ...
    def summary(self) -> str: ...
    def for_component(self, component_id: str) -> list[Decision]: ...
```

---

## Component Design

### 1. Enrichment Layer: `build_option_matrix()`

**Location**: `procureai/pipeline.py`

Identical to previous design — pre-computes all `SupplierOption`s with fitness scoring, shared supplier map, and priority ordering. Pure deterministic code.

**Fitness Scoring**:

```
score = (
    0.25 * price_score          # min/max inversion (lower = better)
  + 0.25 * delivery_score       # 1.0 if on time, gradient toward 0 as lateness increases
  + 0.15 * cert_score           # 1.0 if required cert, else filtered out pre-scoring
  + 0.10 * tier_score           # strategic=1.0, preferred=0.7, standard=0.4
  + 0.10 * sustainability_score # A=1.0, B=0.6, C=0.3
  + 0.10 * domestic_score       # 1.0 domestic, 0.5 international
  + 0.05 * moq_fit_score        # 1.0 if MOQ ≤ gap, else gap/MOQ
)
```

### 2. Greedy Planner: `greedy_allocate()`

**Location**: `procureai/planner.py` (new file — not in agents/, it's deterministic)

**Algorithm**: Process components in priority order (earliest deadline first). For each component:

```
1. Get sorted supplier options from OptionMatrix
2. Check if concentration limit applies
   a. If no limit: allocate full gap to #1 fitness supplier
   b. If limit: compute split quantities (max_pct × gap per supplier),
      allocate across top-N suppliers to cover gap
3. For each allocation:
   - If quantity < MOQ: round up, flag MOQ_OVERBUY conflict
   - Compute delivery date; if late, flag INFEASIBLE_DEADLINE conflict
   - Check if domestic preference threshold is crossed: flag DOMESTIC_VS_COST
   - Check if switching from strategic tier: flag STRATEGIC_LOYALTY
4. Record each allocation in the plan and log the decision
5. Track cumulative spend per supplier for budget threshold checks
6. After all components: check shared supplier load, flag if concerning
```

**Key design choice**: The greedy algorithm *always produces a plan*. Conflicts don't block allocation — they flag decisions that the LLM should review. The greedy choice is the algorithm's best guess; the LLM can override it.

Example decision log entries from greedy allocation:

```
[D-001] CMP-005 | allocate | SUP-101 × 40 | "Sole qualified supplier (ISO-9001)" | greedy_algorithm
[D-002] CMP-003 | split | SUP-108 × 104 + SUP-107 × 104 | "Concentration limit 50%, split across top 2 by fitness" | greedy_algorithm
[D-003] CMP-003 | alert | "Both suppliers deliver late" | "Fastest: SUP-108, 3 days late" | greedy_algorithm | conflict: CF-001
[D-004] CMP-001 | allocate | SUP-101 × 287 | "Top fitness, on-time delivery" | greedy_algorithm
```

### 3. Conflict Detection: `detect_conflicts()`

**Location**: `procureai/planner.py`

Runs after greedy allocation. Some conflicts are detected during allocation (inline), others require a cross-component view:

**Hard conflicts** (always detectable — pure arithmetic):
- `INFEASIBLE_DEADLINE` — no supplier delivers on time
- `CONCENTRATION_SPLIT` — concentration limit forces a multi-supplier split
- `MOQ_OVERBUY` — MOQ forces ordering significantly more than the gap
- `NO_ELIGIBLE_SUPPLIER` — all suppliers filtered out by hard constraints
- `BUDGET_THRESHOLD` — aggregate spend per supplier crosses policy threshold

**Soft-preference tradeoffs** (always flagged — LLM decides if they matter):
- `DOMESTIC_VS_COST` — flagged whenever a domestic and international supplier both exist for a component, regardless of price delta. The greedy algorithm picks by fitness score but surfaces the tradeoff with full cost/delivery data so the LLM can assess whether the premium is justified.
- `STRATEGIC_LOYALTY` — flagged whenever the greedy algorithm picks a non-strategic supplier over a strategic-tier alternative. Could be a $0.05 difference or a $5.00 difference — the algorithm doesn't judge, it flags.
- `SUSTAINABILITY_TRADEOFF` — flagged when a lower-sustainability supplier is chosen over a higher-rated one (e.g., B-rated wins on price over A-rated).
- `SHARED_SUPPLIER_LOAD` — flagged whenever one supplier is allocated across 2+ shortfall components. Informational — the LLM decides if the concentration of business is concerning.

The philosophy: **flag aggressively, let the LLM triage**. The detection layer is cheap (arithmetic comparisons). The LLM reviewer's job is to look at each flag and decide: "this matters, here's what to change" vs "this is a no-op, accept the greedy default." This leverages the LLM's interpretive strength rather than trying to encode judgment thresholds in code.

A conflict flagged as a no-op by the LLM still gets a decision log entry — the rationale for *why* it's a no-op is part of the audit trail.

Each conflict includes:
- What the greedy algorithm chose (its default)
- The alternatives the LLM can pick from
- The data needed to make the decision (prices, dates, percentages, supplier attributes)

Example conflict:

```python
Conflict(
    type=ConflictType.INFEASIBLE_DEADLINE,
    component_id="CMP-003",
    description="No supplier delivers CMP-003 by 2025-09-12. Greedy allocated to SUP-108 (3d late) + SUP-107 (24d late).",
    options=[
        {"action": "accept", "description": "Keep greedy plan, create deadline risk alert"},
        {"action": "expedite_SUP-107", "description": "Air freight SUP-107: 21d → delivers 2025-09-22 (10d late). Est. 3x cost."},
        {"action": "alert_only", "description": "Don't order, create alert for manual procurement"},
    ],
    greedy_choice={"action": "accept"},
    data={
        "deadline": "2025-09-12",
        "fastest_delivery": "2025-09-15",
        "days_late": 3,
        "suppliers": [
            {"id": "SUP-108", "delivery": "2025-09-15", "late_by": 3, "qty": 104},
            {"id": "SUP-107", "delivery": "2025-10-06", "late_by": 24, "qty": 104,
             "air_freight_delivery": "2025-09-22", "air_freight_late_by": 10},
        ],
    },
)
```

### 4. LLM Reviewer (LangGraph Subgraph)

**Location**: `procureai/agents/planner.py` (new file)

**Purpose**: Review the greedy plan, resolve conflicts, optionally override allocations. Only invoked if `len(conflicts) > 0`.

#### What Gets Prompt-Injected

The LLM receives in its system prompt:

1. **The full default plan** — every allocation from the greedy planner, serialized
2. **All conflicts** — each with its type, description, options, and the greedy default
3. **The decision log so far** — every greedy decision with rationale
4. **Constraints** — the full constraint list for reference
5. **Shared supplier summary** — cross-component supplier commitments

This is *not* the raw option matrix. The greedy algorithm already digested that. The LLM sees the plan and the problems, not raw data.

#### Reviewer Tools

| Tool | Purpose |
|------|---------|
| `resolve_conflict` | Pick a resolution for a specific conflict. Appends to decision log. |
| `override_allocation` | Change a greedy allocation (different supplier, different qty, add/remove). Appends to decision log. |
| `simulate_plan` | Dry-run the current plan (greedy + overrides) against all constraints. Returns violations/warnings. |
| `accept_plan` | Finalize the plan. Terminal action. |

##### `resolve_conflict(conflict_id: str, chosen_option: str, rationale: str) -> str`

Picks one of the options presented in the conflict. Logs the decision:

```
[D-010] CMP-003 | resolve_conflict | CF-001 INFEASIBLE_DEADLINE | chose: "accept" |
  "Both suppliers are late but SUP-108 is only 3 days. Accept and create alert." | llm_reviewer
```

Returns the updated plan state + remaining unresolved conflicts.

##### `override_allocation(component_id: str, changes_json: str, rationale: str) -> str`

Replaces or adjusts allocations for a component. The LLM can:
- Change supplier for an allocation
- Adjust quantities
- Add a new allocation (e.g., split to a third supplier)
- Remove an allocation
- Toggle `expedite` on/off

Logs the override:

```
[D-011] CMP-003 | override | SUP-107 qty 104→108 (bump to MOQ), expedite=true |
  "Air freight Nanjing to reduce late days. Accept 8-unit overbuy." | llm_reviewer
```

Returns updated plan + re-runs conflict detection on the changed component.

##### `simulate_plan() -> str`

Validates the entire plan (greedy base + LLM overrides) against all constraints. Same checks as `simulate_allocation` from the previous design: completeness, concentration, MOQ, duplicates, budget, delivery. Returns structured pass/fail/warning report.

The LLM can simulate after making changes to verify it didn't introduce new problems.

##### `accept_plan(rationale: str) -> str`

Finalizes the plan. Appends a terminal decision log entry. The plan is locked — no further changes.

#### Reviewer System Prompt

```
You are a procurement plan reviewer. A greedy algorithm has produced a default allocation
plan for all component shortfalls. Your job is to review it and resolve any conflicts
the algorithm flagged.

## Default Plan
{serialized_plan}

## Conflicts Requiring Resolution
{serialized_conflicts}

## Decision Log (algorithm decisions so far)
{decision_log}

## Active Constraints
{constraints}

## Your Process
1. Review the default plan and conflicts
2. For each conflict, use `resolve_conflict` to pick a resolution and explain why
3. If you want to change any allocation beyond the conflict resolutions, use `override_allocation`
4. Use `simulate_plan` to verify the final plan has no violations
5. Call `accept_plan` when satisfied

## Principles
- The greedy defaults are reasonable — only override with good reason
- Every decision must include rationale (this is auditable)
- Prefer the algorithm's choice unless the tradeoff is clearly wrong
- Create alerts for genuinely infeasible situations rather than forcing bad orders
```

#### Reviewer State

```python
class ReviewerState(MessagesState):
    plan_accepted: bool = False
```

#### Reviewer Graph

```
start → review_node (LLM) ──tool_calls──→ tool_node → review_node
                             │
                             └── no tool calls → end
```

`recursion_limit=25`. If the LLM runs out of iterations without calling `accept_plan`, auto-accept the greedy defaults for any unresolved conflicts and log "auto-accepted due to iteration limit."

### 5. Executor (Deterministic — No LLM)

**Location**: `procureai/agents/executor.py` (new file)

Same as previous design — a `for` loop over the finalized plan calling `place_order` and `create_alert`. No LLM.

```python
def execute_plan(
    plan: AllocationPlan,
    ctx: ProcurementContext,
    tools: list,
) -> ExecutionResult:
    results = []
    for alloc in plan.allocations:
        result = place_order.invoke({
            "component_id": alloc.component_id,
            "supplier_id": alloc.supplier_id,
            "quantity": alloc.quantity,
            "rationale": alloc.rationale,
        })
        results.append({"allocation": alloc, "result": result})

    for alert in plan.alerts:
        create_alert.invoke({"description": alert.description})

    return ExecutionResult(
        orders=results,
        alerts_created=len(plan.alerts),
        errors=[r for r in results if "ERROR" in r["result"]],
    )
```

---

## Decision Log — Full Lifecycle Example

This shows how the decision log tracks the entire planning process across algorithmic and LLM phases:

```
=== DECISION LOG ===

--- Greedy Allocation Phase ---

[D-001] 2025-09-01T00:00:01Z | CMP-005 | allocate
  SUP-101 × 40 ($1,800) | delivers 2025-09-13 ✓
  Rationale: Sole qualified supplier (ISO-9001 required, only SUP-101 certified)
  Source: greedy_algorithm

[D-002] 2025-09-01T00:00:01Z | CMP-001 | allocate
  SUP-101 × 287 ($3,587.50) | delivers 2025-09-11 ✓
  Rationale: Top fitness (0.89), on-time delivery, domestic, strategic tier
  Source: greedy_algorithm

[D-003] 2025-09-01T00:00:01Z | CMP-003 | split
  SUP-108 × 104 ($603.20) + SUP-107 × 104 ($338.00)
  Rationale: Concentration limit 50% (MEMO-2025-041). Split across top 2 by fitness.
  Source: greedy_algorithm

[D-004] 2025-09-01T00:00:01Z | CMP-003 | alert → CF-001 (INFEASIBLE_DEADLINE)
  Both suppliers deliver late. SUP-108: 3d late. SUP-107: 24d late.
  Greedy default: accept and create deadline risk alert.
  Source: greedy_algorithm

[D-005] 2025-09-01T00:00:01Z | CMP-003 | flag → CF-002 (DOMESTIC_VS_COST)
  SUP-108 (domestic, $5.80, 14d) vs SUP-107 (international, $3.25, 35d). Premium: 78%.
  Greedy chose: SUP-108 as primary (higher fitness). Flagged for LLM triage.
  Source: greedy_algorithm

[D-006] 2025-09-01T00:00:01Z | CMP-002 | allocate
  SUP-101 × 164 ($1,435) | delivers 2025-09-11 ✓
  Rationale: Top fitness (0.85), on-time, domestic, strategic tier
  Source: greedy_algorithm

[D-007] 2025-09-01T00:00:01Z | CMP-002 | flag → CF-003 (SUSTAINABILITY_TRADEOFF)
  SUP-101 (sustainability: B, $8.75) chosen over SUP-104 (sustainability: A, $9.20).
  Greedy chose: SUP-101 (higher fitness due to price + tier). Flagged for LLM triage.
  Source: greedy_algorithm

  ... (6 more component allocations) ...

--- Cross-Component Conflict Detection ---

[D-013] 2025-09-01T00:00:02Z | GLOBAL | flag → CF-006 (SHARED_SUPPLIER_LOAD)
  SUP-101 allocated across 5 components (CMP-001, CMP-002, CMP-005, CMP-006, CMP-009).
  Aggregate spend: $8,937.50. Flagged for LLM triage.
  Source: greedy_algorithm

--- LLM Review Phase ---

[D-014] 2025-09-01T00:00:15Z | CMP-003 | resolve_conflict CF-001
  Chose: "accept" — keep greedy allocation, create deadline risk alert.
  Rationale: "SUP-108 is only 3 days late and is the best domestic option.
  Air freight for SUP-107 would still be 10 days late at 3x cost — not worth it."
  Source: llm_reviewer

[D-015] 2025-09-01T00:00:18Z | CMP-003 | resolve_conflict CF-002 → no-op
  Chose: "accept" — keep domestic preference.
  Rationale: "78% premium is high, but SUP-108 delivers 21 days faster than
  SUP-107. For a critical component where both options are already late,
  the speed advantage overwhelmingly justifies the cost. No change needed."
  Source: llm_reviewer

[D-016] 2025-09-01T00:00:19Z | CMP-002 | resolve_conflict CF-003 → no-op
  Chose: "accept" — keep SUP-101 over SUP-104.
  Rationale: "5% price difference and SUP-101 is strategic tier. Sustainability
  delta (B vs A) is minor. No change needed."
  Source: llm_reviewer

[D-017] 2025-09-01T00:00:20Z | GLOBAL | resolve_conflict CF-006 → no-op
  Chose: "accept" — keep SUP-101 across 5 components.
  Rationale: "Aggregate spend $8,937 is well under budget thresholds. SUP-101
  is strategic tier with consistent delivery. Consolidation is a benefit here,
  not a risk."
  Source: llm_reviewer

[D-018] 2025-09-01T00:00:22Z | GLOBAL | simulate_plan
  Result: PASS with 2 warnings (2 late deliveries for CMP-003)
  Source: llm_reviewer

[D-019] 2025-09-01T00:00:24Z | GLOBAL | accept_plan
  Rationale: "All gaps covered. 2 late deliveries for CMP-003 are unavoidable —
  created alerts. All concentration limits satisfied. 3 soft-preference tradeoffs
  reviewed and accepted as no-ops. Total spend: $28,450."
  Source: llm_reviewer
```

This log is:
- **Append-only** — entries are never modified or deleted
- **Source-tagged** — clear which decisions came from the algorithm vs the LLM
- **Conflict-linked** — LLM decisions reference the conflict they resolved
- **Auditable** — you can reconstruct exactly why every order was placed
- **Queryable** — `log.for_component("CMP-003")` returns D-003, D-004, D-005, D-014, D-015

---

## Integration Flow in `agent.py`

```python
# New flow:
constraints = extract_constraints(...)
gap_df = gap_analysis(scenario_data)

# Phase 1: Deterministic planning
option_matrix = build_option_matrix(scenario_data, constraints, gap_df)
plan, conflicts, decision_log = greedy_allocate(option_matrix, constraints, gap_df)

# Phase 2: LLM review (only if conflicts exist)
if conflicts:
    plan, decision_log = llm_review(llm, plan, conflicts, decision_log, constraints)
else:
    decision_log.append(Decision(
        component_id="GLOBAL", action="accept_plan",
        details={}, rationale="No conflicts — greedy plan accepted automatically",
        source="greedy_algorithm", ...
    ))

if plan_only:
    print_plan(plan, decision_log)
    return

# Phase 3: Deterministic execution
ctx = ProcurementContext(scenario=scenario_data, constraints=constraints, gap_df=gap_df)
tools = build_tools(ctx)
exec_result = execute_plan(plan, ctx, tools)

# Phase 4: Persist + report
write_purchase_orders(...)
write_alerts(...)
print_summary(exec_result, decision_log)
```

### `--plan-only` Output

```
ALLOCATION PLAN (12 orders, 2 alerts)
  Generated by: greedy algorithm + LLM review (2 conflicts resolved)

  CMP-003 Neodymium Magnets (gap: 208, deadline: 2025-09-12):
    → SUP-108 MagnetPro × 104 ($603.20) — delivers 2025-09-15 ⚠ 3d late
    → SUP-107 Nanjing RE × 104 ($338.00) — delivers 2025-10-06 ⚠ 24d late
    Decisions: D-003 (split), D-004 (deadline alert), D-005 (domestic pref)
    LLM reviewed: CF-001 accepted, CF-002 accepted

  CMP-005 PCB Assembly (gap: 40, deadline: 2025-09-20):
    → SUP-101 Sterling × 40 ($1,800.00) — delivers 2025-09-13 ✓ on time
    Decisions: D-001 (sole supplier)

  ...

ALERTS:
  ⚠ CMP-003: Both suppliers deliver late. Fastest: SUP-108 (3 days late).

DECISION LOG: 17 entries (13 algorithmic, 4 LLM review)
Estimated total spend: $28,450.00
```

---

## Architecture Options Considered

### Option A: LLM Plans Everything (Original Design)

LLM receives raw option matrix and produces the full plan from scratch.

**Rejected**: Most allocation decisions are mechanical. The LLM would spend tokens on trivial cases (sole supplier, no conflicts) and still risk arithmetic errors on the hard ones.

### Option B: Greedy Plan + LLM Review (Selected)

Algorithm produces a complete default plan. LLM only reviews conflicts.

**Selected**: Minimizes LLM involvement to genuine judgment calls. Decision log provides full auditability. Zero LLM cost when there are no conflicts.

### Option C: Greedy Plan, No LLM

Fully deterministic — algorithm handles everything including conflict resolution via heuristics.

**Considered for later**: If conflict resolution heuristics prove sufficient across scenarios, we could make the LLM review optional. The architecture supports this — just skip the review phase.

---

## Sequencing Relative to In-Progress Work

### What Must Complete First

1. **Constraint extraction agent Steps 7-10** — the planner depends on reliable constraint extraction. If `CONCENTRATION_LIMIT` isn't extracted, `build_option_matrix()` can't compute max quantities.

2. **Step 1 enriched tools** — the enriched output patterns inform the option matrix design. Concentration guardrails in `place_order` serve as the executor's safety net.

### What Can Happen in Parallel

- **`build_option_matrix()` + fitness scoring** — pure deterministic code, testable with mock constraints
- **`greedy_allocate()` + `detect_conflicts()`** — pure deterministic, testable with mock option matrices
- **`DecisionLog`** — data structure, trivially testable
- **Executor** — simple loop, testable independently

### Suggested Phasing

```
Phase 0 (now):     Finish constraint extraction agent (Steps 7-10)
Phase 1 (next):    OptionMatrix + SupplierOption + fitness scoring + tests
Phase 2:           greedy_allocate + detect_conflicts + DecisionLog + tests
Phase 3:           simulate_plan + LLM reviewer subgraph + tests
Phase 4:           Executor + agent.py rewiring + --plan-only flag
Phase 5:           Full scenario runs + model eval comparison
```

Phases 1-2 are fully deterministic — no LLM dependency, fast iteration.

---

## New/Modified Files

### New Files

| File | Purpose |
|------|---------|
| `procureai/planner.py` | `build_option_matrix()`, `greedy_allocate()`, `detect_conflicts()`, data structures |
| `procureai/agents/planner.py` | LLM reviewer subgraph: tools, graph wiring |
| `procureai/agents/executor.py` | Deterministic `execute_plan()` |
| `tests/test_planner.py` | Unit tests for option matrix, greedy allocator, conflict detection |
| `tests/test_reviewer.py` | LLM integration tests for conflict resolution |
| `tests/test_executor.py` | Unit tests for plan execution |

### Modified Files

| File | Changes |
|------|---------|
| `procureai/agents/graph.py` | Rewire `build_agent()` or add `build_planning_agent()` |
| `procureai/agents/state.py` | Add `ReviewerState` |
| `agent.py` | Add `--plan-only`, update pipeline to greedy → review → execute |

### Unchanged Files

| File | Why |
|------|-----|
| `procureai/agents/tools.py` | `place_order`, `create_alert` reused by executor |
| `procureai/constraints.py` | Upstream — unchanged interface |
| `procureai/agents/constraint_graph.py` | Independent, completes separately |
| `procureai/extraction.py` | Independent |
| `procureai/pipeline.py` | `gap_analysis()` unchanged. `build_option_matrix()` goes in new `planner.py` |

---

## Testing Strategy

### Unit Tests (`tests/test_planner.py`)

**Option matrix:**
- `test_option_matrix_filters_blocked_suppliers` — SUP-113 excluded
- `test_option_matrix_filters_unapproved` — non-approved excluded
- `test_option_matrix_computes_delivery_dates` — lead time + current date
- `test_option_matrix_computes_concentration_max` — respects max_pct
- `test_option_matrix_fitness_ranking` — on-time domestic supplier scores highest
- `test_option_matrix_shared_suppliers` — multi-component suppliers detected
- `test_option_matrix_priority_ordering` — earliest deadline first

**Greedy allocator:**
- `test_greedy_sole_supplier` — one eligible supplier → allocate full gap
- `test_greedy_concentration_split` — 50% limit → split across 2 suppliers
- `test_greedy_moq_roundup` — quantity < MOQ → rounds up, flags overbuy
- `test_greedy_covers_all_gaps` — every component in gap_df has allocations or alerts
- `test_greedy_priority_ordering` — earliest-deadline components processed first
- `test_greedy_no_eligible_supplier` — creates alert, not allocation

**Conflict detection (hard):**
- `test_detect_infeasible_deadline` — all suppliers late → INFEASIBLE_DEADLINE
- `test_detect_concentration_split` — split required → CONCENTRATION_SPLIT
- `test_detect_budget_threshold` — aggregate >$50K → BUDGET_THRESHOLD
- `test_detect_moq_overbuy` — MOQ significantly exceeds gap → MOQ_OVERBUY
- `test_detect_no_eligible` — all filtered out → NO_ELIGIBLE_SUPPLIER

**Conflict detection (soft — always flagged when conditions exist):**
- `test_detect_domestic_vs_cost_always_flagged` — domestic + international both eligible → flagged regardless of price delta
- `test_detect_strategic_loyalty_always_flagged` — non-strategic chosen → flagged regardless of savings amount
- `test_detect_sustainability_always_flagged` — lower-rated chosen over higher-rated → flagged
- `test_detect_shared_supplier_load` — 2+ components from same supplier → flagged
- `test_no_soft_conflicts_sole_supplier` — only one eligible supplier → no DOMESTIC_VS_COST (no alternative exists)

**Decision log:**
- `test_log_append_only` — entries accumulate, can't modify
- `test_log_for_component` — filter by component_id
- `test_log_source_tagging` — greedy vs llm entries distinguishable

**Simulation:**
- `test_simulate_complete_plan_passes` — all gaps covered → PASS
- `test_simulate_missing_component` — uncovered gap → FAIL
- `test_simulate_concentration_violation` — over-concentrated → FAIL
- `test_simulate_after_override` — LLM override validated correctly

### LLM Integration Tests (`tests/test_reviewer.py`)

- `test_reviewer_resolves_all_conflicts` — given N conflicts (hard + soft), all get resolutions
- `test_reviewer_triages_soft_as_noop` — soft-preference tradeoffs can be accepted with "no change needed" rationale
- `test_reviewer_overrides_when_warranted` — given a clear bad tradeoff (e.g., 3x price for negligible benefit), LLM overrides the greedy choice
- `test_reviewer_calls_simulate_before_accept` — simulation happens before `accept_plan`
- `test_reviewer_decision_log_has_rationale` — every resolution (including no-ops) includes rationale text
- `test_reviewer_decision_log_grows` — log has more entries after review than before

### End-to-End

- Run all 6 scenarios with greedy + review + execute
- Compare to single-agent baseline
- Measure: conflicts generated per scenario, LLM invocations, pass rate, concentration compliance

---

## Impact on Failure Classes

| Failure | Step 1 Fix | Planner-Executor Fix |
|---------|-----------|---------------------|
| F1: Concentration | Tool guardrail rejects | Greedy splits automatically. No LLM arithmetic needed. |
| F2: Duplicates | Tool duplicate detection | Plan is structural — can't produce duplicates by construction |
| F3: Early termination | N/A | **Fixed**: Greedy covers all gaps deterministically. LLM only reviews conflicts. Can't quit early. |
| F4: Missing alerts | Delivery warning | Greedy flags INFEASIBLE_DEADLINE conflicts. Alerts created deterministically for late deliveries. |

---

## Open Questions

1. **Fallback to single-agent mode?** If the greedy planner or LLM reviewer fails, fall back to the current ReAct agent? Adding `--legacy` flag is cheap insurance. Recommend yes.

2. **Expedite as explicit opt-in** — should air freight be an allocation-level `expedite: bool` that the LLM can toggle per conflict? Or keep auto-applying in `place_order`? Recommend explicit opt-in — the LLM can see the cost/time tradeoff in the conflict data.

3. **Hard-conflict auto-resolution** — for hard conflicts with obvious answers (INFEASIBLE_DEADLINE where only one supplier exists, MOQ_OVERBUY with trivial excess), should the greedy algorithm auto-resolve and log the decision without flagging for LLM review? This would reduce noise in the conflict list. The LLM would still see these in the decision log but wouldn't be asked to act on them. Tradeoff: less LLM work vs less auditability on edge cases.

4. **Decision log persistence** — should the log be written to the SQLite DB alongside POs and alerts? Would enable post-hoc analysis across runs. Recommend yes — add a `decision_log` table.

---

## References

- Original sketch: `research/planner-executor-sketch.md`
- Procurement gaps design: `research/procurement-gaps-design-2026-04-23.md`
- Step 1 plan: `plans/step1-enriched-tools-implementation-plan.md`
- Constraint extraction design: `research/constraint-extraction-agent-design-2026-04-23.md`
- Constraint extraction plan: `plans/constraint-extraction-agent-implementation-plan.md`
- Model eval results: `output/model-eval-*.md`
- TODO tracker: `TODO.md`
