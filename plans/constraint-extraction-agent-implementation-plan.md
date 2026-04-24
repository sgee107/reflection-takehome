# Constraint Extraction Agent — Implementation Plan

## Overview

Replace the current single-call LLM constraint extraction with a two-layer system: (1) cached PDF→Markdown conversion via pymupdf4llm, and (2) a LangGraph subgraph agent with DB grounding tools and a mutable constraint working set that handles document supersession (memos overriding policies).

**Design reference:** `research/constraint-extraction-agent-design-2026-04-23.md`

## Goals

- Decouple PDF parsing (deterministic, cached) from LLM interpretation (runtime, model-evaluatable)
- Ground constraint extraction in real scenario data via lookup tools
- Handle document hierarchy — memos modify/supersede policy constraints via an explicit working set
- Make constraint extraction independently testable and evaluatable across models

## Prerequisites

- Step 1 (enriched tools) code is in the working tree
- `data/policies/` and `data/memos/` contain the 4 source PDFs
- `uv sync` has been run

## Files

| File | Action | Purpose |
|------|--------|---------|
| `procureai/extraction.py` | Create | Layer 1: `ensure_markdown_cache()`, PDF hashing, pymupdf4llm conversion |
| `procureai/agents/constraint_graph.py` | Create | Layer 2: subgraph, working set context, 7 tools, system prompt |
| `tests/test_extraction.py` | Create | Unit tests for markdown cache |
| `tests/test_constraint_agent.py` | Create | Integration tests for constraint agent + working set |
| `procureai/constraints.py` | Modify | Update `extract_constraints()` to orchestrate Layer 1 + Layer 2 |
| `agent.py` | Modify | Pass `scenario_data` to `extract_constraints()` |
| `pyproject.toml` | Modify | Add `pymupdf4llm` dependency |

---

## Step 1: Add pymupdf4llm Dependency

**Goal:** Add pymupdf4llm to the project and verify it works on our PDFs.

- [x] Run `uv add pymupdf4llm`
- [x] Verify install: `python -c "import pymupdf4llm; print(pymupdf4llm.__version__)"`
- [x] Quick smoke test: convert one PDF and inspect the markdown output
  ```python
  python -c "import pymupdf4llm; print(pymupdf4llm.to_markdown('data/policies/procurement_policy.pdf'))"
  ```
- [x] Evaluate output quality: headings preserved, sections readable, no garbled text

---

## Step 2: Layer 1 — Markdown Cache (`procureai/extraction.py`)

**Goal:** Build `ensure_markdown_cache()` that converts PDFs to markdown with hash-based invalidation.

#### Tests (RED)
- [x] Create `tests/test_extraction.py`
- [x] `test_cache_miss_converts_pdf` — no cache dir → creates it, converts PDFs, writes markdown + manifest
- [x] `test_cache_hit_skips_conversion` — manifest hashes match → returns existing markdown paths, no pymupdf4llm calls
- [x] `test_cache_invalidation_on_change` — modify a PDF's hash in manifest → that file re-converted, others untouched
- [x] `test_new_pdf_added` — add a new PDF file → only the new one converted, manifest updated
- [x] `test_removed_pdf_cleanup` — PDF deleted → corresponding markdown and manifest entry removed
- [x] `test_returns_ordered_paths` — policies sorted before memos, memos sorted by date in filename
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Create `procureai/extraction.py`
- [x] Implement `_hash_file(path: Path) -> str` — SHA-256 of file contents
- [x] Implement `_load_manifest(cache_dir: Path) -> dict` — read `.cache_manifest.json`, return empty dict if missing
- [x] Implement `_save_manifest(cache_dir: Path, manifest: dict)` — write manifest atomically
- [x] Implement `_convert_pdf(pdf_path: Path, cache_dir: Path) -> Path` — call `pymupdf4llm.to_markdown()`, write `.md` file, return path
- [x] Implement `ensure_markdown_cache(policy_dir, memo_dir, cache_dir=None) -> list[Path]`:
  1. Default `cache_dir` to `policy_dir.parent / "extracted"`
  2. Create cache dir if needed
  3. Collect all PDFs from both directories
  4. Load manifest, compare hashes
  5. Convert new/changed PDFs, remove orphaned markdown
  6. Save updated manifest
  7. Return sorted list of markdown paths (policies first, memos by date)
- [x] Run tests — verify GREEN

---

## Step 3: Working Set Context

**Goal:** Create the `ConstraintWorkingSet` class that the write tools operate on. This is a pure data structure, testable without LLM.

#### Tests (RED)
- [x] Add tests to `tests/test_constraint_agent.py` (create file)
- [x] `test_submit_assigns_sequential_ids` — submit 3 constraints → IDs C-001, C-002, C-003
- [x] `test_submit_returns_summary` — submit returns the constraint + current set summary
- [x] `test_update_merges_params` — submit with `{max_pct: 0.7}`, update with `{max_pct: 0.5, secondary_min_pct: 0.2}` → params has all 3 keys, max_pct is 0.5
- [x] `test_update_replaces_description` — update with new description → old description replaced
- [x] `test_update_logs_reason` — update with reason → edit_log has entry with reason, timestamp, old/new values
- [x] `test_update_invalid_id_errors` — update with nonexistent ID → error string
- [x] `test_supersede_removes_constraint` — supersede C-002 → working set has C-001, C-003 (no C-002)
- [x] `test_supersede_logs_reason` — supersede → edit_log has entry with reason and removed constraint details
- [x] `test_get_working_set_returns_all` — submit 3 → get returns all 3 with IDs, types, params
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Create `ConstraintWorkingSet` class in `procureai/agents/constraint_graph.py`:
  - `_constraints: list[dict]` — the live set
  - `_edit_log: list[dict]` — append-only audit trail
  - `_seq: int` — auto-increment for IDs
  - `submit(type, params, source, description, effective_date, expiry_date) -> str` — returns ID + summary
  - `update(constraint_id, params, description, reason) -> str` — returns updated constraint + summary
  - `supersede(constraint_id, reason) -> str` — returns confirmation + summary
  - `get_all() -> str` — formatted working set
  - `to_constraints() -> list[Constraint]` — convert to `Constraint` objects
  - `edit_log` property — returns the log
- [x] Run tests — verify GREEN

---

## Step 4: Read Tools (DB Grounding)

**Goal:** Implement the 3 read-only tools that query the scenario DB.

#### Tests (RED)
- [x] Build a `make_scenario()` fixture with minimal DataFrames (components, suppliers, supplier_catalog)
- [x] `test_lookup_component_by_name` — "neodymium magnets" → returns CMP-003 row
- [x] `test_lookup_component_by_raw_material` — "RM-3003" → returns CMP-003 row
- [x] `test_lookup_component_not_found` — "nonexistent" → returns "no matching components" message
- [x] `test_lookup_supplier_by_name` — "MagnetPro" → returns SUP-108 row
- [x] `test_lookup_supplier_by_id` — "SUP-113" → returns SUP-113 row with approval status
- [x] `test_lookup_supplier_not_found` — "nonexistent" → returns "no matching suppliers" message
- [x] `test_search_catalog_by_component` — component_id="CMP-003" → returns all suppliers for CMP-003
- [x] `test_search_catalog_by_supplier` — supplier_id="SUP-108" → returns all components from SUP-108
- [x] `test_search_catalog_both` — component + supplier → returns specific catalog entry
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `_build_read_tools(scenario: ScenarioData) -> list[Tool]`:
  - `lookup_component(search: str) -> str` — fuzzy match against component name, component_id, raw_material_code. Return formatted row(s).
  - `lookup_supplier(search: str) -> str` — fuzzy match against supplier name, supplier_id. Return formatted row(s) including `on_approved_list`, certifications.
  - `search_catalog(component_id: str = "", supplier_id: str = "") -> str` — filter supplier_catalog, return formatted table of matches.
- [x] All tools use `@tool` decorator from `langchain_core.tools`
- [x] Run tests — verify GREEN

---

## Step 5: Write Tools (Working Set)

**Goal:** Implement the 4 write tools that wrap `ConstraintWorkingSet` as LangChain tools.

#### Tests (RED)
- [x] `test_submit_constraint_tool` — invoke via tool interface → constraint added to working set
- [x] `test_update_constraint_tool` — invoke via tool interface → working set updated, edit_log has entry
- [x] `test_supersede_constraint_tool` — invoke via tool interface → constraint removed
- [x] `test_get_working_set_tool` — invoke → returns formatted working set
- [x] `test_write_tools_share_working_set` — submit via one tool, read via another → same state
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Implement `_build_write_tools(working_set: ConstraintWorkingSet) -> list[Tool]`:
  - `submit_constraint(type, params, source, description, effective_date, expiry_date)` → delegates to `working_set.submit()`
  - `update_constraint(constraint_id, params, description, reason)` → delegates to `working_set.update()`
  - `supersede_constraint(constraint_id, reason)` → delegates to `working_set.supersede()`
  - `get_working_set()` → delegates to `working_set.get_all()`
- [x] All tools use `@tool` decorator
- [x] Params use JSON strings for dict arguments (LLM-compatible)
- [x] Run tests — verify GREEN

---

## Step 6: Constraint Agent Subgraph

**Goal:** Build `build_constraint_agent()` that wires the tools into a LangGraph StateGraph.

#### Tests (RED)
- [x] `test_agent_builds_without_error` — `build_constraint_agent(llm, scenario)` returns a compiled graph
- [x] `test_agent_state_has_working_set` — final state includes `working_set` and `edit_log`
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Define state reuse from `ProcurementState(MessagesState)` (existing state schema sufficient)
- [x] Build system prompt for constraint extraction:
  - Include all markdown document texts, labeled and ordered
  - Include `ConstraintType` enum with param schemas
  - Instruct: process documents in order, use lookup tools to resolve IDs, use write tools to build working set
  - Instruct: "When a memo references or overrides a policy section, use `update_constraint` or `supersede_constraint` — do not create duplicates"
- [x] Implement `build_constraint_agent(llm, scenario, documents) -> (CompiledGraph, ConstraintWorkingSet)`:
  1. Create `ConstraintWorkingSet`
  2. Build read tools from scenario
  3. Build write tools from working set
  4. Bind all tools to LLM
  5. Create `StateGraph` with extract_node (LLM), tool_node
  6. Add edges: extract → tools → extract (loop), extract → END (when done)
  7. Compile and return
- [x] Working set accessible via returned reference for constraint/edit_log extraction
- [x] Run tests — verify GREEN

---

## Step 7: Agent Integration Tests (LLM-Dependent)

**Goal:** Verify the constraint agent extracts correct constraints from real documents. These tests call the LLM.

#### Tests
- [x] `test_policy_only_extraction` — feed procurement policy markdown. Assert core types present (APPROVED_SUPPLIER_ONLY, CONCENTRATION_LIMIT, CERT_REQUIRED for CMP-005, HAZMAT_HANDLING, DOMESTIC_PREFERENCE)
- [x] `test_supersession_policy_plus_memo` — feed policy + concentration memo. Assert concentration params contain 50% override from memo, edit log has entries
- [x] `test_full_document_set` — feed all 4 documents. Assert AIR_FREIGHT_ALLOWED, PCB/CERT types, concentration override, reasonable total count
- [x] `test_edit_log_auditability` — verify log has submit entries with timestamps

**Note:** Tests use pydantic-settings fixtures (test_config, test_llm, real_scenario, policy_markdown). Skipped when AgentConfig can't load.

---

## Step 8: Update `extract_constraints()` Orchestrator

**Goal:** Rewire `constraints.py` to use Layer 1 + Layer 2, keeping the same return type.

#### Tests (RED)
- [x] `test_extract_constraints_returns_constraint_list` — returns `list[Constraint]` with correct types
- [x] `test_extract_constraints_uses_cache` — second call doesn't re-convert PDFs
- [x] `test_extract_constraints_logs_edit_log` — edit log is logged (check logger output)
- [x] Run tests — verify RED

#### Implementation (GREEN)
- [x] Update `extract_constraints()` signature: add `scenario: ScenarioData` parameter
- [x] New body:
  1. Call `ensure_markdown_cache(policy_dir, memo_dir)`
  2. Read all cached markdown files
  3. Build constraint agent: `build_constraint_agent(llm, scenario)`
  4. Invoke agent with markdown documents as input
  5. Extract `working_set` from final state → convert to `Constraint[]`
  6. Log `edit_log` at INFO level for observability
  7. Fall back to `DEFAULT_CONSTRAINTS` if agent fails
- [x] Keep `DEFAULT_CONSTRAINTS`, `Constraint`, `ConstraintType` unchanged
- [x] Keep `EXTRACTION_PROMPT` + `_collect_pdf_texts()` as legacy fallback path
- [x] Keep `read_pdf()` available
- [x] Run tests — verify GREEN

---

## Step 9: Update `agent.py` Call Site

**Goal:** Pass `scenario_data` to `extract_constraints()`.

- [x] Update `agent.py` line 120:
  ```python
  # Before
  constraints = extract_constraints(config.policy_dir, config.memo_dir, llm)
  # After
  constraints = extract_constraints(config.policy_dir, config.memo_dir, llm, scenario_data)
  ```
- [x] Verify: `python agent.py --scenario data/scenarios/scenario_06_simple.sqlite` runs end-to-end
- [x] Verify: constraints are extracted via the new agent (check log output for edit_log)

---

## Step 10: Integration — Run All Scenarios

**Goal:** Verify the new extraction pipeline works across all 6 scenarios without regressions.

- [x] Clean all scenarios: `for s in data/scenarios/scenario_*.sqlite; do python agent.py --scenario "$s" --clean; done`
- [x] Run scenario 06 (simple) — smoke test
- [x] Run scenario 01 (baseline) — verify concentration constraint extracted
- [x] Run scenario 03 (tight timeline) — verify air freight constraint extracted
- [x] Run scenario 05 (competing demand) — verify correct date handling
- [x] Run remaining scenarios (02, 04)
- [x] Run verification suite: `uv run python -m pytest tests/test_verification.py -v`
- [x] Compare results to previous Haiku/Sonnet baselines:
  - Magnet concentration extraction improved (agent extracts CONCENTRATION_LIMIT and splits orders)
  - 3 concentration enforcement failures remain (Haiku behavioral — agent places 64% not ≤50%)
  - No regressions on previously passing tests (76 pass, 7 skip)

---

## Step 11: Code Quality

- [x] `uv run ruff check procureai/extraction.py procureai/agents/constraint_graph.py tests/test_extraction.py tests/test_constraint_agent.py`
- [x] `uv run ruff format procureai/extraction.py procureai/agents/constraint_graph.py tests/test_extraction.py tests/test_constraint_agent.py`
- [x] `uv run python -m pytest tests/ -v` — 55 passed (all non-verification)
- [x] `uv run python -m pytest tests/ --cov=procureai` — extraction.py 100%, constraint_graph.py 97%

---

## Validation

This implementation is complete when:

- [x] All `tests/test_extraction.py` tests pass (Layer 1 cache logic)
- [x] All `tests/test_constraint_agent.py` unit tests pass (working set, tools)
- [x] LLM integration tests pass with Haiku (supersession, grounding)
- [x] `tests/test_verification.py`: 76 pass, 4 fail (pre-existing Haiku concentration enforcement)
- [x] 6 scenarios run end-to-end without crashing
- [x] `ruff check` and `ruff format` clean for changed files
- [x] `data/extracted/` directory contains cached markdown files after first run
- [x] Edit log visible in agent output (shows memo → policy supersession trace)

---

## Risk Mitigation

### pymupdf4llm output quality differs from pypdf
**Impact:** Low — our PDFs are simple text. pymupdf4llm should produce equal or better output.
**Mitigation:** Step 1 includes a manual quality check before proceeding.

### Constraint agent tool-calling loop may not converge
**Impact:** Medium — LLM keeps calling tools without finishing.
**Mitigation:** Set `recursion_limit` on the compiled graph (e.g., 25 iterations). If exceeded, fall back to `DEFAULT_CONSTRAINTS`.

### Working set `update_constraint` params merge may be too coarse
**Impact:** Low — design open question #4. The concentration memo applies to all components in the constraint, not just CMP-003.
**Mitigation:** Start with simple merge. If testing shows issues, add per-component override support in a follow-up.

### LLM integration tests are flaky across models
**Impact:** Medium — different models may produce different working set structures.
**Mitigation:** Assert on presence/values of key constraints (CONCENTRATION_LIMIT, SUPPLIER_BLOCKED), not exact count or ordering. Mark tests with `@pytest.mark.llm` so they can be skipped in fast CI.

---

## References

- Design document: `research/constraint-extraction-agent-design-2026-04-23.md`
- Step 1 plan (predecessor): `plans/step1-enriched-tools-implementation-plan.md`
- Known failures: `plans/fix-plan.md`
- Model eval results: `output/model-eval-claude-haiku-4-5-2026-04-23.md`
- pymupdf4llm docs: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/

---

## Progress Tracking

**Started:** —
**Last Updated:** 2026-04-23
**Status:** Not Started
