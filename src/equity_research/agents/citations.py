"""Deterministic citation-coverage gate.

A core DESIGN.md rule: no material number reaches a user without a verifiable
source. This module enforces it *without* an LLM — it scans generated text for
material numeric claims (dollar amounts, percentages, per-share values, large
magnitudes) and checks that each appears alongside a citation marker (an SEC
accession number or a `[10-K ...]`-style tag).

It is intentionally conservative about what counts as a "material" number so it
doesn't flag incidental figures ("10-year window", "3 scenarios"). The result is
advisory by default; the Supervisor can treat low coverage as a blocking gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A material financial number: $amounts, magnitudes, percentages, per-share values.
_MATERIAL_NUMBER = re.compile(
    r"""(
        \$\s?\d[\d,]*\.?\d*           # $1,234.5  or  $421
        | \d+\.?\d*\s?(?:billion|million|B|M)\b   # 69.7B, 5 million
        | \d+\.?\d*\s?%               # 25%, 11.0%
        | \d+\.?\d*\s?/\s?sh\b        # 421/sh
    )""",
    re.VERBOSE | re.IGNORECASE,
)

# A citation marker: an SEC accession, or a form tag like [10-K ...] / "10-K filed".
_CITATION = re.compile(
    r"\d{10}-\d{2}-\d{6}|\b10-[KQ]\b|\b8-K\b|\bDEF\s?14A\b|filed\s+\d{4}-\d{2}-\d{2}",
    re.IGNORECASE,
)

# Numbers that look material by regex but are usually narrative, not financial claims.
_NARRATIVE_CONTEXT = re.compile(
    r"\b(year|years|yr|scenario|scenarios|quarter|step|steps)\b", re.IGNORECASE
)


def _split_sentences(text: str) -> list[str]:
    # Naive but adequate: split on sentence punctuation followed by whitespace.
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


@dataclass
class CitationReport:
    total_claims: int
    cited_claims: int
    uncited_samples: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return 1.0 if self.total_claims == 0 else self.cited_claims / self.total_claims

    def passes(self, threshold: float = 0.8) -> bool:
        return self.coverage >= threshold

    def summary(self) -> str:
        pct = self.coverage * 100
        return (
            f"citation coverage {pct:.0f}% ({self.cited_claims}/{self.total_claims} "
            f"numeric claims cited)"
        )


def check_citations(text: str) -> CitationReport:
    """Scan text and report what fraction of material numeric claims carry a citation.

    A "claim" is a sentence containing at least one material number. The sentence
    is considered cited if it (or its immediate context) contains a citation marker.
    """
    sentences = _split_sentences(text)
    total = 0
    cited = 0
    uncited: list[str] = []
    for i, s in enumerate(sentences):
        numbers = _MATERIAL_NUMBER.findall(s)
        if not numbers:
            continue
        # Skip sentences whose numbers are clearly narrative (e.g. "10-year window").
        if _NARRATIVE_CONTEXT.search(s) and not re.search(r"\$|%|/sh", s):
            continue
        total += 1
        # A claim is cited if its sentence — or the next sentence (citations sometimes
        # trail) — contains a citation marker.
        window = s + (" " + sentences[i + 1] if i + 1 < len(sentences) else "")
        if _CITATION.search(window):
            cited += 1
        else:
            uncited.append(s if len(s) <= 200 else s[:197] + "…")
    return CitationReport(total_claims=total, cited_claims=cited, uncited_samples=uncited[:5])
