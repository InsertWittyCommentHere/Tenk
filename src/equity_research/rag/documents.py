"""Parse filing HTML into clean, sectioned text.

10-K/10-Q documents are large HTML files. We:
  1. strip tags to readable text (preserving paragraph breaks),
  2. split into the canonical SEC "Item" sections so retrieval can be filtered
     to, e.g., just Risk Factors or just MD&A.

Section detection is heuristic (regex on "Item N." headers) — robust enough for
the standardized 10-K structure, and the raw full text is always retained as a
fallback section so nothing is lost if a header isn't matched.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup

try:  # EDGAR docs are often XHTML; silence bs4's XML-as-HTML advisory.
    from bs4 import XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:  # pragma: no cover
    pass

# Canonical 10-K item headers we care about (label shown to users).
ITEM_PATTERNS: list[tuple[str, str]] = [
    ("Item 1. Business", r"item\s+1\.?\s+business"),
    ("Item 1A. Risk Factors", r"item\s+1a\.?\s+risk\s+factors"),
    ("Item 3. Legal Proceedings", r"item\s+3\.?\s+legal\s+proceedings"),
    ("Item 5. Market for Registrant's Equity", r"item\s+5\.?\s+market\s+for"),
    ("Item 7. MD&A", r"item\s+7\.?\s+management.s\s+discussion"),
    ("Item 7A. Market Risk", r"item\s+7a\.?\s+quantitative"),
    ("Item 8. Financial Statements", r"item\s+8\.?\s+financial\s+statements"),
]


@dataclass
class Section:
    label: str
    text: str
    start: int  # char offset within the cleaned full text


def parse_filing_html(html: str) -> str:
    """Convert filing HTML to clean text with sane whitespace."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of whitespace; keep paragraph structure.
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def extract_sections(text: str) -> list[Section]:
    """Split cleaned filing text into canonical Item sections.

    We find the *last* occurrence of each item header (the body, not the table of
    contents) and slice between consecutive headers.
    """
    hits: list[tuple[int, str]] = []
    lower = text.lower()
    for label, pat in ITEM_PATTERNS:
        matches = list(re.finditer(pat, lower))
        if matches:
            # Prefer the last match (TOC entries appear earlier than the real section).
            hits.append((matches[-1].start(), label))
    hits.sort()

    if not hits:
        return [Section(label="Full document", text=text, start=0)]

    sections: list[Section] = []
    for i, (start, label) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        body = text[start:end].strip()
        if len(body) > 200:  # skip empty/near-empty slices
            sections.append(Section(label=label, text=body, start=start))
    return sections or [Section(label="Full document", text=text, start=0)]
