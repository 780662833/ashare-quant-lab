from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_cninfo_announcement_capture import analyze_capture


def test_offline_capture_analysis_preserves_counters_and_classifies_rows(
    tmp_path: Path,
) -> None:
    payload = {
        "totalAnnouncement": 5,
        "totalRecordNum": 5,
        "totalpages": 0,
        "totalSecurities": 0,
        "hasMore": False,
        "announcements": [
            {
                "secCode": "002220",
                "secName": "ST天宝",
                "announcementId": "1207845542",
                "announcementTitle": (
                    "关于股票可能将被<em>终止</em><em>上市</em>的第四次风险提示公告"
                ),
                "adjunctUrl": "finalpage/2020-05-22/1207845542.PDF",
                "pageColumn": "SZZX",
            },
            {
                "secCode": "300090",
                "secName": "盛运环保",
                "announcementId": "1207845413",
                "announcementTitle": (
                    "关于公司股票可能因股价低于面值将被终止上市的第四次风险提示性公告"
                ),
                "adjunctUrl": "finalpage/2020-05-22/1207845413.PDF",
                "pageColumn": "SZCY",
            },
            {
                "secCode": "300156",
                "secName": "神雾环保",
                "announcementId": "1207844344",
                "announcementTitle": (
                    "关于公司股票可能因股价低于面值将被终止上市的第七次风险提示性公告"
                ),
                "adjunctUrl": "finalpage/2020-05-22/1207844344.PDF",
                "pageColumn": "SZCY",
            },
        ],
    }
    source = tmp_path / "page_001.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = analyze_capture(source)

    assert report["mode"] == "OFFLINE_CAPTURE_ANALYSIS_NO_NETWORK"
    assert report["reported_total_announcements"] == 5
    assert report["reported_total_records"] == 5
    assert report["reported_total_securities"] == 0
    assert report["captured_unique_security_codes"] == 3
    assert report["security_count_counter_semantics"] == (
        "unqualified_do_not_use_as_universe_count"
    )
    assert report["classification_counts"] == {
        "TERMINATION_RISK_WARNING_NOT_DECISION": 3
    }
    assert report["safety"]["network_access"] is False
    assert report["safety"]["adjunct_documents_downloaded"] is False
