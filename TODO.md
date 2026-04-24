# ProcureAI — TODO

Prioritized punchlist for submission (due 2026-04-24 morning).

---

## Submission Blockers

- [ ] **Write README.md** — Assignment explicitly requires it. Cover: what this is, how to run, architecture overview (planner-executor), design decisions, known limitations, what's next. This is where "thoughtful design and honest assessment of limitations" lives. (~30 min)
- [ ] **Fix 32 ruff lint errors** — `ruff check --fix . --exclude .venv && ruff format . --exclude .venv`. Submitting with lint errors looks sloppy. (~1 min)
- [ ] **Clean up dead files** — Remove unused `main.py` stub. Remove dead `ProcurementState` fields (`shortfalls_summary`, `constraints_summary`, `scenario_summary`, `orders_placed`, `remaining_gaps` in `state.py`). (~5 min)

## High Value, Low Effort

- [ ] **Re-run Sonnet 4.6 eval** — Sonnet baseline (73 pass, 4 fail) was run before planner-executor rewrite. Would almost certainly hit 79+ now. Confirms architecture improvements work across model tiers. (~10 min wall clock)
- [ ] **Presentation demo script** — 30-min session: ~15 demo, ~15 Q&A. Outline: (1) run scenario 06 live (fast, simple), (2) show `--plan-only` on scenario 01 (planner reasoning + decision log), (3) show Haiku eval results ("it generalizes" story). (~20 min)
- [ ] **Generate coverage report** — `uv run python -m pytest tests/ --cov=procureai` and include the number in README. 226 tests passing is real; showing 80%+ coverage is credible. (~2 min)

## Nice to Have (diminishing returns)

- [ ] **Decision log persistence to SQLite** — `DecisionLog` is in-memory only. Writing to a `decision_log` table would be a great demo moment ("full audit trail of why every order was placed"). (~1-2 hours)
- [ ] **Zip and submit** — Package final deliverable. Exclude `.venv/`, `__pycache__/`, `.git/`. (~5 min)

---

## Known Limitations (document in README, don't fix)

These are honest limitations to surface in the presentation, not items to implement before submission.

- **PDF extraction reliability** — Constraint extraction depends on LLM reading PDFs via pypdf. Weaker models may mis-extract. Mitigated by pre-extracted markdown in `data/extracted/` and `DEFAULT_CONSTRAINTS` fallback, but `AIR_FREIGHT_ALLOWED` is not in defaults — if extraction fails, air freight disappears silently.
- **Temporal constraint enforcement asymmetry** — Air freight date-window is enforced in tool code (`place_order`), but PCB quality memo and other time-scoped constraints rely on LLM prompt interpretation. No generic `is_constraint_active(constraint, current_date)` filter.
- **No delivery-vs-deadline feasibility check in `place_order`** — The tool computes `expected_delivery_date` but never compares against `earliest_needed_by`. Late orders are placed silently. The planner flags `INFEASIBLE_DEADLINE` conflicts, but the executor doesn't double-check. Mitigated by planner generating alerts for late deliveries.
- **Point-in-time planning only** — No rolling horizon, demand pipeline, or multi-period planning. Each run is a single-snapshot assessment.
- **Constraint interaction reasoning** — Constraints are listed flat in the prompt. The LLM handles each individually but may miss interactions (e.g., critical + hazmat + cert + time-constrained component).

## Architecture Roadmap (mention in presentation, don't build)

- **Planner-executor with smaller executor model** — The architecture already supports this (`--model` flag). Planner uses capable model, executor is deterministic (no LLM). Next step: allow different models for constraint extraction vs review.
- **Decision log as first-class audit artifact** — Persist to SQLite, expose via CLI (`--show-decisions`), enable post-hoc analysis across runs.
- **Enriched `get_eligible_suppliers` output** — Pre-compute delivery vs deadline, concentration %, domestic status per supplier. Currently done in planner but not exposed to ad-hoc tool queries.

---

## Completed

- [x] **Multi-model evaluation** — 2026-04-23. Sonnet 4.6, Haiku 4.5, Qwen 2.5 7B. Results in `output/model-eval-*.md`.
- [x] **Planner-Executor architecture** — Greedy planner + LLM reviewer + deterministic executor. 0 failures on Haiku (79/87 pass). Design in `designs/planner-executor-design.md`.
- [x] **Constraint extraction agent** — LangGraph subgraph with DB grounding tools. `procureai/agents/constraint_graph.py`.
- [x] **Tool-level hard constraint enforcement** — Approved supplier, blocked supplier, certs, MOQ, prices, delivery dates, concentration limits, duplicate detection all enforced in code.
- [x] **Pre-extracted PDFs to markdown** — `data/extracted/*.md` for deterministic constraint input.
- [x] **Transaction log** — run/clean/list-runs for safe undo.
- [x] **Discovery tools** — Scenario overview printer + Plotly HTML dashboards.
- [x] **226 tests passing, 0 failures** — Unit tests (85) + integration tests + verification suite.

## Model Eval Results (2026-04-23)

| Metric | Sonnet 4.6 (pre-rewrite) | Haiku 4.5 (current) | Qwen 2.5 7B |
|--------|--------------------------|---------------------|-------------|
| Pass / Fail / Skip | 73 / 4 / 10 | **79 / 0 / 8** | 64 / 12 / 11 |
| All gaps covered | 6/6 | 6/6 | 1/6 |
| Magnet concentration | 4/6 | **6/6** | 3/6 |
| No duplicates | 5/6 | 6/6 | 6/6 |

**Key insight:** Architecture > model size. The jump from 73→79 pass and 10→0 fail came from the planner-executor rewrite, not from changing models.
