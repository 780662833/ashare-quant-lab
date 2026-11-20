from __future__ import annotations

import json
from pathlib import Path

import pytest

from ashare_quant.research.szse_route_aware_reciprocity import (
    audit_szse_route_aware_reciprocity,
)

SOURCE_URL = "https://www.szse.cn/disclosure/notice/t20210104_700001.html"
OLD_TARGET_URL = "https://www.szse.cn/disclosure/notice/general/t20210105_700002.html"
EXACT_TARGET_URL = "https://www.szse.cn/disclosure/notice/t20210105_700002.html"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, *, reverse_url: str = SOURCE_URL) -> tuple[Path, Path]:
    capture = tmp_path / "capture"
    nodes = [
        {
            "doc_id": "700001",
            "url": SOURCE_URL,
            "html_article_id": "700001",
            "json_doc_id": "700001",
            "channel_id": "211",
            "channel_code": "announcement",
            "html_json_identity": "match",
        },
        {
            "doc_id": "700002",
            "url": OLD_TARGET_URL,
            "html_article_id": "700002",
            "json_doc_id": "700002",
            "channel_id": "212",
            "channel_code": "general_news",
            "html_json_identity": "match",
        },
    ]
    edges = [
        {
            "seed_id": "700001",
            "source_id": "700001",
            "source_channel_id": "211",
            "relation": "pre",
            "target_id": "700002",
            "target_url": EXACT_TARGET_URL,
            "reciprocity_status": "not_reciprocal_or_missing",
        }
    ]
    source_neighbor = {
        "data": {
            "chnlDocId": "211_700001",
            "preDocId": "700002",
            "preDocPuburl": EXACT_TARGET_URL,
        }
    }
    target_neighbor = {
        "data": {
            "chnlDocId": "211_700002",
            "nextDocId": "700001",
            "nextDocPuburl": reverse_url,
        }
    }
    source_json_url = SOURCE_URL[:-5] + ".json"
    source_neighbor_url = (
        "https://www.szse.cn/api/report/webdocument/queryPreAndNextDoc"
        "?channelId=211&docId=700001"
    )
    source_requests = [
        {
            "method": "GET", "requested_url": url, "final_url": url,
            "status_code": 200, "error": None,
        }
        for url in (SOURCE_URL, source_json_url, source_neighbor_url)
    ]
    _write_json(capture / "nodes.json", nodes)
    _write_json(capture / "edges.json", edges)
    _write_json(capture / "manifest.json", {
        "request_count": 3, "requests_are_get_only": True, "requests": source_requests,
    })
    _write_json(capture / "neighbor_responses" / "700001.json", source_neighbor)
    _write_json(capture / "neighbor_responses" / "700002.json", target_neighbor)

    report = {
        "status": "CAPTURED_BOUNDED_ROUTE_SAMPLE",
        "requests_are_get_only": True,
        "request_count": 3,
        "route_count": 1,
        "summary": {"request_errors": 0},
        "safety": {
            "host_allowlist": ["www.szse.cn"],
            "https_only": True,
            "source_error_confirmed_by_tool": False,
            "attachments_downloaded": False,
            "production_admitted": False,
            "pit_admitted": False,
            "training_admitted": False,
            "backtest_admitted": False,
        },
        "route_results": [
            {
                "doc_id_from_url": "700002",
                "requested_html_url": EXACT_TARGET_URL,
                "html_context": {
                    "article_id": "700002",
                    "navigation_channel_id": "211",
                    "navigation_channel_code": "announcement",
                },
                "json_identity": {"doc_id": "700002"},
                "neighbor_context": {
                    "chnl_doc_id": "211_700002",
                    "channel_context_match": True,
                    "pre_doc_id": None,
                    "pre_doc_url": None,
                    "next_doc_id": "700001",
                    "next_doc_url": reverse_url,
                },
                "reference_comparison": {
                    "status": "same_business_payload",
                    "checks": {
                        "doc_id_match": True,
                        "title_match": True,
                        "pub_time_match": True,
                        "content_sha256_match": True,
                    },
                },
                "route_identity_status": "html_json_doc_id_match",
            }
        ],
        "request_manifest": [
            {
                "kind": "html",
                "method": "GET",
                "requested_url": EXACT_TARGET_URL,
                "final_url": EXACT_TARGET_URL,
                "status_code": 200,
                "sha256": "a" * 64,
            },
            {
                "kind": "json",
                "method": "GET",
                "requested_url": EXACT_TARGET_URL[:-5] + ".json",
                "final_url": EXACT_TARGET_URL[:-5] + ".json",
                "status_code": 200,
                "sha256": "b" * 64,
            },
            {
                "kind": "neighbor",
                "method": "GET",
                "requested_url": (
                    "https://www.szse.cn/api/report/webdocument/queryPreAndNextDoc"
                    "?channelId=211&docId=700002"
                ),
                "final_url": (
                    "https://www.szse.cn/api/report/webdocument/queryPreAndNextDoc"
                    "?channelId=211&docId=700002"
                ),
                "status_code": 200,
                "sha256": "c" * 64,
            },
        ],
    }
    report_path = tmp_path / "route_context_report.json"
    _write_json(report_path, report)
    return capture, report_path


def test_exact_route_supplement_resolves_id_and_reverse_url(tmp_path: Path) -> None:
    capture, supplement = _fixture(tmp_path)
    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        route_context_report=supplement,
        output_dir=tmp_path / "audit",
    )
    row = report["edge_results"][0]
    assert row["target_route_provenance"] == "supplemental_exact_route_report"
    assert row["source_evidence_status"] == "source_edge_observation_match"
    assert row["observed_reverse_id"] == "700001"
    assert row["reverse_url_match"] is True
    assert row["reciprocity_status"] == "reciprocal_exact_route"
    assert report["summary"]["bounded_edge_set_reciprocity_acceptance"] is True
    assert report["summary"]["full_graph_acceptance"] is False


def test_document_id_only_fallback_is_prohibited(tmp_path: Path) -> None:
    capture, _ = _fixture(tmp_path)
    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        output_dir=tmp_path / "audit",
    )
    row = report["edge_results"][0]
    assert row["target_route_status"] == "unresolved_exact_target_route_missing"
    assert row["reciprocity_status"] == "unresolved_exact_target_route_context"
    assert report["summary"]["bounded_edge_set_reciprocity_acceptance"] is False


def test_reverse_id_match_with_wrong_reverse_route_does_not_pass(tmp_path: Path) -> None:
    capture, supplement = _fixture(
        tmp_path,
        reverse_url="https://www.szse.cn/disclosure/notice/t20210105_799999.html",
    )
    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        route_context_report=supplement,
        output_dir=tmp_path / "audit",
    )
    row = report["edge_results"][0]
    assert row["reverse_id_match"] is True
    assert row["reverse_url_match"] is False
    assert row["reciprocity_status"] == "route_specific_reverse_url_mismatch"
    assert report["summary"]["bounded_edge_set_reciprocity_acceptance"] is False


def test_neighbor_request_must_use_html_navigation_channel(tmp_path: Path) -> None:
    capture, supplement = _fixture(tmp_path)
    payload = json.loads(supplement.read_text(encoding="utf-8"))
    payload["request_manifest"][2]["requested_url"] = payload["request_manifest"][2][
        "requested_url"
    ].replace("channelId=211", "channelId=212")
    payload["request_manifest"][2]["final_url"] = payload["request_manifest"][2][
        "requested_url"
    ]
    _write_json(supplement, payload)
    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        route_context_report=supplement,
        output_dir=tmp_path / "audit",
    )
    row = report["edge_results"][0]
    assert row["target_route_status"] == "unresolved_exact_target_route_missing"
    assert report["summary"]["bounded_edge_set_reciprocity_acceptance"] is False


def test_original_source_request_receipt_failure_stays_unresolved(tmp_path: Path) -> None:
    capture, supplement = _fixture(tmp_path)
    manifest_path = capture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["requests"][2]["status_code"] = 503
    _write_json(manifest_path, manifest)
    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        route_context_report=supplement,
        output_dir=tmp_path / "audit",
    )
    row = report["edge_results"][0]
    assert row["source_evidence_status"] == "unresolved_source_request_receipts"
    assert row["reciprocity_status"] == "unresolved_source_edge_evidence"
    assert report["summary"]["bounded_edge_set_reciprocity_acceptance"] is False


def test_supplemental_report_rejects_any_bad_receipt(tmp_path: Path) -> None:
    capture, supplement = _fixture(tmp_path)
    payload = json.loads(supplement.read_text(encoding="utf-8"))
    payload["request_manifest"][0]["method"] = "POST"
    _write_json(supplement, payload)
    with pytest.raises(ValueError, match="invalid request receipt"):
        audit_szse_route_aware_reciprocity(
            report_dir=capture,
            route_context_report=supplement,
            output_dir=tmp_path / "audit",
        )


def test_identity_coverage_reports_bounded_unique_ids_and_endpoint_closure(tmp_path: Path) -> None:
    capture, supplement = _fixture(tmp_path)
    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        route_context_report=supplement,
        output_dir=tmp_path / "audit",
    )
    identity = report["identity_coverage"]
    nodes = identity["node_identity"]
    endpoints = identity["edge_endpoint_coverage"]
    assert nodes["node_record_count"] == 2
    assert nodes["unique_doc_id_count"] == 2
    assert nodes["duplicate_doc_id_group_count"] == 0
    assert nodes["bounded_sample_doc_id_uniqueness"] is True
    assert nodes["bounded_sample_route_identity"] is True
    assert endpoints["endpoint_id_coverage_within_capture"] == 1.0
    assert endpoints["source_ids_missing_from_node_capture"] == []
    assert endpoints["target_ids_missing_from_node_capture"] == []
    assert identity["archive_completeness_proven"] is False
    assert identity["global_doc_id_uniqueness_proven"] is False


def test_identity_coverage_exposes_duplicate_ids_and_missing_edge_endpoints(tmp_path: Path) -> None:
    capture, supplement = _fixture(tmp_path)
    nodes_path = capture / "nodes.json"
    nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
    nodes.append({
        "doc_id": "700001",
        "url": "https://www.szse.cn/disclosure/notice/general/t20210106_700001.html",
        "html_article_id": "700001",
        "json_doc_id": "700001",
        "channel_id": "211",
        "html_json_identity": "match",
    })
    nodes.append({
        "doc_id": None,
        "url": "https://www.szse.cn/disclosure/notice/t20210105_799990.html",
        "html_json_identity": "match",
    })
    nodes.append({"doc_id": "not-an-id", "url": "https://evil.example/item.html"})
    nodes.append({
        "doc_id": "799999",
        "url": OLD_TARGET_URL,
        "html_json_identity": "match",
    })
    _write_json(nodes_path, nodes)

    edges_path = capture / "edges.json"
    edges = json.loads(edges_path.read_text(encoding="utf-8"))
    edges.append({
        "seed_id": "700001",
        "source_id": "799991",
        "relation": "next",
        "target_id": "799992",
        "target_url": "https://www.szse.cn/disclosure/notice/t20210106_799992.html",
    })
    _write_json(edges_path, edges)

    report = audit_szse_route_aware_reciprocity(
        report_dir=capture,
        route_context_report=supplement,
        output_dir=tmp_path / "audit",
    )
    identity = report["identity_coverage"]
    nodes_summary = identity["node_identity"]
    edges_summary = identity["edge_endpoint_coverage"]
    assert nodes_summary["duplicate_doc_id_group_count"] == 1
    assert nodes_summary["duplicate_doc_id_multiple_route_group_count"] == 1
    assert nodes_summary["missing_doc_id_record_count"] == 1
    assert nodes_summary["invalid_doc_id_record_count"] == 1
    assert nodes_summary["invalid_or_missing_exact_route_record_count"] == 1
    assert nodes_summary["route_doc_id_mismatch_record_count"] == 1
    assert nodes_summary["duplicate_exact_route_group_count"] == 1
    assert nodes_summary["exact_routes_mapping_to_multiple_doc_ids_count"] == 1
    assert nodes_summary["bounded_sample_doc_id_uniqueness"] is False
    assert nodes_summary["bounded_sample_route_identity"] is False
    assert edges_summary["source_ids_missing_from_node_capture"] == ["799991"]
    assert edges_summary["target_ids_missing_from_node_capture"] == ["799992"]
    assert identity["archive_completeness_proven"] is False
    assert identity["global_doc_id_uniqueness_proven"] is False
