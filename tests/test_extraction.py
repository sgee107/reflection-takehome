"""Unit tests for Layer 1: Markdown cache (procureai/extraction.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    """Create temp policy and memo directories with fake PDFs."""
    policy_dir = tmp_path / "policies"
    memo_dir = tmp_path / "memos"
    cache_dir = tmp_path / "extracted"
    policy_dir.mkdir()
    memo_dir.mkdir()

    # Create fake PDF files (content doesn't matter — we mock pymupdf4llm)
    (policy_dir / "procurement_policy.pdf").write_bytes(b"fake-pdf-policy")
    (memo_dir / "memo_2025-04-15_supplier_concentration.pdf").write_bytes(
        b"fake-pdf-memo-1"
    )
    (memo_dir / "memo_2025-07-01_expedited_shipping.pdf").write_bytes(
        b"fake-pdf-memo-2"
    )

    return policy_dir, memo_dir, cache_dir


def _mock_to_markdown(pdf_path, **kwargs):
    """Mock pymupdf4llm.to_markdown — returns deterministic markdown from filename."""
    name = Path(pdf_path).stem
    return f"# {name}\n\nConverted content for {name}."


class TestCacheMiss:
    """No cache dir → creates it, converts PDFs, writes markdown + manifest."""

    def test_cache_miss_converts_pdf(self, tmp_dirs):
        from procureai.extraction import ensure_markdown_cache

        policy_dir, memo_dir, cache_dir = tmp_dirs

        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            paths = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        # Cache dir should now exist
        assert cache_dir.exists()
        # Should have 3 markdown files
        assert len(paths) == 3
        for p in paths:
            assert p.suffix == ".md"
            assert p.exists()
        # Manifest should exist
        manifest_path = cache_dir / ".cache_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert len(manifest) == 3


class TestCacheHit:
    """Manifest hashes match → returns existing markdown paths, no conversion."""

    def test_cache_hit_skips_conversion(self, tmp_dirs):
        from procureai.extraction import ensure_markdown_cache

        policy_dir, memo_dir, cache_dir = tmp_dirs

        # First call — populates cache
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            paths1 = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        # Second call — should skip conversion
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = MagicMock(side_effect=_mock_to_markdown)
            paths2 = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)
            # to_markdown should NOT have been called
            mock_mupdf.to_markdown.assert_not_called()

        assert paths1 == paths2


class TestCacheInvalidation:
    """Modified PDF hash → that file re-converted, others untouched."""

    def test_cache_invalidation_on_change(self, tmp_dirs):
        from procureai.extraction import ensure_markdown_cache

        policy_dir, memo_dir, cache_dir = tmp_dirs

        # First call — populates cache
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        # Modify one PDF
        (policy_dir / "procurement_policy.pdf").write_bytes(b"modified-pdf-content")

        # Second call — should re-convert the modified file only
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = MagicMock(side_effect=_mock_to_markdown)
            paths = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)
            # Only the modified PDF should have been converted
            assert mock_mupdf.to_markdown.call_count == 1
            called_path = str(mock_mupdf.to_markdown.call_args[0][0])
            assert "procurement_policy" in called_path

        assert len(paths) == 3


class TestNewPdfAdded:
    """Add a new PDF → only the new one converted, manifest updated."""

    def test_new_pdf_added(self, tmp_dirs):
        from procureai.extraction import ensure_markdown_cache

        policy_dir, memo_dir, cache_dir = tmp_dirs

        # First call
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        # Add a new memo
        (memo_dir / "memo_2025-08-20_pcb_quality.pdf").write_bytes(b"new-pdf")

        # Second call
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = MagicMock(side_effect=_mock_to_markdown)
            paths2 = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)
            assert mock_mupdf.to_markdown.call_count == 1

        assert len(paths2) == 4

        # Manifest updated
        manifest = json.loads((cache_dir / ".cache_manifest.json").read_text())
        assert len(manifest) == 4


class TestRemovedPdfCleanup:
    """PDF deleted → corresponding markdown and manifest entry removed."""

    def test_removed_pdf_cleanup(self, tmp_dirs):
        from procureai.extraction import ensure_markdown_cache

        policy_dir, memo_dir, cache_dir = tmp_dirs

        # First call
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            paths1 = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        assert len(paths1) == 3

        # Remove a memo PDF
        (memo_dir / "memo_2025-04-15_supplier_concentration.pdf").unlink()

        # Second call
        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            paths2 = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        assert len(paths2) == 2
        # Orphaned markdown should be gone
        orphan = cache_dir / "memo_2025-04-15_supplier_concentration.md"
        assert not orphan.exists()
        # Manifest updated
        manifest = json.loads((cache_dir / ".cache_manifest.json").read_text())
        assert len(manifest) == 2


class TestReturnOrder:
    """Policies sorted before memos, memos sorted by date in filename."""

    def test_returns_ordered_paths(self, tmp_dirs):
        from procureai.extraction import ensure_markdown_cache

        policy_dir, memo_dir, cache_dir = tmp_dirs

        with patch("procureai.extraction.pymupdf4llm") as mock_mupdf:
            mock_mupdf.to_markdown = _mock_to_markdown
            paths = ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        names = [p.stem for p in paths]
        # Policy first, then memos sorted by date
        assert names[0] == "procurement_policy"
        assert names[1] == "memo_2025-04-15_supplier_concentration"
        assert names[2] == "memo_2025-07-01_expedited_shipping"
