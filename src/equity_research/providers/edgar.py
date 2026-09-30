"""SEC EDGAR provider — the data spine.

Implements `FilingsProvider` against the free, authoritative SEC APIs on
`data.sec.gov`. Key compliance/operational rules baked in here:

  * A descriptive `User-Agent` (with contact email) is **required** by the SEC.
  * SEC asks for <= ~10 requests/second; we self-throttle and retry politely.
  * Responses are cached on disk so we never re-hammer the SEC for the same
    payload and so ingestion is reproducible/point-in-time.

Endpoints used:
  * submissions:   https://data.sec.gov/submissions/CIK##########.json
  * companyfacts:  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  * companyconcept:https://data.sec.gov/api/xbrl/companyconcept/CIK##########/{tax}/{concept}.json
  * frames:        https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{unit}/{period}.json
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from equity_research.models import Fact, FactSeries, SubmissionsIndex
from equity_research.models.filings import Filing
from equity_research.models.provenance import Provenance

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{tax}/{concept}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{unit}/{period}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Instant (point-in-time) facts have no "start"; these unit names are non-monetary.
_NON_DURATION_HINT = ("shares", "pure")


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


class RateLimiter:
    """Simple thread-safe minimum-interval limiter (SEC asks for <= ~10 req/s)."""

    def __init__(self, max_per_second: float = 8.0) -> None:
        self._min_interval = 1.0 / max_per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._min_interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


class EdgarProvider:
    """Concrete `FilingsProvider` for SEC EDGAR."""

    name = "SEC EDGAR"
    redistributable = True  # SEC filings are public-domain government records.

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path | str | None = None,
        max_per_second: float = 8.0,
        timeout: float = 30.0,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "SEC requires a descriptive User-Agent including a contact email, e.g. "
                "'Equity Research yourname@example.com'."
            )
        self._limiter = RateLimiter(max_per_second)
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
        )

    # -- HTTP / caching -----------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if not self._cache_dir:
            return None
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self._cache_dir / f"{key}.json"

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
    )
    def _get_json(self, url: str, *, use_cache: bool = True) -> dict[str, Any]:
        cache = self._cache_path(url) if use_cache else None
        if cache and cache.exists():
            return json.loads(cache.read_text())
        self._limiter.wait()
        resp = self._client.get(url)
        # 404 means "no such filer/concept" — surface it without retrying forever.
        if resp.status_code == 404:
            raise FileNotFoundError(f"EDGAR returned 404 for {url}")
        resp.raise_for_status()
        data = resp.json()
        if cache:
            cache.write_text(json.dumps(data))
        return data

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
    )
    def fetch_document(self, url: str, *, use_cache: bool = True) -> str:
        """Fetch a filing document (HTML/text) from EDGAR Archives, cached on disk."""
        cache = self._cache_path(url + "#doc") if (use_cache and self._cache_dir) else None
        if cache and cache.exists():
            return cache.read_text(encoding="utf-8", errors="ignore")
        self._limiter.wait()
        resp = self._client.get(url)
        if resp.status_code == 404:
            raise FileNotFoundError(f"EDGAR returned 404 for {url}")
        resp.raise_for_status()
        text = resp.text
        if cache:
            cache.write_text(text, encoding="utf-8", errors="ignore")
        return text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EdgarProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- FilingsProvider contract -------------------------------------------

    def get_submissions(self, cik: int) -> SubmissionsIndex:
        url = SUBMISSIONS_URL.format(cik=cik)
        data = self._get_json(url)
        recent = data.get("filings", {}).get("recent", {})
        filings = self._parse_recent_filings(cik, recent)
        fye = data.get("fiscalYearEnd")
        return SubmissionsIndex(
            cik=cik,
            name=data.get("name", ""),
            tickers=data.get("tickers", []) or [],
            exchanges=data.get("exchanges", []) or [],
            sic=data.get("sic"),
            sic_description=data.get("sicDescription"),
            fiscal_year_end=fye,
            filings=filings,
        )

    @staticmethod
    def _parse_recent_filings(cik: int, recent: dict[str, list]) -> list[Filing]:
        # The "recent" block is column-oriented (parallel arrays). Zip them into rows.
        accns = recent.get("accessionNumber", [])
        out: list[Filing] = []
        for i, accn in enumerate(accns):
            def col(name: str) -> Any:
                arr = recent.get(name, [])
                return arr[i] if i < len(arr) else None

            filed = _parse_date(col("filingDate"))
            if filed is None:
                continue
            out.append(
                Filing(
                    cik=cik,
                    accession=accn,
                    form=col("form") or "",
                    filed_date=filed,
                    report_date=_parse_date(col("reportDate")),
                    primary_document=col("primaryDocument") or None,
                    primary_doc_description=col("primaryDocDescription") or None,
                    is_xbrl=bool(col("isXBRL")),
                    size=col("size"),
                )
            )
        return out

    def get_facts(self, cik: int) -> list[Fact]:
        url = COMPANYFACTS_URL.format(cik=cik)
        data = self._get_json(url)
        facts: list[Fact] = []
        for taxonomy, concepts in data.get("facts", {}).items():
            for concept, body in concepts.items():
                for unit, rows in body.get("units", {}).items():
                    for row in rows:
                        fact = self._row_to_fact(cik, taxonomy, concept, unit, row, source_url=url)
                        if fact is not None:
                            facts.append(fact)
        return facts

    def get_concept(self, cik: int, taxonomy: str, concept: str) -> FactSeries:
        url = CONCEPT_URL.format(cik=cik, tax=taxonomy, concept=concept)
        data = self._get_json(url)
        facts: list[Fact] = []
        unit_seen = ""
        for unit, rows in data.get("units", {}).items():
            unit_seen = unit
            for row in rows:
                f = self._row_to_fact(cik, taxonomy, concept, unit, row, source_url=url)
                if f is not None:
                    facts.append(f)
        return FactSeries(metric=concept, concept=concept, unit=unit_seen, facts=facts)

    def resolve_ticker(self, ticker: str) -> int | None:
        """Map a ticker symbol to a SEC CIK via the official ticker map. None if not a US filer."""
        data = self._get_json(TICKERS_URL)
        want = ticker.upper().strip()
        # The map is {index: {"cik_str": int, "ticker": str, "title": str}}.
        for row in data.values():
            if str(row.get("ticker", "")).upper() == want:
                return int(row["cik_str"])
        return None

    def get_frame(
        self, taxonomy: str, concept: str, unit: str, period: str
    ) -> list[dict[str, Any]]:
        """A single concept across ALL filers for one period — for instant peer comps."""
        url = FRAMES_URL.format(tax=taxonomy, concept=concept, unit=unit, period=period)
        data = self._get_json(url)
        return data.get("data", [])

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def _row_to_fact(
        cik: int,
        taxonomy: str,
        concept: str,
        unit: str,
        row: dict[str, Any],
        *,
        source_url: str,
    ) -> Fact | None:
        end = _parse_date(row.get("end"))
        if end is None or "val" not in row:
            return None
        start = _parse_date(row.get("start"))
        is_instant = start is None
        accn = row.get("accn")
        filed = _parse_date(row.get("filed"))
        form = row.get("form")
        doc_url = None
        if accn:
            doc_url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik}&type={form or ''}"
            )
        prov = Provenance(
            source="SEC EDGAR",
            url=doc_url or source_url,
            accession=accn,
            form=form,
            filed_date=filed,
        )
        return Fact(
            cik=cik,
            taxonomy=taxonomy,
            concept=concept,
            unit=unit,
            value=float(row["val"]),
            period_start=start,
            period_end=end,
            is_instant=is_instant,
            fiscal_year=row.get("fy"),
            fiscal_period=row.get("fp"),
            frame=row.get("frame"),
            form=form,
            accession=accn,
            filed_date=filed,
            provenance=prov,
        )
