---
name: plan
description: Convert a design document or problem statement into a detailed, test-first implementation plan with checkbox-based progress tracking, written into the current repo's plan_output_dir.
when-to-use: Use when the user has a design or clearly-defined problem and wants a Red→Green→Refactor TDD implementation plan produced as a markdown artifact with trackable checkboxes.
---

# Plan Skill

Convert a design document or problem statement into a detailed, test-first implementation plan with checkboxes for tracking progress.

## Your Task

Create a structured implementation plan following Test-Driven Development (TDD) principles. The plan should be organized in Red→Green→Refactor cycles with checkbox-based tracking.

## Step 1: Read Configuration

Read `./AGENTS.md` (relative to the current working directory) to get the `plan_output_dir` configuration for this repo.

## Step 2: Validate Output Path

Check if the plan output directory exists (commonly `plans/`). If not, create it.

## Step 3: Gather Input (Interactive)

**Prompt the user**:

"I'll help you create a detailed implementation plan. Please provide one of the following:

1. **Path to design document** (e.g., `research/caching-layer-design-2026-04-09.md`)
2. **Problem statement** (if you don't have a design doc yet)
3. **Reference to existing plan** to update or extend

What would you like me to plan?"

**Wait for user response.**

> When dispatched by the orchestrator, the user response may already be supplied as "Issue context" in the initial prompt (e.g. the Linear issue body). Treat that as the problem statement and skip waiting.

## Step 4: Read and Display Design Document

If user provides a design document path:

1. Read the document using the Read tool
2. Extract key sections: Problem statement, Main components, Data model changes, Integration points
3. Display a summary to the user and ask for confirmation

**Wait for user confirmation.**

## Step 5: Ask Prioritization Questions

Based on the design, ask the user:

"To create an effective implementation plan, I need to understand priorities:

1. **Phasing**: Should this be implemented all at once, or in phases?
2. **Dependencies**: Are there any blockers or prerequisite work?
3. **Risk areas**: What parts are you most uncertain about?
4. **Test coverage**: Any specific testing concerns?
5. **Timeline**: Is there a target completion date or milestone?

Please provide any guidance on these points."

**Wait for user answers.**

## Step 6: Generate Draft Plan Structure

Create a structured implementation plan using this template. Where example paths appear, substitute paths appropriate to the current repo (check `./AGENTS.md` for test/source directory conventions).

### Plan Template

```markdown
# [Feature Name] Implementation Plan

## Overview

[Brief description of what's being implemented and why]

## Goals

[What this implementation achieves - bullet points]

## Prerequisites

[Any setup, dependencies, or prior work needed]

---

## Implementation Steps

### Step 1: [Component/Module Name]

**Goal**: [What this step achieves]

**Test-First Approach**:

#### Tests (RED)
- [ ] Create `<test_output_dir>/test_{module}.{ext}`
- [ ] Write test: `test_{specific_behavior}_success`
- [ ] Write test: `test_{specific_behavior}_failure`
- [ ] Write test: `test_{edge_case}`
- [ ] Run tests - verify they fail (RED)

#### Implementation (GREEN)
- [ ] Create `<source_dir>/{module}.{ext}`
- [ ] Implement `{ClassName}.__init__()` (or equivalent)
- [ ] Implement `{ClassName}.{method}()`
- [ ] Run tests - verify they pass (GREEN)

#### Refactor (if needed)
- [ ] Extract common logic to helper functions
- [ ] Add docstrings and type hints
- [ ] Verify test coverage targets

**Success Criteria**:
- [ ] All tests passing
- [ ] Coverage targets met for this module
- [ ] No linting errors
- [ ] Documentation updated

**Estimated Effort**: [e.g., 2-3 hours]

---

### Step 2: [Next Component]

[Repeat same structure]

---

## Integration & Testing

### Integration Tests
- [ ] Create integration test file
- [ ] Test end-to-end workflow
- [ ] Test error handling and edge cases

### Performance Testing (if applicable)
- [ ] Benchmark key operations
- [ ] Verify meets performance targets

---

## Database / Migration Changes (if applicable)

- [ ] Create migration
- [ ] Review generated migration
- [ ] Test upgrade
- [ ] Test downgrade
- [ ] Verify data integrity

---

## Documentation Updates

- [ ] Update `README.md` with new features
- [ ] Update `CLAUDE.md` or `AGENTS.md` if patterns changed
- [ ] Add/update docstrings in code
- [ ] Create usage examples

---

## Validation & Cleanup

### Code Quality
- [ ] Run linting (per-repo command)
- [ ] Run formatting
- [ ] Run full test suite
- [ ] Verify coverage

### Manual Testing
- [ ] Test happy path manually
- [ ] Test error scenarios
- [ ] Verify integration with existing features

### Git & Versioning
- [ ] Review all changes: `git diff`
- [ ] Commit with descriptive message

---

## Success Criteria

This implementation is complete when:

- [ ] All checkbox items above are completed
- [ ] Test coverage targets maintained
- [ ] All tests passing (unit + integration)
- [ ] No linting or type errors
- [ ] Documentation updated and accurate
- [ ] Manually tested end-to-end

---

## Risk Mitigation

### Risk 1: [Potential Issue]
**Impact**: [High/Medium/Low]
**Mitigation**: [How to address]

### Risk 2: [Another Risk]
**Impact**: [High/Medium/Low]
**Mitigation**: [How to address]

---

## Timeline Estimate

- **Step 1**: [Hours/Days]
- **Step 2**: [Hours/Days]
- **Integration**: [Hours/Days]
- **Documentation**: [Hours/Days]
- **Testing & Validation**: [Hours/Days]

**Total**: [Total estimated time]

---

## References

- Design document: [Path to design doc]
- Related plans: [Links to other plan files]
- External docs: [Relevant API docs, libraries, etc.]

---

## Progress Tracking

**Started**: [Date when implementation begins]
**Last Updated**: [Date of last update]
**Status**: [Not Started / In Progress / Completed]
```

## Step 7: Show Draft Plan to User

Present the draft plan with steps overview, total estimated effort, and test strategy. Ask for feedback.

**Wait for user feedback.**

## Step 8: Refine Based on Feedback

Make any adjustments based on user input. If significant changes are needed, show the updated sections and ask for confirmation again.

## Step 9: Generate Output File

Create the final implementation plan.

**File naming**: `{feature-name}-implementation-plan.md`

**Output path**: `{plan_output_dir}/{feature-name}-implementation-plan.md`

## Step 10: Final Confirmation

After writing the file, tell the user where it was saved and offer next steps (revisions, start implementing via `implement` skill, etc.).

## Step 11: Emit Subagent Report

End your response with a structured report block so the orchestrator can parse status:

```
### Subagent Report
- phase: plan
- status: success | blocked | partial
- open_questions:
  - <question 1>
  - <question 2>
```

## Important Guidelines

### Test-First Organization

Every implementation step should follow: write failing tests (RED) → implement minimal code (GREEN) → refactor.

### Checkbox Granularity

Each checkbox should represent a single, completable task (15-60 minutes) with a verifiable outcome. Avoid vague tasks, multi-hour items, and tasks without clear done criteria.

### Success Criteria

Every step and the overall plan should have clear definition of "done", measurable outcomes, and quality gates (linting passes, no errors).

### Reference Existing Plans

If the repo has existing plans under `plan_output_dir`, match their style, level of detail, checkbox formatting, and success criteria format.

## Handling Different Input Types

### If user provides design document:
1. Extract components, data models, workflows
2. Create steps for each major component
3. Include integration steps at the end

### If user provides problem statement:
1. Ask clarifying questions first
2. Do brief design research
3. Create a simpler plan with exploratory tasks
4. Suggest creating a full design doc first if complex

### If user references existing plan:
1. Read the existing plan
2. Ask what they want to change/extend
3. Show current plan structure
4. Add/modify sections as requested

## Error Handling

If the design document is unclear or missing information: point out what's missing, ask specific questions, suggest creating/updating the design first.

If the scope seems too large: suggest breaking into phases, identify an MVP subset, recommend starting with a smaller slice.

## Quality Checks

Before finalizing the plan, verify:
- [ ] Every step has tests defined first
- [ ] Success criteria are clear and measurable
- [ ] Time estimates are realistic
- [ ] Dependencies between steps are clear
- [ ] Risk mitigation is included
- [ ] Documentation updates are included
