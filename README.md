# Equity Research — AI Investment Research Platform

Production-grade AI investment research, starting with **Accenture (ACN)** and
architected so any public company is a config row, not a code change. See
[DESIGN.md](DESIGN.md) for the full architecture, data-source analysis, and plan.

## Status

- **Phase 0 — Foundations:** ✅ package scaffold, provider contracts, config, CLI, tests.
- **Phase 1 — Data spine (SEC EDGAR):** ✅ submissions + XBRL facts ingestion, point-in-time
  storage, canonical normalization, 3-statement reconstruction with citations.
- **Phase 2 — Deterministic analytics:** ✅ ratios, growth/CAGR, multi-scenario + reverse DCF,
  peer comps via EDGAR — all computed in tested Python, never by an LLM.
- **Phase 3 — RAG over filings:** ✅ fetch/parse 10-K HTML → canonical sections → chunks →
  SQLite FTS5 keyword retrieval with metadata filters and citations.
- **Phase 4 — Agent layer:** ✅ Supervisor agent (Claude, `claude-opus-4-8`, adaptive thinking)
  orchestrates the deterministic tools; the LLM only narrates and cites — it never computes
  numbers. Hardened with a deterministic **citation-coverage gate** and an LLM **Critic /
  red-team** review (`--verify`). Needs `ANTHROPIC_API_KEY` (the rest of the platform does not).
- **Phase 5 — v1 product surface:** ✅ deterministic **alerts** engine with **persistence +
  change detection** (only *new* alerts surface) + acknowledge; cross-company **watchlist feed**;
  live **price feed** (Yahoo→Stooq) driving **multiples + reverse-DCF "what's priced in"**;
  cached **peer comps**; optional **scheduled refresh**; **FastAPI** backend + tabbed
  **dashboard** (overview w/ market & valuation verdict, statements, comps, filings, alert feed,
  chat).
- **Phase 6 — Multi-company / genericity:** ✅ onboarding a company is one YAML file. ACN, CTSH,
  and IBM all run through the identical pipeline (statements, ratios, DCF, alerts, comps).

## Quick start

```bash
uv sync --extra dev                       # create venv + install deps
cp .env.example .env                      # set your SEC User-Agent (needs a contact email)

uv run equity companies                   # list configured companies (ACN seeded)
uv run equity ingest ACN                  # pull EDGAR submissions + XBRL facts
uv run equity statements ACN              # reconstructed annual statements (cited)
uv run equity metric ACN revenue          # one canonical metric's series + provenance
uv run equity ratios ACN                  # margins, returns, leverage, growth (cited)
uv run equity dcf ACN                      # bear/base/bull DCF value range
uv run equity comps ACN                    # latest-FY fundamentals vs peer set (live)
uv run equity index ACN --form 10-K        # index recent 10-Ks for retrieval
uv run equity search ACN "AI disruption risk" --section "Risk Factors"
uv run equity alerts ACN                  # live alerts for one company
uv run equity refresh                     # recompute + persist new alerts across watchlist
uv run equity feed --unread               # cross-company alert inbox (then `equity ack <fp>`)
uv run equity research ACN "Is ACN's growth durable?" --verify   # agent + Critic (needs API key)
uv run equity serve                       # dashboard + chat at http://127.0.0.1:8000

uv run pytest                             # run the offline test suite (84 tests)
```

Companies are config rows in `config/companies/` (ACN, CTSH, IBM seeded). Add one and
`uv run equity ingest <TICKER>`. Set `EQR_REFRESH_INTERVAL=3600` before `serve` to enable
background alert refresh.

## Architecture in one breath

```
config (company = data)  →  providers (EDGAR, vendor-swappable)  →  ingest
   →  storage (SQLite, point-in-time)  →  normalize (XBRL → canonical metrics)
   →  CLI / [future: analytics, RAG, agents]
```

Core rules: **the LLM never does arithmetic**, **every fact carries provenance**,
and **the data vendor is swappable behind `DataProvider` contracts** so the retail
(free) and fund (premium) tiers run the same engine.

## Data sources (Phase 1)

[SEC EDGAR](https://www.sec.gov/edgar) `data.sec.gov` JSON APIs — public-domain,
authoritative, free. Requires a descriptive `User-Agent` with a contact email and
~10 req/s politeness (handled automatically). Responses are cached on disk.

## Adding a company

Drop a YAML file in `config/companies/<ticker>.yaml` (see `acn.yaml`), then
`uv run equity ingest <TICKER>`. The CIK is the SEC's company id.
