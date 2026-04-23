# Fix Plan: Agent Reasoning Gaps

## Summary

After running all 6 scenarios and verifying with pytest (72 pass, 5 fail, 10 skip), all failures trace to **agent reasoning**, not code bugs. The deterministic pipeline, tool validation, price/date math, and constraint filtering all work correctly.

## Failures

### F1: Magnet concentration >50% (scenarios 03, 05)

**What happened:**
- Scenario 03: 208/308 (68%) placed with SUP-108, 100 with SUP-107
- Scenario 05: 440/440 (100%) placed with SUP-108, zero to SUP-107

**Root cause:** The agent has `check_concentration` as a tool but doesn't consistently use it, and there's no hard guardrail in `place_order` that blocks an order exceeding 50%.

**Fix options:**

A. **Prompt reinforcement** (low effort, moderate reliability)
   - Add explicit step in workflow: "Before placing magnet orders, calculate the split. For CMP-003/RM-3003, you MUST use at least 2 suppliers with max 50% and min 20% each."
   - File: `procureai/agents/prompts.py`

B. **Tool-level guardrail** (moderate effort, high reliability)
   - In `place_order`, after recording the order, check concentration for components with a `CONCENTRATION_LIMIT` constraint. If the new order would push a supplier above the limit, reject it with an error message telling the agent to split.
   - File: `procureai/agents/tools.py`

**Recommendation:** Both A and B together. The prompt tells the agent what to do; the tool prevents violations even if the agent ignores the prompt.

---

### F2: Duplicate orders (scenario 06)

**What happened:** CMP-016 × 15 from SUP-102 placed twice identically (PO-AGENT-002 and PO-AGENT-003).

**Root cause:** The agent decided to split a quantity across two calls but used the same supplier and same quantity. The gap table decrements correctly, so the second order over-procures.

**Fix options:**

A. **Prompt reinforcement** (low effort)
   - Add: "Never place the same order twice. Check `get_order_status` before placing additional orders for the same component."
   - File: `procureai/agents/prompts.py`

B. **Tool-level warning** (low effort, non-blocking)
   - In `place_order`, if an identical (component_id, supplier_id, quantity) order already exists in `ctx.placed_orders`, return a warning asking the agent to confirm or adjust.
   - File: `procureai/agents/tools.py`

**Recommendation:** B — the tool should catch this without relying on prompt compliance.

---

### F3: Gap reconciliation / double-ordering (scenario 02) — RESOLVED: Test Bug

**What happened:** Test asserted CMP-003 was fully covered by existing POs, but investigation shows CMP-003 has a real gap of 58 units (328 needed - 120 on hand - 150 incoming = 58). Agent correctly ordered more magnets.

**Fix:** Updated test to only flag agent orders for components with gap=0 (truly fully covered). Done.

---

## Implementation Order

1. ~~**Investigate F3**~~ — DONE. Was a test bug (existing POs only partially cover demand). Test fixed.
2. **Run multi-model evaluation** — use the `/model-eval` skill to run all scenarios with haiku, sonnet, and opus. This classifies failures as systemic vs model-dependent before fixing. Run: `/model-eval` and specify the model when prompted.
3. **Fix F1** — prompt + tool guardrail for magnet concentration (highest impact, 3 scenarios affected)
4. **Fix F2** — duplicate order warning in `place_order`
5. **Re-run all scenarios** — use `/model-eval` again after fixes to confirm regressions are resolved

## Files to Modify

| File | Changes |
|------|---------|
| `procureai/agents/prompts.py` | Add explicit magnet split instructions, duplicate warning, gap-only ordering |
| `procureai/agents/tools.py` | Add concentration check in `place_order`, duplicate order warning |
| `tests/test_verification.py` | Potentially update F3 test if investigation shows it's a test issue |
