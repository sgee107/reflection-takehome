---
name: model-eval
description: Run the full test and verification plan against all 6 scenarios with a specified model (or the current default). Cleans previous runs, executes the agent on each scenario, runs the pytest verification suite, and produces a results summary. Use when switching models or providers to validate agent behavior.
when-to-use: Use when the user changes the model name or provider, says "evaluate model", "test with haiku", "run verification", "model eval", or wants to compare agent quality across models.
---

# Model Evaluation Skill

Run ProcureAI against all 6 scenarios with a given model, verify outputs, and produce a structured results report.

## Your Task

Execute the full test procedure from `plans/test-procedure.md` and verify against `plans/verification-plan.md`. This is a **destructive operation** on scenario databases (cleans previous runs, writes new POs), so confirm the model before proceeding.

## Step 1: Read Context

1. Read `plans/test-procedure.md` for the full procedure
2. Read `plans/verification-plan.md` for the verification checks
3. Read `plans/fix-plan.md` for known failures and their status

## Step 2: Confirm Provider and Model

The agent supports multiple model providers. Use `scripts/set-model.sh` to configure `.env` automatically.

| Provider | `--model` examples | `set-model.sh` usage |
|----------|-------------------|---------------------|
| Anthropic | `claude-sonnet-4-6`, `claude-haiku-4-5` | `./scripts/set-model.sh anthropic claude-sonnet-4-6` |
| OpenAI | `gpt-4o`, `gpt-4o-mini` | `./scripts/set-model.sh openai gpt-4o` |
| Ollama | `qwen2.5:7b`, `llama3.1:8b` | `./scripts/set-model.sh ollama qwen2.5:7b` |
| vLLM | `Qwen/Qwen2.5-7B-Instruct` | `./scripts/set-model.sh vllm <model> <url>` |

**Always ask the user** (unless already specified in the prompt):

"Which model and provider should I evaluate?

Examples:
- `anthropic claude-sonnet-4-6`
- `ollama qwen2.5:7b`
- `vllm Qwen/Qwen2.5-7B-Instruct http://localhost:8000/v1`"

**Wait for user to confirm the provider + model name.**

> When dispatched by the orchestrator or the model is already in the prompt, skip waiting.

Once the provider and model are confirmed, **immediately run**:

```bash
./scripts/set-model.sh <provider> <model> [base-url]
```

Then verify the `.env` is correct by reading it. The script preserves existing API keys and only updates the provider/model/base_url fields.

The model name from `.env` will be used automatically by the agent CLI. You do NOT need to pass `--model` separately — the `.env` config is sufficient. However, `--model` can still be used to override if needed.

## Step 2.5: Initialize the Run Log

Create a live output log that users can monitor during the evaluation. This log is written to continuously throughout the run so users can `tail -f` it to watch progress.

```bash
mkdir -p output/
LOG_FILE="output/model-eval-${MODEL_NAME}-$(date +%Y-%m-%d)-run.log"
```

Write the log header immediately:

```
========================================
 ProcureAI Model Evaluation — Live Log
========================================
Model:    {model name}
Started:  {ISO timestamp}
Log file: {LOG_FILE path}
========================================
```

**Tell the user** the log path so they can follow along:

"Writing live progress to `{LOG_FILE}`. You can monitor it with:
```bash
tail -f {LOG_FILE}
```"

**All subsequent steps must append their output to this log file** in addition to reporting status in the conversation. Use `| tee -a "$LOG_FILE"` for shell commands, and explicitly append section headers and summaries to the log between steps.

## Step 3: Clean All Scenarios

Remove any previous agent runs so the model starts from a clean state.

Append to the log:
```
[{timestamp}] === PHASE: CLEAN ===
```

```bash
for scenario in data/scenarios/scenario_*.sqlite; do
  echo "[$(date +%H:%M:%S)] Cleaning $scenario..." | tee -a "$LOG_FILE"
  while python agent.py --scenario "$scenario" --list-runs 2>/dev/null | grep -q "run_id"; do
    python agent.py --scenario "$scenario" --clean 2>&1 | tee -a "$LOG_FILE"
  done
done
echo "[$(date +%H:%M:%S)] All scenarios cleaned." | tee -a "$LOG_FILE"
```

Report: "All scenarios cleaned."

## Step 4: Run All Scenarios

Run scenarios in order of increasing complexity (this is important — simplest first catches basic issues early):

1. `scenario_06_simple.sqlite`
2. `scenario_01_baseline.sqlite`
3. `scenario_02_partial_procurement.sqlite`
4. `scenario_04_low_inventory.sqlite`
5. `scenario_05_competing_demand.sqlite`
6. `scenario_03_tight_timeline.sqlite`

Append to the log before starting:
```
[{timestamp}] === PHASE: RUN SCENARIOS ===
```

For each scenario:
```bash
echo "[$(date +%H:%M:%S)] --- Running <scenario_file> ---" | tee -a "$LOG_FILE"
python agent.py --scenario data/scenarios/<scenario_file> --model <MODEL> 2>&1 | tee -a "$LOG_FILE"
echo "[$(date +%H:%M:%S)] Exit code: $?" | tee -a "$LOG_FILE"
```

**Between each scenario**, report a brief status line (both to conversation and log):
```
✓ scenario_06_simple — 2 POs, $X spend, 0 alerts
→ Running scenario_01_baseline...
```

Append a per-scenario summary block to the log after each run:
```
[{timestamp}] RESULT scenario_06_simple: {POs} POs, ${spend} spend, {alerts} alerts — {✓/✗}
```

**If a scenario fails** (non-zero exit code), capture the error and continue to the next scenario. Do NOT stop the entire evaluation. Log the failure:
```
[{timestamp}] FAILED scenario_XX: {error message}
```

## Step 5: Run Verification Suite

Append to the log:
```
[{timestamp}] === PHASE: VERIFICATION ===
```

```bash
uv run python -m pytest tests/test_verification.py -v --tb=short 2>&1 | tee -a "$LOG_FILE"
```

Capture the full output. Parse:
- Total tests: passed, failed, skipped, errors
- Per-test results (test name → PASS/FAIL/SKIP)

Append a summary to the log:
```
[{timestamp}] VERIFICATION SUMMARY: {passed} passed, {failed} failed, {skipped} skipped
```

## Step 6: Run Linting

Append to the log:
```
[{timestamp}] === PHASE: LINT ===
```

```bash
uv run ruff check . 2>&1 | tee -a "$LOG_FILE"
uv run ruff format --check . 2>&1 | tee -a "$LOG_FILE"
```

## Step 7: Produce Results Report

Create or update the file `output/model-eval-{model-name}-{YYYY-MM-DD}.md` with:

```markdown
# Model Evaluation: {model name}

**Date**: {today}
**Model**: {full model string}
**Scenarios run**: {n}/6
**Test results**: {passed}/{total} passed, {failed} failed, {skipped} skipped

---

## Scenario Summaries

| Scenario | POs Placed | Total Spend | Alerts | Status |
|----------|-----------|-------------|--------|--------|
| 06 Simple | {n} | ${x} | {n} | ✓ / ✗ |
| 01 Baseline | {n} | ${x} | {n} | ✓ / ✗ |
| 02 Partial | {n} | ${x} | {n} | ✓ / ✗ |
| 04 Low Inventory | {n} | ${x} | {n} | ✓ / ✗ |
| 05 Competing | {n} | ${x} | {n} | ✓ / ✗ |
| 03 Tight Timeline | {n} | ${x} | {n} | ✓ / ✗ |

---

## Verification Results

### Cross-Scenario Tests

| # | Test | s01 | s02 | s03 | s04 | s05 | s06 |
|---|------|-----|-----|-----|-----|-----|-----|
| 1 | Blocked suppliers | {P/F/S} | ... | ... | ... | ... | ... |
| 2 | PCB compliance | ... | ... | ... | ... | ... | ... |
| 3 | Magnet max 50% | ... | ... | ... | ... | ... | ... |
| 4 | Magnet min 20% | ... | ... | ... | ... | ... | ... |
| 5 | MOQ compliance | ... | ... | ... | ... | ... | ... |
| 6 | Price accuracy | ... | ... | ... | ... | ... | ... |
| 7 | Delivery dates | ... | ... | ... | ... | ... | ... |
| 8 | Rationale | ... | ... | ... | ... | ... | ... |
| 9 | Valid suppliers | ... | ... | ... | ... | ... | ... |
| 10 | Valid components | ... | ... | ... | ... | ... | ... |
| 11 | All gaps covered | ... | ... | ... | ... | ... | ... |
| 12 | Approved only | ... | ... | ... | ... | ... | ... |
| 13 | No duplicates | ... | ... | ... | ... | ... | ... |

### Scenario-Specific Tests

| Test | Result | Details |
|------|--------|---------|
| S1: s06 orders placed | {P/F} | {details} |
| S2: s06 no critical alerts | {P/F} | {details} |
| S3: s02 existing POs | {P/F} | {details} |
| S4: s02 fewer orders | {P/F} | {details} |
| S5: s02 no double ordering | {P/F} | {details} |
| S6: s03 deadline alerts | {P/F} | {details} |
| S7: s03 no impossible dates | {P/F} | {details} |
| S8: s05 correct date | {P/F} | {details} |
| S9: s05 no air freight | {P/F} | {details} |

---

## Failure Analysis

{For each failure, include:}

### {Test name} — {scenario}
**Expected**: {what should have happened}
**Actual**: {what happened, with specific values}
**Classification**: systemic | model-dependent | test-issue
**Known issue?**: Yes (F1/F2/F3 from fix-plan) | No (new failure)

---

## Comparison with Previous Models

{If previous eval reports exist in output/, compare pass rates}

| Metric | {previous model} | {this model} |
|--------|-----------------|--------------|
| Total pass | {n} | {n} |
| Total fail | {n} | {n} |
| Magnet concentration | {P/F} | {P/F} |
| Duplicate orders | {P/F} | {P/F} |

---

## Recommendations

{Based on results:}
- If this model is better/worse than previous runs
- Which failures are systemic (all models fail) vs model-dependent
- Whether prompt or tool changes are warranted
```

## Step 8: Surface Results

Present a concise summary to the user:

"**Model evaluation complete: {model}**

Results: {passed}/{total} tests passed ({failed} failed, {skipped} skipped)

| Key Check | Result |
|-----------|--------|
| SUP-113 blocked | {P/F} |
| Magnet concentration | {P/F} |
| MOQ compliance | {P/F} |
| Price accuracy | {P/F} |
| No duplicates | {P/F} |

{New failures vs previous}: {list any}
{Regressions}: {list any}

Full report: `output/model-eval-{model}-{date}.md`
Run log: `{LOG_FILE}`"

## Step 8.5: Finalize the Run Log

Append a footer to the log file:

```
[{timestamp}] === PHASE: REPORT ===
[{timestamp}] Results report written to output/model-eval-{model}-{date}.md
========================================
 Evaluation Complete
 Model:    {model name}
 Finished: {ISO timestamp}
 Result:   {passed}/{total} passed, {failed} failed
========================================
```

## Step 9: Emit Subagent Report

End your response with:

```
### Subagent Report
- phase: model-eval
- status: success | partial | blocked
- model: {model name}
- scenarios_run: {n}/6
- tests_passed: {n}
- tests_failed: {n}
- tests_skipped: {n}
- new_failures: [{list}]
- regressions: [{list vs previous model if known}]
- open_questions:
  - <question 1>
```

## Important Constraints

- **Always clean before running** — stale POs from a different model invalidate results
- **Run scenarios in complexity order** — fail fast on simple cases
- **Capture all output** — agent output is needed for the scenario summary table
- **Don't fix failures** — this skill evaluates, it doesn't repair
- **Compare to known failures** — check fix-plan.md to classify failures as known vs new
- **Create output dir if needed** — `mkdir -p output/` before writing the report
