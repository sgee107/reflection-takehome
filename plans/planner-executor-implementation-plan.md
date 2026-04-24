# Planner-Executor Architecture — Implementation Plan

## Overview

Replace the single ReAct agent loop with a three-phase architecture: (1) deterministic greedy planner that produces a complete allocation plan with conflict detection, (2) LLM reviewer that triages conflicts and overrides allocations where judgment is needed, (3) deterministic executor that places orders and creates alerts per the finalized plan. Every decision — algorithmic or LLM — is recorded in an append-only decision log.

**Design reference:** `designs/planner-executor-design.md`

## Goals

- Deterministic default plan covering all gaps (no LLM arithmetic)
- Aggressive conflict flagging — soft-preference tradeoffs always surfaced for LLM triage
- Append-only decision log with source tagging and rationale
- Deterministic execution (orders + alerts) from the finalized plan
- `--plan-only` flag for human review before execution
- `--legacy` flag to fall back to the current single-agent ReAct loop

## Prerequisites

- Constraint extraction agent Steps 7-10 complete (constraints reliably extracted)
- Step 1 enriched tools in working tree (`place_order` guardrails, enriched `get_eligible_suppliers`)
- `uv sync` has been run

## Files

| File | Action | Purpose |
|------|--------|---------|
| `procureai/planner.py` | Create | Data structures, `build_option_matrix()`, `greedy_allocate()`, `detect_conflicts()`, `simulate_plan()` |
| `procureai/agents/planner.py` | Create | LLM reviewer subgraph: tools, graph wiring, system prompt |
| `procureai/agents/executor.py` | Create | Deterministic `execute_plan()` — places orders AND creates alerts |
| `procureai/agents/state.py` | Modify | Add `ReviewerState` |
| `procureai/agents/graph.py` | Modify | Add `build_planning_agent()` alongside existing `build_agent()` |
| `agent.py` | Modify | Add `--plan-only`, `--legacy`, rewire pipeline to greedy → review → execute |
| `tests/test_planner.py` | Create | Unit tests for data structures, option matrix, greedy allocator, conflict detection, simulation |
| `tests/test_reviewer.py` | Create | LLM integration tests for conflict resolution |
| `tests/test_executor.py` | Create | Unit tests for plan execution (orders + alerts) |

---

## Step 1: Data Structures + DecisionLog

**Goal:** Define all data structures used across the planner pipeline. These are the foundation everything else builds on.

#### Tests (RED)
- [x] Create `tests/test_planner.py`
- [x] `test_supplier_option_creation` — construct a `SupplierOption` with all fields, verify accessors
- [x] `test_option_matrix_empty` — empty matrix has no options, no shared suppliers
- [x] `test_allocation_plan_empty` — empty plan has no allocations or alerts
- [x] `test_conflict_creation` — construct a `Conflict` with type, options, greedy_choice, data
- [x] `test_conflict_type_hard_vs_soft` — verify enum categories
- [x] `test_decision_log_append` — append 3 decisions, verify `len(log.entries) == 3`
- [x] `test_decision_log_for_component` — append decisions for CMP-001 and CMP-003, filter by CMP-003 returns only its entries
- [x] `test_decision_log_source_tagging` — decisions tagged `greedy_algorithm` vs `llm_reviewer` are distinguishable
- [x] `test_decision_log_summary` — summary string includes counts by source
- [x] `test_decision_log_immutable_entries` — modifying a returned entry doesn't affect the log
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Create `procureai/planner.py`
- [x] Define `SupplierOption` dataclass with all fields from design
- [x] Define `OptionMatrix` dataclass (`options`, `shared_suppliers`, `component_priorities`)
- [x] Define `Allocation` dataclass (`component_id`, `supplier_id`, `quantity`, `rationale`, `expedite`)
- [x] Define `PlannedAlert` dataclass (`description`, `component_id`)
- [x] Define `AllocationPlan` dataclass (`allocations`, `alerts`) with `to_dict()` / `from_dict()` serialization
- [x] Define `ConflictType` enum (5 hard + 4 soft)
- [x] Define `Conflict` dataclass (`type`, `component_id`, `description`, `options`, `greedy_choice`, `data`)
- [x] Define `Decision` dataclass (`component_id`, `action`, `details`, `rationale`, `source`, `timestamp`, `conflict_id`)
- [x] Define `DecisionLog` class:
  - `append(decision)` — adds to internal list
  - `for_component(component_id)` — returns filtered list
  - `summary()` — returns formatted string with counts
  - `entries` property — returns copy (not reference)
- [x] Run tests — verify GREEN

---

## Step 2: Fitness Scoring

**Goal:** Implement composite fitness scoring for supplier options. Pure function, no side effects.

#### Tests (RED)
- [x] `test_fitness_on_time_domestic_cheapest` — on-time, domestic, cheapest, strategic → highest score
- [x] `test_fitness_late_penalty` — late supplier scores lower than on-time
- [x] `test_fitness_late_gradient` — 1-day late scores higher than 30-day late
- [x] `test_fitness_domestic_bonus` — domestic scores higher than identical international
- [x] `test_fitness_sustainability_ordering` — A > B > C ratings
- [x] `test_fitness_tier_ordering` — strategic > preferred > standard
- [x] `test_fitness_moq_fit` — MOQ ≤ gap scores 1.0, MOQ > gap scores gap/MOQ
- [x] `test_fitness_price_inversion` — cheaper supplier scores higher (within same component's supplier set)
- [x] `test_fitness_range` — all scores between 0.0 and 1.0
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `compute_fitness(option: SupplierOption, price_range: tuple[float, float]) -> float`:
  - `price_score` (0.25): min/max inversion within component's supplier price range
  - `delivery_score` (0.25): 1.0 if on time, gradient toward 0 as days_late increases (cap at 60 days)
  - `cert_score` (0.15): always 1.0 (filtered pre-scoring)
  - `tier_score` (0.10): strategic=1.0, preferred=0.7, standard=0.4
  - `sustainability_score` (0.10): A=1.0, B=0.6, C=0.3
  - `domestic_score` (0.10): 1.0 domestic, 0.5 international
  - `moq_fit_score` (0.05): 1.0 if MOQ ≤ gap, else gap/MOQ
- [x] Run tests — verify GREEN

---

## Step 3: Option Matrix Builder

**Goal:** `build_option_matrix()` pre-computes all eligible supplier options per component with fitness scores, shared supplier map, and priority ordering.

#### Tests (RED)
- [x] Build `make_scenario_data()` fixture with minimal DataFrames (components, suppliers, supplier_catalog, inventory, production_schedule, bom) and mock constraints
- [x] `test_option_matrix_filters_blocked_suppliers` — SUP-113 in SUPPLIER_BLOCKED → excluded from all components
- [x] `test_option_matrix_filters_unapproved` — supplier with `on_approved_list=0` excluded when APPROVED_SUPPLIER_ONLY constraint exists
- [x] `test_option_matrix_filters_cert_required` — CMP-005 requires ISO-9001 → suppliers without it excluded
- [x] `test_option_matrix_computes_delivery_dates` — `delivery_date = current_date + lead_time_days`
- [x] `test_option_matrix_computes_days_late` — late supplier has positive `days_late`, on-time has 0
- [x] `test_option_matrix_computes_air_freight` — international supplier with AIR_FREIGHT_ALLOWED → `air_freight_delivery` computed
- [x] `test_option_matrix_computes_concentration_max` — CONCENTRATION_LIMIT with max_pct=0.5 and gap=208 → `max_concentration_qty=104`
- [x] `test_option_matrix_no_concentration_limit` — component without limit → `max_concentration_qty=None`
- [x] `test_option_matrix_fitness_ranking` — options sorted by fitness score descending
- [x] `test_option_matrix_shared_suppliers` — SUP-101 supplies CMP-001 and CMP-002 → appears in shared_suppliers map
- [x] `test_option_matrix_priority_ordering` — `component_priorities` sorted by earliest deadline first
- [x] `test_option_matrix_empty_component` — component with no eligible suppliers → empty list in options, still in component_priorities
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `build_option_matrix(scenario: ScenarioData, constraints: list[Constraint], gap_df: pd.DataFrame) -> OptionMatrix`:
  1. For each component in gap_df:
     a. Filter supplier_catalog to this component
     b. Join supplier info (name, domestic, sustainability, tier, certs, approved)
     c. Apply hard gates: approved list, blocked, cert required, PCB qualified
     d. For each remaining supplier, build `SupplierOption` with all computed fields
     e. Compute fitness score for each option
     f. Sort by fitness descending
  2. Build `shared_suppliers` map: supplier_id → list of component_ids they serve
  3. Build `component_priorities`: sort component_ids by earliest_needed_by ascending
  4. Return `OptionMatrix`
- [x] Run tests — verify GREEN

---

## Step 4: Greedy Allocator

**Goal:** `greedy_allocate()` produces a complete `AllocationPlan` covering all gaps, plus inline `Conflict` detection and `DecisionLog` entries.

#### Tests (RED)
- [x] `test_greedy_sole_supplier` — one eligible supplier → allocate full gap to it, log "allocate" decision
- [x] `test_greedy_picks_top_fitness` — two suppliers → picks higher fitness, log includes rationale
- [x] `test_greedy_concentration_split` — CONCENTRATION_LIMIT max_pct=0.5, gap=208 → split 104/104 across top 2 suppliers
- [x] `test_greedy_concentration_three_way_split` — max_pct=0.33 → split across 3 suppliers
- [x] `test_greedy_moq_roundup` — gap=30, MOQ=50 → allocates 50, flags MOQ_OVERBUY conflict
- [x] `test_greedy_covers_all_gaps` — 5 components with gaps → all have allocations or alerts
- [x] `test_greedy_priority_ordering` — earliest-deadline components processed first (decisions log in deadline order)
- [x] `test_greedy_no_eligible_supplier` — component with empty options → creates PlannedAlert, flags NO_ELIGIBLE_SUPPLIER conflict
- [x] `test_greedy_infeasible_deadline_conflict` — all suppliers late → allocates anyway + flags INFEASIBLE_DEADLINE + creates PlannedAlert
- [x] `test_greedy_domestic_vs_cost_flagged` — domestic and international both eligible → DOMESTIC_VS_COST conflict flagged
- [x] `test_greedy_domestic_vs_cost_not_flagged_sole` — only domestic suppliers → no DOMESTIC_VS_COST conflict
- [x] `test_greedy_strategic_loyalty_flagged` — non-strategic chosen over strategic → STRATEGIC_LOYALTY conflict flagged
- [x] `test_greedy_sustainability_tradeoff_flagged` — B-rated chosen over A-rated → SUSTAINABILITY_TRADEOFF flagged
- [x] `test_greedy_decision_log_populated` — after allocation, log has one entry per allocation + one per conflict
- [x] `test_greedy_decision_log_source` — all entries have `source="greedy_algorithm"`
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `greedy_allocate(matrix: OptionMatrix, constraints: list[Constraint], gap_df: pd.DataFrame) -> tuple[AllocationPlan, list[Conflict], DecisionLog]`:
  1. Initialize empty `AllocationPlan`, `list[Conflict]`, `DecisionLog`
  2. Track `supplier_spend: dict[str, float]` for budget threshold checks
  3. For each component_id in `matrix.component_priorities`:
     a. Get `options = matrix.options[component_id]`
     b. Get gap_row from gap_df
     c. If no options → create `PlannedAlert`, log alert decision, add `NO_ELIGIBLE_SUPPLIER` conflict, continue
     d. Check concentration limit for component
     e. If no limit: allocate full gap to `options[0]` (top fitness)
     f. If limit: compute split across top-N suppliers to cover gap (each capped at `max_concentration_qty`)
     g. For each allocation:
        - Round up to MOQ if needed; flag `MOQ_OVERBUY` if excess > 50% of gap
        - Check if delivery is late; flag `INFEASIBLE_DEADLINE` if ALL suppliers late
        - Flag `CONCENTRATION_SPLIT` conflict (informational — greedy already split)
     h. Flag soft-preference conflicts:
        - `DOMESTIC_VS_COST`: if options contain both domestic and international suppliers
        - `STRATEGIC_LOYALTY`: if chosen supplier is not strategic but a strategic alternative exists
        - `SUSTAINABILITY_TRADEOFF`: if chosen supplier has lower sustainability than an alternative
     i. Log each allocation as a `Decision`
     j. Log each conflict as a `Decision` (action="flag")
     k. Update `supplier_spend`
  4. Return plan, conflicts, log
- [x] Run tests — verify GREEN

---

## Step 5: Cross-Component Conflict Detection

**Goal:** `detect_cross_component_conflicts()` runs after greedy allocation to catch plan-wide issues the per-component loop can't see.

#### Tests (RED)
- [x] `test_detect_budget_threshold_crossed` — SUP-101 aggregate spend >$50K → BUDGET_THRESHOLD conflict
- [x] `test_detect_budget_threshold_under` — aggregate under threshold → no conflict
- [x] `test_detect_shared_supplier_load` — SUP-101 allocated across 3 components → SHARED_SUPPLIER_LOAD conflict
- [x] `test_detect_shared_supplier_single` — supplier serves only 1 component → no SHARED_SUPPLIER_LOAD
- [x] `test_detect_appends_to_existing_conflicts` — conflicts list grows, doesn't replace
- [x] `test_detect_appends_to_decision_log` — log entries added with source="greedy_algorithm"
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `detect_cross_component_conflicts(plan: AllocationPlan, conflicts: list[Conflict], log: DecisionLog, constraints: list[Constraint]) -> None`:
  1. Compute per-supplier aggregate spend from `plan.allocations`
  2. For each BUDGET_THRESHOLD constraint: if any supplier exceeds threshold, append conflict
  3. Compute per-supplier component count from `plan.allocations`
  4. For each supplier serving 2+ components: append SHARED_SUPPLIER_LOAD conflict
  5. Log each new conflict as a Decision
- [x] Run tests — verify GREEN

---

## Step 6: Plan Simulation

**Goal:** `simulate_plan()` validates a plan against all constraints and returns a structured report. Used by the LLM reviewer to verify its changes.

#### Tests (RED)
- [x] `test_simulate_complete_plan_passes` — all gaps covered, no violations → verdict PASS
- [x] `test_simulate_missing_component` — one gap not covered → verdict FAIL, lists uncovered component
- [x] `test_simulate_concentration_violation` — one supplier >50% of a component → verdict FAIL, lists violation
- [x] `test_simulate_moq_violation` — quantity below MOQ → verdict FAIL
- [x] `test_simulate_duplicate_allocation` — same (component, supplier, qty) twice → verdict FAIL
- [x] `test_simulate_late_delivery_warning` — order delivers after deadline → verdict PASS_WITH_WARNINGS, lists warning
- [x] `test_simulate_budget_threshold_warning` — aggregate >$50K → PASS_WITH_WARNINGS
- [x] `test_simulate_returns_structured_report` — report has `verdict`, `violations`, `warnings`, `summary` fields
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `simulate_plan(plan: AllocationPlan, matrix: OptionMatrix, constraints: list[Constraint], gap_df: pd.DataFrame) -> dict`:
  1. Check completeness: every component_id in gap_df has allocations summing to ≥ gap
  2. Check concentration: per-component per-supplier share vs limits
  3. Check MOQ: each allocation quantity ≥ supplier MOQ
  4. Check duplicates: no identical (component, supplier, qty) pairs
  5. Check delivery: compare delivery dates to deadlines
  6. Check budget: per-supplier aggregate vs thresholds
  7. Return `{"verdict": "PASS" | "PASS_WITH_WARNINGS" | "FAIL", "violations": [...], "warnings": [...], "summary": str}`
- [x] Run tests — verify GREEN

---

## Step 7: LLM Reviewer Tools

**Goal:** Implement the 4 tools the LLM reviewer uses: `resolve_conflict`, `override_allocation`, `simulate_plan` (tool wrapper), `accept_plan`.

#### Tests (RED)
- [x] Create `tests/test_reviewer.py`
- [x] Build fixture: greedy plan with 2 hard conflicts + 2 soft conflicts + populated decision log
- [x] `test_resolve_conflict_appends_decision` — resolve CF-001 → decision log has new entry with source="llm_reviewer"
- [x] `test_resolve_conflict_returns_remaining` — resolve 1 of 3 → response shows 2 remaining
- [x] `test_resolve_conflict_accept_noop` — chosen_option="accept" → plan unchanged, log entry says "no-op"
- [x] `test_resolve_conflict_invalid_id` — conflict_id not found → error message
- [x] `test_override_allocation_changes_plan` — override CMP-003 supplier → plan.allocations updated
- [x] `test_override_allocation_logs_decision` — override → decision log entry with old/new details
- [x] `test_override_allocation_reruns_conflicts` — override triggers conflict re-detection for that component
- [x] `test_simulate_plan_tool` — invoke via tool interface → returns simulation report
- [x] `test_accept_plan_logs_terminal` — accept → decision log has terminal entry
- [x] `test_accept_plan_returns_summary` — accept → response includes plan summary
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Create `procureai/agents/planner.py`
- [x] Implement reviewer context class `ReviewerContext`:
  - Holds mutable references to `plan`, `conflicts`, `decision_log`, `matrix`, `constraints`, `gap_df`
  - `unresolved_conflicts()` returns conflicts not yet resolved
- [x] Implement `_build_reviewer_tools(ctx: ReviewerContext) -> list`:
  - `resolve_conflict(conflict_id: str, chosen_option: str, rationale: str) -> str`
  - `override_allocation(component_id: str, changes_json: str, rationale: str) -> str`
  - `simulate_plan() -> str` (wraps `planner.simulate_plan()`)
  - `accept_plan(rationale: str) -> str`
- [x] All tools use `@tool` decorator, JSON strings for dict params
- [x] Run tests — verify GREEN

---

## Step 8: LLM Reviewer Subgraph

**Goal:** Wire the reviewer tools into a LangGraph StateGraph that the LLM uses to triage conflicts.

#### Tests (RED)
- [x] `test_reviewer_builds_without_error` — `build_reviewer_agent(llm, ...)` returns compiled graph
- [x] `test_reviewer_system_prompt_includes_plan` — system prompt contains serialized plan
- [x] `test_reviewer_system_prompt_includes_conflicts` — system prompt contains all conflicts
- [x] `test_reviewer_system_prompt_includes_decision_log` — system prompt contains greedy decisions
- [x] `test_reviewer_system_prompt_includes_constraints` — system prompt contains constraint list
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Add `ReviewerState` to `procureai/agents/state.py`:
  ```python
  class ReviewerState(MessagesState):
      plan_accepted: bool = False
  ```
- [x] Implement `build_reviewer_prompt(plan, conflicts, decision_log, constraints) -> str`:
  - Serialize plan as readable text (component → allocations)
  - Serialize conflicts with IDs, types, descriptions, options, greedy defaults
  - Serialize decision log summary
  - Serialize constraints list
  - Include reviewer instructions and principles
- [x] Implement `build_reviewer_agent(llm, plan, conflicts, decision_log, matrix, constraints, gap_df) -> tuple[CompiledGraph, ReviewerContext]`:
  1. Create `ReviewerContext`
  2. Build tools from context
  3. Bind tools to LLM
  4. Create `StateGraph(ReviewerState)` with review_node, tool_node
  5. Add conditional edges (tool_calls → tools → review, no tool_calls → END)
  6. Compile with `recursion_limit=25`
  7. Return (graph, context)
- [x] Run tests — verify GREEN

---

## Step 9: LLM Reviewer Integration Tests

**Goal:** Verify the reviewer agent resolves conflicts correctly with real LLM calls.

#### Tests
- [x] `test_reviewer_resolves_all_conflicts` — all conflicts get resolutions via `resolve_conflict` tool calls
- [x] `test_reviewer_calls_simulate_before_accept` — tool call trace includes `simulate_plan` before `accept_plan`
- [x] `test_reviewer_decision_log_has_rationale` — every resolution entry has non-empty rationale
- [x] `test_reviewer_decision_log_grows` — log has more entries after review than before
- [x] `test_reviewer_accepts_plan` — reviewer calls accept_plan
- [x] `test_reviewer_tool_calls_are_valid` — no tool errors during review

**Note:** Mark with `@pytest.mark.llm`. These require an API key and are inherently model-dependent. Assert on structural properties (all conflicts resolved, rationale present), not specific wording.

---

## Step 10: Deterministic Executor

**Goal:** `execute_plan()` takes a finalized plan and places all orders + creates all alerts. No LLM.

#### Tests (RED)
- [x] Create `tests/test_executor.py`
- [x] Build fixture: `AllocationPlan` with 3 allocations + 2 alerts, mock `ProcurementContext` with tools
- [x] `test_executor_places_all_orders` — 3 allocations → 3 `place_order` calls, all recorded in results
- [x] `test_executor_creates_all_alerts` — 2 planned alerts → 2 `create_alert` calls, alerts recorded in context
- [x] `test_executor_records_errors` — if `place_order` returns "ERROR: ...", captured in `errors` list but execution continues
- [x] `test_executor_returns_summary` — `ExecutionResult` has correct `orders_placed`, `alerts_created`, `errors` counts
- [x] `test_executor_preserves_rationale` — rationale from allocation passed through to `place_order`
- [x] `test_executor_handles_empty_plan` — no allocations, no alerts → returns zero counts, no errors
- [x] `test_executor_alert_includes_component_id` — alert description includes component context when `component_id` is set
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Create `procureai/agents/executor.py`
- [x] Define `ExecutionResult` dataclass (`orders: list[dict]`, `alerts_created: int`, `errors: list[dict]`)
- [x] Implement `execute_plan(plan: AllocationPlan, ctx: ProcurementContext, tools: list) -> ExecutionResult`:
  1. Find `place_order` and `create_alert` tools from the tools list
  2. For each allocation in `plan.allocations`:
     - Invoke `place_order` with component_id, supplier_id, quantity, rationale
     - Record result; if "ERROR" in result, add to errors
  3. For each alert in `plan.alerts`:
     - Invoke `create_alert` with description
  4. Return `ExecutionResult`
- [x] Run tests — verify GREEN

---

## Step 11: Agent.py Rewiring

**Goal:** Update `agent.py` to use the greedy → review → execute pipeline, with `--plan-only` and `--legacy` flags.

#### Tests (RED)
- [x] `test_plan_only_flag_no_execution` — mock scenario, `--plan-only` → plan printed, no POs written to DB
- [x] `test_legacy_flag_uses_old_agent` — `--legacy` → calls `build_agent()`, not `greedy_allocate()`
- [x] `test_full_pipeline_writes_results` — default mode → POs and alerts written to DB
- [x] `test_decision_log_printed_verbose` — `--verbose` → decision log entries printed
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Add `--plan-only` flag to Click command
- [x] Add `--legacy` flag to Click command
- [x] Refactor main pipeline in `agent.py` with `_run_legacy()` and `_run_planner_executor()` branches
- [x] Implement `_print_plan(plan, decision_log, option_matrix)` — formatted output showing allocations, alerts, decision summary, total spend
- [x] Update summary output to include decision log stats when not in legacy mode
- [x] Run tests — verify GREEN

---

## Step 12: End-to-End Scenario Runs

**Goal:** Verify the full pipeline works across all 6 scenarios and compare to the single-agent baseline.

- [ ] Clean all scenarios: `for s in data/scenarios/scenario_*.sqlite; do python agent.py --scenario "$s" --clean; done`
- [ ] Run scenario 06 (simple) — smoke test, verify POs written and alerts created
- [ ] Run scenario 01 (baseline) — verify concentration split on CMP-003 (magnets)
- [ ] Run scenario 03 (tight timeline) — verify INFEASIBLE_DEADLINE conflicts detected and alerts created
- [ ] Run scenario 02 (existing POs) — verify incoming supply subtracted from gaps
- [ ] Run scenario 05 (competing demand) — verify priority ordering by deadline
- [ ] Run scenario 04 — full run
- [ ] Run `--plan-only` on scenario 01 — verify output format, no DB writes
- [ ] Run `--legacy` on scenario 06 — verify old ReAct agent still works
- [ ] Run verification suite: `uv run python -m pytest tests/test_verification.py -v`
- [ ] Compare pass rates to single-agent baseline:
  - Concentration tests should pass (greedy splits deterministically)
  - Completeness should be 6/6 (greedy covers all gaps)
  - No regressions on previously passing tests

---

## Step 13: Code Quality

- [ ] `uv run ruff check procureai/planner.py procureai/agents/planner.py procureai/agents/executor.py`
- [ ] `uv run ruff format procureai/planner.py procureai/agents/planner.py procureai/agents/executor.py`
- [ ] `uv run ruff check tests/test_planner.py tests/test_reviewer.py tests/test_executor.py`
- [ ] `uv run ruff format tests/test_planner.py tests/test_reviewer.py tests/test_executor.py`
- [ ] `uv run python -m pytest tests/ -v` — full suite passes
- [ ] `uv run python -m pytest tests/ --cov=procureai` — check coverage delta

---

## Validation

This implementation is complete when:

- [ ] All `tests/test_planner.py` tests pass (data structures, option matrix, greedy allocator, conflicts, simulation)
- [ ] All `tests/test_reviewer.py` tests pass (LLM conflict resolution, tool usage, decision log)
- [ ] All `tests/test_executor.py` tests pass (order placement + alert creation)
- [ ] All `tests/test_verification.py` tests pass (including concentration and completeness tests)
- [ ] 6 scenarios run end-to-end without crashing
- [ ] `--plan-only` prints plan and exits without DB writes
- [ ] `--legacy` runs the old ReAct agent successfully
- [ ] Decision log visible in verbose output (shows greedy decisions + LLM resolutions)
- [ ] `ruff check` and `ruff format` clean for all changed files
- [ ] Concentration compliance ≥ previous baseline across all scenarios
- [ ] Completeness = 6/6 scenarios (all gaps covered)

---

## Risk Mitigation

### LLM reviewer may not converge (keeps calling tools without accepting)
**Impact:** Medium — plan never finalizes, pipeline hangs.
**Mitigation:** `recursion_limit=25` on the reviewer graph. If hit, auto-accept greedy defaults for unresolved conflicts and log "auto-accepted due to iteration limit." The greedy plan is always valid — the LLM only improves it.

### Greedy allocator concentration split may not cover full gap
**Impact:** Medium — if max_pct × gap rounds down and there aren't enough suppliers to cover the remainder.
**Mitigation:** After splitting across all eligible suppliers, if a remainder exists, assign it to the top-fitness supplier (accepting a slight concentration overshoot) and flag as a conflict. `place_order` guardrails will catch true violations at execution time.

### Soft-preference conflict volume may overwhelm the LLM
**Impact:** Low — scenarios have 10-13 components, most with 2-4 suppliers. Worst case ~15-20 soft conflicts.
**Mitigation:** Conflicts are presented as a structured list, not free text. The LLM just calls `resolve_conflict(id, "accept", "reason")` for no-ops. Token cost is low per resolution.

### Agent.py rewiring may break existing flows
**Impact:** Medium — `--legacy` flag preserves the old path, but the new path touches the same DB write functions.
**Mitigation:** Both paths use the same `write_purchase_orders()` and `write_alerts()` functions. Test both modes in Step 12.

### `place_order` rejects an allocation that `simulate_plan` approved
**Impact:** Low — both check the same constraints. But `place_order` checks against dynamically updated `gap_df` (orders reduce gaps), while simulation checks the static plan.
**Mitigation:** Executor processes allocations in the same priority order as the greedy planner. If an error occurs, record it and continue — don't halt execution.

---

## References

- Design document: `designs/planner-executor-design.md`
- Original sketch: `research/planner-executor-sketch.md`
- Procurement gaps design: `research/procurement-gaps-design-2026-04-23.md`
- Step 1 plan: `plans/step1-enriched-tools-implementation-plan.md`
- Constraint extraction plan: `plans/constraint-extraction-agent-implementation-plan.md`
- Model eval results: `output/model-eval-*.md`

---

## Progress Tracking

**Started:** —
**Last Updated:** 2026-04-23
**Status:** Not Started
