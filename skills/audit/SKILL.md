---
name: audit
description: Post-implementation verification that checks every plan checkbox, ICD interface, and design integration point is complete and correct. Produces a structured audit artifact with a PASS / PASS_WITH_NOTES / FAIL verdict and optionally posts a summary comment to the associated Linear issue.
when-to-use: Use after the `implement` skill has completed. Acts as the final gate in the full chain: design → design-review → plan → test-red → implement → audit. Can also be triggered on demand when the user says "audit" or "verify against design".
---

# Audit Skill

Verify that an implementation is complete and consistent with its design, ICD, and plan. Produce a structured audit artifact. Optionally write back to Linear.

## Your Task

This is a **read-only verification** skill — you inspect code, diffs, plan checkboxes, and design artifacts, then produce a verdict. You do NOT fix issues; you report them clearly so the user can decide how to proceed.

## Step 1: Read Configuration

Read `./AGENTS.md` to get:
- `design_output_dir` — where design docs, ICDs, and the audit output live
- `plan_output_dir` — where the plan file lives
- Test command and coverage target

## Step 2: Gather Inputs

**Prompt the user**:

"I'll audit the implementation against the design and plan.

Please provide:
1. **Plan file path** (e.g., `plans/caching-layer-implementation-plan.md`)
2. **Design document path** (optional but recommended)
3. **ICD path** (optional — if a formal interface contract exists)
4. **Linear issue ID** (optional — if provided, I'll post a summary comment after the audit)
5. **Base branch** for the git diff (default: `main`)

What should I audit?"

**Wait for user response.**

> When dispatched by the orchestrator, these may already be in the prompt. Use them and skip waiting.

## Step 3: Read All Inputs

Read each provided file:
1. Plan file — parse all checkboxes (completed vs. pending)
2. Design document — extract integration points, goals, and interface contracts mentioned inline
3. ICD — extract all interface definitions, DTOs, and error contracts
4. Run `git diff {base_branch}...HEAD --name-only` to get the list of changed files
5. Run `git diff {base_branch}...HEAD` to get the full diff
6. Run the test suite and capture output (pass/fail counts, coverage %)

Display a quick summary to the user before proceeding:

"**Inputs loaded:**
- Plan: `{path}` — {n} checkboxes ({k} complete, {m} pending)
- Design: `{path or 'not provided'}`
- ICD: `{path or 'not provided'}`
- Git diff: {n} files changed
- Tests: running...

Proceeding with full audit."

## Step 4: Run the Six-Dimension Audit

### Dimension 1: Plan Completion

Go through every checkbox in the plan file:
- Count total checkboxes and completed (`[x]`) checkboxes
- List every incomplete (`[ ]`) checkbox by step and description
- Identify any steps with partial completion (some done, some not)

**Verdict for this dimension:**
- PASS: all checkboxes are `[x]`
- PASS_WITH_NOTES: unchecked items are documentation/cleanup only, all functional items done
- FAIL: functional implementation or test checkboxes remain unchecked

### Dimension 2: ICD Conformance

If an ICD was provided:
- For each interface/DTO/endpoint in the ICD, search the codebase for the implementation
- Verify: the implementation exists, the signature matches (parameter names, types, return type), the error contract is implemented
- Flag any ICD entry with no implementation, or an implementation with a mismatched signature

If no ICD was provided, check the design document's "Interface Contracts" section instead.

**Verdict for this dimension:**
- PASS: all ICD contracts are implemented with matching signatures
- PASS_WITH_NOTES: minor signature divergence (e.g., additional optional parameter) with no behavioral impact
- FAIL: missing implementation, wrong return type, unimplemented error contract

### Dimension 3: Design Integration Points

Read the design document's "Integration Points" and "Key Workflows" sections:
- Verify each named integration point is wired up in the implementation (check the diff for connection points)
- Verify key sequence diagram flows are implemented (trace through the code)
- Check that no integration point is left as a stub or TODO

### Dimension 4: No Leftover TODOs or Stubs

Search the changed files for:
- `TODO`, `FIXME`, `HACK`, `XXX` comments added in this diff
- `raise NotImplementedError` / `throw new Error("not implemented")` or equivalent
- Functions that contain only `pass`, `return undefined`, `return null` with no logic
- Test stubs created by `test-red` that were never replaced by real implementations

Flag each one with the file path and line number.

### Dimension 5: Test Coverage Delta

- Report the current coverage percentage (from test output)
- Compare against the repo's target (from `AGENTS.md`)
- If coverage dropped below target, identify which new files have the lowest coverage
- Report the delta vs. the pre-implementation baseline if available

### Dimension 6: Final Test Suite Status

- Are all tests passing? Report pass/fail counts
- Are there any skipped or xfailed tests that shouldn't be?
- Run linting and report results (errors vs. warnings)

## Step 5: Determine Overall Verdict

| Verdict | When to use |
|---------|-------------|
| **PASS** | All dimensions pass. Implementation is complete and consistent. |
| **PASS_WITH_NOTES** | No blocking issues, but some non-critical items (cleanup TODOs, coverage slightly under target, documentation gaps). |
| **FAIL** | One or more blocking issues: unchecked functional checkboxes, missing ICD implementations, broken tests, or significant leftover stubs. |

## Step 6: Show Summary to User

Present the audit summary before writing the artifact:

"**Audit Summary** for `{plan filename}`:

**Verdict: {VERDICT}**

**Plan completion**: {k}/{n} checkboxes complete
**ICD conformance**: {PASS/FAIL — n issues}
**Integration points**: {PASS/FAIL — n issues}
**Leftover TODOs**: {n found}
**Test suite**: {n passing, m failing}
**Coverage**: {n}% (target: {target}%)

{If FAIL}: Blocking issues:
- {issue 1}
- {issue 2}

Shall I write the full audit artifact?"

**Wait for user confirmation.**

> When dispatched by the orchestrator without an interactive user, proceed to write without waiting.

## Step 7: Write Audit Artifact

**File naming**: `{feature-name}-audit-{today's date}.md`

**Output path**: `{design_output_dir}/{feature-name}-audit-YYYY-MM-DD.md`

```markdown
# Implementation Audit: [Feature Name]

**Date**: YYYY-MM-DD
**Plan**: `{plan file path}`
**Design**: `{design doc path, or "not provided"}`
**ICD**: `{icd path, or "not provided"}`
**Base branch**: `{base branch}`
**Verdict**: PASS | PASS_WITH_NOTES | FAIL

---

## Summary

{2–4 sentence executive summary of what was implemented and the audit outcome.}

---

## Blocking Issues

{If none, write "None."}

### B1: {Issue title}
**Dimension**: Plan Completion | ICD Conformance | Design Integration | TODOs | Coverage | Tests
**Finding**: {Specific description with file paths and line numbers.}
**Required action**: {What must be done before this audit can be upgraded to PASS.}

---

## Non-Blocking Issues

{If none, write "None."}

### N1: {Issue title}
**Dimension**: {dimension}
**Finding**: {Description.}
**Suggested action**: {Recommendation.}

---

## Dimension Results

### Plan Completion
**Verdict**: PASS | PASS_WITH_NOTES | FAIL
**Checkboxes**: {k} / {n} complete

{List any incomplete checkboxes:}
- [ ] Step {n}: {checkbox text} — {why it's incomplete or unknown}

### ICD Conformance
**Verdict**: PASS | PASS_WITH_NOTES | FAIL

{For each ICD entry, one line: ✓ or ✗ + name + finding}

### Design Integration Points
**Verdict**: PASS | PASS_WITH_NOTES | FAIL

{List integration points and whether each is wired up.}

### Leftover TODOs / Stubs
**Verdict**: PASS | PASS_WITH_NOTES | FAIL

{If none: "No leftover TODOs or stubs found in the diff."}
{Otherwise list each: file:line — comment text}

### Test Coverage
**Verdict**: PASS | PASS_WITH_NOTES | FAIL
**Coverage**: {n}% (target: {target}%)
**Delta**: {+n% / -n% from baseline if known}

{If under target: list lowest-coverage new files}

### Test Suite
**Verdict**: PASS | FAIL
**Results**: {n} passing, {m} failing, {k} skipped
**Linting**: {clean / n errors, m warnings}

---

## Verdict Rationale

{Explain the verdict. If PASS, summarise the quality of the implementation. If FAIL, list blocking issues by ID (B1, B2, …).}

---

## Next Steps

{If PASS or PASS_WITH_NOTES}:
Implementation is complete. Consider:
- Opening a PR if not already done
- Posting to Linear (see Linear Writeback section below)
- Running `design-review` on any follow-on work

{If FAIL}:
Address blocking issues B1, B2, … and re-run `audit`. Do not open a PR until the verdict is PASS or PASS_WITH_NOTES.

---

## Linear Writeback

{If a Linear issue ID was provided, this section will be posted as a comment. If not, write "No Linear issue provided — skipping writeback."}

**Posted to**: {issue ID}
**Comment summary**:
> Audit complete — **{VERDICT}**
> Plan: {k}/{n} checkboxes | Tests: {n} passing | Coverage: {n}%
> {If blocking issues}: Blocking: {B1 title}, {B2 title}
> Full report: `{audit artifact path}`
```

## Step 8: Linear Writeback (if issue ID provided)

If the user provided a Linear issue ID, post a summary comment using the Linear MCP tools available to the orchestrator.

**Note**: This skill runs as a subagent dispatched by the orchestrator. The orchestrator has access to `mcp__linear-server__*` tools. To trigger writeback, include the Linear issue ID and the audit summary in your subagent report — the orchestrator will handle the actual comment posting.

Include in the subagent report:
```
- linear_issue: {issue ID}
- linear_comment: |
    Audit complete — **{VERDICT}**
    Plan: {k}/{n} checkboxes | Tests: {n} passing | Coverage: {n}%
    {blocking issues summary if any}
    Full report: `{artifact path}`
```

## Step 9: Final Confirmation

Tell the user:

"Audit complete. **Verdict: {VERDICT}**

Artifact written to: `{full path}`

{If PASS}: Implementation is clean. Ready to open a PR.
{If PASS_WITH_NOTES}: Implementation is functionally complete. See non-blocking issues for cleanup items.
{If FAIL}: {n} blocking issues found. Address them and re-run `audit`.

{If Linear writeback}: Summary comment posted to {issue ID}."

## Step 10: Emit Subagent Report

End your response with:

```
### Subagent Report
- phase: audit
- status: success | blocked | partial
- verdict: PASS | PASS_WITH_NOTES | FAIL
- linear_issue: {issue ID or "none"}
- linear_comment: |
    {comment text for orchestrator to post, or "none"}
- open_questions:
  - <question 1>
  - <question 2>
```

Use `blocked` if the plan or test suite couldn't be read. Use `partial` if a dimension was skipped. Use `success` otherwise (regardless of verdict).

## Important Constraints

- **Read-only on source files** — do not modify implementation files
- **Run the test suite** — never skip this; an untested audit is incomplete
- **Cite evidence** — every finding must include a file path and the specific issue
- **Don't fix** — report issues, don't silently correct them; the user decides what to do
- **Be honest about verdict** — PASS_WITH_NOTES is not a consolation prize; if there are no blocking issues, it's the right verdict
