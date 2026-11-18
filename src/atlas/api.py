"""HTTP payloads (FR-20, FR-21, FR-23): request parsing in, JSON bodies out.

Kept out of `server.py` so the handler stays a transport. The shapes here are what
`docs/07` describes and what `scripts/../eval/api_contract.py` probes, so a contract claim has
one definition to point at instead of two that can drift.

Parsing is strict on purpose: an unknown field is a 400 rather than something silently ignored,
because a client that misspells `query` should not receive an answer to an empty question. The
`mode` values are the three the retriever actually supports (FR-07), and nothing else.
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs

from .interfaces import InputError

MODES = ("sparse", "dense", "hybrid")
_ASK_FIELDS = frozenset({"query", "mode"})
MAX_BODY_BYTES = 64 * 1024  # a question, not an upload


def parse_get_query(raw_query: str) -> dict:
    """`?q=...&mode=...` - the form the demo page and the runbook use."""
    params = parse_qs(raw_query)
    return {
        "query": params.get("q", [""])[0].strip(),
        "mode": params.get("mode", ["hybrid"])[0],
    }


def parse_post_body(body: bytes) -> dict:
    """`{"query": str, "mode": str}` - the form FR-20 documents for `POST /v1/ask`."""
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
    return {"query": query.strip(), "mode": mode}


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
            }
            for citation in answer.citations
        ],
        "mode": answer.mode,
        "degraded": answer.degraded,
        # "" when answered; otherwise why, so a client can say the true thing instead of
        # blaming the threshold for a topic the corpus does not carry (M14).
        "refusal_reason": answer.refusal_reason,
        "unseen": list(answer.unseen),
        "trace_id": answer.trace_id,
        "timings": {
            **answer.timings_ms,
            # total_ms is the sum of the stages, so it must not be added again here: doing so
            # made this field negative and quietly lied about the overhead.
            "serialize_ms": round((time.perf_counter() - started) * 1000.0, 3)
            - sum(value for key, value in answer.timings_ms.items() if key != "total_ms"),
            "total_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
    }


def error_payload(code: str, message: str) -> dict:
    """FR-23: every non-2xx body is `{"error": {"code", "message"}}`, never a traceback."""
    return {"error": {"code": code, "message": message}}
