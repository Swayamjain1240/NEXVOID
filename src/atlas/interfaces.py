"""The seams stage 2 swaps, the shared value types, and the error tree.

(ATL-SwDD-001 s.1-s.5.) Pipeline code receives the Protocols below through its
constructor; it must never construct a concrete implementation itself, which is
what makes stage 2 a configuration change instead of a rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


class AtlasError(Exception):
    """Base for every expected failure; each subclass owns exactly one HTTP status."""


class InputError(AtlasError):
    """A caller sent something invalid: empty query, bad k. Maps to 400."""


class TenantError(AtlasError):
    """Unknown tenant or a tenant with no documents. Maps to 409/404."""


class ConfigurationError(AtlasError):
    """Bad or missing settings; maps to 500 with a trace_id, details stay in the log."""


class DependencyError(AtlasError):
    """Store or provider unavailable; maps to 503, or degrades with degraded=True."""


@dataclass(frozen=True)
class Passage:
    """A retrieved candidate, before any generation happens."""

    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    score: float
    rank: int
    # M21: the window this passage was in force for. "" at both ends means the document
    # carries no effective date, so it is in force at every date (see versions.in_force).
    valid_from: str = ""
    valid_to: str = ""


@dataclass(frozen=True)
class Citation:
    """A passage the answer leaned on; snippet is bounded so it is safe to render."""

    chunk_id: str
    doc_id: str
    title: str
    section: str
    snippet: str
    valid_from: str = ""
    valid_to: str = ""


@dataclass(frozen=True)
class Answer:
    """The only thing that leaves the pipeline; frozen so nothing downstream mutates it."""

    query: str
    text: str
    citations: tuple[Citation, ...]
    confidence: float
    refused: bool
    mode: str
    degraded: bool
    # Four top-level stages plus, from M22, a nested "retrieval" breakdown of the stage
    # that dominates (embed / bm25 / dense / fuse / rank). The payload keeps them nested so
    # `serialize_ms` cannot double-count a sub-stage as a top-level one.
    timings_ms: dict[str, float | dict[str, float]]
    trace_id: str
    # Why a refusal happened, so a caller can tell "not close enough" from "the corpus never
    # mentions this" - two facts that deserve different words in a UI (M14, FR-16).
    # "" when answered, else "low_confidence", "unknown_terms", "blocked", or
    # "not_yet_in_effect" (M21: the rule existed, but not on the date asked about).
    refusal_reason: str = ""
    unseen: tuple[str, ...] = ()
    # M21: the date the question was asked against ("" = current), and - for a
    # not_yet_in_effect refusal - the date the revision that answers it took effect.
    as_of: str = ""
    effective_from: str = ""


@runtime_checkable
class Embedder(Protocol):
    """Turns texts into unit-norm vectors; MVP is hashed TF-IDF, stage 2 is a real encoder."""

    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


@runtime_checkable
class Retriever(Protocol):
    """tenant_id is required with no default: a caller cannot physically forget it."""

    def search(
        self, query: str, *, tenant_id: str, k: int, mode: str = "hybrid"
    ) -> list[Passage]: ...


@runtime_checkable
class Generator(Protocol):
    """Passages in, cited text out; every sentence must be supported by a cited passage."""

    name: str

    def generate(self, query: str, passages: Sequence[Passage]) -> tuple[str, list[Citation]]: ...
