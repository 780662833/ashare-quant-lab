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
    assert report["safety"]["javascript_executed"] is False


def test_inspector_extracts_controls_outside_forms_and_inline_query_binding_clues(
    tmp_path: Path,
) -> None:
    raw = """<html><body>
      <input id="article-text" class="search-input" placeholder="输入关键字">
      <input id="aticle-datepicker" class="date-input" placeholder="发布日期">
      <button id="query-btn" class="btn primary" type="button"
        onclick="runNoticeQuery()">查询</button>
      <script>
        const keywordInput = document.getElementById('article-text');
        const dateInput = $('#aticle-datepicker');
        $('#query-btn').on('click', function () {
          $.ajax({
            url: '/api/disc/info/search',
            type: 'POST',
            data: { keyword: keywordInput.value, startDate: dateInput.val(),
                    endDate: dateInput.val(), pageNum: 1 }
          });
        });
      </script>
    </body></html>""".encode("utf-8")
    source = tmp_path / "listing.html"
    source.write_bytes(raw)

    report = inspect_szse_notice_html(source)
    controls = report["controls_outside_forms"]

    assert report["summary"]["form_count"] == 0
    assert controls[0]["id"] == "article-text"
    assert controls[0]["class"] == "search-input"
    assert controls[1]["id"] == "aticle-datepicker"
    button = controls[2]
    assert button["class"] == "btn primary"
    assert button["onclick_clue"] == "runNoticeQuery()"

    script = report["first_party_scripts"][0]
    assert {"article-text", "aticle-datepicker", "ajax", "$.post", "$.get"} & set(
        script["query_binding_markers"]
    )
    assert script["candidate_routes"] == ["/api/disc/info/search"]
    request = script["request_call_clues"][0]
    assert set(request["payload_key_candidates"]) >= {
        "keyword",
        "startDate",
        "endDate",
        "pageNum",
    }
    assert any(
        clue["clue_type"] == "article_text_id"
        for clue in script["query_binding_clues"]
    )
    assert report["query_discovery"]["request_call_clue_count"] == 1
    assert report["query_discovery"]["candidate_routes"] == [
        "/api/disc/info/search"
    ]


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
    assert report["summary"]["external_script_reference_count"] == 2
    assert report["summary"]["inline_script_count"] == 0
    assert report["first_party_scripts"][0]["resolved_src"] == (
        "https://www.szse.cn/assets/site.js"
    )
    assert report["summary"]["other_script_reference_count"] == 1
    assert "cdn.example.test" in report["other_script_references"][0]["resolved_src"]


def test_inspector_redacts_credential_like_inline_values_from_clues(
    tmp_path: Path,
) -> None:
    secret = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKL"
    source = tmp_path / "captured.html"
    source.write_text(
        f"""
        <button onclick="run()">查询</button>
        <script>
          $.ajax({{ url: '/api/notice/search', headers: {{ Authorization: 'Bearer {secret}' }},
            data: {{ keyword: '龙力' }} }});
        </script>
        """,
        encoding="utf-8",
    )

    report_text = str(inspect_szse_notice_html(source))

    assert secret not in report_text
    assert "Bearer <redacted>" in report_text or "Authorization=<redacted>" in report_text



def test_inspector_redacts_compound_credential_names_and_query_values(
    tmp_path: Path,
) -> None:
    access_secret = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    csrf_secret = "csrf-0123456789-ABCDEFGHIJKLMNOP"
    refresh_secret = "refresh-0123456789-ABCDEFGHIJKLMNOP"
    source = tmp_path / "captured.html"
    source.write_text(
        f"""
        <button data-url="/search?refresh_token={refresh_secret}">查询</button>
        <script>
          $.ajax({{
            url: "/api/notice/search",
            headers: {{
              "access_token": "{access_secret}",
              csrfToken: "{csrf_secret}"
            }},
            data: {{ keyword: "龙力" }}
          }});
        </script>
        """,
        encoding="utf-8",
    )

    report_text = str(inspect_szse_notice_html(source))

    assert access_secret not in report_text
    assert csrf_secret not in report_text
    assert refresh_secret not in report_text
    assert report_text.count("<redacted>") >= 3


def test_inspector_detects_jquery_class_selector_and_click_handler(
    tmp_path: Path,
) -> None:
    source = tmp_path / "listing.html"
    source.write_text(
        """
        <button class="confirm-query btn-query-primary" type="button">查询</button>
        <script>
          $('.confirm-query').click(function () {
            const keyword = $('#article-text').val();
            $.ajax({ url: '/api/disc/info/search', data: { keyword: keyword } });
          });
        </script>
        """,
        encoding="utf-8",
    )

    report = inspect_szse_notice_html(source)
    script = report["first_party_scripts"][0]
    clue_types = {clue["clue_type"] for clue in script["query_binding_clues"]}

    assert "confirm-query" in script["query_binding_markers"]
    assert "event_binding" in clue_types
    assert "jquery_selector" in clue_types
    assert any(
        ".confirm-query" in clue["context"]
        for clue in script["query_binding_clues"]
    )
    assert script["candidate_routes"] == ["/api/disc/info/search"]
    assert report["query_discovery"]["request_call_clue_count"] == 1

def test_inspector_emits_bounded_contexts_for_known_notice_controls_and_init(
    tmp_path: Path,
) -> None:
    source = tmp_path / "listing.html"
    source.write_text(
        """
        <script>
          function setupNoticeSearch() {
            $('.confirm-query').click(function () {
              const keyword = $('#article-text').val();
              const date = $('#aticle-datepicker').val();
              $.ajax({ url: '/candidate-only/not-an-api-contract', data: { keyword } });
            });
          }
          createPageHTML(16, 0, "index", "html");
          artInitHtml(cmsParam);
        </script>
        """,
        encoding="utf-8",
    )

    report = inspect_szse_notice_html(source)
    script = report["first_party_scripts"][0]
    contexts = script["targeted_script_contexts"]
    clue_types = {item["clue_type"] for item in contexts}

    assert "query_button_token" in clue_types
    assert "keyword_control" in clue_types
    assert "date_control" in clue_types
    assert "query_button_event" in clue_types
    assert "template_or_init" in clue_types
    assert any(
        ".confirm-query" in item["context"] and ".click" in item["context"]
        for item in contexts
    )
    assert all(len(item["context"]) <= 700 for item in contexts)
    assert report["summary"]["targeted_script_context_count"] == len(contexts)
    assert report["query_discovery"]["candidate_routes"] == []


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
