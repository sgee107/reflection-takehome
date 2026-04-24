"""Layer 1: Cached PDF→Markdown conversion via pymupdf4llm.

Converts policy and memo PDFs to markdown with hash-based invalidation.
Policies are returned before memos; memos are sorted by date in filename.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pymupdf4llm

logger = logging.getLogger(__name__)


def _hash_file(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(cache_dir: Path) -> dict:
    """Read .cache_manifest.json from cache_dir. Return empty dict if missing."""
    manifest_path = cache_dir / ".cache_manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return {}


def _save_manifest(cache_dir: Path, manifest: dict) -> None:
    """Write manifest atomically to cache_dir."""
    manifest_path = cache_dir / ".cache_manifest.json"
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.rename(manifest_path)


def _convert_pdf(pdf_path: Path, cache_dir: Path) -> Path:
    """Convert a single PDF to markdown and write to cache_dir."""
    md_text = pymupdf4llm.to_markdown(str(pdf_path))
    md_path = cache_dir / f"{pdf_path.stem}.md"
    md_path.write_text(md_text)
    return md_path


def _sort_key(pdf_path: Path) -> tuple[int, str]:
    """Sort key: policies (0) before memos (1), then by filename."""
    is_memo = 1 if pdf_path.stem.startswith("memo") else 0
    return (is_memo, pdf_path.stem)


def ensure_markdown_cache(
    policy_dir: Path,
    memo_dir: Path,
    cache_dir: Path | None = None,
) -> list[Path]:
    """Convert PDFs to markdown with hash-based caching.

    Returns sorted list of markdown paths (policies first, memos by date).
    """
    if cache_dir is None:
        cache_dir = policy_dir.parent / "extracted"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Collect all PDFs from both directories
    pdfs: list[Path] = []
    for d in [policy_dir, memo_dir]:
        if d.is_dir():
            pdfs.extend(sorted(d.glob("*.pdf")))

    # Build current hash map: stem -> (full_path, hash)
    current: dict[str, tuple[Path, str]] = {}
    for pdf in pdfs:
        current[pdf.stem] = (pdf, _hash_file(pdf))

    # Load existing manifest: stem -> hash
    manifest = _load_manifest(cache_dir)

    # Determine what needs conversion
    for stem, (pdf_path, file_hash) in current.items():
        if manifest.get(stem) != file_hash:
            logger.info("Converting %s → markdown", pdf_path.name)
            _convert_pdf(pdf_path, cache_dir)
            manifest[stem] = file_hash

    # Remove orphaned markdown (PDFs that no longer exist)
    orphaned = set(manifest.keys()) - set(current.keys())
    for stem in orphaned:
        md_path = cache_dir / f"{stem}.md"
        if md_path.exists():
            md_path.unlink()
            logger.info("Removed orphaned cache: %s", md_path.name)
        del manifest[stem]

    _save_manifest(cache_dir, manifest)

    # Return sorted markdown paths
    md_paths = [cache_dir / f"{stem}.md" for stem in current]
    md_paths.sort(key=_sort_key)
    return md_paths
