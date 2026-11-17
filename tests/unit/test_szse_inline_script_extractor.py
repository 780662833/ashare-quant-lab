from __future__ import annotations

import hashlib

import pytest

from scripts.extract_szse_inline_script import extract_inline_script


def test_extracts_requested_inline_script_and_preserves_script_order(tmp_path):
    source = tmp_path / "page.html"
    source.write_text(
        '<script>first();</script><script src="/external.js"></script>'
        '<script>third();\nsecond_line();</script>',
        encoding="utf-8",
    )
    assert extract_inline_script(source, 1) == b"first();"
    assert extract_inline_script(source, 3) == b"third();\nsecond_line();"
    assert hashlib.sha256(extract_inline_script(source, 3)).hexdigest()


def test_rejects_external_script(tmp_path):
    source = tmp_path / "page.html"
    source.write_text('<script src="/a.js"></script>', encoding="utf-8")
    with pytest.raises(ValueError, match="external"):
        extract_inline_script(source, 1)


def test_rejects_missing_index(tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<script>one();</script>", encoding="utf-8")
    with pytest.raises(ValueError, match="found 1"):
        extract_inline_script(source, 4)


def test_rejects_nonpositive_index(tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<script>one();</script>", encoding="utf-8")
    with pytest.raises(ValueError, match=">= 1"):
        extract_inline_script(source, 0)
