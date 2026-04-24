---
name: implement
description: Execute a test-first implementation plan step by step in the current repo, updating checkboxes as tasks complete and maintaining the repo's coverage/quality targets.
when-to-use: Use when the user has an implementation plan (typically produced by the `plan` skill) and wants Claude to execute it, writing code and tests in the current working directory while keeping the plan file in sync.
---

# Implement Skill

Execute an implementation plan step-by-step, following Test-Driven Development (TDD) principles and updating progress in the plan file.

## Your Task

Work through an implementation plan methodically, writing tests first, then implementing features, while maintaining the repo's test coverage target and updating checkboxes as tasks complete.

## Step 1: Read Configuration

Read `./AGENTS.md` (relative to the current working directory) to validate path conventions, output directories, test commands, and coverage targets for this repo.

## Step 2: Get Plan File

**Prompt the user**:

"I'll help you implement a plan step-by-step following TDD.

Please provide:
1. **Path to implementation plan** (e.g., `plans/caching-layer-implementation-plan.md`)
   OR
2. **Use the most recent plan** from the `plan` skill (I'll look in the plan_output_dir)

Which plan should I implement?"

**Wait for user response.**

> When dispatched by the orchestrator, the plan path or issue context may already be provided in the initial prompt.

## Step 3: Read and Validate Plan

1. Read the plan file using the Read tool
2. Parse the structure: total checkboxes, completed vs pending, steps and order, dependencies
3. Display a summary: total steps, completed/pending counts, next pending step, current progress
4. Ask whether to continue from last position, start over, or start from a specific step

**Wait for user confirmation.**

## Step 4: Initialize Task Tracking

Use the task-tracking tooling available in your environment to create a task list matching the current step's checkboxes.

## Step 5: Work Through Current Step (TDD Cycle)

For each checkbox in the current step, follow this process:

### Phase 1: RED (Write Failing Tests)

> **Check first**: If the RED checkboxes for this step are already marked `[x]` (the `test-red` skill ran before you), skip to Phase 2 (GREEN). Before implementing, run the existing failing tests once to confirm they still fail — then proceed to make them pass. Do not re-write tests that were already written by `test-red`.

When checkbox says "Write test: test_name" AND it is NOT already marked `[x]`:

1. **Read existing test files** to understand testing patterns
2. **Create or update test file** following project conventions (see `./AGENTS.md` for `test_output_dir`)
3. **Write the test** that defines expected behavior
4. **Run the test** using the repo's test command (from `./AGENTS.md` or project build file)
5. **Verify it fails** with expected error message
6. **Mark checkbox as complete** in plan file using Edit tool
7. **Update task tracker** to mark this task complete

**Show user**:
"✓ Test written: `test_name` in `{file_path}`
Status: FAILING (as expected - RED phase)
Error: {brief error message}

Next: Implement the code to make this test pass."

### Phase 2: GREEN (Implement Code)

When checkbox says "Implement {component}":

1. **Read related code files** to understand patterns and interfaces
2. **Create or update implementation file**
3. **Write minimal code** to make tests pass
4. **Run tests again**
5. **Verify tests pass** (GREEN)
6. **Check test coverage** - ensure it meets the repo's target
7. **Mark checkbox as complete** in plan file
8. **Update task tracker**

### Phase 3: REFACTOR (Improve Code Quality)

When checkbox says "Refactor" or quality checks:

1. **Run linting** (command from `./AGENTS.md`)
2. **Run formatting**
3. **Review code** for: duplicate logic, missing docstrings/type hints, unclear names, overly complex functions
4. **Make improvements** while keeping tests green
5. **Re-run tests** after each change
6. **Mark checkbox as complete**
7. **Update task tracker**

## Step 6: Handle Breaking Changes or Errors

If you encounter errors or need to make breaking changes, pause and inform the user with: the issue, your proposed solution, the potential impact (files affected, tests that might break, migration needed). Wait for user approval before making breaking changes.

## Step 7: Update Plan File Checkboxes

After completing each checkbox:

1. Use Edit tool to update the plan markdown file
2. Change `- [ ]` to `- [x]` for completed items
3. Verify the edit succeeded

Keep the plan file in sync with actual progress.

## Step 8: Complete Step - Validation

When all checkboxes for a step are complete:

1. Run full test suite (per-repo command)
2. Check coverage against repo target
3. Run linting
4. Review success criteria for this step

Show the user a validation summary and ask whether to continue, pause, or commit.

## Step 9: Git Commits (Optional)

If user requests a commit:

1. Show git status
2. Ask for confirmation with a generated commit message
3. Create commit following project conventions (see CLAUDE.md git protocol in the repo, if present)

## Step 10: Continue or Complete

After each step, either prompt to start the next step or, if all steps complete, summarize the overall result.

## Step 11: Emit Subagent Report

End your response with a structured report block so the orchestrator can parse status:

```
### Subagent Report
- phase: implement
- status: success | blocked | partial
- open_questions:
  - <question 1>
  - <question 2>
```

Use `partial` if some checkboxes remain, `blocked` if user input is required to proceed, `success` if the plan (or the requested subset of steps) is fully complete.

## Important Implementation Principles

### Test-First Always

- **Never implement** before writing a failing test
- **Always run tests** to verify RED state before implementing
- **Always run tests** again to verify GREEN state after implementing
- **Keep tests green** during refactoring

### Incremental Progress

- Complete **one checkbox at a time**
- Update the plan file **immediately** after each checkbox
- Commit after **each major step** (optional but recommended)
- Don't batch multiple tasks without updating progress

### Code Quality

- Follow **existing patterns** in the codebase
- Use **type hints** where the language supports them
- Write **docstrings** for public functions/classes
- Keep **functions small** and focused
- Maintain **consistent style** with existing code

### Test Quality

- Test **both success and failure** cases
- Test **edge cases** and boundary conditions
- Use **descriptive test names** (test_what_when_expected)
- Follow **existing test patterns** in the codebase
- Aim for the repo's coverage target

### Error Handling

If tests fail unexpectedly: show the error, explain what might be wrong, ask if the user wants you to debug or investigate.

If linting fails: show the errors, fix automatically if possible, ask user about style decisions if unclear.

If coverage drops below target: identify uncovered lines, write additional tests, verify coverage improves.

## Progress Tracking

Use the task-tracking tooling throughout to maintain a live view of current task status. Mark tasks in_progress when starting and completed when done. Keep descriptions specific and sync with plan file checkboxes.

## Communication Style

Keep user informed with clear, concise updates. Be specific, show progress, and maintain momentum.

## Handling Complex Steps

If a step has many checkboxes (>10), ask the user whether to work through all consecutively, stop after each sub-section for review, or break into smaller work sessions.

## Final Validation

When all steps are complete, run comprehensive validation using the repo's test and lint commands. Report results with any issues found.

## Error Recovery

If something goes wrong: stop immediately, show the error, explain what you were doing, suggest possible fixes, ask the user how to proceed. Don't try to fix complex errors without user input.

## Summary at Completion

When implementation is fully complete, provide a summary covering: plan path, what was implemented, files created/modified, test coverage, and any follow-up work.
