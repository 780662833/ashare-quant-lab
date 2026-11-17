"""Offline inspection of captured SZSE 本所公告 HTML structure.

This module never performs network requests or executes JavaScript. It reports
visible UI attributes, inline event bindings, first-party script references and
bounded query/request clues from captured source bytes.
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
    r"""(?P<route>(?:https://www\.szse\.cn)?/"""
    r"""(?:api|new|disclosure|marketServices|search)[^'"\s<>)]*)""",
    re.IGNORECASE,
)
_QUERY_KEY = re.compile(r"(?:^|&)([A-Za-z][A-Za-z0-9_-]{0,39})=")
_GENERIC_QUERY_KEY = re.compile(
    r"\b([A-Za-z_$][A-Za-z0-9_$]{0,39})\s*:"
)
_REQUEST_CALL = re.compile(
    r"""(?:\$\.ajax|jQuery\.ajax|XMLHttpRequest|fetch\s*\("""
    r"""|axios\.(?:get|post|request)|\$\.get|\$\.post)""",
    re.IGNORECASE,
)
_QUERY_BINDING_MARKERS = (
    "article-text",
    "aticle-datepicker",
    "keyword",
    "datepicker",
    "publishdate",
    "addEventListener",
    "querySelector",
    "getElementById",
    "ajax",
    "XMLHttpRequest",
    "fetch",
    "axios",
    "$.post",
    "$.get",
    "curHref",
    "curTitle",
    "confirm-query",
)
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
_QUERY_KEY_HINTS = (
    "keyword",
    "key",
    "date",
    "time",
    "begin",
    "end",
    "start",
    "finish",
    "page",
    "limit",
    "size",
    "type",
    "code",
    "stock",
    "column",
    "plate",
    "sort",
)
_SAFE_CONTROL_ATTRS = (
    "type",
    "id",
    "name",
    "placeholder",
    "aria-label",
    "form",
    "class",
    "title",
    "role",
    "data-action",
    "data-target",
    "data-toggle",
    "data-url",
    "data-method",
)
_EVENT_ATTRS = ("onclick", "onchange", "oninput", "onsubmit")


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


def _safe_excerpt(value: str, *, limit: int = 320) -> str:
    """Return a compact code clue with likely secret values redacted."""
    snippet = re.sub(r"\s+", " ", value).strip()
    credential_key = (
        r"(?:[A-Za-z0-9_.-]*(?:access[_-]?token|refresh[_-]?token|"
        r"id[_-]?token|token|csrf|secret|authorization|cookie|password|"
        r"api[_-]?key|signature|session[_-]?id|jwt)[A-Za-z0-9_.-]*)"
    )
    credential_assignment = re.compile(
        rf"""(?i)(?P<key>["']?{credential_key}["']?)\s*[:=]\s*"""
        rf"""(?P<value>["'][^"']*["']|[A-Za-z0-9._~+/=-]{{4,}})"""
    )

    def redact_assignment(match: re.Match[str]) -> str:
        key = match.group("key").strip("'\"")
        return key + "=<redacted>"

    snippet = credential_assignment.sub(redact_assignment, snippet)
    snippet = re.sub(
        r"""(?i)\b(token|csrf|secret|authorization|cookie|password)\b"""
        r"""\s*[:=]\s*(['"]?)[A-Za-z0-9._~+/=-]{4,}""",
        r"\1=<redacted>",
        snippet,
    )
    snippet = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}",
        "Bearer <redacted>",
        snippet,
    )
    snippet = re.sub(r"\b[A-Za-z0-9_-]{48,}\b", "<redacted>", snippet)
    if len(snippet) > limit:
        snippet = snippet[: limit - 1] + "…"
    return snippet


def _field_summary(tag: str, attrs: dict[str, str]) -> dict[str, str]:
    result = {"tag": tag}
    for key in _SAFE_CONTROL_ATTRS:
        if attrs.get(key):
            result[key] = _safe_excerpt(attrs[key], limit=240)
    # Hidden values can carry session or anti-forgery state; never emit them.
    if attrs.get("type", "").lower() != "hidden" and attrs.get("value"):
        result["value"] = _safe_excerpt(attrs["value"], limit=120)
    for key in _EVENT_ATTRS:
        if attrs.get(key):
            result[f"{key}_clue"] = _safe_excerpt(attrs[key], limit=240)
    return result


def _script_analysis(script_text: str) -> dict[str, Any]:
    lowered = script_text.casefold()
    search_markers = [
        marker for marker in _SEARCH_MARKERS if marker.casefold() in lowered
    ]
    query_binding_markers = [
        marker
        for marker in _QUERY_BINDING_MARKERS
        if marker.casefold() in lowered
    ]

    routes: list[str] = []
    query_keys: set[str] = set()
    for match in _ROUTE_LITERAL.finditer(script_text):
        literal = match.group("route").replace(r"\/", "/").rstrip(";,")
        parsed = urlsplit(literal)
        if parsed.scheme and parsed.netloc:
            if parsed.scheme != "https" or parsed.hostname != _ALLOWED_HOST:
                continue
        route = parsed.path
        if route and route not in routes:
            routes.append(route)
        query_keys.update(_QUERY_KEY.findall(parsed.query))

    request_calls: list[dict[str, Any]] = []
    for match in _REQUEST_CALL.finditer(script_text):
        start = max(0, match.start() - 220)
        end = min(len(script_text), match.end() + 520)
        context = script_text[start:end]
        payload_keys = {
            key
            for key in _GENERIC_QUERY_KEY.findall(context)
            if any(hint in key.casefold() for hint in _QUERY_KEY_HINTS)
        }
        request_calls.append(
            {
                "request_marker": _safe_excerpt(match.group(0), limit=80),
                "payload_key_candidates": sorted(payload_keys)[:40],
                "context": _safe_excerpt(context),
            }
        )
        if len(request_calls) >= 8:
            break

    binding_clues: list[dict[str, str]] = []
    binding_patterns = (
        ("article_text_id", re.compile(r"article-text", re.IGNORECASE)),
        ("datepicker_id", re.compile(r"aticle-datepicker", re.IGNORECASE)),
        (
            "dom_lookup",
            re.compile(
                r"(?:getElementById|querySelector|querySelectorAll)\s*\([^)]{0,180}\)",
                re.IGNORECASE,
            ),
        ),
        (
            "event_binding",
            re.compile(
                r"(?:addEventListener\s*\([^)]{0,180}\)|"
                r"\.(?:on|one|click|submit|change|keyup|keydown|input|bind|delegate|live)"
                r"\s*\([^)]{0,180}\)|"
                r"\.on(?:click|submit|change|input|keyup|keydown)\s*=|"
                r"\.(?:onclick|onsubmit|onchange|oninput)\s*=)",
                re.IGNORECASE,
            ),
        ),
        (
            "jquery_selector",
            re.compile(
                r"""(?:\$|jQuery)\s*\(\s*(['"])(?:#[A-Za-z][\w-]*|"""
                r"""\.[A-Za-z][\w-]*)(?:[\w\s>+~.:#-]{0,80})\1\s*\)""",
                re.IGNORECASE,
            ),
        ),
    )
    seen: set[tuple[str, int]] = set()
    for clue_type, pattern in binding_patterns:
        for match in pattern.finditer(script_text):
            identity = (clue_type, match.start())
            if identity in seen:
                continue
            seen.add(identity)
            start = max(0, match.start() - 140)
            end = min(len(script_text), match.end() + 180)
            binding_clues.append(
                {
                    "clue_type": clue_type,
                    "context": _safe_excerpt(script_text[start:end]),
                }
            )
            if len(binding_clues) >= 12:
                break
        if len(binding_clues) >= 12:
            break

    return {
        "search_markers": search_markers,
        "query_binding_markers": query_binding_markers,
        "candidate_routes": routes[:50],
        "query_parameter_names": sorted(query_keys),
        "request_call_clues": request_calls,
        "query_binding_clues": binding_clues,
    }


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
                "script_index": len(self.scripts) + 1,
                "src": src or None,
                "resolved_src": urljoin(self.page_url, src) if src else None,
                "type": values.get("type") or None,
                "id": values.get("id") or None,
                "inline_bytes": 0,
                "inline_sha256": None,
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
            self._script.update(_script_analysis(script_text))
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

    inline_scripts = [item for item in parser.scripts if not item.get("src")]
    unique_routes = sorted(
        {
            route
            for item in parser.scripts
            for route in item.get("candidate_routes", [])
        }
    )
    unique_query_keys = sorted(
        {
            key
            for item in parser.scripts
            for key in item.get("query_parameter_names", [])
        }
    )
    request_clue_count = sum(
        len(item.get("request_call_clues", [])) for item in parser.scripts
    )
    binding_clue_count = sum(
        len(item.get("query_binding_clues", [])) for item in parser.scripts
    )

    return {
        "schema_version": 2,
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
        "query_discovery": {
            "candidate_routes": unique_routes,
            "query_parameter_names": unique_query_keys,
            "request_call_clue_count": request_clue_count,
            "binding_clue_count": binding_clue_count,
            "scripts_with_query_markers": [
                item["script_index"]
                for item in inline_scripts
                if item.get("query_binding_markers")
            ],
        },
        "summary": {
            "form_count": len(parser.forms),
            "control_count": len(parser.controls),
            "script_count": len(parser.scripts),
            "inline_script_count": len(inline_scripts),
            "external_script_reference_count": len(parser.scripts) - len(inline_scripts),
            "first_party_script_reference_count": len(first_party_scripts),
            "other_script_reference_count": len(other_scripts),
            "candidate_route_count": len(unique_routes),
            "request_call_clue_count": request_clue_count,
            "binding_clue_count": binding_clue_count,
        },
        "safety": {
            "network_requests_made": False,
            "captured_html_modified": False,
            "javascript_executed": False,
            "hidden_input_values_emitted": False,
            "pdf_attachments_downloaded": False,
            "local_data_lake_access": False,
            "production_dataset_write": False,
            "pit_admitted": False,
            "training_unlocked": False,
            "backtest_admitted": False,
        },
        "limitations": [
            "This is a static inspection of one captured HTML file; "
            "it does not execute JavaScript.",
            "External script references are listed but never fetched.",
            "Candidate routes, request calls and payload keys are "
            "investigative clues, not a verified API contract.",
            "Inline code excerpts are short, bounded and redact common "
            "credential-like values.",
            "A missing form, route or request clue in captured HTML does "
            "not prove that the live page has no search behavior.",
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
