# ProcureAI

Autonomous AI procurement agent for Apex Manufacturing. Given a scenario database describing products, bills of materials, suppliers, inventory, and production schedules, ProcureAI computes material requirements, identifies shortfalls, and generates purchase orders — while respecting lead times, supplier approvals, concentration limits, certifications, and procurement policies.

## Quick Start

```bash
# Prerequisites: Python 3.12+, uv
uv sync

# Set your API key (or add to .env file — see below)
export ANTHROPIC_API_KEY=sk-...

# Run on the simplest scenario
uv run python agent.py --scenario data/scenarios/scenario_06_simple.sqlite

# See the plan without placing orders
uv run python agent.py --scenario data/scenarios/scenario_06_simple.sqlite --plan-only

# Use a different model
uv run python agent.py --scenario data/scenarios/scenario_06_simple.sqlite --model claude-haiku-4-5

# Undo the last run
uv run python agent.py --scenario data/scenarios/scenario_06_simple.sqlite --clean
```

## Configuration

Copy `.env.example` to `.env` and set your API key:

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

To use a different provider (OpenAI, Ollama, vLLM), set `PROCUREAI_MODEL_PROVIDER` and related fields — see `.env.example` for all options.

## Architecture

ProcureAI uses a **planner-executor architecture** that separates strategic reasoning from mechanical execution:

```
load_scenario → gap_analysis → extract_constraints
                                       │
                              ┌────────▼─────────┐
                              │  GREEDY PLANNER   │  Deterministic. Builds an option
                              │  (no LLM)         │  matrix, scores suppliers by fitness,
                              │                   │  allocates across all gaps.
                              └────────┬──────────┘
                                       │
                              ┌────────▼─────────┐
                              │  LLM REVIEWER     │  Only invoked when conflicts exist.
                              │  (LangGraph)      │  Resolves tradeoffs the algorithm
                              │                   │  can't: deadlines, domestic vs cost,
                              │                   │  sustainability, supplier loyalty.
                              └────────┬──────────┘
                                       │
                              ┌────────▼─────────┐
                              │  EXECUTOR         │  Deterministic. Walks the finalized
                              │  (no LLM)         │  plan, calls place_order/create_alert.
                              └──────────────────┘
```

### Why this architecture?

The original design used a single ReAct loop that handled both reasoning and execution. This caused four failure classes:

| Failure | Root Cause | Planner-Executor Fix |
|---------|-----------|---------------------|
| Concentration violations | No cross-component planning | Greedy planner splits automatically — no LLM arithmetic needed |
| Duplicate orders | Agent loses track of placed orders | Plan is structural — can't produce duplicates by construction |
| Early loop termination | Cognitive overload on small models | Greedy covers all gaps deterministically. Can't quit early. |
| Missing deadline alerts | Late reasoning about lead times | Infeasible deadlines flagged as conflicts. Alerts created deterministically. |

The key insight: **architecture matters more than model size**. The jump from 73 to 79 passing tests (and 4 failures to 0) came from the planner-executor rewrite, not from changing models.

### Pipeline Stages

1. **Gap Analysis** (`procureai/pipeline.py`) — Deterministic BOM explosion + demand aggregation using pandas. No LLM. Computes what needs to be ordered and by when.

2. **Constraint Extraction** (`procureai/constraints.py`, `procureai/agents/constraint_graph.py`) — Reads procurement policy and supplier memo PDFs, extracts structured constraints (approved suppliers, blocked suppliers, certifications, concentration limits, air freight windows). Uses a LangGraph subgraph with DB grounding tools. Falls back to hardcoded defaults on failure.

3. **Greedy Planner** (`procureai/planner.py`) — Builds an `OptionMatrix` of every eligible supplier per component, scores each by a weighted fitness function, then allocates greedily by deadline urgency. Detects hard conflicts (infeasible deadlines, concentration splits, MOQ overbuy) and flags soft-preference tradeoffs (domestic vs cost, strategic loyalty, sustainability) for LLM review.

   **Supplier Fitness Score** — Each eligible supplier option is scored 0–1 using a weighted composite:

   | Dimension | Weight | Scoring |
   |-----------|--------|---------|
   | Price | 25% | Normalized within component's price range (cheapest = 1.0, most expensive = 0.0) |
   | Delivery | 25% | 1.0 if on time; linear decay toward 0 as days late approaches 60 |
   | Certification | 15% | Always 1.0 (ineligible suppliers filtered before scoring) |
   | Relationship tier | 10% | strategic = 1.0, preferred = 0.7, standard = 0.4 |
   | Sustainability | 10% | A = 1.0, B = 0.6, C = 0.3 |
   | Domestic preference | 10% | domestic = 1.0, international = 0.5 |
   | MOQ fit | 5% | 1.0 if MOQ ≤ needed qty; otherwise needed/MOQ (penalizes overbuying) |

   The greedy allocator sorts suppliers by fitness (descending) and assigns each component's gap to the top-scoring supplier. When concentration limits apply, it walks down the fitness-ranked list distributing quantity across multiple suppliers. The fitness score and selection rationale are recorded in the decision log and surfaced in the HTML report.

4. **LLM Reviewer** (`procureai/agents/planner.py`) — A LangGraph agent that receives the greedy plan + conflicts and resolves them using `resolve_conflict`, `override_allocation`, `simulate_plan`, and `accept_plan` tools. Every decision — including "no change needed" — is logged with rationale.

5. **Executor** (`procureai/agents/executor.py`) — A `for` loop. Walks the finalized plan, calls `place_order` and `create_alert`. No LLM.

6. **Tool-level enforcement** (`procureai/agents/tools.py`) — Hard constraints are enforced in code regardless of what the planner or reviewer decided: approved supplier checks, blocked supplier rejection, certification validation, MOQ rounding, concentration limits, duplicate detection, and delivery date warnings.

### Decision Log

Every decision — algorithmic and LLM — is recorded in an append-only decision log with source tagging, conflict linkage, and rationale. The log is persisted to a `decision_log` table in the scenario SQLite database, creating a queryable audit trail across runs. Use `--plan-only` to inspect the plan and decision log without executing.

### HTML Report

Every run generates a standalone HTML report (auto-opens in browser) with seven sections: scenario overview, gap analysis, supplier options considered (with fitness scores and selection markers), allocation plan with spend-by-supplier chart, conflicts and resolutions (showing greedy defaults vs LLM overrides), alerts, and the full decision log. Reports are saved to `output/` and named by scenario and run ID.

## Project Structure

```
agent.py                         CLI entry point (Click)
procureai/
  config.py                      AgentConfig (Pydantic Settings), model factory
  pipeline.py                    BOM explosion + gap analysis (pandas)
  constraints.py                 Constraint types, extraction, defaults
  planner.py                     Option matrix, greedy allocator, conflict detection
  report.py                      Post-run HTML report generator (Plotly)
  extraction.py                  PDF text extraction
  agents/
    graph.py                     LangGraph agent wiring
    tools.py                     6 procurement tools + ProcurementContext
    planner.py                   LLM reviewer subgraph
    executor.py                  Deterministic plan executor
    constraint_graph.py          Constraint extraction LangGraph subgraph
    prompts.py                   System prompt builder
    state.py                     Agent state definitions
  utils/
    db.py                        SQLite data layer, transaction log, decision log
  discovery/
    overview.py                  Scenario summary printer
    dashboard.py                 Plotly HTML dashboard generator
tests/                           201 tests, 70% coverage (unit + integration + verification)
output/                          Generated HTML reports (per-run)
data/
  scenarios/                     6 SQLite databases (simple → complex)
  policies/                      Procurement policy PDFs
  memos/                         Supplier memo PDFs
```

## CLI Reference

```bash
# Core
uv run python agent.py --scenario <path>              # Run the agent
uv run python agent.py --scenario <path> --plan-only  # Plan without executing
uv run python agent.py --scenario <path> --verbose    # Debug logging
uv run python agent.py --scenario <path> --model <m>  # Override model

# Run management (transaction log)
uv run python agent.py --scenario <path> --list-runs         # Show all runs
uv run python agent.py --scenario <path> --clean              # Undo last run
uv run python agent.py --scenario <path> --clean-run-id <id>  # Undo specific run

# Discovery tools
uv run python -m procureai.discovery.overview                           # All scenarios
uv run python -m procureai.discovery.overview --scenario <path>         # One scenario
uv run python -m procureai.discovery.dashboard --scenario <path>        # Plotly dashboard

# Tests
uv run python -m pytest tests/
uv run python -m pytest tests/ --cov=procureai
```

## Model Evaluation

Tested across three model tiers to validate that the architecture generalizes:

| Metric | Sonnet 4.6 (pre-rewrite) | Haiku 4.5 (current arch) | Qwen 2.5 7B |
|--------|--------------------------|--------------------------|-------------|
| Pass / Fail / Skip | 73 / 4 / 10 | **79 / 0 / 8** | 64 / 12 / 11 |
| All gaps covered | 6/6 | 6/6 | 1/6 |
| Magnet concentration | 4/6 | **6/6** | 3/6 |
| No duplicates | 5/6 | 6/6 | 6/6 |

Scenarios are ordered by complexity: 06 (simplest) → 01 → 02 → 04 → 05 → 03 (hardest). The verification suite (`tests/test_verification.py`) checks correctness across all six.

## Design Decisions

**Deterministic by default.** The greedy planner always produces a complete plan covering every gap. The LLM is a reviewer, not an author — it only intervenes where genuine judgment is needed. This means the system works (at reduced quality) even if the LLM fails entirely.

**Flag aggressively, triage lazily.** Soft-preference tradeoffs (domestic vs cost, sustainability, strategic loyalty) are flagged whenever the conditions exist — no threshold gating. The LLM reviewer decides which flags matter. A "no change needed" resolution with rationale is still valuable audit output.

**Constraints in code, not prompts.** Hard constraints (approved suppliers, blocked suppliers, certifications, MOQ, concentration limits) are enforced in tool code. The LLM can't bypass them. This is why a 4.5B parameter model (Haiku) achieves 0 failures — the guardrails are deterministic.

**Transaction log for safe iteration.** Every run is tracked with a run ID. `--clean` undoes the last run by deleting its orders, alerts, and decision log entries from the database. This makes it safe to iterate on prompt tuning or model selection without corrupting scenario data.

**Fitness scoring is a starting point, not a solution.** The weighted composite score (price 25%, delivery 25%, cert 15%, tier 10%, sustainability 10%, domestic 10%, MOQ fit 5%) collapses multiple dimensions into a single number. This has a known weakness: a supplier that's catastrophically bad on one dimension can still rank well if it scores adequately everywhere else. A supplier 30 days late but cheap, domestic, and strategic might outscore one that's on time but international. The weights are also fixed — they assume every procurement context values the same tradeoffs, which isn't true for a company with dozens of product lines. The right fix is constraint-based filtering (hard cutoffs on delivery feasibility, minimum tier, etc.) *before* scoring, with the composite only ranking suppliers that already pass baseline requirements. The current system partially does this — ineligible suppliers are filtered before scoring — but delivery feasibility isn't a filter, it's a score gradient. This is a deliberate tradeoff: filtering late suppliers would leave some components with no options, so instead we score them low and flag `INFEASIBLE_DEADLINE` conflicts for the LLM reviewer to resolve.

## Known Limitations

- **PDF extraction reliability** — Constraint extraction depends on LLM reading PDFs via pypdf. Weaker models may mis-extract. Mitigated by pre-extracted markdown in `data/extracted/` and `DEFAULT_CONSTRAINTS` fallback, but `AIR_FREIGHT_ALLOWED` is not in defaults — if extraction fails, air freight disappears silently.

- **Temporal constraint enforcement asymmetry** — Air freight date-window is enforced in tool code, but PCB quality memo and other time-scoped constraints rely on LLM prompt interpretation. No generic `is_constraint_active(constraint, current_date)` filter.

- **No delivery-vs-deadline feasibility check in `place_order`** — The tool computes `expected_delivery_date` but never compares against `earliest_needed_by`. Late orders are placed silently. Mitigated by the planner generating `INFEASIBLE_DEADLINE` conflicts and alerts.

- **Point-in-time planning only** — No rolling horizon, demand pipeline, or multi-period planning. Each run is a single-snapshot assessment.

## Next Steps

**Supply chain risk scoring.** The current supplier model is binary — approved or blocked. A real procurement system needs nuanced risk assessment: financial health indicators, geopolitical exposure, quality history, capacity utilization. This would feed into the fitness scoring function as an additional dimension, letting the planner prefer resilient suppliers when risk-adjusted cost is comparable.

**Safety stock and buffer planning.** ProcureAI orders exactly what's needed to fill each gap. In practice, procurement teams maintain safety stock to absorb demand spikes, supply delays, and quality rejections. Adding configurable buffer percentages per component criticality (e.g., 10% for standard parts, 25% for single-source critical components) would make the plans more production-ready.

**Demand forecasting and rolling horizon.** Each run is a point-in-time snapshot. Extending to multi-period planning — projecting demand across future production windows, sequencing orders to smooth supplier load, and incorporating demand forecasts — would let ProcureAI shift from reactive gap-filling to proactive procurement.

**Scrap rate and yield loss.** Manufacturing waste is real — ordering exactly the gap quantity assumes 100% yield. A `YIELD_ADJUSTMENT` constraint per component would let the planner automatically inflate order quantities to account for expected scrap rates.

**Configurable constraint enforcement.** Today constraints are either hard-coded gates or LLM-interpreted guidelines. A richer model with explicit enforcement modes (`gate` vs `advisory` vs `informational`) per constraint would give procurement teams fine-grained control over which rules block orders vs which generate warnings.

## Tech Stack

Python 3.12 | uv | LangGraph | LangChain | Anthropic | pandas | Click | Pydantic Settings | pypdf | Plotly | pytest | ruff
