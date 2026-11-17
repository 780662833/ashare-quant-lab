"""Offline inspection of captured SZSE 本所公告 HTML structure.

This module never performs network requests. It reports visible form controls,
first-party script references and limited static route/query-key indicators so
operators can inspect captured source bytes without guessing hidden endpoints.
"""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

_MAX_HTML_BYTES = 20 * 1024 * 1024
_ALLOWED_HOST = "www.szse.cn"
_ROUTE_LITERAL = re.compile(
    r"""(?P<route>(?:https://www\.szse\.cn)?/(?:api|new|disclosure|marketServices|search)[^'"\s<>)]*)""",
    re.IGNORECASE,
)
_QUERY_KEY = re.compile(r"[?&]([A-Za-z][A-Za-z0-9_-]{0,39})=")
_SEARCH_MARKERS = (
    "keyword",
    "timetype",
    "search",
    "notice",
    "date",
    "begin",
    "end",
    "start",
    "page",
    "curHref",
    "curTitle",
)


def _safe_page_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower().rstrip(".") != _ALLOWED_HOST
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("page_url must be a canonical HTTPS URL on www.szse.cn")
    return value


def _decode_html(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("captured page is neither UTF-8 nor GB18030 text")


def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key.lower(): value or "" for key, value in attrs}


def _field_summary(tag: str, attrs: dict[str, str]) -> dict[str, str]:
    result = {"tag": tag}
    for key in ("type", "id", "name", "placeholder", "aria-label", "form"):
        if attrs.get(key):
            result[key] = attrs[key]
    # Hidden values can carry session or anti-forgery state; never emit them.
    if attrs.get("type", "").lower() != "hidden" and attrs.get("value"):
        result["value"] = attrs["value"]
    return result


class _SourceStructureParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.title_parts: list[str] = []
        self._in_title = False
        self.forms: list[dict[str, Any]] = []
        self.controls: list[dict[str, str]] = []
        self.controls_outside_forms: list[dict[str, str]] = []
        self.scripts: list[dict[str, Any]] = []
        self._form_stack: list[dict[str, Any]] = []
        self._script: dict[str, Any] | None = None
        self._script_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        values = _attrs_dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "form":
            form: dict[str, Any] = {
                "id": values.get("id") or None,
                "name": values.get("name") or None,
                "action": values.get("action") or "",
                "method": (values.get("method") or "get").lower(),
                "fields": [],
            }
            action = form["action"]
            if action:
                form["action_resolved"] = urljoin(self.page_url, action)
            self.forms.append(form)
            self._form_stack.append(form)
        elif tag in {"input", "select", "textarea", "button"}:
            field = _field_summary(tag, values)
            self.controls.append(field)
            if self._form_stack:
                self._form_stack[-1]["fields"].append(field)
            else:
                self.controls_outside_forms.append(field)
        elif tag == "script":
            src = values.get("src") or ""
            self._script = {
                "src": src or None,
                "resolved_src": urljoin(self.page_url, src) if src else None,
                "type": values.get("type") or None,
                "id": values.get("id") or None,
                "inline_bytes": 0,
                "inline_sha256": None,
                "search_markers": [],
                "candidate_routes": [],
                "query_parameter_names": [],
            }
            self._script_parts = []

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._script is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "form" and self._form_stack:
            self._form_stack.pop()
        elif tag == "script" and self._script is not None:
            script_text = "".join(self._script_parts)
            encoded = script_text.encode("utf-8")
            self._script["inline_bytes"] = len(encoded)
            self._script["inline_sha256"] = (
                hashlib.sha256(encoded).hexdigest() if encoded else None
            )
            lowered = script_text.casefold()
            self._script["search_markers"] = [
                marker
                for marker in _SEARCH_MARKERS
                if marker.casefold() in lowered
            ]
            routes: list[str] = []
            query_keys: set[str] = set()
            for match in _ROUTE_LITERAL.finditer(script_text):
                literal = match.group("route").rstrip(";,")
                parsed = urlsplit(literal)
                if parsed.scheme and parsed.netloc:
                    if parsed.hostname != _ALLOWED_HOST:
                        continue
                route = parsed.path
                if route and route not in routes:
                    routes.append(route)
                query_keys.update(_QUERY_KEY.findall(parsed.query))
            self._script["candidate_routes"] = routes[:50]
            self._script["query_parameter_names"] = sorted(query_keys)
            self.scripts.append(self._script)
            self._script = None
            self._script_parts = []


def inspect_szse_notice_html(
    html_path: str | Path,
    *,
    page_url: str = "https://www.szse.cn/disclosure/notice/index.html",
) -> dict[str, Any]:
    """Inspect one previously captured SZSE HTML file without any network I/O."""
    _safe_page_url(page_url)
    source = Path(html_path)
    if source.stat().st_size > _MAX_HTML_BYTES:
        raise ValueError(
            f"captured HTML exceeds the {_MAX_HTML_BYTES}-byte inspection limit"
        )
    raw = source.read_bytes()
    if not raw:
        raise ValueError("captured HTML file is empty")
    if len(raw) > _MAX_HTML_BYTES:
        raise ValueError(
            f"captured HTML exceeds the {_MAX_HTML_BYTES}-byte inspection limit"
        )
    parser = _SourceStructureParser(page_url)
    parser.feed(_decode_html(raw))
    parser.close()

    first_party_scripts = []
    other_scripts = []
    for item in parser.scripts:
        resolved = item.get("resolved_src")
        if resolved:
            parsed = urlsplit(resolved)
            if parsed.scheme == "https" and parsed.hostname == _ALLOWED_HOST:
                first_party_scripts.append(item)
            else:
                other_scripts.append(item)
        else:
            first_party_scripts.append(item)

    return {
        "schema_version": 1,
        "inspection": "szse_notice_captured_html_offline",
        "source_file": source.name,
        "page_url": page_url,
        "source_bytes": len(raw),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "page_title": " ".join(" ".join(parser.title_parts).split()),
        "forms": parser.forms,
        "controls_outside_forms": parser.controls_outside_forms,
        "first_party_scripts": first_party_scripts,
        "other_script_references": other_scripts,
        "summary": {
            "form_count": len(parser.forms),
            "control_count": len(parser.controls),
            "script_count": len(parser.scripts),
            "first_party_script_reference_count": len(first_party_scripts),
            "other_script_reference_count": len(other_scripts),
            "candidate_route_count": sum(
                len(item["candidate_routes"]) for item in parser.scripts
            ),
        },
        "safety": {
            "network_requests_made": False,
            "captured_html_modified": False,
            "hidden_input_values_emitted": False,
            "pdf_attachments_downloaded": False,
            "local_data_lake_access": False,
            "production_dataset_write": False,
            "pit_admitted": False,
            "training_unlocked": False,
            "backtest_admitted": False,
        },
        "limitations": [
            "This is a static inspection of one captured HTML file; it does not execute JavaScript.",
            "External script references are listed but never fetched.",
            "Candidate routes and query names are clues only, not a verified API contract.",
            "A missing form or route in the captured HTML does not prove that the live page has no search behavior.",
        ],
    }


def write_szse_html_inspection_report(
    report: dict[str, Any], output_path: str | Path
) -> Path:
    """Write an offline inspection report to an explicit caller-selected path."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
