# Discovery Scripts & Shared SQLite Utils

## Context
Before building the procurement agent, we need to understand the scenario data. This plan creates reusable SQLite loading utilities and discovery scripts to visually explore the 6 scenario databases.

---

## Phase 1 (Now)

### 1. `procureai/utils/__init__.py`
Re-export key names: `ScenarioData`, `load_scenario`

### 2. `procureai/utils/db.py` — Core data loading
- **`ScenarioData` dataclass** — bundles all tables as DataFrames plus `current_date`, `description`, `db_path`
  - Tables: `products`, `components`, `bom`, `suppliers`, `supplier_catalog`, `inventory`, `production_schedule`, `purchase_orders`, `alerts`
- **`load_scenario(db_path) -> ScenarioData`** — loads all tables + config
- **`load_table(db_path, table) -> DataFrame`** — load a single table
- **`list_scenarios(data_dir?) -> list[Path]`** — find all `.sqlite` files (defaults to `data/scenarios/` relative to project root)
- Default data dir resolved via `Path(__file__).resolve().parent.parent.parent / "data" / "scenarios"`

### 3. `procureai/discovery/overview.py` — Scenario overview + data viewer
- `python -m procureai.discovery.overview [--scenario PATH]`
- **Single scenario mode** (with `--scenario`):
  - Config: current_date, description
  - Row counts per table
  - Print each table's full contents (formatted with pandas) so we can see the actual data
  - For larger tables (bom, supplier_catalog): print all rows — these are small enough (max ~44 rows)
- **All scenarios mode** (no arg):
  - Comparison table: file, date, description, counts of products/components/suppliers/production_orders/existing_POs
- Uses Click CLI (matching `agent.py` convention)

### Implementation Order
1. `procureai/utils/__init__.py` + `db.py`
2. `procureai/discovery/overview.py`
3. Run against `scenario_06_simple` to validate, then all scenarios

---

## Phase 2 (Later — after reviewing Phase 1 output)

These scripts build on the utils layer to provide analytical views. Exact scope TBD based on what we learn from Phase 1 data exploration.

- **`procureai/utils/analysis.py`** — Shared computation helpers (BOM explosion, gap analysis)
- **`procureai/discovery/demand.py`** — BOM explosion: what components are needed per production order
- **`procureai/discovery/gaps.py`** — Inventory gap analysis: needed vs. on_hand vs. incoming POs
- **`procureai/discovery/suppliers.py`** — Supplier options for shortage components
- **`procureai/discovery/compare.py`** — Cross-scenario comparison

---

## Key Schema Details (for reference)
- `production_schedule`: order_id, product_id, quantity, customer, materials_needed_by
- `bom`: product_id, component_id, quantity_per
- `inventory`: component_id, quantity_on_hand, warehouse_location
- `purchase_orders`: po_number, component_id, supplier_id, quantity, unit_price, order_date, expected_delivery_date, rationale
- `supplier_catalog`: supplier_id, component_id, unit_price, lead_time_days, minimum_order_qty, notes
- `suppliers`: supplier_id, name, country, is_domestic, certifications, sustainability_rating, relationship_tier, on_approved_list, notes
- `components`: component_id, name, description, category, unit_of_measure, is_hazardous, requires_certification
- `alerts`: alert_id (INTEGER), description (TEXT)

## CLI Convention
All scripts use Click (matching existing `agent.py`). Each has `if __name__ == "__main__"` for `python -m` invocation.

## Verification
- Run `overview.py --scenario data/scenarios/scenario_06_simple.sqlite` — should print all tables with data
- Run `overview.py` with no args — should print comparison across all 6 scenarios
