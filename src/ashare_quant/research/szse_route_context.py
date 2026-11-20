"""Bounded, read-only verification of exact SZSE announcement routes."""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SZSE_HOST = "www.szse.cn"
NEIGHBOR_API_PATH = "/api/report/webdocument/queryPreAndNextDoc"
DEFAULT_ROUTE_URLS = (
    "https://www.szse.cn/disclosure/notice/t20210104_584083.html",
    "https://www.szse.cn/disclosure/notice/company/t20210105_584085.html",
)
MAX_ROUTE_COUNT = 10
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MIN_REQUEST_INTERVAL_SECONDS = 0.5
ROUTE_RE = re.compile(
    r"^/disclosure/notice/(?:general/|company/)?t(?P<date>\d{8})_(?P<doc_id>\d+)\.html$",
    re.IGNORECASE,
)
HTML_FIELDS = {
    "article_id": re.compile(r"\barticleId\s*:\s*['\"]?(\d+)['\"]?", re.I),
    "navigation_channel_id": re.compile(r"\bchannelId\s*:\s*['\"]?(\d+)['\"]?", re.I),
    "navigation_channel_code": re.compile(r"\bchannelCode\s*:\s*['\"]([^'\"]+)['\"]", re.I),
}
REVERSE_CHECKS = {
    "584083": (
        ("584082", "pre", "nextDocId"),
        ("584084", "next", "preDocId"),
    ),
    "584085": (("584068", "pre", "nextDocId"),),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_origin(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid URL: {url}") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower().rstrip(".") != SZSE_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("only HTTPS URLs on the exact host www.szse.cn are allowed")
    return parsed


def _route_identity(url: str) -> tuple[str, str]:
    parsed = _parse_origin(url)
    if parsed.query:
        raise ValueError("announcement route URL must not contain a query string")
    match = ROUTE_RE.fullmatch(parsed.path)
    if match is None:
        raise ValueError("unsupported SZSE announcement detail route")
    return match.group("doc_id"), match.group("date")


def extract_html_route_context(raw_html: bytes | str) -> dict[str, str | None]:
    text = raw_html.decode("utf-8", errors="replace") if isinstance(raw_html, bytes) else raw_html
    result: dict[str, str | None] = {}
    for field, pattern in HTML_FIELDS.items():
        match = pattern.search(text)
        result[field] = match.group(1).strip() if match else None
    return result


def _json_data(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return None, "JSON response has no object at data"
    return payload["data"], None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        destination_url = urllib.parse.urljoin(req.full_url, newurl)
        try:
            destination = _parse_origin(destination_url)
            source = _parse_origin(req.full_url)
        except ValueError:
            # Returning None makes urllib surface the 3xx response as HTTPError;
            # the caller records Location without following an untrusted host.
            return None
        if destination.path != source.path or destination.query != source.query:
            # Do not issue a second request under a changed route or docId/channelId.
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> Any:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _SameHostRedirectHandler()
    )


def _request_url(route_url: str, kind: str, navigation_channel_id: str | None = None) -> str:
    parsed = _parse_origin(route_url)
    doc_id, _ = _route_identity(route_url)
    if kind == "html":
        return route_url
    if kind == "json":
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path[:-5] + ".json", "", "")
        )
    if kind == "neighbor":
        if not navigation_channel_id or not navigation_channel_id.isdigit():
            raise ValueError("HTML navigation channelId is required")
        query = urllib.parse.urlencode(
            {"channelId": navigation_channel_id, "docId": doc_id}
        )
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, NEIGHBOR_API_PATH, query, "")
        )
    raise ValueError(f"unsupported request kind: {kind}")


def _get(opener: Any, url: str, kind: str, timeout: float) -> tuple[bytes | None, dict[str, Any]]:
    parsed = _parse_origin(url)
    if kind in {"html", "json"}:
        route_url = url if kind == "html" else url[:-5] + ".html"
        _route_identity(route_url)
        if parsed.query or not parsed.path.lower().endswith(".html" if kind == "html" else ".json"):
            raise ValueError("invalid detail-page request URL")
    elif kind == "neighbor":
        if parsed.path != NEIGHBOR_API_PATH:
            raise ValueError("neighbor request path is not allowlisted")
        query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        if set(query) != {"channelId", "docId"} or any(
            len(v) != 1 or not v[0].isdigit() for v in query.values()
        ):
            raise ValueError("neighbor request must contain numeric channelId and docId only")
    else:
        raise ValueError(f"unsupported request kind: {kind}")

    record: dict[str, Any] = {
        "kind": kind, "method": "GET", "requested_url": url, "requested_at": _now(),
        "final_url": None, "status_code": None, "content_type": None,
        "bytes_received": 0, "sha256": None, "capture_file": None, "error": None,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/json,text/javascript;q=0.9,*/*;q=0.5",
    }
    if kind == "neighbor":
        headers["X-Requested-With"] = "XMLHttpRequest"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            final = _parse_origin(final_url)
            if kind in {"html", "json"}:
                expected = _parse_origin(url)
                if final.path != expected.path:
                    raise ValueError("redirect changed the exact announcement route")
            elif final.path != NEIGHBOR_API_PATH or final.query != parsed.query:
                raise ValueError("neighbor API redirect changed route or query context")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response exceeds 20 MiB capture limit")
            status = int(getattr(response, "status", 200))
            record.update(
                final_url=final_url, status_code=status,
                content_type=response.headers.get("Content-Type"),
                bytes_received=len(raw), sha256=_sha256(raw), response_captured_at=_now(),
            )
            if not 200 <= status < 300:
                record["error"] = f"HTTP status {status}"
            return raw, record
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raw = b""
            record["error"] = f"HTTP status {exc.code}; error body exceeded capture limit"
        else:
            record.update(
                status_code=exc.code,
                content_type=exc.headers.get("Content-Type") if exc.headers else None,
                bytes_received=len(raw), sha256=_sha256(raw) if raw else None,
                error=f"HTTP status {exc.code}",
            )
        location = exc.headers.get("Location") if exc.headers else None
        if location:
            redirect_location = urllib.parse.urljoin(exc.geturl(), location)
            record["redirect_location"] = redirect_location
            try:
                destination = _parse_origin(redirect_location)
                redirect_changes_context = (
                    destination.path != parsed.path or destination.query != parsed.query
                )
            except ValueError:
                redirect_changes_context = True
            if redirect_changes_context:
                record["redirect_blocked"] = True
                record["error"] = "redirect blocked: request path or query context changed"
        record.update(final_url=exc.geturl(), response_captured_at=_now())
        return raw, record
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {exc}", response_captured_at=_now())
        return None, record


def _reference(reference_dir: Path | None, doc_id: str) -> dict[str, Any] | None:
    if reference_dir is None:
        return None
    path = reference_dir / "pages" / doc_id / "announcement.json"
    if not path.is_file():
        return {"status": "missing_reference_json", "path": str(path)}
    data, error = _json_data(path.read_bytes())
    if error or data is None:
        return {"status": "invalid_reference_json", "path": str(path), "error": error}
    content = data.get("content")
    return {
        "status": "loaded", "doc_id": str(data.get("docId", "")),
        "title": data.get("title"), "pub_time": data.get("pubTime"),
        "content_sha256": _sha256(content.encode("utf-8")) if isinstance(content, str) else None,
        "data_channel_id": str(data.get("channelId", "")) or None,
    }


def _compare_reference(reference: dict[str, Any] | None, data: dict[str, Any] | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    if reference.get("status") != "loaded" or data is None:
        return {"status": "not_comparable", "reference_status": reference.get("status")}
    content = data.get("content")
    content_hash = _sha256(content.encode("utf-8")) if isinstance(content, str) else None
    checks = {
        "doc_id_match": reference["doc_id"] == str(data.get("docId", "")),
        "title_match": reference.get("title") == data.get("title"),
        "pub_time_match": reference.get("pub_time") == data.get("pubTime"),
        "content_sha256_match": reference.get("content_sha256") is not None
            and content_hash is not None and reference["content_sha256"] == content_hash,
    }
    return {
        "status": "same_business_payload" if all(checks.values()) else "payload_difference_requires_review",
        "checks": checks, "reference_content_sha256": reference.get("content_sha256"),
        "route_content_sha256": content_hash,
        "reference_data_channel_id": reference.get("data_channel_id"),
        "route_data_channel_id": str(data.get("channelId", "")) or None,
    }


def _reverse_checks(doc_id: str, neighbor: dict[str, Any] | None, channel_match: bool | None) -> list[dict[str, Any]]:
    results = []
    for source_id, relation, field in REVERSE_CHECKS.get(doc_id, ()):
        value = None if neighbor is None else neighbor.get(field)
        observed = str(value) if value not in (None, "") else None
        if neighbor is None:
            status = "unresolved_neighbor_unavailable"
        elif channel_match is not True:
            status = "unresolved_neighbor_channel_mismatch"
        elif observed is None:
            status = "unresolved_missing_reverse_value"
        elif observed == source_id:
            status = "match"
        else:
            status = "route_specific_reverse_mismatch"
        results.append({
            "source_id": source_id, "source_relation": relation,
            "target_field": field, "observed_value": observed, "status": status,
        })
    return results


def capture_szse_route_context(
    *, output_dir: Path, route_urls: list[str] | tuple[str, ...] = DEFAULT_ROUTE_URLS,
    reference_dir: Path | None = None, min_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0, opener: Any | None = None,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    if not 1 <= len(route_urls) <= MAX_ROUTE_COUNT:
        raise ValueError("route_urls must contain 1-10 URLs")
    if min_interval_seconds < MIN_REQUEST_INTERVAL_SECONDS:
        raise ValueError("minimum request interval must be at least 0.5 seconds")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    normalized = []
    seen = set()
    for url in route_urls:
        doc_id, date_text = _route_identity(url)
        if url in seen:
            raise ValueError(f"duplicate route URL: {url}")
        seen.add(url)
        normalized.append((url, doc_id, date_text))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = opener or _opener()
    report: dict[str, Any] = {
        "schema_version": "1.0.0", "status": "CAPTURED_BOUNDED_ROUTE_SAMPLE",
        "captured_at": _now(), "route_count": len(normalized), "request_count": 0,
        "requests_are_get_only": True, "minimum_interval_seconds": min_interval_seconds,
        "reference_dir": str(reference_dir) if reference_dir else None,
        "route_results": [], "request_manifest": [],
        "safety": {
            "host_allowlist": [SZSE_HOST], "https_only": True,
            "attachments_downloaded": False, "production_admitted": False,
            "pit_admitted": False, "training_admitted": False,
            "backtest_admitted": False, "source_error_confirmed_by_tool": False,
        },
    }
    last_request = None

    def fetch(url: str, kind: str, route_dir: Path) -> dict[str, Any] | None:
        nonlocal last_request
        if last_request is not None:
            wait = min_interval_seconds - (time.monotonic() - last_request)
            if wait > 0:
                sleep_fn(wait)
        last_request = time.monotonic()
        raw, record = _get(client, url, kind, timeout_seconds)
        if raw is not None:
            filename = f"{kind}.{'html' if kind == 'html' else 'json'}"
            target = route_dir / filename
            target.write_bytes(raw)
            record["capture_file"] = str(target.relative_to(output_dir))
        report["request_manifest"].append(record)
        report["request_count"] += 1
        return {"raw": raw, "record": record} if raw is not None else None

    for index, (route_url, doc_id, route_date) in enumerate(normalized, start=1):
        route_dir = output_dir / "routes" / f"{index:02d}_{doc_id}"
        route_dir.mkdir(parents=True, exist_ok=True)
        result: dict[str, Any] = {
            "route_index": index, "doc_id_from_url": doc_id, "date_from_url": route_date,
            "requested_html_url": route_url, "html_context": None, "json_identity": None,
            "neighbor_context": None, "reference_comparison": None,
            "reverse_checks": [], "route_identity_status": "capture_incomplete",
        }
        html_capture = fetch(route_url, "html", route_dir)
        html_context = None
        if html_capture and 200 <= int(html_capture["record"].get("status_code") or 0) < 300:
            html_context = extract_html_route_context(html_capture["raw"])
            result["html_context"] = html_context

        json_capture = fetch(_request_url(route_url, "json"), "json", route_dir)
        json_data = None
        if json_capture and 200 <= int(json_capture["record"].get("status_code") or 0) < 300:
            json_data, error = _json_data(json_capture["raw"])
            if error:
                result["json_error"] = error
            if json_data is not None:
                body = json_data.get("content")
                result["json_identity"] = {
                    "doc_id": str(json_data.get("docId", "")) or None,
                    "title": json_data.get("title"), "pub_time": json_data.get("pubTime"),
                    "data_channel_id": str(json_data.get("channelId", "")) or None,
                    "data_channel_code": json_data.get("chnlCode"),
                    "content_sha256": _sha256(body.encode("utf-8")) if isinstance(body, str) else None,
                }

        html_id = html_context.get("article_id") if html_context else None
        navigation_channel = html_context.get("navigation_channel_id") if html_context else None
        json_id = str(json_data.get("docId", "")) if json_data else None
        identity_match = html_id == doc_id and json_id == doc_id
        if identity_match:
            result["route_identity_status"] = "html_json_doc_id_match"
        elif (html_id and html_id != doc_id) or (json_id and json_id != doc_id):
            result["route_identity_status"] = "doc_id_mismatch"

        result["reference_comparison"] = _compare_reference(_reference(reference_dir, doc_id), json_data)
        neighbor_data = None
        if identity_match and navigation_channel and navigation_channel.isdigit():
            neighbor_capture = fetch(
                _request_url(route_url, "neighbor", navigation_channel), "neighbor", route_dir
            )
            if neighbor_capture and 200 <= int(neighbor_capture["record"].get("status_code") or 0) < 300:
                neighbor_data, error = _json_data(neighbor_capture["raw"])
                if error:
                    result["neighbor_error"] = error
                else:
                    channel_match = neighbor_data.get("chnlDocId") == f"{navigation_channel}_{doc_id}"
                    result["neighbor_context"] = {
                        "chnl_doc_id": neighbor_data.get("chnlDocId"),
                        "channel_context_match": channel_match,
                        "pre_doc_id": str(neighbor_data.get("preDocId", "")) or None,
                        "pre_doc_url": neighbor_data.get("preDocPuburl"),
                        "next_doc_id": str(neighbor_data.get("nextDocId", "")) or None,
                        "next_doc_url": neighbor_data.get("nextDocPuburl"),
                    }
        else:
            result["neighbor_skipped_reason"] = "HTML/JSON identity or HTML navigation channel unavailable"

        channel_match = (result.get("neighbor_context") or {}).get("channel_context_match")
        result["reverse_checks"] = _reverse_checks(doc_id, neighbor_data, channel_match)
        report["route_results"].append(result)

    statuses = [c["status"] for r in report["route_results"] for c in r["reverse_checks"]]
    errors = sum(bool(r.get("error")) for r in report["request_manifest"])
    report["status"] = "CAPTURED_BOUNDED_ROUTE_SAMPLE" if errors == 0 else "CAPTURED_WITH_REQUEST_ERRORS"
    report["summary"] = {
        "request_errors": errors,
        "routes_captured_identity_match": sum(r["route_identity_status"] == "html_json_doc_id_match" for r in report["route_results"]),
        "reverse_checks_total": len(statuses), "reverse_checks_match": statuses.count("match"),
        "reverse_checks_mismatch": statuses.count("route_specific_reverse_mismatch"),
        "reverse_checks_unresolved": sum(s.startswith("unresolved") for s in statuses),
        "full_graph_acceptance": False,
    }
    report_path = output_dir / "route_context_report.json"
    report["report_file"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
