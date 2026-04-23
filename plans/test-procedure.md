# Test Procedure: Multi-Model Evaluation

## Purpose

Run ProcureAI against all 6 scenarios with multiple models, verify outputs with pytest, and compare which constraints each model handles correctly. This identifies model-dependent vs systemic issues.

## Prerequisites

```bash
uv sync
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Procedure

### Step 1: Clean All Scenarios

Remove any previous agent runs so each model starts from a clean state.

```bash
for scenario in data/scenarios/scenario_*.sqlite; do
  echo "Cleaning $scenario..."
  # Clean all runs (repeat until no runs remain)
  while python agent.py --scenario "$scenario" --list-runs 2>/dev/null | grep -q "run_id"; do
    python agent.py --scenario "$scenario" --clean
  done
done
```

### Step 2: Run All Scenarios with a Model

```bash
MODEL="claude-sonnet-4-6"  # Change per evaluation run

for scenario in \
  data/scenarios/scenario_06_simple.sqlite \
  data/scenarios/scenario_01_baseline.sqlite \
  data/scenarios/scenario_02_partial_procurement.sqlite \
  data/scenarios/scenario_04_low_inventory.sqlite \
  data/scenarios/scenario_05_competing_demand.sqlite \
  data/scenarios/scenario_03_tight_timeline.sqlite; do
  echo "=== Running $scenario with $MODEL ==="
  python agent.py --scenario "$scenario" --model "$MODEL"
  echo ""
done
```

### Step 3: Run Verification Suite

```bash
uv run python -m pytest tests/test_verification.py -v --tb=short 2>&1 | tee "output/results_${MODEL}.txt"
```

### Step 4: Record Results

Copy the pass/fail summary into the results matrix below.

### Step 5: Clean and Repeat

Clean all scenarios (Step 1), change `MODEL`, repeat Steps 2-4 for each model.

---

## Models to Evaluate

| Model | CLI Flag |
|-------|----------|
| Sonnet 4.6 | `--model claude-sonnet-4-6` |
| Haiku 4.5 | `--model claude-haiku-4-5` |
| Opus 4.6 | `--model claude-opus-4-6` |

---

## Results Matrix

### Test Legend

| # | Test | What It Checks |
|---|------|---------------|
| 1 | `test_no_blocked_suppliers` | SUP-113 never ordered |
| 2 | `test_pcb_supplier_compliance` | CMP-005 from ISO-9001 only |
| 3 | `test_magnet_concentration_max` | ≤50% per magnet supplier |
| 4 | `test_magnet_concentration_min_secondary` | ≥20% secondary magnet supplier |
| 5 | `test_moq_compliance` | Quantities ≥ minimum order qty |
| 6 | `test_price_accuracy` | Prices match catalog |
| 7 | `test_delivery_date_math` | Dates = current + lead time |
| 8 | `test_rationale_present` | Every PO has rationale |
| 9 | `test_no_hallucinated_suppliers` | All supplier IDs real |
| 10 | `test_no_hallucinated_components` | All component IDs real |
| 11 | `test_all_shortfalls_addressed` | Every gap has PO or alert |
| 12 | `test_only_approved_suppliers` | No unapproved suppliers |
| 13 | `test_no_exact_duplicate_orders` | No identical duplicate POs |
| S1 | `TestScenario06::test_orders_placed` | ≥2 POs for simple scenario |
| S2 | `TestScenario06::test_no_critical_alerts` | No critical alerts in simple |
| S3 | `TestScenario02::test_existing_pos_present` | Existing POs present |
| S4 | `TestScenario02::test_fewer_orders_than_baseline` | Fewer orders than s01 |
| S5 | `TestScenario02::test_no_double_ordering` | No orders for zero-gap items |
| S6 | `TestScenario03::test_has_deadline_alerts` | Deadline alerts generated |
| S7 | `TestScenario03::test_no_impossible_delivery_dates` | No past-date deliveries |
| S8 | `TestScenario05::test_correct_current_date` | Date is 2025-10-05 |
| S9 | `TestScenario05::test_no_air_freight_reduction` | No air freight after Sept 30 |

### Sonnet 4.6 (baseline run)

| Test | s01 | s02 | s03 | s04 | s05 | s06 |
|------|-----|-----|-----|-----|-----|-----|
| 1. Blocked suppliers | PASS | PASS | PASS | PASS | PASS | PASS |
| 2. PCB compliance | PASS | skip | PASS | PASS | PASS | skip |
| 3. Magnet max 50% | PASS | PASS | **FAIL** (68%) | PASS | **FAIL** (100%) | skip |
| 4. Magnet min 20% | PASS | PASS | PASS | PASS | **FAIL** (single) | skip |
| 5. MOQ compliance | PASS | PASS | PASS | PASS | PASS | PASS |
| 6. Price accuracy | PASS | PASS | PASS | PASS | PASS | PASS |
| 7. Delivery dates | PASS | PASS | PASS | PASS | PASS | PASS |
| 8. Rationale | PASS | PASS | PASS | PASS | PASS | PASS |
| 9. Valid suppliers | PASS | PASS | PASS | PASS | PASS | PASS |
| 10. Valid components | PASS | PASS | PASS | PASS | PASS | PASS |
| 11. All gaps covered | PASS | PASS | PASS | PASS | PASS | PASS |
| 12. Approved only | PASS | PASS | PASS | PASS | PASS | PASS |
| 13. No duplicates | PASS | PASS | PASS | PASS | PASS | **FAIL** |

**Scenario-specific:** S1-S9 all PASS

**Summary:** 79/87 pass, 4 fail, 4 skip. Failures: magnet concentration (s03, s05), duplicate orders (s06).

### Haiku 4.5

| Test | s01 | s02 | s03 | s04 | s05 | s06 |
|------|-----|-----|-----|-----|-----|-----|
| 1. Blocked suppliers | | | | | | |
| 2. PCB compliance | | | | | | |
| 3. Magnet max 50% | | | | | | |
| 4. Magnet min 20% | | | | | | |
| 5. MOQ compliance | | | | | | |
| 6. Price accuracy | | | | | | |
| 7. Delivery dates | | | | | | |
| 8. Rationale | | | | | | |
| 9. Valid suppliers | | | | | | |
| 10. Valid components | | | | | | |
| 11. All gaps covered | | | | | | |
| 12. Approved only | | | | | | |
| 13. No duplicates | | | | | | |

**Scenario-specific:**

**Summary:**

### Opus 4.6

| Test | s01 | s02 | s03 | s04 | s05 | s06 |
|------|-----|-----|-----|-----|-----|-----|
| 1. Blocked suppliers | | | | | | |
| 2. PCB compliance | | | | | | |
| 3. Magnet max 50% | | | | | | |
| 4. Magnet min 20% | | | | | | |
| 5. MOQ compliance | | | | | | |
| 6. Price accuracy | | | | | | |
| 7. Delivery dates | | | | | | |
| 8. Rationale | | | | | | |
| 9. Valid suppliers | | | | | | |
| 10. Valid components | | | | | | |
| 11. All gaps covered | | | | | | |
| 12. Approved only | | | | | | |
| 13. No duplicates | | | | | | |

**Scenario-specific:**

**Summary:**

---

## Analysis Template

After all models are evaluated, fill in:

### Model Comparison

| Failure | Sonnet 4.6 | Haiku 4.5 | Opus 4.6 | Classification |
|---------|------------|-----------|----------|---------------|
| Magnet concentration (s03) | FAIL | | | systemic / model-dependent? |
| Magnet concentration (s05) | FAIL | | | systemic / model-dependent? |
| Magnet single-source (s05) | FAIL | | | systemic / model-dependent? |
| Duplicate orders (s06) | FAIL | | | systemic / model-dependent? |

### Conclusions

- **Systemic failures** (all models fail) → fix in tool logic / guardrails
- **Model-dependent** (some pass, some fail) → fix in prompt or accept as model capability difference
- **Cost/quality tradeoff** — note total POs, spend, and alert quality per model
