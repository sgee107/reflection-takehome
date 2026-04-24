# Procurement Gaps & Missing Considerations — Design Document

**Date**: 2026-04-23
**Author**: Claude Code
**Status**: Draft

## Problem Statement

ProcureAI handles the "happy path" of procurement well — BOM explosion, gap analysis, supplier filtering, order placement — but leaves several real-world procurement considerations unaddressed. Model eval results (2026-04-23) confirm the pattern: constraints enforced in tool code pass on all models (Sonnet, Haiku, Qwen 7B), while constraints left to LLM reasoning degrade with model capability. This design catalogs what we're missing, separates what's actionable within the current fixed schema from what requires future work, and proposes a phased approach.

## Goals

- Identify procurement edge cases and failure modes not currently handled
- Classify each gap as "current scope" (fixed schema) or "future phase" (schema changes / new capabilities)
- For current-scope items, propose concrete changes that follow the established pattern: move enforcement into deterministic tool code
- For future-phase items, sketch the direction without over-designing

## Non-Goals

- Changing the scenario DB schema (fixed constraint)
- Implementing multi-period / rolling horizon planning (future phase, requires forecasting design)
- Replacing the existing PO management approach (being worked by another issue)

---

## Part 1: Gaps Within Current Scope (Fixed Schema)

These can be addressed with changes to tool code, pipeline logic, or prompt structure — no schema modifications needed.

### 1.1 Cross-Component Supplier Dependencies

**The gap**: Each shortfall is processed independently. The agent never sees that SUP-101 supplies 5 of 13 shortfall components, and ordering all from SUP-101 could push aggregate spend past the $50K budget threshold even though each individual order looks fine.

**Why it matters**: Budget thresholds, capacity limits, and delivery scheduling all interact across components when they share a supplier. The current per-component workflow can't reason about these interactions.

**Proposed approach**: A `get_supplier_portfolio` tool that surfaces cross-component visibility:

```
SUP-101 (Sterling Components) — supplies 5 of your 13 shortfall components:
  CMP-001: gap 287, $12.50/unit → $3,587.50
  CMP-002: gap 164, $8.75/unit  → $1,435.00
  CMP-005: gap 40,  $45.00/unit → $1,800.00
  CMP-006: gap 96,  $15.00/unit → $1,440.00
  CMP-009: gap 193, $3.50/unit  → $675.50

  Potential aggregate: $8,937.50 (under $50K threshold)
  Shared lead time: 10-12 days
  Consolidation benefit: single shipment, one receipt
```

This is complementary to the enriched `get_eligible_suppliers` output in Step 1 — that tool annotates per-component, this tool annotates per-supplier. Together they give the planner (Step 2) or the current single agent a complete picture.

**Fits the pattern**: Deterministic computation surfaced as tool output. No new LLM reasoning burden — the agent just gets better information.

### 1.2 Composite Supplier Fitness Scoring

**The gap**: `get_eligible_suppliers` returns a flat table of fields. The LLM must mentally synthesize price, lead time, domestic status, sustainability, relationship tier, certifications, and MOQ into a ranking. This is the kind of arithmetic that degrades on weaker models.

**Why it matters**: Supplier selection is a multi-criteria decision. Humans use weighted scoring models; the LLM is doing this implicitly and inconsistently.

**Proposed approach**: Compute a normalized fitness score per supplier using available fields:

| Criterion | Field(s) | Weight | Normalization |
|---|---|---|---|
| Price | `unit_price` | 25% | min/max inversion (lower = better) |
| Lead Time Feasibility | `lead_time_days` vs deadline | 25% | 0 if late, gradient if feasible |
| Certification Match | `certifications` | 15% | 1.0 if required cert present, else 0 (hard gate) |
| Relationship Tier | `relationship_tier` | 10% | strategic=1.0, preferred=0.7, standard=0.4 |
| Sustainability | `sustainability_rating` | 10% | A=1.0, B=0.6, C=0.3 |
| Domestic Preference | `is_domestic` | 10% | 1.0 domestic, 0.5 international |
| MOQ Fit | `minimum_order_qty` vs gap | 5% | 1.0 if MOQ ≤ gap, else gap/MOQ |

Hard gates (approved list, blocked, required certs) filter *before* scoring — they're constraints, not tradeoffs. The score only ranks suppliers that pass all hard gates.

**Key design decision**: Weights could be derived from policy constraints (e.g., if `DOMESTIC_PREFERENCE` has `max_premium_pct: 35`, that implies a weight). For v1, fixed weights surfaced in the enriched output are sufficient. The LLM can still override the ranking — the score is advisory, not binding.

**Output integration**: Add a `fitness: 0.82` line to each supplier in the enriched `get_eligible_suppliers` output (Step 1, Step 5). Sort suppliers by fitness score descending. The LLM sees the recommended ranking but retains judgment.

### 1.3 Lot Sizing Beyond MOQ

**The gap**: The system uses lot-for-lot ordering — each gap is filled exactly (rounded up to MOQ). No consideration of ordering frequency, consolidation, or the cost of placing many small orders.

**Why it matters in the current scope**: When multiple production orders need the same component at different dates, the current pipeline aggregates them into a single `total_needed` with a single `earliest_needed_by`. This is already a form of lot consolidation (period order quantity = the full planning horizon). But it means a component needed in week 1 and week 4 is ordered entirely in week 1, potentially over-ordering relative to what's needed immediately.

**Within fixed schema**: The current approach (aggregate everything, order once) is actually reasonable for a single-snapshot system. The risk is over-ordering vs. the benefit of fewer POs. Since we can't change the schema to add lot-sizing policies per component, this is best left as a future consideration.

**What we CAN do now**: When `MOQ > gap`, the enriched tool output should explicitly flag the overage: "MOQ 100, gap 40 → forced overbuy of 60 units (150% excess)". This gives the LLM information to decide whether to accept the overbuy or explore alternatives.

### 1.4 Scrap Rate / Yield Loss Awareness

**The gap**: `explode_demand()` computes `quantity_needed = quantity * quantity_per` with no scrap adjustment. If a component has a 5% historical reject rate, the agent systematically under-orders.

**Within fixed schema**: The BOM table doesn't have a scrap rate column. We can't add one. But we *can* add a configuration mechanism outside the schema — either a `scrap_rates.json` config file or a constraint type `YIELD_ADJUSTMENT` that the LLM extracts from policy docs.

**Proposed approach**: Add `YIELD_ADJUSTMENT` to the `ConstraintType` enum:

```python
YIELD_ADJUSTMENT = "YIELD_ADJUSTMENT"
# params: {"component_id": "CMP-005", "scrap_pct": 0.05}
```

If extracted from policy documents, apply it in `pipeline.py` after demand aggregation:

```
adjusted_need = total_needed / (1 - scrap_pct)
```

If no yield constraint exists for a component, assume 0% scrap (current behavior).

**Tradeoff**: This is a lightweight extension that doesn't touch the DB schema but does add a new constraint type. The extraction prompt already handles arbitrary types via `OTHER`. Worth implementing only if the policy/memo PDFs actually mention scrap rates or quality rejection rates. If they don't, this is speculative.

### 1.5 Air Freight as a Deliberate Cost Decision (Not Automatic)

**The gap**: Air freight is currently applied automatically to all international suppliers when the `AIR_FREIGHT_ALLOWED` constraint is active and the date falls within the window. The agent never sees the cost delta — it just gets a silently adjusted lead time.

**Why it matters**: Air freight is a cost/time tradeoff. The decision to expedite should be visible: "standard delivery: 35 days, $3.25/unit. Air freight: 21 days, ~$9.75/unit (estimated 3x). Which do you prefer for this order?"

**Proposed approach**: Don't auto-apply air freight in `place_order`. Instead:
1. In enriched `get_eligible_suppliers`, show both standard and air-freight options for international suppliers (already in the Step 1 plan)
2. Add an `expedite: bool` parameter to `place_order` — the agent explicitly opts in
3. When `expedite=True`, apply the lead time reduction and add a cost surcharge alert

This transforms air freight from an implicit system behavior into an explicit agent decision — which is where the LLM adds value (judging the cost/time tradeoff).

---

## Part 2: Supply Pipeline Visibility

The user raised the concept of the supply pipeline — the full lifecycle of a PO from placement through delivery. This sits between "current scope" and "future phase."

### What the Pipeline Looks Like

Real procurement systems track POs through stages:

```
Draft → Approved → Sent → Confirmed → Shipped → Received → Inspected → Closed
```

For an AI procurement agent, the minimum viable pipeline model is three stages:

| Stage | Meaning | Confidence |
|---|---|---|
| **Ordered** | PO placed, not yet confirmed by supplier | Low — can be canceled, may not be accepted |
| **Confirmed** | Supplier acknowledged, production/shipping underway | Medium — delivery date is a commitment |
| **Delivered** | Goods received and available for production | High — supply is real |

### How Pipeline Visibility Affects the Current System

Currently, `compute_incoming()` treats all existing POs as equally reliable supply. This is optimistic. A PO that was placed yesterday and hasn't been confirmed is less reliable than one that shipped last week.

**What we can do within the fixed schema**: The `purchase_orders` table likely has `order_date` and `expected_delivery_date`. We can infer a rough pipeline stage:

- If `expected_delivery_date < current_date` → likely received (or overdue — a risk signal)
- If `expected_delivery_date` is near but `order_date` was recent → freshly placed, lower confidence

This is crude but better than treating all POs equally.

**What the enriched tool output should surface**: When showing existing POs for a component, annotate them:

```
Existing supply pipeline for CMP-001:
  EXIST-001: 100 units from SUP-101, expected 2025-09-05 (4 days out, likely in transit)
  EXIST-002: 50 units from SUP-103, expected 2025-09-20 (19 days out, early stage)
  Total pipeline: 150 units | High-confidence: 100 | At-risk: 50
```

### Future Pipeline Work

A real pipeline model would require:
- A `po_status` column in `purchase_orders` (schema change)
- Status transition tracking (when did it move from ordered → confirmed?)
- Partial receipt handling (50 of 100 units received)
- Expediting as an action the agent can take on existing POs (not just new orders)

This is out of scope but should inform schema design if the scenarios are ever extended.

---

## Part 3: Tradeoffs We're Making

These are deliberate architectural tradeoffs in the current system, stated explicitly so they can be revisited.

### 3.1 Single Snapshot vs. Rolling Horizon

**What we do**: One `current_date`, one gap analysis, one procurement pass. Done.

**What we give up**: No concept of demand evolution over time. Can't sequence orders strategically (e.g., "order CMP-001 now but wait 5 days for CMP-009 since the deadline is 3 weeks out"). Can't model inventory burn rate or reorder points.

**Why it's acceptable for now**: The take-home scenarios are each a single decision point. The agent is evaluated on whether it makes correct procurement decisions for that snapshot.

**What a shift would require**: See "Future Phase: Rolling Horizon" below.

### 3.2 Lot-for-Lot Ordering

**What we do**: Order exactly the gap quantity (rounded up to MOQ).

**What we give up**: Volume discounts, reduced ordering frequency, strategic inventory building.

**Why it's acceptable**: Without price-break data in the schema, there's nothing to optimize against. MOQ rounding is the only lot-sizing we can do.

### 3.3 Advisory Alerts vs. Hard Gates

**What we do**: Budget thresholds, deadline infeasibility, and other warnings generate alerts but don't block order placement.

**What we give up**: Compliance enforcement. An agent can (and does) place orders that exceed budget thresholds without approval.

**Why it's acceptable for the take-home**: The scenarios test whether the agent *identifies* issues and *creates appropriate alerts*. Blocking would require an approval workflow (human-in-the-loop) that's out of scope.

**Future direction**: Make gate behavior policy-driven. A constraint could specify `enforcement: "gate"` vs `enforcement: "advisory"`:

```python
Constraint(
    type=ConstraintType.BUDGET_THRESHOLD,
    params={"amount": 50000, "approver": "Procurement Manager", "enforcement": "gate"},
    ...
)
```

When `enforcement: "gate"`, `place_order` refuses the order and returns: "BLOCKED: Requires Procurement Manager approval for orders >$50K. Total: $67,200. Use `request_approval(po_details)` to escalate."

### 3.4 No Safety Stock

**What we do**: `gap = total_needed - on_hand - incoming`. If on_hand exactly covers demand, gap = 0, no order placed.

**What we give up**: Buffer against demand spikes, supply delays, or quality rejections.

**Why it's acceptable**: The scenarios define exact demand. There's no demand uncertainty to buffer against. Safety stock makes sense in a live system with variable demand, not in a deterministic take-home.

### 3.5 Binary Supplier Risk (Approved / Blocked)

**What we do**: Suppliers are either approved (can order) or blocked (can't).

**What we give up**: Nuanced risk assessment — financial health, capacity utilization, quality history, geopolitical exposure.

**Why it's acceptable**: The schema provides `on_approved_list`, `sustainability_rating`, and `relationship_tier`. These are proxies for risk. The composite fitness score (§1.2) makes better use of what we have without requiring external risk data.

---

## Part 4: Future Phases (Out of Scope, Directional)

### Phase A: Rolling Horizon Planning

**Trigger**: When the product moves beyond single-snapshot evaluation to ongoing procurement management.

**Key capabilities**:

1. **Multi-period demand**: Instead of one `current_date`, model a planning horizon (e.g., 8 weeks). Each period has its own demand, and the gap analysis produces a time-phased requirements schedule.

2. **Inventory projection**: Track projected on-hand over time: `projected_oh[t] = oh[t-1] + receipts[t] - consumption[t]`. Orders are placed when projected on-hand drops below reorder point.

3. **Order sequencing**: Not all orders need to be placed today. If Component A is needed in week 1 and Component B in week 4, the agent can stagger orders to manage cash flow and warehouse capacity.

4. **Demand forecasting integration**: This is where it gets interesting and where we'd need to collaborate. Options range from simple (exponential smoothing on historical demand) to complex (ML-based demand sensing from sales pipeline data). The forecasting approach drives the entire rolling horizon — it determines how far out you plan and how much confidence you have in future periods.

**Schema implications**: `production_schedule` would need a `period` or `required_date` per order. `inventory` would need `reorder_point` and `safety_stock` columns. A new `demand_forecast` table could provide forward-looking requirements.

**Architecture implications**: The pipeline becomes iterative — run gap analysis per period, cascade surplus/deficit forward. The planner (Step 2) reasons across periods, not just across components.

### Phase B: Policy-Driven Enforcement Modes

**Trigger**: When the system needs to support different organizational policies (e.g., "strict compliance" vs. "field expedience").

**Key capability**: Each constraint gets an `enforcement` field:

| Mode | Behavior |
|---|---|
| `hard_gate` | `place_order` refuses the order. Agent must find an alternative or escalate. |
| `soft_gate` | `place_order` warns and asks for confirmation. Agent can override with rationale. |
| `advisory` | Alert logged, order proceeds. Current behavior for budget thresholds. |
| `informational` | Surfaced in tool output but no enforcement. Current behavior for domestic preference. |

This makes the system configurable per deployment context. A defense contractor might run everything as `hard_gate`; a startup might use `advisory` across the board.

**Architecture implications**: The `Constraint` model gets an `enforcement` field. `place_order` checks enforcement mode before deciding whether to block, warn, or log.

### Phase C: Supplier Risk Pipeline

**Trigger**: When the system integrates with external data sources (financial APIs, news feeds, trade databases).

**Key capabilities**:
- Real-time financial health scoring (Dun & Bradstreet, credit ratings)
- Geopolitical risk overlays (tariffs, sanctions, conflict zones)
- Quality history from receiving inspection data
- Capacity utilization from supplier collaboration portals

**Within the current system**: The composite fitness score (§1.2) is the foundation. It uses available fields today. As external data sources come online, they become additional scoring dimensions with their own weights.

---

## Architecture Options

### Option 1: Enrich Tools Only (Minimal Change)

Add cross-component visibility and fitness scoring to the existing toolset without restructuring.

**Pros**: Smallest change, builds on Step 1 plan, no new agents or graph changes.
**Cons**: Still relies on a single agent to synthesize cross-component information.

### Option 2: Enrich Tools + Planner/Executor (Step 1 + Step 2)

The enriched tools feed into a planner agent that reasons holistically, then an executor follows the plan.

**Pros**: Fixes all four failure classes (F1-F4). Cross-component reasoning happens in the planner. Executor can be deterministic or use a cheap model.
**Cons**: More complex architecture. Two-phase execution adds latency.

### Option 3: Full Enrichment Layer (Deterministic Pre-computation)

Build a `SupplierOptionMatrix` that pre-computes everything — fitness scores, concentration impacts, delivery feasibility, cross-component dependencies — before the LLM sees anything.

**Pros**: Maximizes deterministic computation. LLM only makes genuine judgment calls. Most robust across model tiers.
**Cons**: Largest upfront investment. Risk of over-engineering for the current take-home scope.

### Recommendation

**Option 2** — it's already the planned trajectory (Step 1 → Step 2). The gaps identified in this document (§1.1-§1.5) fit naturally as additions to Step 1's enrichment work. The planner/executor decomposition (Step 2) then gets better inputs from the enriched tools, and the future phases (Part 4) extend naturally from this foundation.

Sequencing:
1. **Step 1** (in progress): Enriched tools + hard constraint enforcement. Add §1.2 (fitness scoring), §1.3 (MOQ overbuy flagging), §1.5 (explicit air freight) to the Step 1 plan.
2. **Step 1.5** (new): Cross-component supplier visibility tool (§1.1). Needed before Step 2 so the planner has the information.
3. **Step 2** (planned): Planner/executor decomposition. The planner consumes enriched per-component data AND cross-component supplier data.
4. **Future phases**: Rolling horizon, policy-driven gates, external risk — each phase extends the foundation without restructuring.

---

## Integration Points

- **Step 1 plan** ([plans/step1-enriched-tools-implementation-plan.md](../plans/step1-enriched-tools-implementation-plan.md)): §1.2, §1.3, §1.5 are additions to Steps 3-5 in that plan
- **Planner/executor sketch** ([research/planner-executor-sketch.md](planner-executor-sketch.md)): §1.1 feeds directly into the planner's `get_shared_suppliers` tool concept
- **Pipeline work** (separate issue): §2 (supply pipeline) should be coordinated — the pipeline visibility work affects how `compute_incoming()` weights existing POs

## Testing Strategy

- **Fitness scoring**: Unit test with known supplier data → verify ranking matches expected order
- **Cross-component tool**: Unit test with multi-component scenarios (01, 03) → verify aggregate spend calculation
- **MOQ overbuy flagging**: Unit test with MOQ > gap → verify warning text
- **Air freight opt-in**: Unit test with `expedite=True/False` → verify lead time adjustment is conditional

## Open Questions

1. **Fitness score weights** — Should weights be fixed or derived from constraints? E.g., if `DOMESTIC_PREFERENCE` has `max_premium_pct: 35`, does that imply a higher weight for domestic preference?
2. **Cross-component tool scope** — Should `get_supplier_portfolio` show ALL suppliers, or only those relevant to current shortfalls?
3. **Scrap rates** — Do the policy/memo PDFs mention reject rates or quality yields? If not, §1.4 is speculative and should be deferred.
4. **Air freight cost modeling** — The `AIR_FREIGHT_ALLOWED` constraint has no cost multiplier. Should we add one to `params`, or is a hardcoded estimate (e.g., 3x) sufficient?

## References

- Step 1 plan: `plans/step1-enriched-tools-implementation-plan.md`
- Planner/executor sketch: `research/planner-executor-sketch.md`
- Model eval results: `output/model-eval-*.md`
- TODO tracker: `TODO.md`
- MRP lot sizing: [SAP MRP Lot Size Calculations](https://sites.google.com/site/sapswords/home/sap-mrp/sap-mrp-functionality/step-7---lot-size-calculations-for-the-procurement-proposals-in-an-sap-mrp-run)
- Supplier scoring frameworks: [Weighted Scoring for Supplier Evaluation](https://wicely.com/resources/supplier-evaluation-criteria-weighted-scoring)
- Supply pipeline stages: [Purchase Order Overview - Dynamics 365](https://learn.microsoft.com/en-us/dynamics365/supply-chain/procurement/purchase-order-overview)
