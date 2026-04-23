# Plan 2: Verify the Agent Loop

## Context

After the agent is built (Plan 1), systematically verify it across all 6 scenarios, checking policy compliance, constraint enforcement, and output correctness. Run scenarios in order of increasing complexity.

---

## Test Order

### Test 1: Scenario 06 — Simple (Happy Path)
- **Input:** 1 order (10 × SensorArray Pro), 2 shortfalls, 44 days available
- **Expected POs:** ~2 (pressure transducers + sensor housings)
- **Expected alerts:** 0 critical
- **Validates:** BOM explosion works, tools callable, POs written to DB, basic supplier selection

**Checks:**
- [ ] purchase_orders table has rows with all required columns populated
- [ ] No orders to SUP-113
- [ ] Quantities ≥ MOQ for each supplier
- [ ] expected_delivery_date ≤ materials_needed_by
- [ ] Rationale field present and non-empty
- [ ] unit_price matches supplier_catalog

---

### Test 2: Scenario 01 — Baseline (Standard Complexity)
- **Input:** 4 orders (63 units total), 13 shortfalls, 11–39 days
- **Key challenges:** Magnet dual-sourcing, domestic preference, hazmat handling

**Checks:**
- [ ] All 13 shortfalls addressed (PO or alert for each)
- [ ] Magnet orders: concentration ≤50% per supplier, ≥20% from secondary (MEMO-2025-041)
- [ ] PCBs sourced only from ISO-9001 certified, previously-qualified suppliers (MEMO-2025-085)
- [ ] Domestic suppliers preferred; international only with justification
- [ ] CMP-010/CMP-011 orders flagged with hazmat handling notes
- [ ] PO-5001 (11-day deadline): alert if no supplier can deliver magnets in time
- [ ] Budget alerts for any single PO >$50K
- [ ] Strategic suppliers (Sterling, Bayern, Pinnacle) not bypassed for <15% savings

---

### Test 3: Scenario 02 — Partial Procurement (Gap Reconciliation)
- **Input:** Same as 01, but 4 existing POs (150 magnets, 50 PCBs, 200 steel incoming)
- **Key challenge:** Don't double-order what's already in flight

**Checks:**
- [ ] Existing POs reduce gap correctly (e.g., magnet gap should be smaller than Scenario 01)
- [ ] No duplicate orders for components with incoming POs that fully cover demand
- [ ] Remaining gaps still addressed
- [ ] Total magnet orders (existing + new) still respect concentration limits

---

### Test 4: Scenario 04 — Low Inventory (Bulk Ordering)
- **Input:** Same orders as 01, but near-zero inventory across all 19 components
- **Key challenge:** All components short, MOQ compliance at scale

**Checks:**
- [ ] All 19 components have POs or alerts
- [ ] Every PO quantity ≥ supplier MOQ
- [ ] Concentration limits respected across large order volumes
- [ ] Total spend tracked; appropriate budget threshold alerts generated
- [ ] No component missed due to agent loop terminating early

---

### Test 5: Scenario 05 — Competing Demand (Multi-Supplier Orchestration)
- **Input:** 90 units, 560 magnets needed (highest across all scenarios), different current_date (2025-10-05)
- **Key challenge:** Magnet contention between PowerDrive 3000 and 5000

**Checks:**
- [ ] Magnet orders: SUP-107 (Nanjing) ≤ 50% of 560 = ≤280 units
- [ ] Magnet orders: SUP-108 (MagnetPro) ≥ 20% of 560 = ≥112 units
- [ ] Current date correctly read as 2025-10-05 (not 2025-09-01)
- [ ] Expedited shipping memo expired (ends Sept 30) — no air freight applied
- [ ] All 4 products' demands aggregated before ordering (not ordered per-product independently)

---

### Test 6: Scenario 03 — Tight Timeline (Infeasibility + Alerts)
- **Input:** 5 orders (113 units), PO-5005 due in 9 days (50 × ControlHub X1)
- **Key challenge:** 9-day deadline vs 10+ day PCB lead time = impossible

**Checks:**
- [ ] Alert created for PO-5005 PCB infeasibility (fastest PCB lead time > 9 days)
- [ ] Expedited shipping memo applied where valid (July-Sept 2025, international suppliers)
- [ ] Air freight lead time reduction: catalog_lead_time - 14 days (minimum 7 days)
- [ ] Air freight cost tracking (toward $25K cap)
- [ ] Partial fulfillment: orders placed for what IS achievable, alerts for what isn't
- [ ] No phantom orders with impossible delivery dates

---

## Cross-Scenario Validation (Run After All 6)

| Check | Description |
|-------|-------------|
| SUP-113 exclusion | Zero orders to SUP-113 across all scenarios |
| PCB compliance | All PCB orders from ISO-9001 + previously-qualified suppliers |
| Magnet memo | All magnet orders respect 50%/20% split |
| MOQ compliance | Every PO quantity ≥ supplier's minimum_order_qty |
| Price accuracy | Every unit_price matches supplier_catalog exactly |
| Date math | Every expected_delivery_date = current_date + lead_time_days (± air freight) |
| Rationale quality | Every PO has a rationale explaining supplier choice |
| Alert coverage | Every infeasible shortfall has a corresponding alert |
| No hallucinated suppliers | All supplier_ids exist in suppliers table |
| No hallucinated components | All component_ids exist in components table |

---

## Automation Approach

Write a `verify.py` script that:
1. Loads the post-agent SQLite database
2. Runs each check programmatically against the purchase_orders and alerts tables
3. Joins against supplier_catalog, components, suppliers for validation
4. Outputs pass/fail for each check with details on failures

This makes verification repeatable and can be run against held-out scenarios too.
