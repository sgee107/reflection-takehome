"""Constraint schema and LLM-based extraction from policy/memo PDFs."""

from __future__ import annotations

import enum
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field
from pypdf import PdfReader

if TYPE_CHECKING:
    from procureai.utils.db import ScenarioData

logger = logging.getLogger(__name__)


class ConstraintType(str, enum.Enum):
    APPROVED_SUPPLIER_ONLY = "APPROVED_SUPPLIER_ONLY"
    SUPPLIER_BLOCKED = "SUPPLIER_BLOCKED"
    CERT_REQUIRED = "CERT_REQUIRED"
    DOMESTIC_PREFERENCE = "DOMESTIC_PREFERENCE"
    CONCENTRATION_LIMIT = "CONCENTRATION_LIMIT"
    CRITICAL_COMPONENT = "CRITICAL_COMPONENT"
    MOQ_COMPLIANCE = "MOQ_COMPLIANCE"
    HAZMAT_HANDLING = "HAZMAT_HANDLING"
    BUDGET_THRESHOLD = "BUDGET_THRESHOLD"
    SUSTAINABILITY_PREFERENCE = "SUSTAINABILITY_PREFERENCE"
    STRATEGIC_SUPPLIER_PROTECTION = "STRATEGIC_SUPPLIER_PROTECTION"
    AIR_FREIGHT_ALLOWED = "AIR_FREIGHT_ALLOWED"
    PCB_QUALIFIED_ONLY = "PCB_QUALIFIED_ONLY"
    OTHER = "OTHER"


class Constraint(BaseModel):
    """A single procurement constraint extracted from policy documents."""

    type: ConstraintType
    params: dict = Field(default_factory=dict)
    source: str = ""
    effective_date: str | None = None
    expiry_date: str | None = None
    description: str = ""


EXTRACTION_PROMPT = """\
You are a procurement policy analyst. Extract all procurement constraints from the document text below.

Return a JSON array of constraint objects. Each object must have:
- "type": one of {types}
- "params": dict of key parameters (see schema below)
- "source": the filename this came from
- "effective_date": ISO date if mentioned, else null
- "expiry_date": ISO date if mentioned, else null
- "description": brief human-readable summary of the constraint

Constraint type schemas:
- APPROVED_SUPPLIER_ONLY: {{}} (no params — applies globally)
- SUPPLIER_BLOCKED: {{"supplier_id": str, "reason": str}}
- CERT_REQUIRED: {{"component_id": str, "cert_name": str}}
- DOMESTIC_PREFERENCE: {{"max_premium_pct": float, "critical_max_premium_pct": float}}
- CONCENTRATION_LIMIT: {{"component_ids": list[str], "max_pct": float, "secondary_min_pct": float}}
- CRITICAL_COMPONENT: {{"component_ids": list[str]}}
- MOQ_COMPLIANCE: {{}} (global)
- HAZMAT_HANDLING: {{"component_ids": list[str]}}
- BUDGET_THRESHOLD: {{"amount": float, "approver": str}}
- SUSTAINABILITY_PREFERENCE: {{"price_tolerance_pct": float, "lead_time_tolerance_days": int, "min_rating": str}}
- STRATEGIC_SUPPLIER_PROTECTION: {{"min_savings_pct": float}}
- AIR_FREIGHT_ALLOWED: {{"start_date": str, "end_date": str, "lead_time_reduction": int, "min_lead_time": int, "max_cost": float}}
- PCB_QUALIFIED_ONLY: {{"component_id": str, "note": str}}
- OTHER: {{"rule": str}} (use for any constraint that doesn't fit the types above)

IMPORTANT: Only extract constraints that are explicitly stated. Do not infer.
Use OTHER for any real constraint that doesn't match a predefined type — do not discard it.
Return ONLY the JSON array, no other text.

---
DOCUMENT ({filename}):

{text}
"""

# Fallback if LLM extraction fails — hard-coded from known policy docs
DEFAULT_CONSTRAINTS: list[Constraint] = [
    Constraint(
        type=ConstraintType.APPROVED_SUPPLIER_ONLY,
        source="procurement_policy.pdf",
        description="Only approved suppliers may be used",
    ),
    Constraint(
        type=ConstraintType.MOQ_COMPLIANCE,
        source="procurement_policy.pdf",
        description="Orders must meet supplier minimum order quantities",
    ),
    Constraint(
        type=ConstraintType.SUPPLIER_BLOCKED,
        params={
            "supplier_id": "SUP-113",
            "reason": "Removed from approved list — quality issues",
        },
        source="procurement_policy.pdf",
        description="SUP-113 (Jiangsu Electronics) is blocked",
    ),
    Constraint(
        type=ConstraintType.CERT_REQUIRED,
        params={"component_id": "CMP-005", "cert_name": "ISO-9001"},
        source="procurement_policy.pdf",
        description="CMP-005 (PCB Assembly) requires ISO-9001 certified supplier",
    ),
    Constraint(
        type=ConstraintType.HAZMAT_HANDLING,
        params={"component_ids": ["CMP-010", "CMP-011"]},
        source="procurement_policy.pdf",
        description="CMP-010 and CMP-011 are hazardous materials requiring special handling",
    ),
    Constraint(
        type=ConstraintType.BUDGET_THRESHOLD,
        params={"amount": 50000, "approver": "Procurement Manager"},
        source="procurement_policy.pdf",
        description="Orders over $50K require Procurement Manager approval",
    ),
    Constraint(
        type=ConstraintType.DOMESTIC_PREFERENCE,
        params={"max_premium_pct": 35, "critical_max_premium_pct": 50},
        source="procurement_policy.pdf",
        description="Prefer domestic suppliers up to 35% premium (50% for critical)",
    ),
]


def read_pdf(path: Path) -> str:
    """Extract all text from a PDF file."""
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _collect_pdf_texts(directories: list[Path]) -> list[tuple[str, str]]:
    """Read all PDFs from the given directories.

    Returns list of (filename, text) tuples.
    """
    results: list[tuple[str, str]] = []
    for d in directories:
        if not d.is_dir():
            logger.warning("Directory does not exist: %s", d)
            continue
        for pdf_path in sorted(d.glob("*.pdf")):
            text = read_pdf(pdf_path)
            if text.strip():
                results.append((pdf_path.name, text))
    return results


def extract_constraints(
    policy_dir: Path,
    memo_dir: Path,
    llm: BaseChatModel,
    scenario: ScenarioData | None = None,
) -> list[Constraint]:
    """Extract procurement constraints from all policy and memo PDFs.

    Uses a two-layer pipeline:
      Layer 1: PDF → Markdown conversion with hash-based caching (pymupdf4llm)
      Layer 2: LangGraph constraint extraction agent with DB grounding tools

    Falls back to DEFAULT_CONSTRAINTS if extraction fails.

    Args:
        policy_dir: Directory containing policy PDFs.
        memo_dir: Directory containing memo PDFs.
        llm: Chat model for extraction.
        scenario: ScenarioData for DB grounding (enables Layer 2 agent).
            If None, falls back to legacy single-call extraction.
    """
    from procureai.extraction import ensure_markdown_cache

    # Layer 1: Convert PDFs to markdown (cached)
    try:
        md_paths = ensure_markdown_cache(policy_dir, memo_dir)
    except Exception:
        logger.exception("Markdown cache failed — falling back to pypdf")
        md_paths = []

    if not md_paths:
        # Fallback: try legacy PDF text extraction
        pdf_texts = _collect_pdf_texts([policy_dir, memo_dir])
        if not pdf_texts:
            logger.warning("No PDF documents found — using default constraints")
            return list(DEFAULT_CONSTRAINTS)
        # Legacy single-call extraction (no agent, no grounding)
        return _legacy_extract(pdf_texts, llm)

    # Read cached markdown documents
    documents = []
    for md_path in md_paths:
        documents.append(md_path.read_text())

    if scenario is None:
        # No scenario data — use legacy extraction with markdown text
        pdf_texts = [(p.stem, doc) for p, doc in zip(md_paths, documents)]
        return _legacy_extract(pdf_texts, llm)

    # Layer 2: Constraint extraction agent with DB grounding
    try:
        from procureai.agents.constraint_graph import build_constraint_agent

        graph, working_set = build_constraint_agent(llm, scenario, documents)
        graph.invoke(
            {
                "messages": [
                    ("user", "Extract all constraints from the documents provided.")
                ]
            },
            config={"recursion_limit": 40},
        )

        constraints = working_set.to_constraints()
        edit_log = working_set.edit_log

        if not constraints:
            logger.warning("Constraint agent extracted no constraints — using defaults")
            return list(DEFAULT_CONSTRAINTS)

        logger.info(
            "Extracted %d constraints via agent (%d edit log entries)",
            len(constraints),
            len(edit_log),
        )
        for entry in edit_log:
            logger.info(
                "  edit_log: %s %s — %s",
                entry["action"],
                entry.get("constraint_id", ""),
                entry.get("reason", ""),
            )

        return constraints

    except Exception:
        logger.exception("Constraint agent failed — using default constraints")
        return list(DEFAULT_CONSTRAINTS)


def _legacy_extract(
    pdf_texts: list[tuple[str, str]],
    llm: BaseChatModel,
) -> list[Constraint]:
    """Legacy single-call LLM extraction (no agent, no DB grounding)."""
    type_names = ", ".join(t.value for t in ConstraintType)
    all_constraints: list[Constraint] = []

    for filename, text in pdf_texts:
        prompt = EXTRACTION_PROMPT.format(
            types=type_names,
            filename=filename,
            text=text,
        )
        try:
            response = llm.invoke(prompt)
            content = (
                response.content if hasattr(response, "content") else str(response)
            )

            # Strip markdown fences if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            raw = json.loads(content)
            for item in raw:
                try:
                    constraint = Constraint(**item)
                    if not constraint.source:
                        constraint.source = filename
                    all_constraints.append(constraint)
                except Exception:
                    logger.warning(
                        "Skipping invalid constraint from %s: %s", filename, item
                    )
        except Exception:
            logger.exception("Failed to extract constraints from %s", filename)

    if not all_constraints:
        logger.warning("LLM extraction yielded no constraints — using defaults")
        return list(DEFAULT_CONSTRAINTS)

    logger.info(
        "Extracted %d constraints from %d documents",
        len(all_constraints),
        len(pdf_texts),
    )
    return all_constraints
