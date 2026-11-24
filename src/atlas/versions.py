"""Effective dates and validity windows (M21, FR-29/FR-30).

A chunk is in force over a closed date range: `valid_from` is the day the revision took
effect, `valid_to` the last day it applied. Both are ISO dates (`YYYY-MM-DD`) so they
compare as plain strings, and `""` means *unbounded* - an unversioned document is in force
at every date, which is what "we never recorded an effective date" should mean.

Nothing here touches SQLite or retrieval: the window arithmetic is a leaf so both the store
and the ranking layer can share one definition of "in force", and a test can pin it without
a database.
"""

from __future__ import annotations

from datetime import date, timedelta

from .interfaces import InputError

# "" is the unbounded end of a window, on either side. It sorts before every ISO date,
# which is exactly the behaviour a missing effective date should have.
UNBOUNDED = ""
_FIELD = "as_of"


def parse_date(value: str, *, field: str = _FIELD) -> str:
    """Validate one ISO date, or raise InputError (HTTP 400) naming the field.

    Strict on purpose: `2025-13-01` and `01/02/2025` are rejected rather than coerced,
    because a silently reinterpreted date is an answer about the wrong policy.
    """
    text = (value or "").strip()
    if not text:
        return UNBOUNDED
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise InputError(f"{field} must be an ISO date (YYYY-MM-DD), not {value!r}") from error


def parse_as_of(value: str | None) -> str:
    """`as_of` as a validated ISO date, or "" when the caller did not ask about a date."""
    return parse_date(value or "", field="as_of")


def previous_day(iso: str) -> str:
    """The day before `iso`; the last day a superseded revision was in force."""
    if not iso:
        raise InputError("cannot close a window that has no effective date")
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def in_force(valid_from: str, valid_to: str, as_of: str) -> bool:
    """Is a chunk in force on `as_of`? An empty `as_of` means "now", i.e. every version."""
    if not as_of:
        return True
    return (not valid_from or valid_from <= as_of) and (not valid_to or valid_to >= as_of)


def window(valid_from: str, valid_to: str) -> str:
    """Human label for a citation: `2025-01-01..2024-12-31` style, `open` when unbounded."""
    return f"{valid_from or 'start'}..{valid_to or 'open'}"


def effective_from(valid_from: str) -> str:
    """What to tell a caller whose question is only answered by a later revision."""
    return valid_from or "an unrecorded date"


def later_revision(report, *, as_of: str, theta: float) -> str:
    """The date a revision that *could* answer took effect, when it post-dates `as_of` (FR-30).

    `report` is an unmasked `(passage, components)` pair - the same retrieval run without the
    date filter. Returning "" means "this is an ordinary refusal": either the best evidence is
    already in force, or it would not have cleared the gate even undated. Pure and total, so
    the answer path can call it without a second retrieval abstraction.
    """
    if not report or not as_of:
        return ""
    passage, parts = report
    if not passage.valid_from or passage.valid_from <= as_of:
        return ""
    return passage.valid_from if parts["confidence"] >= theta else ""
