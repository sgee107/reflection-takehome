# AGENTS.md

This file provides cross-platform AI agent configuration for the ProcureAI project (ReflectionAI Take-Home Assignment).

## Project Overview

**ProcureAI** is an autonomous AI procurement agent for Apex Manufacturing, built with LangGraph/LangChain on the Anthropic API.

**Architecture Type**: Single-service Python application
- CLI entry point: `agent.py`
- Config (Pydantic Settings): `procureai/config.py`
- Deterministic pipeline: `procureai/pipeline.py`
- Constraint extraction: `procureai/constraints.py`
- Agent logic (LangGraph ReAct): `procureai/agents/` (graph.py, tools.py, prompts.py, state.py)
- Discovery utilities: `procureai/discovery/`
- Data layer + transaction log: `procureai/utils/db.py`

## Output Path Configuration

Agents should write generated files to these standardized locations:

```yaml
design_output_dir: research/
plan_output_dir: plans/
diagram_output_dir: research/
test_output_dir: tests/
```

**File Naming Conventions**:
- Design documents: `{feature-name}-design-YYYY-MM-DD.md`
- Implementation plans: `{feature-name}-implementation-plan.md`
- Diagrams: `current-state-YYYY-MM-DD.md` or `{feature}-architecture.md`
- Tests: `test_{module}.py`

## Development Workflow

### Package Manager

**Tool**: `uv` (modern Python package manager)

### Commands

```bash
# Install dependencies
uv sync

# Run the agent
python agent.py --scenario data/scenarios/scenario_06_simple.sqlite
python agent.py --scenario data/scenarios/scenario_06_simple.sqlite --verbose
python agent.py --scenario data/scenarios/scenario_06_simple.sqlite --model claude-haiku-4-5

# Transaction log management
python agent.py --scenario <path> --list-runs        # Show all recorded runs
python agent.py --scenario <path> --clean             # Undo the last run
python agent.py --scenario <path> --clean-run-id <id> # Undo a specific run

# Run tests
uv run python -m pytest tests/

# Run tests with coverage
uv run python -m pytest tests/ --cov=procureai

# Run linting
uv run ruff check .

# Run formatting
uv run ruff format .
```

### Testing Strategy

**Target**: 80%+ test coverage

**Approach**:
- Test structure: `tests/test_*.py`
- Use pytest fixtures for setup/teardown
- Test both success and failure paths

## Core Architecture Patterns

### Agent Pattern

The project uses a LangGraph ReAct agent with LangChain tools, backed by `ChatAnthropic`. The pipeline is:
1. Load scenario from SQLite (`procureai/utils/db.py`)
2. Extract constraints from policy/memo PDFs via LLM (`procureai/constraints.py`)
3. Compute deterministic gap analysis (`procureai/pipeline.py`)
4. Run ReAct agent loop with 6 tools (`procureai/agents/graph.py`)
5. Write results (POs + alerts) back to SQLite, tagged with a run_id for cleanup

### Transaction Log

Each agent run is tracked in `run_log`, `run_orders`, and `run_alerts` tables within the scenario DB. This allows precise cleanup via `--clean` without affecting original scenario data (e.g. `EXIST-*` POs in scenario 02).

### Data

Source data lives in `data/`. Agent output (dashboards, reports) goes to `output/`.

## File Path Conventions

- Agent implementations: `procureai/agents/`
- Discovery logic: `procureai/discovery/`
- Shared utilities: `procureai/utils/`
- Tests: `tests/`
- Plans: `plans/`
- Research/design docs: `research/`
- Input data: `data/`
- Agent output: `output/`

## Agent Workflow Integration

When using slash commands (`/design`, `/plan`, `/implement`, `/current-state`, `/design-review`, `/icd`, `/test-red`, `/audit`, `/dev-orchestrate`):

1. **Always read this file first** to get output path configuration
2. **Validate paths exist** before writing files
3. **Confirm with user** the output location
4. **Follow naming conventions** specified in Output Path Configuration
5. **Update planning documents** with checkbox progress tracking
