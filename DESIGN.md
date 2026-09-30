# AI Investment Research Platform — Architecture & Implementation Plan

**Initial coverage:** Accenture (ACN, CIK 0001467373) · **Design goal:** any public company is a config row, not a code change.
**Two personas:** (A) Funds — extend with premium data + drive faster decisions; (B) Retail — institutional-grade rigor on free data.
**Status:** Design only. No application code written yet.

### Confirmed scope (v1)
- **Persona:** Retail-first. Build the generic engine; expose the retail surface now, fund plugins stubbed for later.
- **Deployment:** Personal / internal. `yfinance` + polite IR scraping are acceptable for v1, but stay behind `DataProvider` contracts so a future commercial/licensed swap is a config change, not a rewrite.
- **Primary v1 output:** Interactive **chat Q&A** over ACN's filings/financials **+ a dashboard with alerts** (new filings, guidance changes, insider clusters, estimate revisions). The generated PDF/MD report drops to a secondary, later surface.
- **Implication:** lighter compliance burden (no client-facing outputs, no audit-export requirement yet), but the provenance/citation discipline and point-in-time storage stay — they're cheap to keep and expensive to retrofit.

---

## 1. Data Source Analysis

Principle: **separate the data *interface* (a `DataProvider` contract) from the data *vendor*.** The same agent code runs whether the bytes come from free SEC EDGAR or a fund's Bloomberg/FactSet entitlement. Below is the honest free-vs-paid landscape, with licensing caveats called out because ToS violations are the #1 way a platform like this gets shut down.

### 1.1 Primary / canonical (free, authoritative, redistributable)

| Source | What it gives | Access | Cost | Notes / caveats |
|---|---|---|---|---|
| **SEC EDGAR** (`data.sec.gov`) | 10-K, 10-Q, 8-K, DEF 14A (proxy), Forms 3/4/5 (insider), 13F (institutional holdings), full XBRL financials | REST/JSON: `companyfacts`, `companyconcept`, `submissions`; plus full-text search | Free | **Must** send a descriptive `User-Agent` (email). ~10 req/s soft limit. This is the **source of truth** for financials and the legal anchor of the whole system. |
| **SEC XBRL Frames** (`/api/xbrl/frames`) | A single GAAP concept across all filers for a period — instant peer comps | REST/JSON | Free | Lets us build peer financials without scraping each company. |
| **Company IR site** (Accenture: `investor.accenture.com`) | Earnings press releases, slide decks, fact sheets, transcripts, guidance | HTML/PDF scrape | Free | Earliest source of guidance & non-GAAP bridges. Polite scraping; cache aggressively. |
| **Federal Reserve FRED** | Macro: rates, FX, CPI, GDP, employment | API key, JSON | Free | Context for discount rates and demand drivers. |

### 1.2 Market & estimates data (free tier, **non-redistributable** — use as input, don't republish raw)

| Source | What it gives | Cost | Caveat |
|---|---|---|---|
| **Yahoo Finance** (via `yfinance`) | Prices, multiples, consensus EPS/revenue, analyst count, calendar | Free | **Unofficial / ToS-gray.** Fine for personal & prototyping; **not** for a commercial product. Treat as a swappable provider, not a dependency. |
| **Financial Modeling Prep (FMP)** | Normalized statements, **analyst estimates & price targets**, ratios, DCF | Freemium → paid | Cleanest path to *legitimately licensed* consensus estimates at low cost. Good default for retail tier. |
| **Finnhub / Alpha Vantage / Tiingo / Polygon** | Prices, fundamentals, news, some estimates | Freemium | Each has a free key; use as fallback/redundancy providers. |
| **Stooq / Nasdaq Data Link** | EOD prices, some datasets | Free/freemium | Redundancy for price series. |

### 1.3 Qualitative / narrative & news

| Source | What it gives | Cost | Caveat |
|---|---|---|---|
| **Earnings transcripts** | Management tone, Q&A, analyst concerns | Free via IR + Seeking Alpha/Motley Fool (HTML); FMP/API-Ninjas (licensed) | Prefer IR audio/transcript or a **licensed** transcript API for a product. Seeking Alpha scraping is ToS-restricted. |
| **News / RSS** | Catalysts, sentiment | GDELT (free), NewsAPI/Benzinga (freemium/paid), company RSS | GDELT is genuinely open and good for event detection. |
| **AlphaSense** | Premium document search + sentiment | Paid (enterprise) | **Fund-tier only.** Wire as an optional `DocumentProvider`; gated behind entitlement. Do not assume access. |

### 1.4 Accessibility verdict
- **Buildable today, fully free & compliant for retail:** SEC EDGAR (financials, filings, insiders, ownership) + IR site (guidance/transcripts) + FRED (macro) + FMP free tier (estimates) + GDELT (news).
- **AlphaSense / Seeking Alpha premium / Bloomberg / FactSet / Visible Alpha:** entitlement-gated provider plugins, **off by default**, enabled when a fund supplies credentials.
- **Yahoo/`yfinance`:** allowed in dev/personal mode; flagged and swappable for any hosted/commercial deployment.

---

## 2. System Architecture

### 2.1 Layered view
```
┌──────────────────────────────────────────────────────────────────────┐
│  PRESENTATION    Chat UI · Research report (PDF/MD) · Dashboard · API  │
├──────────────────────────────────────────────────────────────────────┤
│  ORCHESTRATION   Supervisor agent · task graph · memory · tracing      │
├──────────────────────────────────────────────────────────────────────┤
│  SPECIALIST      Fundamentals · Valuation · Filings/RAG · Sentiment ·  │
│  AGENTS          Estimates · Risk · Peer/Industry · Insider/Ownership  │
├──────────────────────────────────────────────────────────────────────┤
│  TOOL / SKILL    DCF engine · comps engine · ratio calc · retriever ·  │
│  LAYER           charting · backtest · screen — deterministic, tested  │
├──────────────────────────────────────────────────────────────────────┤
│  DATA ACCESS     DataProvider contracts (Filings, Prices, Estimates,   │
│  (PROVIDERS)     Transcripts, News, Macro, Ownership) — vendor-swap    │
├──────────────────────────────────────────────────────────────────────┤
│  STORAGE         Object store (raw filings) · Postgres (normalized     │
│                  facts, time-series) · Vector DB (chunks) · Cache      │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Core design decisions
1. **Numbers are computed by deterministic tools, never by the LLM.** DCF, multiples, growth rates, and ratios live in tested Python (the Tool layer). The LLM *orchestrates and narrates*; it does not do arithmetic. This is the single most important rule for trustworthiness.
2. **Every claim is citation-backed.** Each fact carries provenance: `{source, url, filing accession, period, retrieved_at}`. Reports render footnotes; unsourced assertions are blocked.
3. **Provider abstraction = multi-tenant data tiers.** Retail config binds `EstimatesProvider→FMP`; fund config binds `EstimatesProvider→VisibleAlpha`. Agent code is identical.
4. **Point-in-time correctness.** Store `filed_at` / `period_end` / `as_of` so backtests and "what did we know then" don't leak future data — critical for fund credibility.
5. **Company-agnostic from day one.** A company is `{ticker, CIK, exchange, currency, fiscal_year_end, peer_set, segment_map}`. ACN is the first seeded row.

### 2.3 Multi-agent design (supervisor + specialists)
- **Supervisor / Orchestrator** — decomposes a query ("Is ACN a buy?") into a task graph, dispatches specialists, resolves conflicts, assembles the thesis with a confidence score.
- **Fundamentals Agent** — pulls XBRL facts, builds normalized 3-statement history, computes growth/margins/returns/FCF, flags non-GAAP vs GAAP gaps.
- **Valuation Agent** — runs DCF (multi-scenario: bear/base/bull), reverse-DCF (what's priced in), and relative comps; outputs a value range, not a false-precision point.
- **Filings/RAG Agent** — retrieves over chunked 10-K/10-Q/8-K/proxy; answers "what changed YoY in risk factors / MD&A," extracts guidance and segment detail with citations.
- **Estimates Agent** — consensus vs actuals, revision trends, estimate dispersion, beat/miss history; surfaces where our model diverges from the Street.
- **Sentiment/News Agent** — transcript tone, catalyst timeline, news clustering (GDELT), guidance-language deltas QoQ.
- **Risk Agent** — concentration, FX, bookings/book-to-bill (key for ACN), client/segment risk, accounting red flags (Beneish-style screens), litigation.
- **Insider & Ownership Agent** — Form 4 buying/selling, 13F institutional flow, buyback/dilution from cash-flow statement.
- **Peer/Industry Agent** — builds the comp set (ACN vs IBM, CTSH, INFY, WIT, CAP.PA, DXC, etc.) using XBRL Frames; normalizes for fiscal-year and currency.
- **Critic/Red-Team Agent** — adversarially attacks the thesis (steelman the bear case), checks every number against provenance, scores citation coverage before release.

### 2.4 Data pipeline (ingestion → serving)
```
EDGAR / IR / FRED / FMP / GDELT
        │  (scheduled pollers + on-demand)
        ▼
  Ingestion workers ──► Raw object store (immutable, hashed)
        │
        ▼
  Normalizer (XBRL→canonical schema, unit/fiscal alignment, restatement handling)
        │
        ├─► Postgres: facts, time-series, estimates, ownership (point-in-time)
        └─► Chunker + embedder ─► Vector DB (filings, transcripts) w/ metadata filters
        │
        ▼
  Feature/metric builder (ratios, comps tables, screens) — cached, versioned
        │
        ▼
  Agents query via tools (SQL + vector + metric API), never raw vendor calls
```
Freshness handled by an event scheduler: EDGAR full-text-search poll for new ACN accessions; IR-page diffing; pre/post-earnings refresh windows.

### 2.5 Trust, safety & compliance layer (non-negotiable for finance)
- **Provenance enforcement** on every rendered number/claim.
- **Hallucination guards:** numeric outputs cross-checked against the metric store; the Critic agent blocks low-citation reports.
- **Not-investment-advice framing**, disclosures, and an audit log of every model run (inputs, data versions, prompts, outputs) — needed for any fund using this in a real process.
- **Data-licensing guardrails:** providers tagged `redistributable: true/false`; reports only embed redistributable raw data, otherwise link/aggregate.
- **PII / entitlement isolation** between tenants (a fund's premium data never leaks to another tenant).

### 2.6 Suggested tech stack (pragmatic, swappable)
- **Language:** Python 3.12.
- **Agent framework:** start with the **Claude Agent SDK** (tool-use + sub-agents + caching) — matches the multi-agent + tool design directly; LangGraph as an alternative if a hard task-graph DSL is wanted.
- **LLM:** Claude (Opus for synthesis/critic, Sonnet/Haiku for extraction/routing to control cost).
- **Storage:** Postgres (+ TimescaleDB for series) · object store (S3/local) · pgvector or Qdrant for embeddings.
- **Orchestration/jobs:** a lightweight scheduler (APScheduler/Prefect) for pollers.
- **API/UI:** FastAPI backend; report renderer to Markdown→PDF; later a thin web dashboard.
- **Quality:** deterministic golden tests on the valuation engine; eval harness for agent answers against known filings.

---

## 3. Implementation Plan (phased)

**Phase 0 — Foundations (scaffold, no business logic yet)**
- Repo layout, config schema for a "company" (ACN seeded), `DataProvider` interfaces, secrets/entitlement config, logging/tracing, test harness.

**Phase 1 — Data spine (the moat)**
- EDGAR provider (companyfacts/submissions/frames + full-text search), normalizer to canonical schema, Postgres models, point-in-time storage. Goal: pull ACN's full financial history and reconstruct the 3 statements with citations.

**Phase 2 — Deterministic analytics engine**
- Ratio/metric library, DCF (multi-scenario + reverse-DCF), comps engine via XBRL Frames, screens. Golden-number tests vs ACN's actual 10-K. **No LLM yet** — earn trust on the math first.

**Phase 3 — RAG over filings & transcripts**
- Chunking + embeddings + metadata-filtered retrieval; "what changed YoY," guidance extraction, segment Q&A — all citation-backed.

**Phase 4 — Agents & orchestration**
- Implement specialist agents over the tools, then the Supervisor and Critic/red-team. End-to-end "research ACN" produces a structured, sourced thesis with a value range and confidence.

**Phase 5 — v1 product surface (chat + dashboard/alerts)** *(confirmed primary)*
- **Chat Q&A** over ACN's filings/financials with sourced answers (the Supervisor agent behind a conversational API).
- **Dashboard:** key metrics, comps table, valuation range, segment/bookings view.
- **Alerts:** new EDGAR accession, IR-guidance language change, insider (Form 4) clustering, consensus estimate revision.
- *(Secondary, later)* Research-report generator (PDF/MD).

**Phase 6 — Multi-company & multi-tenant**
- Onboard a second company (e.g., CTSH) to prove genericity; add tenant isolation and the fund-vs-retail config profiles.

**Phase 7 — Premium provider plugins (fund tier)**
- AlphaSense/Visible Alpha/Bloomberg/FactSet adapters behind entitlements; portfolio-level and faster-decision workflows; backtesting of the model's historical calls.

---

## 4. Two-persona configuration

| Dimension | **Retail tier** | **Fund tier** |
|---|---|---|
| Data | EDGAR, IR, FRED, FMP free, GDELT | + AlphaSense, Visible Alpha/I-B-E-S, Bloomberg/FactSet, premium transcripts, alt-data |
| Estimates | FMP consensus | Proprietary + Street granular (analyst-level) |
| Output | Plain-English thesis, education, "what to watch," risk flags, value range | Model export, point-in-time backtests, conviction scoring, portfolio context, API into their stack |
| Speed | On-demand report | Pre-computed, alerting, batch across coverage universe |
| Customization | Templates | Custom agents, custom factors, override assumptions, white-label |
| Guardrails | Heavy "not advice" + education | Audit logs, compliance export, entitlement isolation |

Both run the **same engine**; the difference is provider bindings + output templates + config — which is exactly why the provider abstraction is the central design choice.

---

## 5. Missing Requirements & Open Questions

**Product / scope**
1. Primary persona for v1 — build retail-first or fund-first? (Changes data-licensing posture immediately.)
2. Output shape that matters most — chat Q&A, a generated PDF research report, or a live dashboard/screen?
3. Decision support vs. decision automation — recommendations only, or position sizing / portfolio integration?

**Data / legal**
4. Is this personal/internal or a hosted commercial product? (Decides whether `yfinance`/scraping is acceptable or must be replaced with licensed feeds.)
5. Do you have, or plan to license, any paid data (FMP paid, AlphaSense, Bloomberg)? Determines fund-tier realism.
6. Coverage breadth target after ACN — US-only, or international (ADRs, IFRS, multi-currency like Accenture's Irish domicile)?

**Engineering / ops**
7. Deployment target & budget — local-first, or cloud (and LLM spend ceiling)? Drives model routing.
8. Real-time vs. periodic — intraday signals, or filing/earnings-cadence research?
9. Latency/cost tolerance per report (affects Opus-vs-Sonnet routing and caching strategy).

**Compliance**
10. Regulatory posture — will outputs be shown to clients/investors? If so, disclosures, record-keeping, and possibly compliance review are in-scope, not optional.
11. Backtesting/track-record requirements for the fund tier (point-in-time rigor cost).

**My default assumptions if unanswered:** retail-first, free+compliant sources, local→cloud, periodic (earnings-cadence) research, Markdown/PDF report + chat, ACN then a second comp to prove genericity.

---

## 6. Recommended first move
Build **Phases 1–2 (data spine + deterministic valuation, no LLM)** against ACN and validate every number against Accenture's latest 10-K. Trust in the math is the foundation everything else stands on; agents are only as credible as the numbers they cite.
```
```
