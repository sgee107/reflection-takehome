# Design Sketch: Planner → Executor Architecture

**Date:** 2026-04-23
**Status:** Exploration / not yet planned

---

## Problem Statement

The current agent does strategy and execution in one pass. It sees a flat list of shortfalls, a flat list of constraints, and makes per-component decisions sequentially. This causes:

1. **No cross-component reasoning** — magnets and MOSFETs might share a supplier with limited capacity, but the agent doesn't consider that placing a large magnet order affects MOSFET availability from the same supplier.
2. **Constraint interactions missed** — the LLM handles each constraint individually but fails to compose them (concentration + domestic preference + timeline = split order).
3. **No feasibility pre-screening** — the agent discovers "this deadline is infeasible" mid-execution rather than planning around it upfront.
4. **Arithmetic burden on LLM** — percentage calculations, date comparisons, and quantity splits are done in the LLM's head instead of in code.

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    DETERMINISTIC LAYER                   │
│  load_scenario → gap_analysis → constraint_extraction   │
│  (unchanged from current)                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    ENRICHMENT LAYER (new)                │
│  For each (component, supplier) pair:                   │
│  - compute delivery date vs deadline                    │
│  - compute concentration impact                         │
│  - flag cert/approved/blocked status                    │
│  - annotate domestic, sustainability, strategic tier    │
│  - check if supplier is shared across shortfalls        │
│                                                         │
│  Output: SupplierOptionMatrix (all options, annotated)  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    PLANNER AGENT (new)                   │
│  Input: gap_df + SupplierOptionMatrix + constraints     │
│  Task: produce an AllocationPlan                        │
│                                                         │
│  The planner reasons about:                             │
│  - Which shortfalls to prioritize (deadline urgency)    │
│  - How to split orders (concentration limits)           │
│  - Supplier capacity conflicts across components        │
│  - Which deadlines are infeasible (pre-flag alerts)     │
│  - Domestic preference tradeoffs                        │
│                                                         │
│  Output: AllocationPlan (structured, not free text)     │
│  ┌───────────────────────────────────────────────────┐  │
│  │ CMP-003: SUP-107 × 150 (49%), SUP-108 × 158 (51%)│  │
│  │ CMP-005: SUP-101 × 40 (sole qualified supplier)  │  │
│  │ CMP-001: ALERT — no supplier meets 11-day deadline│  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  Tools available to planner:                            │
│  - get_option_matrix(component_id) → annotated table   │
│  - simulate_allocation(plan) → constraint violations   │
│  - get_shared_suppliers() → supplier-component map      │
└────────────────────────┬────────────────────────────────┘
                         │
                    (human review checkpoint — optional)
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    EXECUTOR AGENT (simplified)           │
│  Input: AllocationPlan                                  │
│  Task: place orders per the plan                        │
│                                                         │
│  For each allocation in plan:                           │
│    call place_order(component, supplier, qty, rationale)│
│  For each alert in plan:                                │
│    call create_alert(description)                       │
│                                                         │
│  Minimal reasoning needed — just follows the plan.      │
│  Could potentially be fully deterministic (no LLM).     │
└─────────────────────────────────────────────────────────┘
```

## Enriched `get_eligible_suppliers` — Example Output

Current output (flat table):
```
supplier_id    name             unit_price  lead_time_days  minimum_order_qty  is_domestic  certifications
SUP-108        MagnetPro Inc.   5.80        14              50                 1            ISO-9001, ISO-14001
SUP-107        Nanjing Rare     3.25        35              100                0            ISO-9001
```

Proposed annotated output:
```
CMP-003 (Neodymium Magnets) — gap: 208 units, needed by: 2025-09-12

SUP-108 MagnetPro Inc. | $5.80 | 14 days → delivers 2025-09-15
  ⚠ LATE by 3 days (needed 2025-09-12)
  ✓ domestic | ✓ approved | ✓ ISO-9001 | sustainability: A | tier: preferred
  ⚠ concentration: 0% now → would be 100% if sole supplier (limit: 50%)
  MOQ: 50 | gap coverage: full (208 units)

SUP-107 Nanjing Rare Earth | $3.25 | 35 days → delivers 2025-10-06
  ✗ LATE by 24 days (needed 2025-09-12)  [air freight: 21 days → 2025-09-22, still late by 10 days]
  ✗ international | ✓ approved | ✓ ISO-9001 | sustainability: B | tier: standard
  ✓ concentration: 0% now
  MOQ: 100 | gap coverage: full (208 units)

CONSTRAINT NOTES for CMP-003:
  - CONCENTRATION_LIMIT: max 50% per supplier, min 20% secondary (MEMO-2025-041)
  - CRITICAL_COMPONENT: yes (neodymium magnets) — 50% domestic premium threshold
  - Recommended split: ≤104 to any single supplier
  - ⚠ TIMELINE: no supplier delivers on time. Fastest: SUP-108 at 14 days (3 days late).
```

The LLM no longer needs to:
- Compute delivery dates (done)
- Compare against deadlines (done)
- Calculate concentration percentages (done)
- Look up which constraints apply (done)
- Figure out the max quantity per supplier (done)

It only needs to make the judgment call: "given that both options are late, how should I split this order and what alert should I create?"

## `simulate_allocation` Tool — Planner Validation

Before the planner finalizes, it can test its plan:

```python
@tool
def simulate_allocation(allocations: list[dict]) -> str:
    """Dry-run a set of allocations and report constraint violations.

    Input: [{"component_id": "CMP-003", "supplier_id": "SUP-108", "quantity": 104}, ...]
    Output: list of violations, warnings, and a pass/fail summary.
    """
    # Check each allocation against:
    # - concentration limits (across the full plan, not just individual orders)
    # - delivery feasibility
    # - MOQ compliance
    # - budget thresholds (aggregate)
    # - duplicate detection
    # - remaining uncovered gaps
```

This lets the planner iterate: propose a plan, simulate it, adjust, simulate again. The feedback loop is deterministic — the planner can't fool itself.

## Why Planner + Executor Instead of Validator

A validator (post-hoc check) has the problem of being reactive — the agent places an order, the validator rejects it, the agent tries again. This burns tokens and can loop.

A planner (pre-hoc) reasons about the whole problem at once. It sees that CMP-003 needs splitting *before* placing any orders, not after placing 208 units with one supplier and getting rejected.

The executor can potentially be **fully deterministic** — if the plan is structured enough, no LLM needed for execution. This would make the system:
- Deterministic pipeline (gap analysis) → **LLM** (planning/judgment) → Deterministic execution (order placement)
- LLM is sandboxed to the one phase where judgment is actually needed
- Every failure is traceable to the plan, not to execution

## Shared Supplier Dependencies (Cross-Component Reasoning)

One thing the current architecture completely misses. Example from scenario 01:

```
SUP-101 (Sterling Components) supplies:
  CMP-001 Steel Laminations    — $12.50, 10 days, MOQ 100
  CMP-002 Copper Wire          — $8.75,  10 days, MOQ 50
  CMP-005 PCB Assembly         — $45.00, 12 days, MOQ 25
  CMP-006 Microcontroller IC   — $15.00, 10 days, MOQ 50
  CMP-009 Industrial Connector — $3.50,  10 days, MOQ 100
```

If the agent orders all 5 components from SUP-101 (because they're domestic, strategic-tier, reasonably priced), the total cost might exceed the $50K budget threshold when viewed in aggregate — but each individual order looks fine. The planner could catch this because it sees the full allocation at once.

A `get_shared_suppliers()` tool would surface this:

```
Supplier SUP-101 supplies 5 of your 13 shortfall components.
If all allocated to SUP-101, aggregate spend: $87,400 (triggers $50K + $150K thresholds).
Consider spreading across: SUP-102, SUP-105, SUP-106 for overlapping components.
```

## Impact on Current Codebase

| File | Change |
|------|--------|
| `procureai/agents/tools.py` | Enrich `get_eligible_suppliers` with annotations. Add `simulate_allocation`, `get_shared_suppliers`. Possibly add `get_option_matrix`. |
| `procureai/agents/graph.py` | Wire two-phase graph: planner subgraph → executor subgraph. Or keep single graph with planner tools first, execution tools second. |
| `procureai/agents/prompts.py` | Separate planner prompt (strategic) from executor prompt (tactical). Planner gets constraint-heavy context; executor gets the plan. |
| `procureai/pipeline.py` | Add `build_option_matrix()` — precompute all (component, supplier) pairs with annotations. |
| `agent.py` | Add optional `--plan-only` flag to stop after plan phase for human review. |

## Open Questions

1. **Single graph or two separate agents?** LangGraph supports subgraphs — could wire planner and executor as sequential subgraphs within one compiled graph. Simpler than managing two separate agent invocations.
2. **How structured should the plan be?** JSON allocation table (machine-readable, executor can be deterministic) vs free-text plan (more flexible, executor needs LLM). Probably JSON with an optional rationale field.
3. **Does the executor need to be an LLM at all?** If the plan is structured and validated by `simulate_allocation`, the executor could be a simple loop: `for alloc in plan: place_order(...)`. The only value the LLM adds in execution is generating human-readable rationale text for each PO.
4. **Planner model vs executor model** — could use a more capable model (opus) for planning and a cheaper one (haiku) for execution, or skip LLM execution entirely. This has cost implications.
