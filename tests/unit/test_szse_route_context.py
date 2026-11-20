from __future__ import annotations

import json
from pathlib import Path

import pytest

from ashare_quant.research.szse_route_context import (
    DEFAULT_ROUTE_URLS,
    capture_szse_route_context,
    extract_html_route_context,
)


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, status: int = 200) -> None:
        self.body = body
        self.url = url
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def geturl(self) -> str:
        return self.url


class FakeOpener:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.requests = []

    def open(self, request, *, timeout: float):
        self.requests.append(request)
        url = request.full_url
        if url not in self.responses:
            raise AssertionError(f"unexpected request: {url}")
        return self.responses[url]


def _html(doc_id: str, channel_id: str, channel_code: str) -> bytes:
    return (
        "<script>var cfg={"
        f"articleId:'{doc_id}',channelId:'{channel_id}',channelCode:'{channel_code}'"
        "};</script>"
    ).encode()


def _json(data: dict) -> bytes:
    return json.dumps({"code": 0, "message": "success", "data": data}).encode()


def _neighbor_url(channel_id: str, doc_id: str) -> str:
    return (
        "https://www.szse.cn/api/report/webdocument/queryPreAndNextDoc"
        f"?channelId={channel_id}&docId={doc_id}"
    )


def test_html_navigation_context_is_separate_from_json_channel() -> None:
    assert extract_html_route_context(_html("584085", "211", "announcement")) == {
        "article_id": "584085",
        "navigation_channel_id": "211",
        "navigation_channel_code": "announcement",
    }


def test_exact_route_context_checks_all_three_reverse_edges(tmp_path: Path) -> None:
    url_a, url_b = DEFAULT_ROUTE_URLS
    json_a, json_b = url_a[:-5] + ".json", url_b[:-5] + ".json"
    body_a, body_b = "<p>A</p>", "<p>B</p>"
    opener = FakeOpener({
        url_a: FakeResponse(_html("584083", "211", "announcement"), url=url_a),
        json_a: FakeResponse(_json({
            "docId": 584083, "title": "A", "pubTime": 1, "channelId": 212,
            "chnlCode": "general_news", "content": body_a,
        }), url=json_a),
        _neighbor_url("211", "584083"): FakeResponse(_json({
            "chnlDocId": "211_584083", "nextDocId": "584082", "preDocId": "584084",
        }), url=_neighbor_url("211", "584083")),
        url_b: FakeResponse(_html("584085", "214", "ipocompany_news"), url=url_b),
        json_b: FakeResponse(_json({
            "docId": 584085, "title": "B", "pubTime": 2, "channelId": 214,
            "chnlCode": "ipocompany_news", "content": body_b,
        }), url=json_b),
        _neighbor_url("214", "584085"): FakeResponse(_json({
            "chnlDocId": "214_584085", "nextDocId": "584068",
        }), url=_neighbor_url("214", "584085")),
    })
    report = capture_szse_route_context(
        output_dir=tmp_path, reference_dir=None, opener=opener, sleep_fn=lambda _: None,
    )
    assert report["request_count"] == 6
    assert report["summary"]["reverse_checks_match"] == 3
    assert report["summary"]["reverse_checks_unresolved"] == 0
    route_a, route_b = report["route_results"]
    assert route_a["html_context"]["navigation_channel_id"] == "211"
    assert route_a["json_identity"]["data_channel_id"] == "212"
    assert [item["status"] for item in route_a["reverse_checks"]] == ["match", "match"]
    assert [item["status"] for item in route_b["reverse_checks"]] == ["match"]
    assert [r.full_url for r in opener.requests if "queryPreAndNextDoc" in r.full_url] == [
        _neighbor_url("211", "584083"), _neighbor_url("214", "584085"),
    ]


def test_channel_mismatch_stays_unresolved(tmp_path: Path) -> None:
    route = DEFAULT_ROUTE_URLS[0]
    json_url = route[:-5] + ".json"
    neighbor_url = _neighbor_url("211", "584083")
    opener = FakeOpener({
        route: FakeResponse(_html("584083", "211", "announcement"), url=route),
        json_url: FakeResponse(_json({"docId": 584083, "content": ""}), url=json_url),
        neighbor_url: FakeResponse(_json({
            "chnlDocId": "212_584083", "nextDocId": "999999", "preDocId": "999999",
        }), url=neighbor_url),
    })
    report = capture_szse_route_context(
        output_dir=tmp_path, route_urls=(route,), opener=opener, sleep_fn=lambda _: None,
    )
    assert [c["status"] for c in report["route_results"][0]["reverse_checks"]] == [
        "unresolved_neighbor_channel_mismatch", "unresolved_neighbor_channel_mismatch",
    ]
    assert report["summary"]["reverse_checks_mismatch"] == 0


def test_neighbor_redirect_cannot_change_query_context(tmp_path: Path) -> None:
    route = DEFAULT_ROUTE_URLS[0]
    json_url = route[:-5] + ".json"
    neighbor_url = _neighbor_url("211", "584083")
    opener = FakeOpener({
        route: FakeResponse(_html("584083", "211", "announcement"), url=route),
        json_url: FakeResponse(_json({"docId": 584083, "content": ""}), url=json_url),
        neighbor_url: FakeResponse(
            _json({"chnlDocId": "211_584083"}),
            url=neighbor_url + "&docId=999999",
        ),
    })
    report = capture_szse_route_context(
        output_dir=tmp_path, route_urls=(route,), opener=opener, sleep_fn=lambda _: None,
    )
    assert "query context" in report["request_manifest"][2]["error"]
    assert report["summary"]["reverse_checks_unresolved"] == 2


def test_non_200_is_preserved_but_not_parsed(tmp_path: Path) -> None:
    route = DEFAULT_ROUTE_URLS[0]
    json_url = route[:-5] + ".json"
    opener = FakeOpener({
        route: FakeResponse(b"not found", url=route, status=404),
        json_url: FakeResponse(b"not found", url=json_url, status=404),
    })
    report = capture_szse_route_context(
        output_dir=tmp_path, route_urls=(route,), opener=opener, sleep_fn=lambda _: None,
    )
    assert report["status"] == "CAPTURED_WITH_REQUEST_ERRORS"
    assert report["summary"]["request_errors"] == 2
    assert report["route_results"][0]["route_identity_status"] == "capture_incomplete"


@pytest.mark.parametrize("url", [
    "https://example.com/disclosure/notice/t20210104_584083.html",
    "https://www.szse.cn/disclosure/notice/file.pdf",
])
def test_rejects_external_or_non_article_route(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError):
        capture_szse_route_context(output_dir=tmp_path, route_urls=(url,), opener=FakeOpener({}))


def test_duplicate_routes_and_rate_limit_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        capture_szse_route_context(
            output_dir=tmp_path, route_urls=(DEFAULT_ROUTE_URLS[0], DEFAULT_ROUTE_URLS[0]),
            opener=FakeOpener({}),
        )
    with pytest.raises(ValueError, match="0.5"):
        capture_szse_route_context(
            output_dir=tmp_path, route_urls=(DEFAULT_ROUTE_URLS[0],),
            min_interval_seconds=0.1, opener=FakeOpener({}),
        )
