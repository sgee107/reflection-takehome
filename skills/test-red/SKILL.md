---
name: test-red
description: Write all failing tests for a feature plan (the RED phase of TDD) in a dedicated context window, separate from the GREEN/REFACTOR implementation phase. Verifies every new test fails before handing off to `implement`.
when-to-use: Use after the `plan` skill has produced an implementation plan and before running `implement`. Ensures tests are written against the specification (the plan), not against the implementation. Can also be triggered on demand when the user says "write tests" or "red phase".
---

# Test-Red Skill

Write all tests defined in the RED sections of an implementation plan, confirm they all fail, and hand off a clean failing test suite to the `implement` skill.

## Your Task

You are writing **tests only**. You must NOT create implementation files or modify source files outside of the test directory. Your job is to faithfully translate the plan's RED checkboxes into real, runnable tests that currently fail because the implementation doesn't exist yet.

Cognitive separation is the entire point of this skill — you operate in a fresh context without implementation details in view, so your tests reflect the specification rather than the code.

## Step 1: Read Configuration

Read `./AGENTS.md` to get:
- `plan_output_dir` — where to find the plan file
- `test_output_dir` — where to write test files
- Test command — how to run the test suite
- Language/framework — what test library is in use

## Step 2: Get Plan File

**Prompt the user**:

"I'll write the failing tests (RED phase) from your implementation plan.

Please provide:
1. **Path to implementation plan** (e.g., `plans/caching-layer-implementation-plan.md`)
2. **Path to ICD** (optional — if an interface contract document exists, I'll use it for exact type signatures)

Which plan should I work from?"

**Wait for user response.**

> When dispatched by the orchestrator, the plan path may already be in the prompt. Use it directly and skip waiting.

## Step 3: Read and Parse the Plan

1. Read the plan file using the Read tool
2. Read the ICD file if provided
3. Extract ONLY the RED checkboxes — items under `#### Tests (RED)` headings
4. Count total RED checkboxes across all steps
5. Identify any RED checkboxes already marked `[x]` — these were done previously and should be skipped

Display to the user:

"**Plan parsed**: `{plan filename}`

RED checkboxes found: {n} across {m} steps
Already completed: {k} (will skip)
To write: {n - k} tests

Steps:
- Step 1: {n1} tests — {description}
- Step 2: {n2} tests — {description}
...

{If ICD provided}: ICD loaded — will use type signatures from `{icd filename}`

Proceeding to write tests. I will NOT touch implementation files."

> No user confirmation needed here — proceed directly.

## Step 4: Read Existing Test Patterns

Before writing any test, read the existing test files to understand:
- Test file naming conventions
- Import patterns
- Test class vs. function style
- Fixture/setup patterns
- Assertion style (assert vs. expect vs. should)
- How stubs/mocks are structured

Read at least 2–3 existing test files using the Read tool. This is mandatory — tests that deviate from existing style create friction during implementation.

## Step 5: Write Tests Step by Step

Work through each step's RED checkboxes in order.

For each RED checkbox:

### 5a. Determine the test file path

- Follow the naming convention from existing tests
- If the plan specifies a file path, use it exactly
- If not, derive it from the step's module/component name using the `test_output_dir`

### 5b. Write the test

1. Open or create the test file
2. Write the test function/method as specified in the checkbox
3. Use the ICD for exact type signatures if available
4. Follow existing patterns for imports, fixtures, setup

**Key rules:**
- If a test requires a type or interface that doesn't exist yet, create a **minimal stub** in the test file or in a `tests/fixtures/` or `tests/stubs/` directory — do NOT create the actual implementation
- If a type is partially defined in existing code, import and use it as-is; add only what the test needs
- Write tests that test behavior, not implementation — test what the code should do, not how it does it

### 5c. Run the test immediately after writing it

Run only this test (or this test file) using the repo's test command. Verify it fails.

**Expected failure modes (all valid RED states):**
- `ImportError` / `ModuleNotFoundError` — implementation module doesn't exist yet
- `AttributeError` — method doesn't exist yet
- `NotImplementedError` — stub exists but isn't implemented
- Assertion failure — code exists but behavior is wrong

**Unexpected pass (flag as spec error):**
If a new test PASSES before any implementation is written, this means one of:
- The behavior already exists in the codebase
- The test doesn't actually exercise new behavior (it's vacuously true)
- There's a bug in the test

Flag any unexpected pass in the report. Do NOT mark its checkbox complete and do NOT proceed without flagging it.

### 5d. Mark the checkbox complete

Update the plan file: change `- [ ] Write test: {name}` to `- [x] Write test: {name}`.

### 5e. Report progress

After each test file is complete (not after each individual test):
"✓ `{test_file_path}` — {n} tests written, all failing as expected"

## Step 6: Handle Stubs for Missing Types

When a test requires a type, function, or constant that doesn't exist yet:

1. Create a stub in the test directory — NOT in the source directory
2. Use the minimal definition needed to make the test syntactically valid:
   - Python: `class MyService: pass` or a Protocol definition
   - TypeScript: `interface MyService {}` or `type MyResult = never`
   - Other: equivalent minimal stub
3. Add a comment: `# Stub — will be replaced by implementation`
4. Document the stub in the report

The stub must be in a test-only location so that `implement` knows it needs to create the real implementation.

## Step 7: Run Full Test Suite

After all tests are written, run the complete test suite once:

```
{test command from AGENTS.md}
```

Summarize the output:
- New tests: {n} failing (expected)
- Pre-existing tests: {n} passing, {m} failing
- Any new tests unexpectedly passing: list them

If pre-existing tests are now failing (i.e., your test files broke something), investigate and fix. Your test files should not break pre-existing tests.

## Step 8: Write Test-Red Report

Write a summary report at `{plan_output_dir}/{feature-name}-test-red-report.md`:

```markdown
# Test-Red Report: [Feature Name]

**Date**: YYYY-MM-DD
**Plan**: `{plan file path}`
**ICD**: `{icd file path, or "not provided"}`

## Summary

- Tests written: {n}
- Test files created: {list}
- Test files modified: {list}
- All new tests failing: {YES / NO — if NO, see Unexpected Passes below}

## Tests Written by Step

### Step 1: {Step Name}
- `{test_file}`: {n} tests
  - `test_{name_1}` — FAILING ({reason})
  - `test_{name_2}` — FAILING ({reason})

### Step 2: {Step Name}
...

## Stubs Created

{If none, write "None."}

- `{stub_file}`: {what it stubs and why}

## Unexpected Passes

{If none, write "None."}

- `test_{name}` in `{file}`: PASSED unexpectedly. Possible reason: {analysis}. **Action required**: verify this test actually exercises new behavior before proceeding with implementation.

## Pre-existing Test Impact

Pre-existing tests: {n passing, m failing}
Tests broken by this change: {list, or "None"}

## Handoff to Implement

{If all tests fail as expected}:
All {n} new tests are failing. The RED phase is complete. Run the `implement` skill to proceed with GREEN.

{If unexpected passes exist}:
{n} tests passed unexpectedly (see above). Resolve ambiguities before running `implement`.
```

## Step 9: Final Confirmation

Tell the user:

"**RED phase complete.**

- {n} tests written across {m} files
- All new tests are FAILING (as expected)
- Report written to: `{report path}`
- Plan checkboxes updated: all RED items marked complete

{If unexpected passes}: ⚠️ {k} tests passed unexpectedly — review the Unexpected Passes section in the report before proceeding.

Ready to hand off to the `implement` skill. The GREEN phase can begin."

## Step 10: Emit Subagent Report

End your response with:

```
### Subagent Report
- phase: test-red
- status: success | blocked | partial
- tests_written: {n}
- unexpected_passes: {n}
- open_questions:
  - <question 1>
  - <question 2>
```

Use `blocked` if the plan file couldn't be found or was unparseable. Use `partial` if some steps' RED checkboxes couldn't be written (e.g., unclear spec). Use `success` otherwise.

## Important Constraints

- **NO implementation files** — do not create or modify files outside of `test_output_dir` and stub locations within the test directory
- **NO source file modifications** — if a test requires a change to existing source (e.g., adding a hook point), flag it as an open question instead of making the change
- **Run every test** — never write a test without running it to verify RED state
- **Stubs in test dir only** — never create implementation stubs in the source tree
- **Update the plan file** — every completed RED checkbox must be marked `[x]` in the plan file
- **Flag unexpected passes** — a passing test before implementation is a spec error, not a win
