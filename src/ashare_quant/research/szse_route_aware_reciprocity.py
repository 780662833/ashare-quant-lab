"""Offline, exact-route reciprocity audit for a bounded SZSE chain capture.

This module never performs network requests and never edits source captures.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

SZSE_ORIGIN = "https://www.szse.cn"
DETAIL_ROUTE_RE = re.compile(
    r"^/disclosure/notice/(?:general/|company/|temp/)?t\d{8}_(?P<doc_id>\d+)\.html$",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {label}: {exc}") from exc


def _normalize_szse_url(raw: str | None, *, base: str = SZSE_ORIGIN) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    resolved = urljoin(base, raw.strip())
    try:
        parsed = urlsplit(resolved)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower().rstrip(".") != "www.szse.cn"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return urlunsplit(("https", "www.szse.cn", parsed.path, "", ""))


def _url_doc_id(url: str | None) -> str | None:
    normalized = _normalize_szse_url(url)
    if normalized is None:
        return None
    match = DETAIL_ROUTE_RE.fullmatch(urlsplit(normalized).path)
    return match.group("doc_id") if match else None


def _original_request_key(raw: str | None) -> str | None:
    """Normalize official capture URLs while preserving the allowlisted API query."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = urlsplit(raw.strip())
        port = parsed.port
        query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower().rstrip(".") != "www.szse.cn"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    if parsed.path == "/api/report/webdocument/queryPreAndNextDoc":
        if (
            set(query) != {"channelId", "docId"}
            or any(len(values) != 1 or not values[0].isdigit() for values in query.values())
        ):
            return None
        return urlunsplit(("https", "www.szse.cn", parsed.path, parsed.query, ""))
    if parsed.query:
        return None
    return urlunsplit(("https", "www.szse.cn", parsed.path, "", ""))


def _neighbor_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        return None
    return value["data"]


class _CaptureStore:
    def __init__(self, source: Path) -> None:
        self.source = source.resolve()
        self._zip: zipfile.ZipFile | None = None
        self._prefix = ""
        if self.source.is_dir():
            self.mode = "directory"
        elif self.source.is_file() and zipfile.is_zipfile(self.source):
            self.mode = "zip"
            self._zip = zipfile.ZipFile(self.source)
            candidates = [
                name[: -len("nodes.json")]
                for name in self._zip.namelist()
                if name.endswith("nodes.json")
            ]
            if len(candidates) != 1:
                raise ValueError("archive must contain exactly one nodes.json")
            self._prefix = candidates[0]
        else:
            raise ValueError("report-dir must be an extracted directory or a ZIP archive")

    def read_bytes(self, relative: str) -> bytes:
        if self.mode == "directory":
            path = self.source / relative
            if not path.is_file():
                raise FileNotFoundError(path)
            return path.read_bytes()
        assert self._zip is not None
        return self._zip.read(self._prefix + relative)

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


def _load_route_context_report(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}

    report = _json_bytes(path.read_bytes(), str(path))
    if not isinstance(report, dict) or not isinstance(report.get("route_results"), list):
        raise ValueError("route-context report must contain route_results")
    if report.get("status") != "CAPTURED_BOUNDED_ROUTE_SAMPLE":
        raise ValueError("supplemental route report is not a successful bounded capture")
    if report.get("requests_are_get_only") is not True:
        raise ValueError("supplemental route report is not GET-only")

    summary = report.get("summary") or {}
    if summary.get("request_errors") != 0:
        raise ValueError("supplemental route report contains request errors")

    safety = report.get("safety") or {}
    if (
        safety.get("https_only") is not True
        or safety.get("host_allowlist") != ["www.szse.cn"]
        or safety.get("attachments_downloaded") is not False
        or safety.get("source_error_confirmed_by_tool") is not False
        or safety.get("production_admitted") is not False
        or safety.get("pit_admitted") is not False
        or safety.get("training_admitted") is not False
        or safety.get("backtest_admitted") is not False
    ):
        raise ValueError("supplemental route report safety boundary is missing or unsafe")

    manifest = report.get("request_manifest")
    if not isinstance(manifest, list):
        raise ValueError("route-context report has no request_manifest")
    if report.get("request_count") != len(manifest):
        raise ValueError("route-context request_count does not match request_manifest")
    if report.get("route_count") != len(report["route_results"]):
        raise ValueError("route-context route_count does not match route_results")

    manifest_by_route: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in manifest:
        if not isinstance(item, dict):
            continue
        requested_url = item.get("requested_url")
        kind = item.get("kind")

        if kind == "neighbor":
            try:
                parsed = urlsplit(str(requested_url or ""))
                port = parsed.port
                query = parse_qs(parsed.query, strict_parsing=True)
            except ValueError:
                continue
            if (
                parsed.scheme.lower() != "https"
                or (parsed.hostname or "").lower().rstrip(".") != "www.szse.cn"
                or port not in (None, 443)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or parsed.path != "/api/report/webdocument/queryPreAndNextDoc"
                or set(query) != {"channelId", "docId"}
                or any(len(values) != 1 or not values[0].isdigit() for values in query.values())
            ):
                continue
            doc_id = query["docId"][0]
            channel_id = query["channelId"][0]
            route_key = f"__neighbor_for_doc__:{doc_id}:{channel_id}"
        else:
            normalized = _normalize_szse_url(requested_url)
            if normalized is None:
                continue
            if kind == "html" and normalized.lower().endswith(".html"):
                route_key = normalized
            elif kind == "json" and normalized.lower().endswith(".json"):
                route_key = normalized[:-5] + ".html"
            else:
                continue
        manifest_by_route[route_key][kind].append(item)

    def valid_receipt(item: dict[str, Any] | None) -> bool:
        return bool(
            item is not None
            and item.get("method") == "GET"
            and item.get("status_code") == 200
            and item.get("error") is None
            and item.get("final_url") == item.get("requested_url")
            and isinstance(item.get("sha256"), str)
            and SHA256_RE.fullmatch(item["sha256"]) is not None
        )

    # Every request in a successful supplemental report must satisfy the
    # declared safety and receipt contract, not only the three selected below.
    if not all(isinstance(item, dict) and valid_receipt(item) for item in manifest):
        raise ValueError("supplemental route report contains an invalid request receipt")

    routes: dict[str, dict[str, Any]] = {}
    for route in report["route_results"]:
        if not isinstance(route, dict):
            continue
        route_url = _normalize_szse_url(route.get("requested_html_url"))
        doc_id = str(route.get("doc_id_from_url") or "")
        if not route_url or not doc_id or _url_doc_id(route_url) != doc_id:
            continue

        html = route.get("html_context") or {}
        json_identity = route.get("json_identity") or {}
        neighbor = route.get("neighbor_context") or {}
        reference = route.get("reference_comparison") or {}
        nav_channel = str(html.get("navigation_channel_id") or "")
        channel_context_match = (
            neighbor.get("channel_context_match") is True
            and neighbor.get("chnl_doc_id") == f"{nav_channel}_{doc_id}"
        )
        identity_ok = (
            route.get("route_identity_status") == "html_json_doc_id_match"
            and str(html.get("article_id") or "") == doc_id
            and str(json_identity.get("doc_id") or "") == doc_id
        )
        payload_checks = reference.get("checks") or {}
        payload_ok = reference.get("status") == "same_business_payload" and all(
            payload_checks.get(name) is True
            for name in ("doc_id_match", "title_match", "pub_time_match", "content_sha256_match")
        )

        route_receipts = manifest_by_route.get(route_url, {})
        neighbor_receipts = manifest_by_route.get(
            f"__neighbor_for_doc__:{doc_id}:{nav_channel}", {}
        )
        request_items = {
            "html": route_receipts.get("html", []),
            "json": route_receipts.get("json", []),
            "neighbor": neighbor_receipts.get("neighbor", []),
        }
        receipts_ok = all(len(items) == 1 for items in request_items.values())
        if not (
            identity_ok
            and payload_ok
            and channel_context_match
            and nav_channel.isdigit()
            and receipts_ok
        ):
            continue

        if route_url in routes:
            raise ValueError(f"duplicate exact route in supplemental report: {route_url}")
        routes[route_url] = {
            "doc_id": doc_id,
            "route_url": route_url,
            "navigation_channel_id": nav_channel,
            "navigation_channel_code": html.get("navigation_channel_code"),
            "neighbor": {
                "chnlDocId": neighbor.get("chnl_doc_id"),
                "preDocId": neighbor.get("pre_doc_id"),
                "preDocPuburl": neighbor.get("pre_doc_url"),
                "nextDocId": neighbor.get("next_doc_id"),
                "nextDocPuburl": neighbor.get("next_doc_url"),
            },
            "provenance": "supplemental_exact_route_report",
            "payload_match": True,
            "request_receipts_match": True,
        }
    return routes


def _same_neighbor_context(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    return (
        left.get("navigation_channel_id") == right.get("navigation_channel_id")
        and str(left.get("preDocId") or "") == str(right.get("preDocId") or "")
        and str(left.get("nextDocId") or "") == str(right.get("nextDocId") or "")
        and _normalize_szse_url(left.get("preDocPuburl"))
        == _normalize_szse_url(right.get("preDocPuburl"))
        and _normalize_szse_url(left.get("nextDocPuburl"))
        == _normalize_szse_url(right.get("nextDocPuburl"))
    )

def audit_szse_route_aware_reciprocity(
    *, report_dir: Path,
    output_dir: Path,
    route_context_report: Path | None = None,
) -> dict[str, Any]:
    """Audit every captured edge against the exact target route's neighbor data."""
    store = _CaptureStore(report_dir)
    output_dir = Path(output_dir).resolve()
    output_inside_source = (
        output_dir == store.source or store.source in output_dir.parents
    )
    if store.mode == "directory" and output_inside_source:
        store.close()
        raise ValueError("output_dir must be outside the source capture directory")

    try:
        raw_nodes = store.read_bytes("nodes.json")
        raw_edges = store.read_bytes("edges.json")
        raw_manifest = store.read_bytes("manifest.json")
        nodes_value = _json_bytes(raw_nodes, "nodes.json")
        edges_value = _json_bytes(raw_edges, "edges.json")
        manifest_value = _json_bytes(raw_manifest, "manifest.json")
        if not isinstance(nodes_value, list) or not isinstance(edges_value, list):
            raise ValueError("nodes.json and edges.json must be JSON arrays")
        if not isinstance(manifest_value, dict):
            raise ValueError("manifest.json must be an object")
        original_requests = manifest_value.get("requests")
        if not isinstance(original_requests, list):
            raise ValueError("manifest.json must contain a requests array")
        if manifest_value.get("request_count") != len(original_requests):
            raise ValueError("manifest request_count does not match requests array")
        if manifest_value.get("requests_are_get_only") is not True:
            raise ValueError("original capture manifest is not declared GET-only")

        original_receipts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        original_manifest_policy_ok = bool(original_requests)
        for item in original_requests:
            if not isinstance(item, dict):
                original_manifest_policy_ok = False
                continue
            requested_url = _original_request_key(item.get("requested_url"))
            final_url = _original_request_key(item.get("final_url"))
            if requested_url:
                original_receipts[requested_url].append(item)
            # The original manifest has no response SHA field. Validate request
            # identity and HTTP outcome; raw neighbor JSON is hashed separately.
            item_ok = bool(
                item.get("method") == "GET"
                and item.get("status_code") == 200
                and item.get("error") is None
                and requested_url is not None
                and final_url == requested_url
            )
            original_manifest_policy_ok = original_manifest_policy_ok and item_ok

        def original_receipt_ok(url: str) -> bool:
            items = original_receipts.get(url, [])
            return bool(
                original_manifest_policy_ok
                and len(items) == 1
                and items[0].get("method") == "GET"
                and items[0].get("status_code") == 200
                and items[0].get("error") is None
                and _original_request_key(items[0].get("final_url")) == url
            )

        nodes_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        nodes_by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes_value:
            if not isinstance(node, dict):
                continue
            doc_id = str(node.get("doc_id") or "")
            route_url = _normalize_szse_url(node.get("url"))
            if doc_id:
                nodes_by_id[doc_id].append(node)
            if route_url:
                nodes_by_url[route_url].append(node)

        supplemental_routes = _load_route_context_report(route_context_report)
        supplemental_by_url = supplemental_routes
        route_contexts: dict[str, dict[str, Any]] = {}
        neighbor_input_hashes: dict[str, str] = {}
        for route_url, matches in nodes_by_url.items():
            if len(matches) != 1:
                continue
            node = matches[0]
            doc_id = str(node.get("doc_id") or "")
            nav_channel = str(node.get("channel_id") or "")
            neighbor = None
            neighbor_hash = None
            neighbor_path = f"neighbor_responses/{doc_id}.json"
            try:
                neighbor_raw = store.read_bytes(neighbor_path)
                neighbor_hash = _sha256(neighbor_raw)
                neighbor_input_hashes[neighbor_path] = neighbor_hash
                neighbor = _neighbor_payload(_json_bytes(neighbor_raw, neighbor_path))
            except (FileNotFoundError, ValueError, KeyError):
                neighbor = None
            html_json_identity = node.get("html_json_identity") == "match"
            identity_ok = (
                html_json_identity
                and str(node.get("html_article_id") or "") == doc_id
                and str(node.get("json_doc_id") or "") == doc_id
                and _url_doc_id(route_url) == doc_id
            )
            channel_match = bool(
                neighbor is not None
                and nav_channel.isdigit()
                and neighbor.get("chnlDocId") == f"{nav_channel}_{doc_id}"
            )
            route_html_url = route_url
            route_json_url = route_url[:-5] + ".json"
            route_neighbor_url = (
                f"{SZSE_ORIGIN}/api/report/webdocument/queryPreAndNextDoc"
                f"?channelId={nav_channel}&docId={doc_id}"
                if nav_channel.isdigit() else ""
            )
            request_receipts_ok = bool(
                route_html_url and original_receipt_ok(route_html_url)
                and original_receipt_ok(route_json_url)
                and route_neighbor_url and original_receipt_ok(route_neighbor_url)
            )
            route_contexts[route_url] = {
                "doc_id": doc_id,
                "route_url": route_url,
                "navigation_channel_id": nav_channel,
                "navigation_channel_code": node.get("channel_code"),
                "neighbor": neighbor,
                "channel_context_match": channel_match,
                "identity_ok": identity_ok,
                "request_receipts_ok": request_receipts_ok,
                "neighbor_sha256": neighbor_hash,
                "provenance": "original_archive",
            }
        # Supplemental captures are used only for the exact URL they captured;
        # an ID-only fallback is intentionally prohibited.
        for route_url, supplemental in supplemental_by_url.items():
            existing = route_contexts.get(route_url)
            if existing is not None:
                current_context = {
                    "navigation_channel_id": existing.get("navigation_channel_id"),
                    **(existing.get("neighbor") or {}),
                }
                supplemental_context = {
                    "navigation_channel_id": supplemental.get("navigation_channel_id"),
                    **(supplemental.get("neighbor") or {}),
                }
                equivalent = _same_neighbor_context(current_context, supplemental_context)
                if not equivalent:
                    existing["ambiguous_exact_route_evidence"] = True
            else:
                route_contexts[route_url] = {
                    **supplemental,
                    "channel_context_match": True,
                    "identity_ok": True,
                    "request_receipts_ok": True,
                    "neighbor_sha256": None,
                }

        records: list[dict[str, Any]] = []
        source_evidence_counts: Counter[str] = Counter()
        route_resolution_counts: Counter[str] = Counter()
        reciprocity_counts: Counter[str] = Counter()
        for edge_index, edge in enumerate(edges_value, start=1):
            if not isinstance(edge, dict):
                continue
            source_id = str(edge.get("source_id") or "")
            target_id = str(edge.get("target_id") or "")
            relation = str(edge.get("relation") or "")
            target_url = _normalize_szse_url(edge.get("target_url"))
            source_candidates = nodes_by_id.get(source_id, [])
            source_node = source_candidates[0] if len(source_candidates) == 1 else None
            source_url = _normalize_szse_url(source_node.get("url")) if source_node else None
            source_context = route_contexts.get(source_url) if source_url else None
            source_neighbor = source_context.get("neighbor") if source_context else None
            source_channel_id = (
                str(source_context.get("navigation_channel_id") or "")
                if source_context
                else ""
            )

            if source_node is None or source_context is None:
                source_status = "unresolved_source_exact_route"
            elif source_context.get("ambiguous_exact_route_evidence"):
                source_status = "unresolved_source_route_evidence_conflict"
            elif not source_context.get("identity_ok"):
                source_status = "unresolved_source_route_identity"
            elif not source_context.get("channel_context_match"):
                source_status = "unresolved_source_neighbor_channel_context"
            elif not source_context.get("request_receipts_ok"):
                source_status = "unresolved_source_request_receipts"
            elif str(edge.get("source_channel_id") or "") != source_channel_id:
                source_status = "source_channel_id_mismatch"
            elif source_neighbor is None:
                source_status = "unresolved_source_neighbor_response"
            else:
                source_id_field = (
                    "preDocId" if relation == "pre" else "nextDocId" if relation == "next" else None
                )
                source_url_field = (
                    "preDocPuburl"
                    if relation == "pre"
                    else "nextDocPuburl" if relation == "next" else None
                )
                observed_target_id = (
                    str(source_neighbor.get(source_id_field) or "") if source_id_field else ""
                )
                observed_target_url = (
                    _normalize_szse_url(source_neighbor.get(source_url_field))
                    if source_url_field
                    else None
                )
                edge_target_id = str(target_id)
                if not source_id_field:
                    source_status = "unresolved_unsupported_relation"
                elif observed_target_id != edge_target_id or observed_target_url != target_url:
                    source_status = "source_edge_observation_mismatch"
                else:
                    source_status = "source_edge_observation_match"

            source_evidence_counts[source_status] += 1
            target_context = route_contexts.get(target_url) if target_url else None
            if target_url is None:
                target_status = "unresolved_target_url_invalid"
            elif target_context is None:
                target_status = "unresolved_exact_target_route_missing"
            elif target_context.get("ambiguous_exact_route_evidence"):
                target_status = "unresolved_target_route_evidence_conflict"
            elif not target_context.get("identity_ok"):
                target_status = "unresolved_target_route_identity"
            elif not target_context.get("channel_context_match"):
                target_status = "unresolved_target_neighbor_channel_context"
            elif not target_context.get("request_receipts_ok", True):
                target_status = "unresolved_target_request_receipts"
            elif not target_context.get("neighbor"):
                target_status = "unresolved_target_neighbor_response"
            else:
                target_status = "target_exact_route_context_match"

            if target_status == "target_exact_route_context_match":
                route_resolution_counts[str(target_context.get("provenance"))] += 1
            else:
                route_resolution_counts[target_status] += 1

            reverse_id_field = (
                "nextDocId" if relation == "pre" else "preDocId" if relation == "next" else None
            )
            reverse_url_field = (
                "nextDocPuburl"
                if relation == "pre"
                else "preDocPuburl" if relation == "next" else None
            )
            reverse_neighbor = target_context.get("neighbor") if target_context else None
            observed_reverse_id = (
                str(reverse_neighbor.get(reverse_id_field) or "")
                if reverse_neighbor is not None and reverse_id_field else ""
            )
            observed_reverse_url = (
                _normalize_szse_url(reverse_neighbor.get(reverse_url_field))
                if reverse_neighbor is not None and reverse_url_field else None
            )
            if source_status != "source_edge_observation_match":
                reciprocity_status = "unresolved_source_edge_evidence"
            elif target_status != "target_exact_route_context_match":
                reciprocity_status = "unresolved_exact_target_route_context"
            elif observed_reverse_id != source_id:
                reciprocity_status = (
                    "route_specific_reverse_id_mismatch"
                    if observed_reverse_id
                    else "unresolved_reverse_id_missing"
                )
            elif observed_reverse_url != source_url:
                reciprocity_status = "route_specific_reverse_url_mismatch"
            else:
                reciprocity_status = "reciprocal_exact_route"
            reciprocity_counts[reciprocity_status] += 1

            target_route_provenance = (
                target_context.get("provenance") if target_context else None
            )
            target_route_payload_match = (
                target_context.get("payload_match") if target_context else None
            )
            target_route_identity_ok = (
                target_context.get("identity_ok") if target_context else None
            )
            target_navigation_channel_id = (
                target_context.get("navigation_channel_id") if target_context else None
            )
            records.append({
                "edge_index": edge_index,
                "seed_id": str(edge.get("seed_id") or ""),
                "source_id": source_id,
                "source_url": source_url,
                "source_navigation_channel_id": source_channel_id or None,
                "source_neighbor_chnl_doc_id": (source_neighbor or {}).get("chnlDocId"),
                "relation": relation,
                "target_id": target_id,
                "target_url": target_url,
                "source_evidence_status": source_status,
                "target_route_provenance": target_route_provenance,
                "target_route_payload_match": target_route_payload_match,
                "target_route_identity_ok": target_route_identity_ok,
                "target_route_request_receipts_ok": (
                    target_context.get("request_receipts_ok") if target_context else None
                ),
                "target_navigation_channel_id": target_navigation_channel_id,
                "target_neighbor_chnl_doc_id": (reverse_neighbor or {}).get("chnlDocId"),
                "target_route_status": target_status,
                "expected_reverse_id_field": reverse_id_field,
                "observed_reverse_id": observed_reverse_id or None,
                "reverse_id_match": (
                    observed_reverse_id == source_id if observed_reverse_id else False
                ),
                "expected_reverse_url": source_url,
                "observed_reverse_url": observed_reverse_url,
                "reverse_url_match": (
                    observed_reverse_url == source_url if observed_reverse_url else False
                ),
                "reciprocity_status": reciprocity_status,
                "legacy_reciprocity_status": edge.get("reciprocity_status"),
                "legacy_channel_status": edge.get("channel_status"),
                "chronology_status": edge.get("chronology_status"),
                "time_delta_days_target_minus_source": edge.get(
                    "time_delta_days_target_minus_source"
                ),
            })

        unique_edges = len({(r["source_id"], r["relation"], str(r["target_id"])) for r in records})
        passed = sum(r["reciprocity_status"] == "reciprocal_exact_route" for r in records)
        source_matches = sum(
            r["source_evidence_status"] == "source_edge_observation_match" for r in records
        )
        route_context_matches = sum(
            r["target_route_status"] == "target_exact_route_context_match" for r in records
        )
        report = {
            "schema_version": "1.0.0",
            "audit_type": "SZSE_BOUNDED_CAPTURE_EXACT_ROUTE_RECIPROCITY",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "network_requests_made": 0,
            "source_capture": str(report_dir),
            "source_capture_kind": store.mode,
            "supplemental_route_report": (
                str(route_context_report) if route_context_report else None
            ),
            "input_sha256": {
                "capture_archive_sha256": (
                    _sha256(store.source.read_bytes()) if store.mode == "zip" else None
                ),
                "nodes.json": _sha256(raw_nodes),
                "edges.json": _sha256(raw_edges),
                "manifest.json": _sha256(raw_manifest),
                "neighbor_responses": dict(sorted(neighbor_input_hashes.items())),
                **(
                    {"route_context_report.json": _sha256(route_context_report.read_bytes())}
                    if route_context_report
                    else {}
                ),
            },
            "capture_scope": {
                "captured_node_count": len(nodes_value),
                "captured_edge_record_count": len(records),
                "unique_edge_count": unique_edges,
                "manifest_request_count": manifest_value.get("request_count"),
                "original_manifest_policy_ok": original_manifest_policy_ok,
                "supplemental_exact_route_count": len(supplemental_routes),
            },
            "summary": {
                "source_edge_evidence_match": source_matches,
                "source_edge_evidence_not_match_or_unresolved": len(records) - source_matches,
                "target_exact_route_context_match": route_context_matches,
                "target_exact_route_unresolved": len(records) - route_context_matches,
                "route_aware_reciprocal": passed,
                "route_aware_nonreciprocal_or_unresolved": len(records) - passed,
                "reciprocity_status_counts": dict(sorted(reciprocity_counts.items())),
                "source_evidence_status_counts": dict(sorted(source_evidence_counts.items())),
                "target_route_status_counts": dict(sorted(route_resolution_counts.items())),
                "bounded_edge_set_reciprocity_acceptance": (
                    bool(records) and passed == len(records) and unique_edges == len(records)
                ),
                "full_graph_acceptance": False,
                "production_admitted": False,
                "pit_admitted": False,
                "training_admitted": False,
                "backtest_admitted": False,
            },
            "edge_results": records,
            "limitations": [
                "This evaluates every edge in the supplied bounded capture, not the complete SZSE "
                "historical announcement graph.",
                "Exact target routes are resolved only by exact URL match; document-ID-only "
                "fallback is prohibited.",
                "Supplemental routes require HTML/JSON identity, same-business-payload checks, "
                "matching HTML navigation context, and successful GET receipts.",
                "Historical archive completeness, global announcement-ID uniqueness, and complete "
                "cross-period chain continuity are not established.",
                "No network access or source-data mutation is performed by this audit.",
            ],
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "route_aware_reciprocity_report.json"
        csv_path = output_dir / "route_aware_reciprocity_edges.csv"
        fields = list(records[0].keys()) if records else []
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
        report["output_files"] = {"json": str(json_path), "csv": str(csv_path)}
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline exact-route reciprocity audit for a bounded SZSE capture."
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        required=True,
        help="Extracted report directory or ZIP archive",
    )
    parser.add_argument(
        "--route-context-report",
        type=Path,
        help="Optional exact-route capture report JSON",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = audit_szse_route_aware_reciprocity(
        report_dir=args.report_dir,
        route_context_report=args.route_context_report,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "capture_scope": report["capture_scope"],
                "output_files": report["output_files"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
