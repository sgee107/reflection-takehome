# Step 1: Enriched Tools + Hard Constraint Enforcement

## Context

All known agent failures (F1: magnet concentration, F2: duplicates, delivery feasibility misses) trace to constraints left entirely to LLM prompt reasoning. This plan adds deterministic enforcement for hard constraints and enriches tool output so the LLM only handles genuine judgment calls.

**Scope:** Changes to `tools.py` only (+ new tests). No new agents, no graph rewiring. This is the foundation that a future planner/executor architecture (Step 2) would build on.

**Design reference:** `research/planner-executor-sketch.md`

---

## Files Modified

| File | What changes |
|------|-------------|
| `procureai/agents/tools.py` | Enrich `get_eligible_suppliers`, add guardrails to `place_order`, add `_annotate_supplier` helper |
| `tests/test_tools.py` | New — unit tests for tool-level enforcement |
| `tests/test_verification.py` | Minor — tighten expectations now that tools enforce more |

---

## Step 1: Test Scaffolding

**Goal:** Create `tests/test_tools.py` with fixtures that build a minimal `ProcurementContext` in-memory (no scenario DB needed for unit tests).

#### Tests (RED)
- [ ] Create `tests/test_tools.py`
- [ ] Build `make_context()` fixture that creates a `ProcurementContext` with:
  - Minimal `ScenarioData` (fake DataFrames for suppliers, supplier_catalog, inventory)
  - A `gap_df` with 2-3 components including CMP-003 (magnets)
  - `constraints` list including `CONCENTRATION_LIMIT`, `APPROVED_SUPPLIER_ONLY`, `MOQ_COMPLIANCE`
  - `current_date = "2025-09-01"`
- [ ] Build helper to invoke tools from the context (call `build_tools(ctx)`, find tool by name, invoke)

---

## Step 2: Delivery-vs-Deadline Warning in `place_order`

**Goal:** `place_order` compares `expected_delivery_date` against `earliest_needed_by` from `gap_df`. If late, the order still goes through but the response includes a clear `⚠ LATE` warning so the LLM can decide whether to also create an alert.

#### Tests (RED)
- [ ] `test_place_order_on_time` — delivery before deadline → no warning in response
- [ ] `test_place_order_late` — delivery after deadline → response contains "LATE" warning with days late and the deadline date
- [ ] `test_place_order_no_gap_row` — component not in gap_df (edge case) → no crash, no warning
- [ ] Run tests — verify RED

#### Implementation (GREEN)
- [ ] In `place_order`, after computing `delivery_date`, look up `earliest_needed_by` from `ctx.gap_df` for the component
- [ ] If `delivery_date > earliest_needed_by`, append a warning line to the return string:
  `⚠ LATE: delivers {delivery_date} but needed by {earliest_needed_by} ({N} days late)`
- [ ] Also auto-create an alert: `ctx.placed_alerts.append(f"DEADLINE RISK: ...")`
- [ ] Run tests — verify GREEN

**Decision:** warn + auto-alert, but do NOT block the order. Some late orders are better than no order — that's a judgment the LLM should still get to make. The warning ensures the LLM has the information it needs.

---

## Step 3: Concentration Limit Enforcement in `place_order`

**Goal:** `place_order` checks if a new order would push a supplier past the concentration limit for that component. If so, reject with an error telling the agent the max quantity it can place.

#### Tests (RED)
- [ ] `test_place_order_concentration_under_limit` — 40% after order → order goes through
- [ ] `test_place_order_concentration_over_limit` — would be 65% after order (limit 50%) → ERROR response, order NOT placed
- [ ] `test_place_order_concentration_error_includes_max_qty` — error message includes the max quantity this supplier can receive
- [ ] `test_place_order_concentration_no_constraint` — component has no `CONCENTRATION_LIMIT` → no check, order goes through
- [ ] `test_place_order_concentration_accounts_for_prior_orders` — two sequential orders to same supplier, second should count the first
- [ ] Run tests — verify RED

#### Implementation (GREEN)
- [ ] Add helper `_concentration_limit(ctx, component_id) -> dict | None` — returns `{"max_pct": 0.5, "secondary_min_pct": 0.2}` if a `CONCENTRATION_LIMIT` constraint applies to this component, else None
- [ ] In `place_order`, after supplier validation but before recording:
  1. Look up concentration limit for component
  2. If exists, compute: total ordered for this component (from `ctx.placed_orders`) + current quantity
  3. Compute this supplier's share: (supplier_qty + quantity) / total_projected
  4. If share > max_pct, compute max_allowed = floor(max_pct * total_needed) - already_ordered_from_supplier
  5. Return ERROR: `"Concentration limit: {supplier_id} would have {pct}% of {component_id} (limit: {max_pct}%). Max additional quantity: {max_allowed}. Split across suppliers."`
- [ ] Run tests — verify GREEN

**Edge case:** First order for a component — no prior orders, so 100% of the *placed* volume goes to one supplier. But total_needed from `gap_df` is the denominator, not placed volume. So if gap is 308 magnets, limit is 50%, max for any supplier = 154 regardless of what's placed so far.

---

## Step 4: Duplicate Order Detection in `place_order`

**Goal:** `place_order` detects if an identical (component_id, supplier_id, quantity) order already exists in `ctx.placed_orders`. If so, return a warning asking the agent to confirm or adjust.

#### Tests (RED)
- [ ] `test_place_order_no_duplicate` — first order for this combo → goes through
- [ ] `test_place_order_exact_duplicate` — same component, supplier, quantity already placed → ERROR, order NOT placed
- [ ] `test_place_order_same_component_different_supplier` — different supplier → goes through (not a duplicate)
- [ ] `test_place_order_same_component_different_quantity` — same supplier, different quantity → goes through (deliberate split)
- [ ] Run tests — verify RED

#### Implementation (GREEN)
- [ ] In `place_order`, before recording the order, check `ctx.placed_orders` for any entry matching all three of (component_id, supplier_id, quantity)
- [ ] If found, return: `"ERROR: Duplicate order. {po_number} already placed {component_id} × {quantity} from {supplier_id}. Adjust quantity or choose a different supplier."`
- [ ] Run tests — verify GREEN

---

## Step 5: Enriched `get_eligible_suppliers` Output

**Goal:** Replace the flat DataFrame dump with an annotated output that pre-computes delivery feasibility, concentration impact, and constraint flags per supplier.

#### Tests (RED)
- [ ] `test_eligible_suppliers_shows_delivery_status` — output contains "ON TIME" or "LATE by X days" per supplier
- [ ] `test_eligible_suppliers_shows_concentration` — output contains current concentration % and what it would be
- [ ] `test_eligible_suppliers_shows_constraint_notes` — output includes applicable constraint summaries for the component
- [ ] `test_eligible_suppliers_shows_max_qty_per_supplier` — when concentration limit applies, shows max quantity each supplier can receive
- [ ] `test_eligible_suppliers_air_freight_annotation` — when air freight is active, shows both standard and air freight delivery dates for international suppliers
- [ ] Run tests — verify RED

#### Implementation (GREEN)
- [ ] Add `_annotate_supplier(ctx, component_id, supplier_row, gap_row) -> str` helper that builds the annotation block for one supplier:
  - Delivery: compute `expected_delivery_date`, compare to `earliest_needed_by`, label ON TIME / LATE
  - Air freight: if international + air freight active, show adjusted date too
  - Concentration: compute current % from `ctx.placed_orders`, show projected % if ordered
  - Flags: domestic ✓/✗, certifications, sustainability rating, relationship tier
  - MOQ vs gap: show MOQ and whether it covers the full gap
- [ ] Add `_constraint_notes(ctx, component_id, gap_row) -> str` that summarizes applicable constraints:
  - Concentration limit with max qty per supplier
  - Critical component status
  - Hazmat flag
  - Cert requirements
- [ ] Rewrite `get_eligible_suppliers` to:
  1. Filter suppliers (same logic as current — approved, not blocked, certs)
  2. Look up gap row for this component
  3. Call `_annotate_supplier` for each eligible supplier
  4. Append `_constraint_notes` section at the end
  5. Return the assembled string
- [ ] Run tests — verify GREEN

**Example output format:**
```
CMP-003 (gap: 208, needed by: 2025-09-12)

SUP-108 MagnetPro Inc. | $5.80 | 14d → 2025-09-15 | ⚠ LATE by 3 days
  ✓ domestic | ✓ approved | sustainability: A | tier: preferred
  concentration: 0% → would be 100% (limit: 50%, max qty: 104)
  MOQ: 50

SUP-107 Nanjing Rare Earth | $3.25 | 35d → 2025-10-06 | ✗ LATE by 24 days
  [air freight: 21d → 2025-09-22 | ⚠ LATE by 10 days]
  ✗ international | ✓ approved | sustainability: B | tier: standard
  concentration: 0% → would be 100% (limit: 50%, max qty: 104)
  MOQ: 100

CONSTRAINTS for CMP-003:
  - CONCENTRATION_LIMIT: max 50%/supplier, min 20% secondary (MEMO-2025-041)
  - CRITICAL_COMPONENT: neodymium magnets
  - Recommended max per supplier: 104 units
```

---

## Step 6: Integration — Run Against Known Failure Scenarios

**Goal:** Verify that the enriched tools fix F1 and F2 without regressing passing tests.

- [ ] Clean all scenario DBs: `python agent.py --scenario <path> --clean` for each
- [ ] Run scenario 06 — verify no duplicate orders (F2 fixed by Step 4)
- [ ] Run scenario 03 — verify magnet concentration ≤50% (F1 fixed by Step 3)
- [ ] Run scenario 05 — verify magnet concentration ≤50% AND no air freight applied (date = Oct 5)
- [ ] Run scenario 01 — baseline, verify no regressions
- [ ] Run full test suite: `uv run python -m pytest tests/ -v`
- [ ] Verify previously-failing tests now pass:
  - `test_magnet_concentration_max`
  - `test_magnet_concentration_min_secondary`
  - `test_no_exact_duplicate_orders`

---

## Step 7: Prompt Update (Minimal)

**Goal:** Update the system prompt to reflect that tools now enforce hard constraints — the LLM doesn't need to manually track concentration or duplicates.

- [ ] In `prompts.py`, update Decision Guidelines:
  - Change concentration guidance from "consider splitting" → "the `place_order` tool enforces concentration limits and will reject orders that exceed them. Plan your splits before placing orders."
  - Change duplicate guidance → "the `place_order` tool rejects exact duplicate orders."
  - Add: "`get_eligible_suppliers` shows delivery feasibility, concentration impact, and max quantities per supplier. Use this information to plan your orders."
- [ ] Remove or simplify workflow step about `check_concentration` (now less needed since `place_order` enforces and `get_eligible_suppliers` shows it)

---

## Validation

This step is complete when:

- [ ] All `tests/test_tools.py` tests pass
- [ ] All `tests/test_verification.py` tests pass (including previously-failing F1, F2)
- [ ] `uv run ruff check .` clean
- [ ] `uv run ruff format .` clean
- [ ] 6 scenarios run end-to-end without crashing

---

## Risk Mitigation

### Concentration limit rejection could cause agent loops
**Impact:** Medium — agent tries to place 208 magnets with SUP-108, gets rejected, tries again with same supplier
**Mitigation:** The error message includes the max allowed quantity. The enriched `get_eligible_suppliers` also shows max quantities. The LLM has the information to split correctly on the first retry.

### Enriched output could be too long for smaller models
**Impact:** Low — the annotation adds ~5-8 lines per supplier vs the current 1-line table row
**Mitigation:** Keep annotations concise. Test with haiku to verify it still parses correctly.

### Late-delivery auto-alerts could generate noise
**Impact:** Low — in tight-timeline scenarios, many orders will trigger late warnings
**Mitigation:** Auto-alert text is informational, not blocking. The agent can still reason about which late orders are acceptable.

---

## What This Does NOT Cover (Deferred to Step 2)

- Cross-component reasoning (shared supplier dependencies)
- Planner/executor agent decomposition
- `simulate_allocation` tool
- Forward planning / multi-period
- Pre-extracting PDFs to markdown

---

## References

- Design sketch: `research/planner-executor-sketch.md`
- Known failures: `plans/fix-plan.md`
- Current tools: `procureai/agents/tools.py`
- Current tests: `tests/test_verification.py`
- TODO tracker: `TODO.md`

---

## Progress Tracking

**Started:** _not yet_
**Last Updated:** 2026-04-23
**Status:** Not Started
