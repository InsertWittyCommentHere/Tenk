"""Tests for the deterministic citation-coverage gate."""

from equity_research.agents.citations import check_citations


def test_fully_cited_answer_passes():
    text = (
        "Revenue was $69.7B in FY2025 [10-K 0001467373-25-000217]. "
        "Net income reached $7.68B, also per the 10-K filed 2025-10-10."
    )
    rep = check_citations(text)
    assert rep.total_claims == 2
    assert rep.cited_claims == 2
    assert rep.coverage == 1.0
    assert rep.passes()


def test_uncited_number_is_flagged():
    text = (
        "Revenue was $69.7B [10-K 0001467373-25-000217]. "
        "I think the stock is worth $500 per share."
    )
    rep = check_citations(text)
    assert rep.total_claims == 2
    assert rep.cited_claims == 1
    assert not rep.passes()
    assert any("500" in s for s in rep.uncited_samples)


def test_trailing_citation_in_next_sentence_counts():
    # Citation sometimes trails the claim sentence.
    text = "Operating margin was 14.7%. Source: 10-K 0001467373-25-000217."
    rep = check_citations(text)
    assert rep.cited_claims == 1
    assert rep.passes()


def test_narrative_numbers_not_counted():
    # "10-year window" and "3 scenarios" are not financial claims.
    text = "We used a 10-year DCF window across 3 scenarios to frame the analysis."
    rep = check_citations(text)
    assert rep.total_claims == 0
    assert rep.coverage == 1.0  # vacuously passes


def test_no_numbers_passes_vacuously():
    rep = check_citations("Accenture is a consulting firm with a diversified client base.")
    assert rep.total_claims == 0
    assert rep.passes()


def test_percentage_claims_detected():
    text = "ROE was 24.6% and net margin 11.0% with no citation anywhere."
    rep = check_citations(text)
    assert rep.total_claims >= 1
    assert rep.cited_claims == 0
    assert not rep.passes()


def test_summary_renders():
    rep = check_citations("Revenue $69.7B [10-K 0001467373-25-000217].")
    assert "citation coverage" in rep.summary()
