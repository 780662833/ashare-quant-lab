"""Bounded, read-only probe for the unofficially documented CNINFO search endpoint.

This module is a source-research tool only. It does not create production
security metadata, download original announcement files, or prove archive
completeness or PIT eligibility.
"""
from __future__ import annotations

import hashlib
import html
import http.cookiejar
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SEARCH_PAGE_URL = (
    "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch"
    "?url=disclosure/list/search"
)
QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STOCK_LOOKUP_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
SHANGHAI = ZoneInfo("Asia/Shanghai")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_REQUIRED_ANNOUNCEMENT_FIELDS = (
    "secCode",
    "secName",
    "orgId",
    "announcementId",
    "announcementTitle",
    "announcementTime",
    "adjunctUrl",
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _validate_request(
    *,
    start_date: str,
    end_date: str,
    column: str,
    search_key: str,
    stock: str,
    page_size: int,
    max_pages: int,
    min_interval_seconds: float,
    timeout_seconds: float = 20.0,
) -> tuple[date, date]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("start_date and end_date must use YYYY-MM-DD") from exc
    if start.isoformat() != start_date or end.isoformat() != end_date:
        raise ValueError("start_date and end_date must use canonical YYYY-MM-DD")
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if (end - start).days > 30:
        raise ValueError("a probe date window must not exceed 31 calendar days")
    if column not in {"sse", "szse"}:
        raise ValueError("column must be sse or szse")
    if not isinstance(search_key, str):
        raise ValueError("search_key must be a string")
    if not isinstance(stock, str):
        raise ValueError("stock must be a string")
    if not search_key.strip() and not stock.strip():
        raise ValueError("at least one of search_key or stock must be provided")
    if stock and not re.fullmatch(r"\d{6}", stock):
        raise ValueError("stock must be a six-digit A-share code")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be between 1 and 10")
    if (
        not math.isfinite(min_interval_seconds)
        or min_interval_seconds < 0.5
    ):
        raise ValueError("min_interval_seconds must be finite and at least 0.5")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    return start, end


def _extract_stock_identity(payload: Any, stock_code: str) -> dict[str, str]:
    """Select one exact code/orgId pair from CNINFO's reverse-engineered lookup."""
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("stockList")
        if records is None:
            records = payload.get("data")
    else:
        records = None

    if not isinstance(records, list):
        raise ValueError("CNINFO stock lookup did not return a recognized stock list")

    matches: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        code_value = (
            record.get("stockCode")
            or record.get("secCode")
            or record.get("code")
        )
        org_id = record.get("orgId") or record.get("orgid")
        if str(code_value or "").strip() != stock_code or not str(org_id or "").strip():
            continue
        matches.append(
            {
                "stock_code": stock_code,
                "org_id": str(org_id).strip(),
                "sec_name": str(
                    record.get("zwjc")
                    or record.get("secName")
                    or record.get("fullName")
                    or ""
                ).strip(),
            }
        )

    unique_org_ids = {item["org_id"] for item in matches}
    if len(unique_org_ids) != 1:
        raise ValueError(
            f"CNINFO stock lookup must return exactly one orgId for {stock_code}; "
            f"found {len(unique_org_ids)} distinct matches"
        )
    return matches[0]


def _publication_time_observation(value: Any) -> dict[str, Any]:
    """Decode a timestamp-shaped field without claiming it is a release instant."""
    raw = str(value) if value is not None else None
    result: dict[str, Any] = {
        "raw": raw,
        "local_datetime": None,
        "local_date": None,
        "timestamp_semantics": "unknown",
    }
    if value is None:
        return result
    try:
        numeric = int(value)
        if numeric <= 0:
            return result
        # The reverse-engineered client documents milliseconds. Do not silently
        # reinterpret smaller numbers as seconds.
        observed = datetime.fromtimestamp(numeric / 1000, tz=SHANGHAI)
    except (TypeError, ValueError, OverflowError, OSError):
        return result
    result["local_datetime"] = observed.isoformat()
    result["local_date"] = observed.date().isoformat()
    result["timestamp_semantics"] = (
        "midnight_date_stamp_suspected"
        if observed.time().hour == 0
        and observed.time().minute == 0
        and observed.time().second == 0
        else "intraday_value_unverified"
    )
    return result


def _make_opener() -> Any:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _read_response(
    opener: Any,
    request: urllib.request.Request,
    timeout: float,
) -> tuple[bytes, int, str]:
    with opener.open(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
        return response.read(), status, str(final_url)


def _official_host(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and host in {"www.cninfo.com.cn", "static.cninfo.com.cn"}
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


def _announcement_identity(row: dict[str, Any]) -> str | None:
    value = row.get("announcementId")
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def classify_cninfo_announcement_row(row: dict[str, Any]) -> dict[str, Any]:
    """Classify title-level termination hits without asserting a legal outcome.

    This is a triage label only. Original notice content and exchange decisions
    must be reviewed before a row can be treated as a listing/delisting event.
    """
    raw_title = row.get("announcementTitle")
    title = html.unescape(re.sub(r"<[^>]*>", "", str(raw_title or "")))
    title = re.sub(r"\s+", " ", title).strip()

    if "终止上市" not in title:
        classification = "OTHER_ANNOUNCEMENT"
        decision_candidate = False
        reason = "normalized title does not contain 终止上市"
    elif any(marker in title for marker in ("风险提示", "可能", "存在被", "将被", "面临")):
        classification = "TERMINATION_RISK_WARNING_NOT_DECISION"
        decision_candidate = False
        reason = "title expresses a possible risk or warning, not a completed exchange decision"
    elif (
        "终止上市决定" in title
        or re.search(r"决定.{0,30}终止上市|终止上市.{0,30}决定", title)
        or "股票终止上市的公告" in title
    ):
        classification = "TERMINATION_DECISION_OR_RESULT_CANDIDATE"
        decision_candidate = True
        reason = "title resembles a decision/result notice; original document and exchange source still require review"
    else:
        classification = "TERMINATION_KEYWORD_HIT_REQUIRES_DOCUMENT_REVIEW"
        decision_candidate = False
        reason = "title contains termination wording but does not establish an actual decision or result"

    return {
        "normalized_title": title,
        "classification": classification,
        "decision_or_result_candidate": decision_candidate,
        "requires_original_document_review": True,
        "classification_basis": reason,
    }


def probe_cninfo_announcement_api(
    *,
    start_date: str,
    end_date: str,
    column: str,
    plate: str,
    search_key: str,
    output_dir: str | Path,
    stock: str = "",
    page_size: int = 30,
    max_pages: int = 2,
    min_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    opener: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Capture a bounded metadata query and its raw JSON pages.

    The supplied opener/sleep function are injectable for unit tests. No
    announcement PDF is downloaded, and a complete page traversal is only
    complete for the response's own query counters, not proof of source-wide
    historical coverage.
    """
    _validate_request(
        start_date=start_date,
        end_date=end_date,
        column=column,
        search_key=search_key,
        stock=stock,
        page_size=page_size,
        max_pages=max_pages,
        min_interval_seconds=min_interval_seconds,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(plate, str):
        raise ValueError("plate must be a string; use the value for the board under test")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    client = opener if opener is not None else _make_opener()
    report: dict[str, Any] = {
        "schema_version": 1,
        "probe": "cninfo_reverse_engineered_announcement_query",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ERROR",
        "request": {
            "start_date": start_date,
            "end_date": end_date,
            "column": column,
            "plate": plate,
            "search_key": search_key,
            "stock": stock,
            "stock_filter_semantics": "code_plus_resolved_orgId",
            "page_size": page_size,
            "max_pages": max_pages,
            "min_interval_seconds": min_interval_seconds,
            "timeout_seconds": timeout_seconds,
            "tab_name": "fulltext",
            "secid": "",
            "category": "",
            "trade": "",
            "sort_name": "",
            "sort_type": "",
            "highlight_title": True,
        },
        "endpoint": QUERY_URL,
        "search_page": SEARCH_PAGE_URL,
        "safety": {
            "read_only": True,
            "bounded_date_window": True,
            "bounded_page_count": True,
            "local_data_lake_access": False,
            "production_dataset_write": False,
            "announcement_documents_downloaded": False,
            "historical_universe_completeness_proven": False,
            "pit_admitted": False,
            "training_unlocked": False,
            "backtest_admitted": False,
        },
        "bootstrap": {},
        "stock_identity_lookup": {},
        "pages": [],
        "announcement_classifications": [],
        "classification_counts": {},
        "query_counters": {
            "reported_total_pages": None,
            "effective_expected_pages": None,
            "reported_total_records": None,
            "reported_total_announcements": None,
            "reported_total_securities": None,
            "captured_unique_security_codes": 0,
            "security_count_counter_semantics": "unqualified_do_not_use_as_universe_count",
            "captured_pages": 0,
            "captured_records": 0,
            "unique_announcement_ids": 0,
            "duplicate_announcement_ids": [],
            "rows_missing_identity_fields": [],
            "midnight_timestamp_rows": 0,
            "intraday_timestamp_rows_unverified": 0,
        },
        "anomalies": [],
        "page_traversal_complete_for_query": False,
        "source_coverage_proven": False,
        "notes": [
            (
                "The query endpoint and parameter schema are reverse-engineered "
                "research leads, not an official CNINFO API contract."
            ),
            (
                "A complete traversal covers only the server-reported query result "
                "and does not prove historical archive completeness."
            ),
            (
                "announcementTime is not accepted as an exact first-publication "
                "timestamp by this probe."
            ),
            (
                "Stock-specific queries resolve the CNINFO orgId first and send "
                "stock as code,orgId; the identity lookup schema is reverse-engineered."
            ),
            (
                "Raw response hashes are capture identities, not source revision "
                "receipts or immutable-document proofs."
            ),
        ],
    }

    try:
        bootstrap_request = urllib.request.Request(
            SEARCH_PAGE_URL,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        bootstrap_requested_at = datetime.now(timezone.utc).isoformat()
        bootstrap_bytes, bootstrap_status, bootstrap_url = _read_response(
            client, bootstrap_request, timeout_seconds
        )
        bootstrap_captured_at = datetime.now(timezone.utc).isoformat()
        if not _official_host(bootstrap_url):
            raise ValueError("search-page bootstrap redirected outside the official CNINFO host")
        bootstrap_path = target_dir / "search_page_bootstrap.html"
        bootstrap_path.write_bytes(bootstrap_bytes)
        report["bootstrap"] = {
            "request_started_at": bootstrap_requested_at,
            "captured_at": bootstrap_captured_at,
            "status_code": bootstrap_status,
            "final_url": bootstrap_url,
            "bytes": len(bootstrap_bytes),
            "sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
            "capture_file": bootstrap_path.name,
        }
        if bootstrap_status != 200:
            raise RuntimeError(f"search-page bootstrap returned HTTP {bootstrap_status}")

        stock_query_value = ""
        referer_url = SEARCH_PAGE_URL
        previous_request_at: float | None = time.monotonic()
        if stock:
            elapsed = time.monotonic() - previous_request_at
            if elapsed < min_interval_seconds:
                sleep_fn(min_interval_seconds - elapsed)
            lookup_form = {"keyWord": stock, "maxNum": "10"}
            lookup_body = urllib.parse.urlencode(lookup_form).encode("utf-8")
            lookup_request = urllib.request.Request(
                STOCK_LOOKUP_URL,
                data=lookup_body,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": SEARCH_PAGE_URL,
                    "Origin": "https://www.cninfo.com.cn",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                method="POST",
            )
            lookup_started_at = datetime.now(timezone.utc).isoformat()
            lookup_bytes, lookup_status, lookup_url = _read_response(
                client, lookup_request, timeout_seconds
            )
            previous_request_at = time.monotonic()
            lookup_captured_at = datetime.now(timezone.utc).isoformat()
            if not _official_host(lookup_url):
                raise ValueError("stock lookup redirected outside the official CNINFO host")
            lookup_path = target_dir / "stock_identity_lookup.json"
            lookup_path.write_bytes(lookup_bytes)
            lookup_report = {
                "request_started_at": lookup_started_at,
                "captured_at": lookup_captured_at,
                "request_form": lookup_form,
                "request_body_sha256": hashlib.sha256(lookup_body).hexdigest(),
                "status_code": lookup_status,
                "final_url": lookup_url,
                "bytes": len(lookup_bytes),
                "sha256": hashlib.sha256(lookup_bytes).hexdigest(),
                "capture_file": lookup_path.name,
            }
            report["stock_identity_lookup"] = lookup_report
            if lookup_status != 200:
                raise RuntimeError(f"stock lookup returned HTTP {lookup_status}")
            try:
                lookup_payload = json.loads(lookup_bytes.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("stock lookup did not return UTF-8 JSON") from exc
            stock_identity = _extract_stock_identity(lookup_payload, stock)
            stock_query_value = f"{stock},{stock_identity['org_id']}"
            referer_query = urllib.parse.urlencode(
                {"stockCode": stock, "orgId": stock_identity["org_id"]}
            )
            referer_url = (
                "https://www.cninfo.com.cn/new/disclosure/stock?"
                f"{referer_query}"
            )
            report["request"]["resolved_org_id"] = stock_identity["org_id"]
            report["request"]["resolved_sec_name"] = stock_identity["sec_name"]
            report["request"]["stock_query_value"] = stock_query_value
            report["stock_identity_lookup"]["matched_stock_code"] = stock_identity[
                "stock_code"
            ]
            report["stock_identity_lookup"]["resolved_org_id"] = stock_identity[
                "org_id"
            ]
            report["stock_identity_lookup"]["resolved_sec_name"] = stock_identity[
                "sec_name"
            ]

        expected_pages: int | None = None
        expected_records: int | None = None
        all_ids: list[str] = []
        all_security_codes: set[str] = set()
        for page_num in range(1, max_pages + 1):
            if expected_pages is not None and page_num > expected_pages:
                break
            if previous_request_at is not None:
                elapsed = time.monotonic() - previous_request_at
                if elapsed < min_interval_seconds:
                    sleep_fn(min_interval_seconds - elapsed)

            form = {
                "pageNum": str(page_num),
                "pageSize": str(page_size),
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": stock_query_value,
                "searchkey": search_key,
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start_date}~{end_date}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            encoded = urllib.parse.urlencode(form).encode("utf-8")
            request = urllib.request.Request(
                QUERY_URL,
                data=encoded,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": referer_url,
                    "Origin": "https://www.cninfo.com.cn",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                method="POST",
            )
            request_started_at = datetime.now(timezone.utc).isoformat()
            raw_bytes, status, final_url = _read_response(client, request, timeout_seconds)
            previous_request_at = time.monotonic()
            if not _official_host(final_url):
                raise ValueError("query request redirected outside the official CNINFO host")
            page_path = target_dir / f"page_{page_num:03d}.json"
            page_path.write_bytes(raw_bytes)
            page_report: dict[str, Any] = {
                "page_num": page_num,
                "request_started_at": request_started_at,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "request_form": form,
                "request_body_sha256": hashlib.sha256(encoded).hexdigest(),
                "status_code": status,
                "final_url": final_url,
                "bytes": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "capture_file": page_path.name,
            }
            report["pages"].append(page_report)
            if status != 200:
                raise RuntimeError(f"query page {page_num} returned HTTP {status}")
            try:
                payload = json.loads(raw_bytes.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"query page {page_num} did not return UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"query page {page_num} JSON root must be an object")

            announcements = payload.get("announcements")
            if announcements is None:
                empty_result_counters = (
                    "totalpages",
                    "totalRecordNum",
                    "totalAnnouncement",
                    "totalSecurities",
                )
                confirmed_empty = (
                    all(_as_int(payload.get(key)) == 0 for key in empty_result_counters)
                    and payload.get("hasMore") is False
                )
                if not confirmed_empty:
                    raise ValueError(
                        f"query page {page_num} has null announcements without "
                        "reconciled zero-result counters"
                    )
                announcements = []
            elif not isinstance(announcements, list):
                raise ValueError(
                    f"query page {page_num} announcements must be an array or null"
                )
            total_pages_raw = _as_int(payload.get("totalpages"))
            total_records_raw = _as_int(payload.get("totalRecordNum"))
            total_announcements_raw = _as_int(payload.get("totalAnnouncement"))
            total_securities_raw = _as_int(payload.get("totalSecurities"))
            has_more = payload.get("hasMore")
            if total_pages_raw is None and total_records_raw is None:
                raise ValueError(
                    f"query page {page_num} has neither totalpages nor totalRecordNum"
                )
            calculated_pages = (
                (total_records_raw + page_size - 1) // page_size
                if total_records_raw is not None and total_records_raw > 0
                else 0
            )
            if total_pages_raw == 0 and total_records_raw is not None and total_records_raw > 0:
                # CNINFO has been observed returning totalpages=0 together with
                # a positive totalRecordNum and hasMore=false. Keep the raw
                # counter, but derive the expected page count from records and
                # pageSize. The inconsistency remains a warning, not a reason
                # to discard an otherwise fully reconciled one-page query.
                reported_pages = calculated_pages
                report["anomalies"].append(
                    "totalpages is zero while totalRecordNum is positive; "
                    "effective page count derived from totalRecordNum and pageSize"
                )
            else:
                reported_pages = (
                    total_pages_raw
                    if total_pages_raw is not None
                    else calculated_pages
                )
            if (
                total_pages_raw is not None
                and total_pages_raw > 0
                and total_records_raw is not None
                and total_records_raw > 0
                and calculated_pages != total_pages_raw
            ):
                report["anomalies"].append(
                    "totalpages differs from the page count implied by "
                    "totalRecordNum and pageSize"
                )
            if expected_pages is None:
                expected_pages = reported_pages
                expected_records = total_records_raw
                report["query_counters"]["reported_total_pages"] = total_pages_raw
                report["query_counters"]["effective_expected_pages"] = expected_pages
                report["query_counters"]["reported_total_records"] = expected_records
                report["query_counters"]["reported_total_announcements"] = (
                    total_announcements_raw
                )
                report["query_counters"]["reported_total_securities"] = (
                    total_securities_raw
                )
            else:
                if reported_pages != expected_pages:
                    report["anomalies"].append(
                        "effective page count changed from "
                        f"{expected_pages} to {reported_pages} on page {page_num}"
                    )
                if (
                    total_pages_raw is not None
                    and report["query_counters"]["reported_total_pages"] is not None
                    and total_pages_raw != report["query_counters"]["reported_total_pages"]
                ):
                    report["anomalies"].append(
                        "raw totalpages counter changed across query pages"
                    )
                if (
                    total_records_raw is not None
                    and expected_records is not None
                    and total_records_raw != expected_records
                ):
                    report["anomalies"].append(
                        "reported record count changed from "
                        f"{expected_records} to {total_records_raw} on page {page_num}"
                    )
                if (
                    total_announcements_raw is not None
                    and report["query_counters"]["reported_total_announcements"] is not None
                    and total_announcements_raw
                    != report["query_counters"]["reported_total_announcements"]
                ):
                    report["anomalies"].append(
                        "reported totalAnnouncement counter changed across query pages"
                    )
                if (
                    total_securities_raw is not None
                    and report["query_counters"]["reported_total_securities"] is not None
                    and total_securities_raw
                    != report["query_counters"]["reported_total_securities"]
                ):
                    report["anomalies"].append(
                        "reported totalSecurities counter changed across query pages"
                    )

            page_report["record_count"] = len(announcements)
            page_report["reported_total_pages"] = total_pages_raw
            page_report["effective_expected_pages"] = reported_pages
            page_report["reported_total_records"] = total_records_raw
            page_report["reported_total_announcements"] = total_announcements_raw
            page_report["reported_total_securities"] = total_securities_raw
            page_report["has_more"] = has_more
            missing_identity_rows: list[dict[str, Any]] = []
            for row_index, row in enumerate(announcements):
                if not isinstance(row, dict):
                    missing_identity_rows.append({
                        "page_num": page_num,
                        "row_index": row_index,
                        "reason": "announcement row is not an object",
                    })
                    continue
                missing_fields = [
                    field for field in _REQUIRED_ANNOUNCEMENT_FIELDS
                    if row.get(field) in (None, "")
                ]
                if missing_fields:
                    missing_identity_rows.append({
                        "page_num": page_num,
                        "row_index": row_index,
                        "missing_fields": missing_fields,
                    })
                announcement_id = _announcement_identity(row)
                if announcement_id is not None:
                    all_ids.append(announcement_id)
                security_code = row.get("secCode")
                if security_code is not None and str(security_code).strip():
                    all_security_codes.add(str(security_code).strip())
                time_observation = _publication_time_observation(
                    row.get("announcementTime")
                )
                classification = classify_cninfo_announcement_row(row)
                classification_row = {
                    "page_num": page_num,
                    "row_index": row_index,
                    "sec_code": row.get("secCode"),
                    "sec_name": row.get("secName"),
                    "announcement_id": announcement_id,
                    "announcement_title_raw": row.get("announcementTitle"),
                    "normalized_title": classification["normalized_title"],
                    "adjunct_url": row.get("adjunctUrl"),
                    "page_column": row.get("pageColumn"),
                    "announcement_time_observation": time_observation,
                    "classification": classification["classification"],
                    "decision_or_result_candidate": classification[
                        "decision_or_result_candidate"
                    ],
                    "requires_original_document_review": classification[
                        "requires_original_document_review"
                    ],
                    "classification_basis": classification["classification_basis"],
                }
                report["announcement_classifications"].append(classification_row)
                report["classification_counts"][classification["classification"]] = (
                    report["classification_counts"].get(classification["classification"], 0) + 1
                )
                if time_observation["timestamp_semantics"] == "midnight_date_stamp_suspected":
                    report["query_counters"]["midnight_timestamp_rows"] += 1
                elif time_observation["timestamp_semantics"] == "intraday_value_unverified":
                    report["query_counters"]["intraday_timestamp_rows_unverified"] += 1
            page_report["rows_missing_identity_fields"] = len(missing_identity_rows)
            report["query_counters"]["rows_missing_identity_fields"].extend(
                missing_identity_rows
            )
            report["query_counters"]["captured_records"] += len(announcements)
            if has_more is False and page_num < (expected_pages or 0):
                report["anomalies"].append(
                    f"hasMore=false before effective final page {expected_pages}"
                )
                break

        effective_pages_final = report["query_counters"]["effective_expected_pages"]
        captured_pages = len(report["pages"])
        report["query_counters"]["captured_pages"] = captured_pages
        duplicates = sorted(
            value for value, count in Counter(all_ids).items() if count > 1
        )
        report["query_counters"]["unique_announcement_ids"] = len(set(all_ids))
        report["query_counters"]["duplicate_announcement_ids"] = duplicates
        report["query_counters"]["captured_unique_security_codes"] = len(
            all_security_codes
        )
        if duplicates:
            report["anomalies"].append(
                "duplicate announcementId values appeared across captured pages"
            )
        if (
            isinstance(expected_records, int)
            and report["query_counters"]["captured_records"] != expected_records
        ):
            report["anomalies"].append(
                "captured record count "
                f"{report['query_counters']['captured_records']} differs from "
                f"server-reported totalRecordNum {expected_records}"
            )
        pagination_anomalies = [
            item
            for item in report["anomalies"]
            if (
                "reported page count changed" in item
                or "reported record count changed" in item
                or "totalpages differs" in item
                or "effective page count changed" in item
                or "raw totalpages counter changed" in item
                or "hasMore=false before" in item
                or "captured record count" in item
            )
        ]
        # Completion must use the effective page count. The raw
        # totalpages value is retained separately because CNINFO has been
        # observed returning zero even when totalRecordNum implies multiple
        # pages.
        expected_response_pages = (
            max(1, effective_pages_final)
            if isinstance(effective_pages_final, int)
            else None
        )
        page_complete = (
            expected_response_pages is not None
            and captured_pages == expected_response_pages
            and not pagination_anomalies
        )
        if (
            isinstance(effective_pages_final, int)
            and captured_pages == expected_response_pages
            and report["pages"]
            and report["pages"][-1].get("has_more") is True
        ):
            report["anomalies"].append(
                "hasMore=true on the effective final page"
            )
            page_complete = False
        report["page_traversal_complete_for_query"] = page_complete
        if not page_complete:
            report["status"] = "PARTIAL"
            report["anomalies"].append(
                "page traversal is partial; a bounded probe must not claim query completeness"
            )
        elif report["query_counters"]["captured_records"] == 0:
            report["status"] = "NO_RESULTS"
        elif (
            report["anomalies"]
            or report["query_counters"]["duplicate_announcement_ids"]
            or report["query_counters"]["rows_missing_identity_fields"]
        ):
            report["status"] = "COMPLETE_WITH_WARNINGS"
        else:
            report["status"] = "COMPLETE_FOR_QUERY"
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        report["status"] = "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["page_traversal_complete_for_query"] = False

    report_path = target_dir / "probe_report.json"
    report["report_file"] = report_path.name
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def render_probe_plan(
    *,
    start_date: str,
    end_date: str,
    column: str,
    plate: str,
    search_key: str,
    page_size: int,
    max_pages: int,
    min_interval_seconds: float,
    timeout_seconds: float = 20.0,
    stock: str = "",
) -> str:
    """Return a no-network plan for the requested bounded probe."""
    _validate_request(
        start_date=start_date,
        end_date=end_date,
        column=column,
        search_key=search_key,
        stock=stock,
        page_size=page_size,
        max_pages=max_pages,
        min_interval_seconds=min_interval_seconds,
        timeout_seconds=timeout_seconds,
    )
    return json.dumps(
        {
            "mode": "PLAN_ONLY_NO_NETWORK",
            "query_url": QUERY_URL,
            "stock_identity_lookup_url": STOCK_LOOKUP_URL,
            "request": {
                "start_date": start_date,
                "end_date": end_date,
                "column": column,
                "plate": plate,
                "search_key": search_key,
                "stock": stock,
                "stock_filter_semantics": "code_plus_resolved_orgId",
                "page_size": page_size,
                "max_pages": max_pages,
                "min_interval_seconds": min_interval_seconds,
                "timeout_seconds": timeout_seconds,
            },
            "writes": (
                "Only the report and raw responses under the selected probe "
                "output directory after --execute."
            ),
            "production_admitted": False,
        },
        ensure_ascii=False,
        indent=2,
    )
