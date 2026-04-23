# ProcureAI — Open Items & Explore List

Track unfinished work, ideas to explore, and known issues here. Check items off as they're resolved.

---

## Investigate / Explore

- [ ] **PDF extraction reliability across models** — constraint extraction depends on LLM reading PDFs via pypdf text. Weaker models (haiku) may mis-extract or miss constraints entirely. Consider pre-extracting PDFs to markdown so constraint data is deterministic regardless of model. Key risk: `AIR_FREIGHT_ALLOWED` is not in `DEFAULT_CONSTRAINTS`, so if extraction fails, air freight disappears silently.
- [ ] **Temporal constraint enforcement asymmetry** — air freight date-window is enforced in tool code (`place_order`), but PCB quality memo and other time-scoped constraints rely entirely on LLM prompt interpretation. Consider a generic `is_constraint_active(constraint, current_date)` filter applied before prompt injection, or adding date checks in `get_eligible_suppliers`.
- [ ] **`DEFAULT_CONSTRAINTS` coverage gaps** — the fallback set is missing: air freight, PCB quality memo restrictions, sustainability preference, strategic supplier protection. If LLM extraction fails, the agent loses these rules.
- [ ] **No delivery-vs-deadline feasibility check in `place_order`** — the tool computes `expected_delivery_date = current_date + lead_time` but never compares it against `earliest_needed_by` from the gap table. Late orders are placed silently. The LLM is told to create alerts for infeasible deadlines, but the tool doesn't enforce or even warn. This is the most critical constraint left entirely to prompt reasoning.
- [ ] **Constraint enforcement spectrum** — hard split between tool-enforced constraints (approved supplier, blocked supplier, cert, MOQ, budget, air freight dates) and prompt-only constraints (domestic preference, sustainability, strategic supplier loyalty, concentration limits, hazmat, lead time feasibility, critical component classification). All known failures trace to the prompt-only side. Need to decide: which constraints should move to tool-level enforcement vs which are genuinely soft preferences the LLM should weigh?

## Architecture Questions

- [ ] **Point-in-time vs forward planning** — the system is purely reactive: single `current_date` snapshot → fill all gaps → done. No concept of rolling horizon, demand pipeline, inventory burn rate, or order sequencing. If we move the calendar forward (e.g., run again with a later `current_date`), the previous run's POs are in the DB but there's no mechanism to reason about them as a cohesive plan across time. Real MRP systems plan across periods. What would a multi-period planning mode look like? Could the agent run iteratively (week 1, week 2, ...) with the prior period's orders feeding into the next period's gap analysis?
- [ ] **How well does the LLM reason about constraint interactions?** — constraints aren't independent. Example: a component might be critical (tighter concentration limits + higher domestic premium threshold) AND hazmat (special handling) AND have a cert requirement AND be time-constrained. The prompt lists constraints flat, but the agent needs to compose them. Are there failure modes where the LLM handles each constraint individually but misses interactions? The magnet case (F1) may be an example — the LLM understands "prefer domestic" and "check concentration" separately but doesn't synthesize "I need to split this order 50/20 across two suppliers even though MagnetPro is domestic and cheaper per my preference rules."

- [ ] **Planner → Executor agent decomposition** — current architecture has one agent doing both strategy (which components, what order, how to split) and execution (individual `place_order` calls). Consider splitting into: (1) a **planner agent** that looks across all shortfalls, supplier capacity, constraint interactions, and shared supplier dependencies to produce an allocation plan (e.g., "CMP-003: 150 to SUP-107, 158 to SUP-108"), then (2) an **executor agent** that takes the plan and places individual orders. The planner is where multi-constraint reasoning lives; the executor is mechanical. This also creates a natural checkpoint for human review before orders are placed. See `research/planner-executor-sketch.md` for the design sketch.
- [ ] **Enriched `get_eligible_suppliers` output** — instead of a flat table, annotate each supplier with pre-computed constraint checks: delivery vs deadline (ON TIME / LATE), current concentration %, domestic status, cert compliance, sustainability rating. Moves arithmetic from LLM reasoning to deterministic code. See `research/planner-executor-sketch.md` for example output format.

## Known Failures (from fix-plan.md + model evals)

- [ ] **F1: Magnet concentration violations — SYSTEMIC, all models** — every model single-sources magnets to SUP-108. Sonnet fails 2/6, Haiku fails 4/6, Qwen fails 3/6. Worse on weaker models = proof that prompt-only guidance is insufficient. Fix: tool-level guardrail in `place_order` (Step 1, Step 3).
- [ ] **F2: Duplicate orders — Sonnet-only** (scenario 06). Haiku and Qwen don't reproduce. Still worth adding duplicate detection as cheap insurance.
- [ ] **F3: Early loop termination — small models only** (Qwen 7B). Places 1-5 orders out of 13-19 needed, then stops. Sonnet and Haiku iterate to completion. Not fixable by Step 1 alone — this is the strongest argument for planner/executor (Step 2), where the planner (capable model) produces a complete plan and the executor (cheap model) just follows it. Also: enriched tool output (Step 1, Step 5) may reduce cognitive load enough to help smaller models iterate further.
- [ ] **F4: Missing deadline alerts — small models only** (Qwen 7B, scenario 03). Doesn't reason about lead time vs deadline infeasibility. Fix: auto-alert in `place_order` when delivery is late (Step 1, Step 2) makes this deterministic.

## Model Eval Results (2026-04-23)

| Metric | Sonnet 4.6 | Haiku 4.5 | Qwen 2.5 7B |
|--------|-----------|-----------|-------------|
| Pass / Fail / Skip | 73 / 4 / 10 | 73 / 8 / 6 | 64 / 12 / 11 |
| All gaps covered | 6/6 | 6/6 | **1/6** |
| Magnet concentration | 4/6 | **2/6** | **3/6** |
| Duplicates | **5/6** | 6/6 | 6/6 |
| Deadline alerts (s03) | P | P | **F** |
| Scenario-specific | 9/9 | 9/9 | 8/9 |

**Key insight:** Tool-enforced constraints (approved supplier, blocked, certs, MOQ, prices, delivery dates) pass on ALL models. Prompt-only constraints (concentration, completeness, deadline alerts) degrade with model capability. This validates the Step 1 approach of moving enforcement into tools.

## Planned Work

- [x] **Multi-model evaluation** — completed 2026-04-23. Results in `output/model-eval-*.md`. Ran Sonnet 4.6, Haiku 4.5, Qwen 2.5 7B.
- [ ] **Step 1: Enriched tools + hard constraint enforcement** — see `plans/step1-enriched-tools-implementation-plan.md`. Fixes F1, F2, F4 across all models.
- [ ] **Step 2: Planner/Executor decomposition** — see `research/planner-executor-sketch.md`. Fixes F3 (early termination on small models) and enables cross-component reasoning. Builds on Step 1.

## Cleanup / Tech Debt

- [ ] **Dead `ProcurementState` fields** — `shortfalls_summary`, `constraints_summary`, `scenario_summary`, `orders_placed`, `remaining_gaps` in `state.py` are defined but never populated. Wire them in or remove.
- [ ] **Unused `main.py`** — stub file at project root, not used by anything.
