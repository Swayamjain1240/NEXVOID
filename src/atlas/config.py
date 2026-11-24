"""Every tunable number in the system lives here (ATL-SwDD-001 s.2, s.8).

If a value is typed inline anywhere else, that is a review rejection: the eval
sweeps in M3 can only move numbers they can see.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_number(name: str, default, cast):
    """An env var that must parse: a typo refuses to start, with the variable named."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, not {raw!r}") from error


@dataclass(frozen=True)
class Settings:
    """Frozen container; a running process never mutates configuration."""

    # chunking (ATL-TDD-001 s.2)
    chunk_tokens: int = 180
    overlap_paragraphs: int = 1

    # sparse (ATL-TDD-001 s.3)
    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    # dense stand-in (ATL-TDD-001 s.4)
    dense_dim: int = 1024
    embedder_name: str = "hashed-tfidf-d1024"

    # stage-2 trained encoder (ATL-TDD-001 s.10). Opt-in, because C-3/NFR-17 make the
    # offline hashed stand-in the default: ATLAS_EMBEDDER=potion-base-8M selects the real
    # one. results/embeddings.md is the only place the swap is allowed to argue.
    embedder_kind: str = os.environ.get("ATLAS_EMBEDDER", "hashed-tfidf-d1024")
    trained_model: str = "minishlab/potion-base-8M"
    trained_dim: int = 256
    embedder_cache_dir: str = "data/embedder_cache"  # vectors, never committed

    # fusion + rerank (ATL-TDD-001 s.5-s.6); rrf_k is swept by the ablation
    rrf_k: int = 60
    rrf_k_candidates: tuple[int, ...] = (10, 60, 100)
    # M16: each retriever votes only with documents it would itself return. Fusing pools of
    # 50 let items outside either list's top 10 collect two weak votes and displace a strong
    # single-retriever hit (v1 a009, results/fusion.md). 0 disables the truncation.
    fusion_depth: int = 10
    # per-retriever evidence weights in the fusion blend; 1.0/1.0 is classic RRF,
    # and the stage-2 ablation (results/embeddings.md) is the only thing that may move them
    rrf_weight_sparse: float = 1.0
    rrf_weight_dense: float = 1.0
    rerank_weight_fuse: float = 0.40
    rerank_weight_coverage: float = 0.40
    rerank_weight_bigram: float = 0.15
    rerank_weight_title: float = 0.05
    # M8: "learned" loads committed weights trained on a document-held-out split, but the
    # loader only honours a file marked "adopted": true, so an experiment cannot ship by
    # accident. "blend" is the MVP hand weighting and the fallback in every other case.
    # M16: the shipped default is fusion order. `results/ablation.md` measures the blend as a
    # -0.0416 recall change once the dense side carries meaning, and `results/rerank.md` refused
    # the learned weights out of sample; `results/fusion.md` re-measures all three on three sets.
    # "none" keeps fusion order; "blend" and "learned" stay available and measured.
    rerank_kind: str = "none"
    rerank_model_path: str = "models/rerank-v1.json"
    # eval-only switch, set by eval/ablation.py to score the *unadopted* weights in place.
    # Production stays false, so the adoption gate in build_reranker is never bypassed by
    # a config edit; the ablation has to ask for it by name.
    rerank_allow_unadopted: bool = False

    # candidate pools (ATL-TDD-001 s.1)
    sparse_candidates: int = 50
    dense_candidates: int = 50
    fused_candidates: int = 50
    rerank_candidates: int = 20

    # confidence gate (ATL-TDD-001 s.7): confidence = 0.65*cosine + 0.35*coverage.
    # 0.13 is the lowest false-accept-free theta (results/calibration.md, M4, in-sample,
    # safety-first: wrong citations refuse-shipped; answer rate recovers at stage 2).
    # theta_candidates is the harness sweep; theta changes only on committed evidence.
    confidence_theta: float = 0.13
    confidence_cosine_weight: float = 0.65
    confidence_coverage_weight: float = 0.35
    theta_candidates: tuple[float, ...] = (0.20, 0.25, 0.30, 0.35, 0.40)

    # theta is a property of the SCORE SCALE, so each embedder carries its own
    # calibrated knee (results/embeddings.md, M7). Sharing one number across two
    # embedders would silently compare two different rulers.
    confidence_theta_trained: float = 0.31

    # Second refusal signal (M14, results/support.md): refuse when at least this share of the
    # question's content terms occurs nowhere in the tenant's corpus, and at least two of them
    # do. Fitted on v1 (the highest novelty among its answerable questions is 0.40, so 0.50 is
    # the tightest floor that refuses none of them) and judged on v3, where it moved refusal
    # accuracy 0.5000 -> 0.6667 without refusing a single answerable question. 0.0 disables it.
    refusal_novelty_floor: float = 0.50
    theta_candidates_trained: tuple[float, ...] = (
        0.20,
        0.25,
        0.28,
        0.30,
        0.31,
        0.33,
        0.35,
        0.38,
        0.40,
    )

    # answers (ATL-TDD-001 s.8)
    max_answer_sentences: int = 4
    max_citations: int = 5
    citation_snippet_chars: int = 320

    # M23 admission control (NFR-01). `max_inflight` is deliberately above the 10-user
    # operating point so the measured target never queues: the bound exists to stop
    # *overload* from becoming everyone's latency, not to throttle normal traffic.
    # Beyond it, a request may wait `admission_wait_ms` and is then rejected as 503.
    # default_factory, not a plain default: a dataclass evaluates a default once at import,
    # which would freeze the environment before a test (or a benchmark) could change it.
    max_inflight: int = field(
        default_factory=lambda: _env_number("ATLAS_MAX_INFLIGHT", 32, int)
    )
    max_waiting: int = field(
        default_factory=lambda: _env_number("ATLAS_MAX_WAITING", 64, int)
    )
    admission_wait_ms: float = field(
        default_factory=lambda: _env_number("ATLAS_ADMISSION_WAIT_MS", 25.0, float)
    )

    # Benchmark-only fault injection (M23): pad every *answered* request so the admission bound
    # is reachable on one laptop, where ~2 ms answers never queue. The 503 path skips it, which
    # is the point - a shed request must not pay the cost of the work it was refused.
    slow_ms: float = field(default_factory=lambda: _env_number("ATLAS_SLOW_MS", 0.0, float))

    # M22 ablation arm: `ATLAS_PERF_NAIVE=1` restores the pre-M22 code path (the gate
    # re-tokenises the winning passage, and the query is embedded twice) so
    # `bench/perf.py` can measure both arms in one run, on one machine, with identical
    # scores. Production is always the fast arm; nothing selects this but the benchmark.
    perf_naive: bool = os.environ.get("ATLAS_PERF_NAIVE", "") == "1"

    # persistence
    default_db_path: str = "data/atlas.db"
    default_tenant: str = "campus"

    # degraded mode (NFR-16): consecutive generation failures before the breaker opens
    breaker_threshold: int = 2
    breaker_cooldown_s: float = 30.0

    # API (M5): the token comes from the environment, never from the repo
    api_token: str = os.environ.get("ATLAS_API_TOKEN", "")
    serve_host: str = "127.0.0.1"
    serve_port: int = 8070

    @property
    def trained(self) -> bool:
        """True when a trained encoder is selected instead of the hashed stand-in."""
        return self.embedder_kind not in (self.embedder_name, "hashed")

    @property
    def theta(self) -> float:
        """The calibrated gate threshold for the selected embedder's score scale."""
        return self.confidence_theta_trained if self.trained else self.confidence_theta

    @property
    def theta_grid(self) -> tuple[float, ...]:
        """Sweep grid matching the selected scale (harness and calibration read this)."""
        return self.theta_candidates_trained if self.trained else self.theta_candidates


SETTINGS = Settings()
