"""HTTP payloads (FR-20, FR-21, FR-23, FR-29): request parsing in, JSON bodies out.

Kept out of `server.py` so the handler stays a transport. The shapes here are what
`docs/07` describes and what `eval/api_contract.py` probes, so a contract claim has
one definition to point at instead of two that can drift.

Parsing is strict on purpose: an unknown field is a 400 rather than something silently ignored,
because a client that misspells `query` should not receive an answer to an empty question. The
`mode` values are the three the retriever actually supports (FR-07), and nothing else. `as_of`
(M21) is an ISO date or absent; a malformed date is a 400, because quietly answering with the
current policy when the caller asked about 2024 is the failure the feature exists to prevent.
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs

from .interfaces import InputError
from .versions import parse_as_of

MODES = ("sparse", "dense", "hybrid")
_ASK_FIELDS = frozenset({"query", "mode", "as_of"})
MAX_BODY_BYTES = 64 * 1024  # a question, not an upload


def parse_get_query(raw_query: str) -> dict:
    """`?q=...&mode=...&as_of=YYYY-MM-DD` - the form the demo page and the runbook use."""
    params = parse_qs(raw_query)
    return {
        "query": params.get("q", [""])[0].strip(),
        "mode": params.get("mode", ["hybrid"])[0],
        "as_of": parse_as_of(params.get("as_of", [""])[0]),
    }


def parse_post_body(body: bytes) -> dict:
    """`{"query": str, "mode": str, "as_of": str}` - the form FR-20 documents for POST."""
    if len(body) > MAX_BODY_BYTES:
        raise InputError(f"body exceeds {MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError as error:
        raise InputError("body must be JSON") from error
    if not isinstance(data, dict):
        raise InputError("body must be a JSON object")
    unknown = sorted(set(data) - _ASK_FIELDS)
    if unknown:
        raise InputError(f"unknown field(s): {', '.join(unknown)}")
    query = data.get("query")
    if not isinstance(query, str):
        raise InputError("query must be a string")
    mode = data.get("mode", "hybrid")
    if mode not in MODES:
        raise InputError(f"mode must be one of {MODES}, not {mode!r}")
    as_of = data.get("as_of", "")
    if not isinstance(as_of, str):
        raise InputError("as_of must be a string (YYYY-MM-DD)")
    return {"query": query.strip(), "mode": mode, "as_of": parse_as_of(as_of)}


def answer_payload(answer, started: float) -> dict:
    """One `Answer` as the documented response body; `timings` always carries four stages."""
    return {
        "query": answer.query,
        "text": answer.text,
        "refused": answer.refused,
        "confidence": answer.confidence,
        "citations": [
            {
                "doc_id": citation.doc_id,
                "chunk_id": citation.chunk_id,
                "title": citation.title,
                "section": citation.section,
                "snippet": citation.snippet,
                # M21: which revision was cited, so a reader can see the date the rule applied
                # from. Both are "" for a document with no recorded effective date.
                "valid_from": citation.valid_from,
                "valid_to": citation.valid_to,
            }
            for citation in answer.citations
        ],
        "mode": answer.mode,
        "degraded": answer.degraded,
        # M25: "hit" or "miss", so a caller (and the cache report) can tell a served answer
        # from a cached one without inferring it from latency.
        "cache": answer.cache,
        # "" when answered; otherwise why, so a client can say the true thing instead of
        # blaming the threshold for a topic the corpus does not carry (M14), or for a rule
        # that only exists in a later revision (M21).
        "refusal_reason": answer.refusal_reason,
        "unseen": list(answer.unseen),
        "as_of": answer.as_of,
        "effective_from": answer.effective_from,
        "trace_id": answer.trace_id,
        "timings": {
            **answer.timings_ms,
            # total_ms is the sum of the stages, so it must not be added again here: doing so
            # made this field negative and quietly lied about the overhead. From M22 the
            # retrieval breakdown is nested, so a sub-stage cannot be counted twice either.
            "serialize_ms": round((time.perf_counter() - started) * 1000.0, 3)
            - _stage_total(answer.timings_ms),
            "total_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
    }


def _stage_total(timings: dict[str, float | dict[str, float]]) -> float:
    """Sum of the top-level numeric stages; `total_ms` and nested breakdowns are excluded."""
    return sum(
        value
        for key, value in timings.items()
        if key != "total_ms" and not isinstance(value, dict)
    )


def error_payload(code: str, message: str) -> dict:
    """FR-23: every non-2xx body is `{"error": {"code", "message"}}`, never a traceback."""
    return {"error": {"code": code, "message": message}}
