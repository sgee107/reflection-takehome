# Stage 1: Build the Agent Loop

## Context

Build an autonomous procurement agent for Apex Manufacturing. Takes a scenario SQLite database, determines component shortfalls, places purchase orders respecting policies/memos, and writes results back to the DB. Must generalize to held-out scenarios.

**Tech stack:** LangGraph/LangChain, factory method for model swappability, Anthropic as default provider.

**Core pattern:** Deterministic pipeline (BOM explosion → gap analysis) feeds a ReAct agent loop that selects suppliers and places orders through constraint-validated tools.

---

## Package Structure

```
procureai/
  config.py                    # NEW — model factory + config loader
  constraints.py               # NEW — constraint schema + LLM extraction
  pipeline.py                  # NEW — BOM explosion + gap analysis
  agents/
    state.py                   # NEW — LangGraph state schema
    tools.py                   # NEW — agent tools + ProcurementContext
    prompts.py                 # NEW — system prompt templates
    graph.py                   # NEW — build_agent(), LangGraph wiring
  utils/
    db.py                      # MODIFY — add write_purchase_orders(), write_alerts()
agent.py                       # MODIFY — wire full pipeline
pyproject.toml                 # MODIFY — add langgraph, langchain-anthropic, pypdf deps
```

---

## Step 1: Dependencies (`pyproject.toml`)

Add: `langgraph`, `langchain-anthropic`, `langchain-core`, `pypdf`

---

## Step 2: Config + Model Factory (`procureai/config.py`)

- `AgentConfig` dataclass: `model_provider` ("anthropic"|"openai"), `model_name` (default "claude-sonnet-4-20250514"), `temperature` (0.0), `max_tokens`, `policy_dir`, `memo_dir`
- `load_config(overrides) -> AgentConfig` — from env vars / defaults
- `get_chat_model(config) -> BaseChatModel` — factory returning `ChatAnthropic` or `ChatOpenAI` based on provider

---

## Step 3: Deterministic Pipeline (`procureai/pipeline.py`)

Pure pandas, no LLM:

- `explode_demand(scenario) -> DataFrame` — join production_schedule × bom → `(order_id, component_id, product_id, quantity_needed, materials_needed_by)`
- `aggregate_demand(demand) -> DataFrame` — group by component_id, sum quantity_needed, compute `earliest_needed_by = min(materials_needed_by)`
- `compute_incoming(scenario) -> DataFrame` — sum existing purchase_orders by component_id → `(component_id, incoming_quantity)`
- `gap_analysis(scenario) -> DataFrame` — `gap = total_needed - on_hand - incoming` (only rows where gap > 0)
- `check_lead_time_feasibility(gap_df, catalog, suppliers, current_date, constraints) -> DataFrame` — for each shortfall, find fastest eligible supplier, flag `feasible=False` if fastest delivery > earliest_needed_by

---

## Step 4: Constraint Extraction (`procureai/constraints.py`)

**Fixed constraint types** (enum):

| Type | Key Params |
|------|-----------|
| `APPROVED_SUPPLIER_ONLY` | — |
| `SUPPLIER_BLOCKED` | supplier_id, reason |
| `CERT_REQUIRED` | component_id, cert_name |
| `DOMESTIC_PREFERENCE` | max_premium_pct (35%), critical_max_premium_pct (50%) |
| `CONCENTRATION_LIMIT` | component_ids, max_pct, secondary_min_pct |
| `CRITICAL_COMPONENT` | component_ids |
| `MOQ_COMPLIANCE` | — |
| `HAZMAT_HANDLING` | component_ids |
| `BUDGET_THRESHOLD` | amount, approver |
| `SUSTAINABILITY_PREFERENCE` | price_tolerance_pct, lead_time_tolerance_days, min_rating |
| `STRATEGIC_SUPPLIER_PROTECTION` | min_savings_pct |
| `AIR_FREIGHT_ALLOWED` | start_date, end_date, lead_time_reduction (14), min_lead_time (7), max_cost (25000) |
| `PCB_QUALIFIED_ONLY` | component_id, note |

**`Constraint` dataclass:** type, params (dict), source (filename), effective_date, expiry_date

**`extract_constraints(config, llm) -> list[Constraint]`:**
1. Read all PDFs from policy_dir + memo_dir using pypdf
2. Prompt LLM with full text + JSON schema for each constraint type
3. Parse structured JSON response → `list[Constraint]`
4. Validate types recognized; fallback to `DEFAULT_CONSTRAINTS` on failure

---

## Step 5: Agent Tools (`procureai/agents/tools.py`)

Shared `ProcurementContext` dataclass holds: ScenarioData, constraints, placed_orders (list[dict]), placed_alerts (list[str]), gap_df (updated as orders placed).

| Tool | Signature | Purpose | Constraints Enforced |
|------|-----------|---------|---------------------|
| `get_shortfalls` | `() -> str` | Component gaps table | — |
| `get_eligible_suppliers` | `(component_id: str) -> str` | Filtered supplier list | Approved list, certs, PCB memo, blocked suppliers |
| `place_order` | `(component_id, supplier_id, quantity, rationale) -> str` | Place PO | MOQ, concentration limits, magnet memo, budget flags |
| `create_alert` | `(description: str) -> str` | Log alert | — |
| `check_concentration` | `(component_id: str) -> str` | Current supplier % | — |
| `get_order_status` | `() -> str` | Progress summary | — |

**`place_order` details:**
- Generates PO number (`PO-AGENT-{seq}`)
- Computes `order_date` from `scenario_config.current_date`
- Computes `expected_delivery_date` = `current_date + lead_time_days` (with air freight memo's -14 day adjustment if applicable)
- Looks up `unit_price` from supplier_catalog
- Validates MOQ, concentration limits; creates budget alert if >$50K/$150K
- Accumulates to `context.placed_orders` (batch write at end)

---

## Step 6: Agent State + Graph (`procureai/agents/state.py`, `graph.py`, `prompts.py`)

**State** (`ProcurementState(MessagesState)`):
- `shortfalls_summary: str`, `constraints_summary: str`, `scenario_summary: str`
- `orders_placed: int`, `remaining_gaps: int`

**System prompt** (`prompts.py`):
- Role: autonomous procurement planner
- Scenario context: current_date, production_schedule, description
- Constraints: all extracted constraints as readable text
- Gap analysis: shortfalls table
- Soft-constraint instructions: domestic preference reasoning, sustainability tie-breaking, strategic supplier loyalty, process by earliest deadline first

**Graph** (`graph.py`):
- `build_agent(config, scenario, constraints) -> (CompiledGraph, ProcurementContext)`
- Creates ProcurementContext, precomputes gap_analysis
- Binds tools as closures over context
- `create_react_agent(llm, tools, prompt=system_prompt)`
- Agent terminates when LLM stops calling tools

---

## Step 7: DB Write Functions (`procureai/utils/db.py`)

Add to existing file:
- `write_purchase_orders(db_path, orders: list[dict]) -> int` — INSERT rows (preserves existing POs for scenario 02)
- `write_alerts(db_path, alerts: list[str]) -> int` — INSERT with autoincrement

---

## Step 8: CLI Integration (`agent.py`)

Wire the full pipeline:
```
1. load_scenario(scenario_path)
2. load_config() + get_chat_model()
3. extract_constraints(config, llm)
4. gap_analysis(scenario)  [log summary]
5. build_agent(config, scenario, constraints)
6. agent.invoke({"messages": [("user", "Begin procurement planning.")]})
7. write_purchase_orders() + write_alerts()
8. Print summary
```

CLI options: `--scenario` (required), `--model` (optional), `--verbose` (flag)

---

## Data Flow

```
scenario.sqlite
     │
     ├─→ load_scenario() → ScenarioData
     │
     ├─→ policy PDFs + memo PDFs
     │        └─→ extract_constraints(llm) → list[Constraint]
     │
     ├─→ pipeline.gap_analysis() → shortfall DataFrame
     │
     └─→ build_agent(config, scenario, constraints)
              └─→ ReAct loop (LLM + tools)
                       ├─→ read-only tools (shortfalls, suppliers, concentration, status)
                       ├─→ place_order → context.placed_orders
                       └─→ create_alert → context.placed_alerts
                              └─→ write to SQLite
```

---

## Key Files to Reference

- `procureai/utils/db.py` — existing ScenarioData, load_scenario()
- `agent.py` — existing CLI stub
- `pyproject.toml` — existing deps
- `data/policies/procurement_policy.pdf` — base policy
- `data/memos/*.pdf` — 3 management memos

---

## Smoke Test

After building, run: `python3 agent.py --scenario data/scenarios/scenario_06_simple.sqlite --verbose`

Expected: 2 purchase orders placed (pressure transducers + sensor housings), 0 critical alerts, completes in <60s.
