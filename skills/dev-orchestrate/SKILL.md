---
name: dev-orchestrate
description: Chain design-review → plan → test-red → implement → audit in a single session after design is finalized. Gates on design-review verdict and audit verdict. Runs each phase by invoking its skill sequentially.
when-to-use: Use when the user has a finalized design document and wants to go from review through implementation and audit without manually invoking each skill. Triggers include "dev-orchestrate", "build this", "implement this design", "take it from here".
---

# Dev-Orchestrate Skill

Run the full post-design workflow in sequence: `design-review → plan → test-red → implement → audit`.

## Your Task

You are a single-session orchestrator. You will invoke each phase's skill via the Skill tool, check its outcome, and either proceed to the next phase or stop at a gate. Track artifact paths produced by each phase and pass them forward.

**Important**: This will be a long session. Stay focused on the chain. Between phases, briefly report status to the user before continuing.

## Step 1: Read Configuration

Read `./AGENTS.md` to get `design_output_dir`, `plan_output_dir`, and `test_output_dir`.

## Step 2: Gather Inputs

**Prompt the user**:

"I'll run the full post-design workflow: **design-review → plan → test-red → implement → audit**.

Please provide:
1. **Design document path** (e.g., `research/feature-design-2026-04-16.md`)
2. **ICD path** (optional — if a standalone ICD exists)
3. **Linear issue ID** (optional — for requirements traceability and audit writeback)

Which design should I build from?"

**Wait for user response.**

> When dispatched by the orchestrator, inputs may already be in the prompt. Use them and skip waiting.

## Step 3: Record Artifact Tracker

Before starting, initialize a mental tracker for artifact paths produced by each phase. You'll pass these forward as inputs to subsequent phases:

- `design_doc_path`: (from user input)
- `icd_path`: (from user input, or may be produced during planning)
- `review_artifact_path`: (produced by design-review)
- `plan_file_path`: (produced by plan)
- `test_red_report_path`: (produced by test-red)
- `audit_artifact_path`: (produced by audit)

---

## Phase 1: Design Review

Tell the user:
"**→ Phase 1/5: design-review**"

Invoke the `design-review` skill using the Skill tool.

When it prompts for input, provide the design document path (and Linear issue ID if available) — do not wait for the user again.

When it completes, extract:
- The **verdict**: APPROVE, APPROVE_WITH_COMMENTS, or REQUEST_CHANGES
- The **review artifact path**

### Gate check

| Verdict | Action |
|---------|--------|
| APPROVE | Continue to Phase 2 |
| APPROVE_WITH_COMMENTS | Continue to Phase 2. Note the non-blocking issues. |
| REQUEST_CHANGES | **STOP.** Tell the user: "Design review returned REQUEST_CHANGES. The chain is paused. Address the blocking issues in the review artifact, revise the design, and re-run `dev-orchestrate`." Emit a subagent report with status `blocked`. |

If continuing, tell the user:
"**✓ design-review**: {verdict} ({n} blocking, {m} non-blocking issues)
**→ Phase 2/5: plan**"

---

## Phase 2: Plan

Invoke the `plan` skill using the Skill tool.

When it prompts for input, provide the design document path — do not wait for the user again.

When it completes, extract:
- The **plan file path**

Tell the user:
"**✓ plan**: written to `{plan_file_path}`
**→ Phase 3/5: test-red**"

---

## Phase 3: Test-Red

Invoke the `test-red` skill using the Skill tool.

When it prompts for input, provide the plan file path (and ICD path if available) — do not wait for the user again.

When it completes, extract:
- **Tests written count**
- **Unexpected passes count**
- **Test-red report path**

### Unexpected pass check

If unexpected passes > 0:
Tell the user: "⚠️ {n} tests passed unexpectedly — see the test-red report. These may indicate the behavior already exists or the test doesn't exercise new code. Continue anyway?"
**Wait for user confirmation** before proceeding.

If all tests fail as expected, tell the user:
"**✓ test-red**: {n} tests written, all failing as expected
**→ Phase 4/5: implement**"

---

## Phase 4: Implement

Invoke the `implement` skill using the Skill tool.

When it prompts for input, provide the plan file path — do not wait for the user again. The implement skill will detect that RED checkboxes are already marked `[x]` and skip to GREEN.

When it completes, tell the user:
"**✓ implement**: complete
**→ Phase 5/5: audit**"

---

## Phase 5: Audit

Invoke the `audit` skill using the Skill tool.

When it prompts for input, provide: the plan file path, design document path, ICD path (if exists), and Linear issue ID (if provided). Also provide the base branch (typically `main`). Do not wait for the user again.

When it completes, extract:
- The **verdict**: PASS, PASS_WITH_NOTES, or FAIL
- The **audit artifact path**

Tell the user:
"**✓ audit**: {verdict}
Artifact: `{audit_artifact_path}`"

---

## Step 4: Final Summary

After all phases complete (or after a gate stops the chain), present a summary:

```
## dev-orchestrate Summary

| Phase | Status | Artifact |
|-------|--------|----------|
| design-review | {verdict} | `{path}` |
| plan | ✓ | `{path}` |
| test-red | ✓ ({n} tests) | `{path}` |
| implement | ✓ | — |
| audit | {verdict} | `{path}` |

{If audit PASS or PASS_WITH_NOTES}:
**Implementation is complete.** Ready to open a PR or commit.

{If audit FAIL}:
**Audit found blocking issues.** Address them and re-run `audit`.
```

## Step 5: Emit Subagent Report

End your response with:

```
### Subagent Report
- phase: dev-orchestrate
- status: success | blocked | partial
- phases_completed: [list of phases that ran]
- stopped_at: {phase name, if chain was halted}
- design_review_verdict: {verdict}
- audit_verdict: {verdict}
- open_questions:
  - <question 1>
  - <question 2>
```

Use `success` if all 5 phases completed. Use `blocked` if a gate stopped the chain. Use `partial` if a phase returned `partial` or `blocked` for non-gate reasons.

## Important Constraints

- **Pass artifacts forward** — every phase needs paths from prior phases; don't lose track
- **Respect gates** — REQUEST_CHANGES on design-review and FAIL on audit are hard stops
- **Don't re-prompt** — when invoking sub-skills, supply their required inputs yourself from what you already have; only pause for the user at gates or unexpected situations
- **Surface progress** — the user should see a status line between every phase
- **Single context** — you're running all 5 phases in one session; keep responses concise within each phase to preserve context space
