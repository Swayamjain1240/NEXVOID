"""M21: revision windows, as_of filtering, and the NOT_YET_IN_EFFECT refusal (FR-29, FR-30).

Three revisions of one policy, dated 2025-01-01 and 2026-07-01, over an undated base. The
assertions are about *which revision* answers, not about answer text: a dated answer that
quotes the right sentence from the wrong revision is exactly the failure this milestone is
about, and it would pass a text-only test.
"""

from __future__ import annotations

import dataclasses

import pytest

from atlas import api
from atlas.answer import answer_query
from atlas.config import SETTINGS
from atlas.interfaces import InputError
from atlas.retrieval import HybridIndex
from atlas.store import Store
from atlas.versions import in_force, parse_as_of, previous_day

BASE = (
    "# Attendance Policy\n\n"
    "## Minimum attendance\n\n"
    "Every student must keep 75 percent attendance in each course of the semester. "
    "Below the bar the student is detained and cannot sit the end term examination.\n\n"
    "## Condonation\n\n"
    "Condonation between 65 and 74 percent is available only for a certified medical "
    "reason, and is decided by the faculty together with the head of department.\n"
)
REV_2025 = BASE.replace("75 percent", "80 percent").replace("65 and 74", "70 and 79")
REV_2026 = (
    REV_2025.replace("80 percent", "85 percent").replace("70 and 79", "75 and 84")
    + "\n## Laboratory courses\n\nLaboratory courses require ninety percent attendance and "
    "that requirement cannot be condoned under any circumstances whatsoever.\n"
)


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def versioned_store(tmp_path):
    """One document with three revisions; the later two arrive with effective dates."""
    store = Store(tmp_path / "temporal.db")
    store.upsert_document(
        tenant_id="campus", path=_write(tmp_path, "attendance-policy.md", BASE)
    )
    store.upsert_document(
        tenant_id="campus",
        path=_write(tmp_path, "attendance-policy@2025-01-01.md", REV_2025),
        effective="2025-01-01",
        doc_id="attendance-policy",
    )
    store.upsert_document(
        tenant_id="campus",
        path=_write(tmp_path, "attendance-policy@2026-07-01.md", REV_2026),
        effective="2026-07-01",
        doc_id="attendance-policy",
    )
    yield store
    store.close()


def test_an_unbounded_window_is_in_force_at_every_date():
    assert in_force("", "", "2024-06-01")
    assert in_force("2025-01-01", "", "2024-06-01") is False
    assert in_force("", "2024-12-31", "2025-01-01") is False
    # the window is closed on both ends: the last day still applies
    assert in_force("2020-01-01", "2024-12-31", "2024-12-31")


def test_previous_day_handles_month_and_year_boundaries():
    assert previous_day("2025-01-01") == "2024-12-31"
    assert previous_day("2024-03-01") == "2024-02-29"  # leap year, not a 28


def test_as_of_parsing_is_strict_about_shape():
    assert parse_as_of("") == ""
    assert parse_as_of("2025-06-01") == "2025-06-01"
    for bad in ("2025-13-01", "01/06/2025", "yesterday"):
        with pytest.raises(InputError):
            parse_as_of(bad)
    # a date is validated before retrieval, so a bad one never silently means "now"
    with pytest.raises(InputError):
        api.parse_get_query("q=attendance&as_of=2025-13-01")


def test_a_revision_closes_the_previous_window(versioned_store):
    revisions = versioned_store.revisions(tenant_id="campus", doc_id="attendance-policy")
    assert [start for start, _, _ in revisions] == ["", "2025-01-01", "2026-07-01"]
    assert [end for _, end, _ in revisions] == ["2024-12-31", "2026-06-30", ""]
    assert all(count > 0 for _, _, count in revisions)


def test_back_dating_an_open_version_is_refused(versioned_store, tmp_path):
    """A late-arriving older revision would change answers already given, so it is an error."""
    older = _write(tmp_path, "attendance-policy@2024-01-01.md", BASE.replace("75", "70"))
    with pytest.raises(InputError):
        versioned_store.upsert_document(
            tenant_id="campus",
            path=older,
            effective="2024-01-01",
            doc_id="attendance-policy",
        )


def test_as_of_returns_the_text_in_force_on_that_date(versioned_store):
    index = HybridIndex(versioned_store)
    query = "minimum attendance required in each course"
    assert "75 percent" in index.search(query, tenant_id="campus", k=1, as_of="2024-06-01")[0].text
    assert "80 percent" in index.search(query, tenant_id="campus", k=1, as_of="2025-06-01")[0].text
    assert "85 percent" in index.search(query, tenant_id="campus", k=1, as_of="2026-09-01")[0].text
    # no date means the newest revision, which is the pre-M21 behaviour
    assert "85 percent" in index.search(query, tenant_id="campus", k=1)[0].text


def test_no_passage_ever_post_dates_the_question(versioned_store):
    """FR-29, stated as a property: the filter is inside both retrievers, so nothing leaks."""
    index = HybridIndex(versioned_store)
    for as_of in ("2024-06-01", "2025-06-01", "2026-09-01"):
        for mode in ("sparse", "dense", "hybrid"):
            passages = index.search(
                "attendance condonation laboratory", tenant_id="campus", k=10, mode=mode,
                as_of=as_of,
            )
            assert all(not p.valid_from or p.valid_from <= as_of for p in passages)


def test_a_rule_that_only_exists_later_refuses_and_names_the_date(versioned_store):
    index = HybridIndex(versioned_store)
    query = "What attendance do laboratory courses require?"
    early = answer_query(index, query, tenant_id="campus", as_of="2024-06-01")
    assert early.refused
    assert early.refusal_reason == "not_yet_in_effect"
    assert early.effective_from == "2026-07-01"
    # and the same question is answered once the revision is in force
    late = answer_query(index, query, tenant_id="campus", as_of="2026-09-01")
    assert not late.refused
    assert "ninety" in late.text
    assert late.citations[0].valid_from == "2026-07-01"


def test_payload_carries_the_date_and_the_citation_window(versioned_store):
    # the gate is irrelevant here, so it is opened rather than argued with: this test is about
    # which window the citation carries, not about whether the passage clears theta
    open_gate = dataclasses.replace(SETTINGS, confidence_theta=0.0)
    index = HybridIndex(versioned_store)
    answer = answer_query(
        index, "minimum attendance required in each course", tenant_id="campus",
        as_of="2025-06-01", settings=open_gate,
    )
    payload = api.answer_payload(answer, 0.0)
    assert payload["as_of"] == "2025-06-01"
    assert payload["effective_from"] == ""
    assert payload["citations"][0]["valid_from"] == "2025-01-01"
    assert payload["citations"][0]["valid_to"] == "2026-06-30"
