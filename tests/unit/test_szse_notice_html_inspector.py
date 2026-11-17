from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ashare_quant.research.szse_notice_html_inspector import (
    inspect_szse_notice_html,
    write_szse_html_inspection_report,
)


def test_inspector_reports_search_form_and_script_route_clues_without_execution(
    tmp_path: Path,
) -> None:
    raw = """<html>
    <head><title>SZSE Notice</title>
      <script src="/static/js/notice.js"></script>
      <script>
        const endpoint = '/api/disc/info/search?keyword=&timetype=0';
        const searchKeys = ['keyword', 'timetype', 'startDate', 'endDate'];
      </script>
    </head>
    <body>
      <form id="searchForm" action="/disclosure/notice/" method="post">
        <input type="text" name="keyword" placeholder="输入关键字">
        <input type="text" id="publishDate" name="publishDate" placeholder="发布日期">
        <input type="hidden" name="csrf" value="do-not-emit-this">
        <button type="submit" name="query">查询</button>
      </form>
    </body></html>""".encode("utf-8")
    source = tmp_path / "archive_page_00000.html"
    source.write_bytes(raw)

    report = inspect_szse_notice_html(source)

    assert report["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["page_title"] == "SZSE Notice"
    assert report["summary"]["form_count"] == 1
    form = report["forms"][0]
    assert form["id"] == "searchForm"
    assert form["method"] == "post"
    fields = form["fields"]
    assert [item["name"] for item in fields if item.get("name")] == [
        "keyword",
        "publishDate",
        "csrf",
        "query",
    ]
    assert all("value" not in item for item in fields if item.get("name") == "csrf")
    script = report["first_party_scripts"][1]
    assert script["candidate_routes"] == ["/api/disc/info/search"]
    assert set(script["query_parameter_names"]) == {"keyword", "timetype"}
    assert "keyword" in script["search_markers"]
    assert report["safety"]["network_requests_made"] is False
    assert report["safety"]["hidden_input_values_emitted"] is False


def test_inspector_resolves_first_party_and_keeps_other_script_reference_separate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "captured.html"
    source.write_text(
        """
        <script src="/assets/site.js"></script>
        <script src="https://cdn.example.test/vendor.js"></script>
        """,
        encoding="utf-8",
    )

    report = inspect_szse_notice_html(source)

    assert report["summary"]["first_party_script_reference_count"] == 1
    assert report["first_party_scripts"][0]["resolved_src"] == (
        "https://www.szse.cn/assets/site.js"
    )
    assert report["summary"]["other_script_reference_count"] == 1
    assert "cdn.example.test" in report["other_script_references"][0]["resolved_src"]


def test_inspector_rejects_non_szse_page_url_and_empty_or_oversized_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "captured.html"
    source.write_text("<html/>", encoding="utf-8")
    with pytest.raises(ValueError, match="page_url must be a canonical HTTPS URL"):
        inspect_szse_notice_html(source, page_url="https://outside.example/page.html")

    source.write_bytes(b"")
    with pytest.raises(ValueError, match="captured HTML file is empty"):
        inspect_szse_notice_html(source)

    with source.open("wb") as handle:
        handle.truncate(20 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="inspection limit"):
        inspect_szse_notice_html(source)


def test_write_inspection_report_preserves_json_shape(tmp_path: Path) -> None:
    source = tmp_path / "captured.html"
    source.write_text("<html><title>Notice</title></html>", encoding="utf-8")
    report = inspect_szse_notice_html(source)
    destination = write_szse_html_inspection_report(
        report, tmp_path / "reports" / "inspection.json"
    )

    assert destination.exists()
    assert '"network_requests_made": false' in destination.read_text(encoding="utf-8")
