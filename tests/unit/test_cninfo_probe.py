from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pytest

from ashare_quant.research.cninfo_probe import (
    QUERY_URL,
    probe_cninfo_announcement_api,
    render_probe_plan,
)


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, status: int = 200) -> None:
        self.body = body
        self.url = url
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


def _timestamp(value: str) -> int:
    observed = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(observed.timestamp() * 1000)


def _announcement(
    announcement_id: str,
    *,
    announcement_time: str = "2026-11-02T00:00:00",
) -> dict[str, Any]:
    return {
        "secCode": "600000",
        "secName": "浦发银行",
        "orgId": "gssh0600000",
        "announcementId": announcement_id,
        "announcementTitle": f"关于上市事项的公告-{announcement_id}",
        "announcementTime": _timestamp(announcement_time),
        "adjunctUrl": f"finalpage/2026-11-02/{announcement_id}.PDF",
        "adjunctType": "PDF",
    }


class FakeOpener:
    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        url = str(getattr(request, "full_url"))
        if url.startswith("https://www.cninfo.com.cn/new/commonUrl/"):
            return FakeResponse(
                b"<html>search bootstrap</html>",
                url=url,
            )
        assert url == QUERY_URL
        body = getattr(request, "data")
        form = parse_qs(body.decode("utf-8"))
        page_num = int(form["pageNum"][0])
        return FakeResponse(
            json.dumps(self.pages[page_num], ensure_ascii=False).encode("utf-8"),
            url=url,
        )


def _payload(
    announcements: list[dict[str, Any]],
    *,
    totalpages: int,
    total_records: int,
    has_more: bool,
) -> dict[str, Any]:
    return {
        "announcements": announcements,
        "totalpages": totalpages,
        "totalRecordNum": total_records,
        "hasMore": has_more,
    }


def _probe(
    tmp_path: Path,
    opener: FakeOpener,
    *,
    max_pages: int = 2,
) -> dict[str, Any]:
    return probe_cninfo_announcement_api(
        start_date="2026-11-02",
        end_date="2026-11-03",
        column="sse",
        plate="sh",
        search_key="上市公告书",
        output_dir=tmp_path,
        page_size=1,
        max_pages=max_pages,
        min_interval_seconds=0.5,
        opener=opener,
        sleep_fn=lambda _: None,
    )


def test_probe_plan_is_explicitly_no_network() -> None:
    plan = render_probe_plan(
        start_date="2026-11-02",
        end_date="2026-11-03",
        column="sse",
        plate="sh",
        search_key="上市公告书",
        page_size=30,
        max_pages=2,
        min_interval_seconds=1.0,
    )

    assert '"mode": "PLAN_ONLY_NO_NETWORK"' in plan
    assert "https://www.cninfo.com.cn/new/hisAnnouncement/query" in plan
    assert '"production_admitted": false' in plan


def test_probe_captures_all_reported_pages_and_raw_hashes(tmp_path: Path) -> None:
    opener = FakeOpener(
        {
            1: _payload(
                [_announcement("notice-1")],
                totalpages=2,
                total_records=2,
                has_more=True,
            ),
            2: _payload(
                [
                    _announcement(
                        "notice-2",
                        announcement_time="2026-11-03T15:00:00",
                    )
                ],
                totalpages=2,
                total_records=2,
                has_more=False,
            ),
        }
    )

    report = _probe(tmp_path, opener)

    assert report["status"] == "COMPLETE_FOR_QUERY"
    assert report["page_traversal_complete_for_query"] is True
    assert report["source_coverage_proven"] is False
    assert report["safety"]["pit_admitted"] is False
    assert report["query_counters"]["captured_pages"] == 2
    assert report["query_counters"]["captured_records"] == 2
    assert report["query_counters"]["midnight_timestamp_rows"] == 1
    assert report["query_counters"]["intraday_timestamp_rows_unverified"] == 1
    assert (tmp_path / "search_page_bootstrap.html").exists()
    for page in report["pages"]:
        raw_file = tmp_path / page["capture_file"]
        assert raw_file.exists()
        assert len(page["sha256"]) == 64
    stored_report = json.loads((tmp_path / "probe_report.json").read_text("utf-8"))
    assert stored_report["status"] == "COMPLETE_FOR_QUERY"
    assert [getattr(req, "method") for req in opener.requests] == [
        "GET",
        "POST",
        "POST",
    ]


def test_probe_stops_at_page_cap_and_marks_partial(tmp_path: Path) -> None:
    opener = FakeOpener(
        {
            1: _payload(
                [_announcement("notice-1")],
                totalpages=3,
                total_records=3,
                has_more=True,
            ),
            2: _payload(
                [_announcement("notice-2")],
                totalpages=3,
                total_records=75,
                has_more=True,
            ),
        }
    )

    report = _probe(tmp_path, opener, max_pages=2)

    assert report["status"] == "PARTIAL"
    assert report["query_counters"]["captured_pages"] == 2
    assert report["page_traversal_complete_for_query"] is False
    assert report["source_coverage_proven"] is False
    assert "partial" in " ".join(report["anomalies"]).lower()


def test_probe_reports_duplicate_ids_as_complete_with_warnings(tmp_path: Path) -> None:
    opener = FakeOpener(
        {
            1: _payload(
                [_announcement("same-id")],
                totalpages=2,
                total_records=2,
                has_more=True,
            ),
            2: _payload(
                [_announcement("same-id")],
                totalpages=2,
                total_records=2,
                has_more=False,
            ),
        }
    )

    report = _probe(tmp_path, opener)

    assert report["status"] == "COMPLETE_WITH_WARNINGS"
    assert report["page_traversal_complete_for_query"] is True
    assert report["query_counters"]["duplicate_announcement_ids"] == ["same-id"]


def test_probe_reports_no_results_without_claiming_source_coverage(tmp_path: Path) -> None:
    opener = FakeOpener(
        {
            1: _payload(
                [],
                totalpages=0,
                total_records=0,
                has_more=False,
            )
        }
    )

    report = _probe(tmp_path, opener)

    assert report["status"] == "NO_RESULTS"
    assert report["page_traversal_complete_for_query"] is True
    assert report["source_coverage_proven"] is False


def test_probe_rejects_query_windows_over_31_calendar_days() -> None:
    with pytest.raises(ValueError, match="must not exceed 31 calendar days"):
        render_probe_plan(
            start_date="2026-01-01",
            end_date="2026-02-01",
            column="sse",
            plate="sh",
            search_key="上市公告书",
            page_size=30,
            max_pages=2,
            min_interval_seconds=1.0,
        )


def test_probe_marks_missing_identity_fields_as_warning(tmp_path: Path) -> None:
    bad_row = {
        "secCode": "600000",
        "announcementTitle": "公告",
    }
    opener = FakeOpener(
        {
            1: _payload(
                [bad_row],
                totalpages=1,
                total_records=1,
                has_more=False,
            )
        }
    )

    report = _probe(tmp_path, opener, max_pages=1)

    assert report["status"] == "COMPLETE_WITH_WARNINGS"
    missing = report["query_counters"]["rows_missing_identity_fields"]
    assert missing[0]["missing_fields"] == [
        "announcementId",
        "announcementTime",
        "adjunctUrl",
    ]
