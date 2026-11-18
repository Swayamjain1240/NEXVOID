"""Phrase-level novelty (M17, FR-17): does the corpus contain these word *pairs* at all?

M14's signal asks whether each of a question's content terms occurs somewhere in the corpus.
`results/support.md` records exactly where that stops working: v3's sports-scholarship question
uses three words the corpus knows ("sports", "scholarship", "attendance") and scores 0.000
novelty, so a question about a scholarship that does not exist is answered from a document about
a different one.

A word can be attested while the *claim* it names is not. The corpus says "merit scholarship" and
"need-based scholarship"; it never says "sports scholarship". So this module asks the same
question one level up: how many of the question's adjacent content-word pairs occur nowhere in the
corpus?

Three properties matter, and they are the same three that made the word signal worth having:

- it is corpus-level, never a function of the retrieved passage or the embedding;
- it counts absence only, so a passage that scores high on cosine cannot talk it out of a refusal;
- the pair order follows the question, so "scholarship attendance" is not satisfied by a document
  that contains both words apart.

The bias is the same too, one level up: ordinary policy paraphrases join words the corpus joins
rarely ("need aggregate", "days timings"), so the floor is fitted on v1 and the decision is read off
a set written afterwards, never the reverse.
"""

from __future__ import annotations

from collections.abc import Collection

from .support import QUESTION_WORDS
from .text.tokenize import tokenize

# Two unseen pairs, not one: a single unusual adjective-noun join is not a missing policy.
MIN_UNSEEN_PHRASES = 2


def content_bigrams(query: str) -> tuple[str, ...]:
    """Adjacent content-term pairs of the question, in question order, de-duplicated.

    Question words are dropped *before* pairing, exactly as the word signal drops them, so
    "what is the hostel curfew" yields `hostel curfew` rather than `hostel what`.
    """
    terms = [term for term in tokenize(query) if term not in QUESTION_WORDS]
    return tuple(sorted({f"{left} {right}" for left, right in zip(terms, terms[1:], strict=False)}))


def unseen_phrases(query: str, phrases: Collection[str]) -> tuple[str, ...]:
    """The question's content pairs that occur nowhere in the tenant's corpus, sorted."""
    return tuple(phrase for phrase in content_bigrams(query) if phrase not in phrases)


def novelty(query: str, phrases: Collection[str]) -> float:
    """Share of the question's content pairs the corpus has never contained, in [0, 1]."""
    pairs = content_bigrams(query)
    if not pairs:
        return 0.0
    return round(sum(1 for pair in pairs if pair not in phrases) / len(pairs), 4)


def refuses(*, novel: float, unseen: int, floor: float) -> bool:
    """The added refusal: a question whose word pairs the corpus does not contain at all.

    `floor` of 0.0 disables the signal, which is the default until a floor is adopted on
    committed evidence (`results/phrase.md`).
    """
    return floor > 0.0 and unseen >= MIN_UNSEEN_PHRASES and novel >= floor
