---
name: design-review
description: Validate a design document against the current codebase for feasibility, pattern consistency, edge-case coverage, ICD completeness, and requirements traceability. Produces a structured review artifact with a APPROVE / APPROVE_WITH_COMMENTS / REQUEST_CHANGES verdict.
when-to-use: Use after the `design` skill has produced a design document and before running the `plan` skill. Acts as a gate — REQUEST_CHANGES blocks progression to planning. Can also be triggered on demand when the user says "review this design" or "check design".
---

# Design Review Skill

Validate a design document against the current codebase, producing a structured review with a clear verdict. This is a **read-only** skill — it does not modify source files, only writes its review artifact.

## Your Task

Perform a systematic review across six dimensions: feasibility, pattern consistency, edge-case coverage, ICD completeness, cross-cutting concerns, and requirements traceability. Produce a verdict and a written artifact. If the verdict is REQUEST_CHANGES, the `plan` skill must not be started until issues are addressed.

## Step 1: Read Configuration

Read `./AGENTS.md` to get `design_output_dir`. This is where both the input design doc and the output review artifact live.

## Step 2: Locate Design Document

**Prompt the user**:

"I'll review a design document for quality and feasibility.

Please provide:
1. **Path to design document** (e.g., `research/caching-layer-design-2026-04-09.md`)
2. **Linear issue ID** (optional — used for requirements traceability, e.g. `PER-42`)

Which design should I review?"

**Wait for user response.**

> When dispatched by the orchestrator, the design doc path and issue context may already be in the prompt. Use them directly and skip waiting.

Read the design document using the Read tool. If it doesn't exist, report the error and emit a blocked subagent report.

## Step 3: Understand the Codebase

Before evaluating the design, read enough of the repo to make informed judgements:

1. Read `./AGENTS.md` fully for stack/language/test conventions
2. Glob for source files to understand the project structure
3. Read key entry points, interfaces, and base classes that the design touches or extends
4. Note the existing architectural patterns (layering, naming conventions, dependency injection style, error handling approach, etc.)

Do NOT skip this step — reviews that don't reference actual code are not useful.

## Step 4: Run the Six-Dimension Review

Evaluate each dimension and write detailed findings. Be specific: cite file paths and line numbers when calling out pattern divergences, and quote the design doc when flagging missing coverage.

### Dimension 1: Feasibility

- Does the stack support the proposed approach? (runtime, language features, available libraries)
- Are there any dependencies that need to be added? Do they conflict with existing ones?
- Does the design require changes to infrastructure or deployment that aren't called out?
- Is the scope achievable given the current codebase structure?

### Dimension 2: Pattern Consistency

- Does the design follow existing layering conventions (e.g., service → repository → model)?
- Do the proposed interfaces match the naming and signature style of existing interfaces?
- Does error handling follow existing patterns (exceptions vs. result types, error shapes)?
- Do the proposed data models follow existing schema conventions?
- Are there existing utilities or abstractions the design should reuse but doesn't?

### Dimension 3: Edge Cases and Missing Coverage

Systematically check for:
- **Error paths**: What happens when dependencies fail? Are all error states named?
- **Empty/zero states**: Empty collections, null/undefined, zero values
- **Concurrency**: Race conditions, double-submission, cache stampede
- **Auth boundaries**: Is every endpoint/operation gated correctly? Can an unprivileged caller reach any new surface?
- **Idempotency**: For mutating operations, what happens on retry?
- **Rollback / partial failure**: If a multi-step operation fails partway, what's the state?

### Dimension 4: ICD Completeness

- Are all method signatures fully typed (parameters + return types + error types)?
- Are all DTOs complete — no `any`, no `object`, no unspecified fields?
- Are request/response shapes defined for every API endpoint?
- Are error response shapes defined (not just "throw an error")?
- Are backward-compatibility implications called out for any changed interfaces?
- Can each interface be tested in isolation (testability check)?

### Dimension 5: Cross-Cutting Concerns

- **Security**: Input validation, injection risks, sensitive data in logs, CORS, auth token handling
- **Observability**: Are key operations instrumented? Errors logged with enough context? Metrics proposed?
- **Performance**: Any N+1 queries, missing indexes, unbounded loops, or synchronous blocking calls in hot paths?
- **Configuration**: Are environment-specific values externalised vs. hardcoded?

### Dimension 6: Requirements Traceability

If a Linear issue was provided:
- Read the issue title and body (from the prompt context — do not re-fetch)
- Check that every stated requirement appears somewhere in the design
- Flag any requirement that has no corresponding design section or interface
- Note any design section that doesn't map to any stated requirement (potential scope creep)

If no issue was provided, skip this dimension and note its omission.

## Step 5: Determine Verdict

Based on your findings:

| Verdict | When to use |
|---------|-------------|
| **APPROVE** | No blocking issues. Minor nits only, or nothing at all. |
| **APPROVE_WITH_COMMENTS** | Non-blocking issues that should be addressed before or during implementation, but don't require a design revision. |
| **REQUEST_CHANGES** | One or more blocking issues that make the design unimplementable, unsafe, or significantly incomplete. The design must be revised before `plan` can proceed. |

A single blocking issue in any dimension is sufficient for REQUEST_CHANGES. Be direct about the verdict — don't soften REQUEST_CHANGES into APPROVE_WITH_COMMENTS to be polite.

## Step 6: Show Summary to User

Present a concise summary before writing the artifact:

"**Design Review Summary** for `{design doc filename}`:

**Verdict: {VERDICT}**

**Blocking issues** ({n}):
- {issue 1}
- {issue 2}

**Non-blocking issues** ({n}):
- {issue 1}

**No issues found in**: {dimensions with clean bill of health}

{If REQUEST_CHANGES}: The `plan` skill should not be started until these are resolved. You can revise the design and re-run `design-review`, or address the issues inline.

Shall I write the full review artifact?"

**Wait for user confirmation** before writing the file.

> When dispatched by the orchestrator without an interactive user, proceed to write the file without waiting.

## Step 7: Write Review Artifact

**File naming**: `{feature-name}-design-review-{today's date}.md`

**Output path**: `{design_output_dir}/{feature-name}-design-review-YYYY-MM-DD.md`

Use this structure:

```markdown
# Design Review: [Feature Name]

**Date**: YYYY-MM-DD
**Design document**: `{path to design doc}`
**Reviewer**: Claude Code
**Verdict**: APPROVE | APPROVE_WITH_COMMENTS | REQUEST_CHANGES

---

## Summary

{2–4 sentence executive summary of the design and the review outcome.}

---

## Blocking Issues

{If none, write "None."}

### B1: {Issue title}

**Dimension**: Feasibility | Pattern Consistency | Edge Cases | ICD Completeness | Cross-Cutting | Requirements Traceability

**Finding**: {Specific description. Cite file paths and line numbers. Quote the design if relevant.}

**Required change**: {What must be fixed before this review can be upgraded.}

---

## Non-Blocking Issues

{If none, write "None."}

### N1: {Issue title}

**Dimension**: {dimension}

**Finding**: {Description.}

**Suggested change**: {Recommendation — optional to act on.}

---

## Dimension Assessments

### Feasibility
{Assessment. PASS / PASS_WITH_NOTES / FAIL}
{Findings or "No issues found."}

### Pattern Consistency
{Assessment.}

### Edge Cases
{Assessment.}

### ICD Completeness
{Assessment.}

### Cross-Cutting Concerns
{Assessment.}

### Requirements Traceability
{Assessment, or "Skipped — no Linear issue provided."}

---

## Verdict Rationale

{Explain why this verdict was chosen. If APPROVE, summarise the strengths. If REQUEST_CHANGES, list the blockers by ID (B1, B2, …).}

---

## Next Steps

{If APPROVE or APPROVE_WITH_COMMENTS}: This design is ready for the `plan` skill. Non-blocking issues above can be addressed during implementation.

{If REQUEST_CHANGES}: Revise the design to address blocking issues B1, B2, … then re-run `design-review`. The `plan` skill is blocked until the verdict is APPROVE or APPROVE_WITH_COMMENTS.
```

## Step 8: Final Confirmation

After writing the file, tell the user:

"Review artifact written to: `{full path}`

**Verdict: {VERDICT}**

{If APPROVE or APPROVE_WITH_COMMENTS}: Ready to proceed with `plan`.
{If REQUEST_CHANGES}: `plan` is blocked. Revise the design and re-run `design-review`."

## Step 9: Emit Subagent Report

End your response with:

```
### Subagent Report
- phase: design-review
- status: success | blocked | partial
- verdict: APPROVE | APPROVE_WITH_COMMENTS | REQUEST_CHANGES
- open_questions:
  - <question 1>
  - <question 2>
```

Use `blocked` if the design doc couldn't be found or was unreadable. Use `partial` if a dimension had to be skipped. Use `success` otherwise (regardless of verdict — the review itself succeeded even if the verdict is REQUEST_CHANGES).

**IMPORTANT**: If the verdict is REQUEST_CHANGES, the orchestrator should NOT proceed to the `plan` phase. Surface the blocking issues to the user.

## Important Constraints

- **Read-only on source files** — never modify implementation files
- **Cite evidence** — every finding must reference specific code, design text, or a requirement
- **Be direct** — a REQUEST_CHANGES verdict is not a failure of the process; it's the gate working correctly
- **Dimension scores are independent** — a PASS on five dimensions with a FAIL on one is still REQUEST_CHANGES
- **Don't gold-plate** — only flag issues that actually affect correctness, safety, or maintainability
