"""The deterministic tool surface the Supervisor agent calls.

Each tool wraps Phase 2 (analytics) or Phase 3 (RAG) and returns model-friendly
text that *includes provenance*, so the agent's narration can cite filings. The
agent decides which tools to call and in what order; it never does the math.

`TOOL_SCHEMAS` are raw JSON-Schema tool definitions for the Messages API. The
`ResearchTools` class holds context and dispatches by tool name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from equity_research.analytics.dcf import DCFAssumptions, scenario_range
from equity_research.analytics.metrics import MetricStore
from equity_research.analytics.ratios import REPORT_RATIOS, compute_ratios
from equity_research.config import CompanyConfig
from equity_research.normalize.concepts import canonical_metrics
from equity_research.rag.store import ChunkStore
from equity_research.storage import Database


@dataclass
class ResearchContext:
    """Everything a research session needs, bound to one company."""

    company: CompanyConfig
    db: Database
    chunks: ChunkStore | None = None


def _fmt(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:,.2f}M"
    return f"{v:,.2f}"


# JSON-Schema tool definitions for the Messages API.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_financials",
        "description": (
            "Get a company's historical financial metrics (annual) with source citations. "
            "Use for revenue, net_income, operating_income, free_cash_flow, eps_diluted, "
            "total_assets, stockholders_equity, etc. Returns values per fiscal year plus the "
            "filing accession each came from. Call this for any factual financial number — "
            "never estimate numbers yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Canonical metric names, e.g. ['revenue','net_income'].",
                }
            },
            "required": ["metrics"],
        },
    },
    {
        "name": "get_ratios",
        "description": (
            "Get computed profitability, return, liquidity, leverage, and growth ratios per "
            "fiscal year (gross/operating/net margin, ROE, ROA, current ratio, revenue growth, "
            "etc.). All computed deterministically from filings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "last_n": {"type": "integer", "description": "How many recent years (default 5)."}
            },
        },
    },
    {
        "name": "run_dcf",
        "description": (
            "Run a multi-scenario discounted cash flow valuation (bear/base/bull) from the "
            "latest free cash flow. Returns an equity value and per-share value range. Use to "
            "assess intrinsic value. State the assumptions you chose and why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "discount_rate": {"type": "number", "description": "e.g. 0.09 for 9%."},
                "terminal_growth": {"type": "number", "description": "e.g. 0.025."},
                "base_growth": {"type": "number", "description": "Base-case FCF growth, e.g. 0.08."},
                "bear_growth": {"type": "number", "description": "Bear-case FCF growth."},
                "bull_growth": {"type": "number", "description": "Bull-case FCF growth."},
            },
            "required": ["discount_rate", "base_growth", "bear_growth", "bull_growth"],
        },
    },
    {
        "name": "search_filings",
        "description": (
            "Keyword-search the company's indexed SEC filings (10-K/10-Q) for qualitative "
            "context — risk factors, MD&A, strategy, guidance language. Returns cited passages. "
            "Use for the 'why' behind the numbers and for risks. Optionally filter by section "
            "('Risk Factors', 'MD&A', 'Business') or form."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "section": {"type": "string", "description": "Optional section filter."},
                "form": {"type": "string", "description": "Optional form filter, e.g. '10-K'."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_metrics",
        "description": "List the canonical financial metric names available via get_financials.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class ResearchTools:
    """Dispatches tool calls against a ResearchContext. No LLM here — pure functions."""

    def __init__(self, ctx: ResearchContext) -> None:
        self.ctx = ctx
        self._store = MetricStore.from_db(ctx.db, ctx.company.cik)

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> str:
        """Run a tool by name; always returns a string (never raises to the agent loop)."""
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return f"Error: unknown tool {name!r}."
            return handler(tool_input)
        except Exception as e:  # noqa: BLE001 — surface errors to the model, don't crash the loop
            return f"Error running {name}: {e}"

    # -- tools --------------------------------------------------------------

    def _tool_list_metrics(self, _: dict[str, Any]) -> str:
        return "Available metrics: " + ", ".join(canonical_metrics())

    def _tool_get_financials(self, inp: dict[str, Any]) -> str:
        metrics = inp.get("metrics", [])
        if not metrics:
            return "Error: provide at least one metric name."
        lines: list[str] = []
        known = set(canonical_metrics())
        derived_only = {"free_cash_flow", "gross_margin", "operating_margin", "net_margin",
                        "fcf_margin", "return_on_equity", "return_on_assets", "current_ratio",
                        "effective_tax_rate", "total_liabilities_derived"}
        for metric in metrics:
            if metric in derived_only:
                series = self._store.derived(metric)
                if not series:
                    lines.append(f"{metric}: no data.")
                    continue
                for pe in sorted(series)[-6:]:
                    c = series[pe]
                    accns = ",".join(sorted({p.accession for p in c.sources if p.accession}))
                    lines.append(f"{metric} FY{pe.year}: {_fmt(c.value)}  [{c.formula}; {accns}]")
            elif metric in known:
                series = self._store.series(metric)
                if not series:
                    lines.append(f"{metric}: no data.")
                    continue
                for pe in sorted(series)[-6:]:
                    f = series[pe]
                    lines.append(
                        f"{metric} FY{pe.year}: {_fmt(f.value)}  "
                        f"[{f.form} {f.accession} filed {f.filed_date}]"
                    )
            else:
                lines.append(f"{metric}: unknown metric (use list_metrics).")
        return "\n".join(lines)

    def _tool_get_ratios(self, inp: dict[str, Any]) -> str:
        last_n = int(inp.get("last_n", 5))
        report = compute_ratios(self._store, last_n=last_n)
        if not report:
            return "No ratio data."
        pct = set(REPORT_RATIOS) - {"current_ratio"} | {"revenue_growth", "eps_growth"}
        lines = []
        for pe in sorted(report):
            parts = []
            for name, v in report[pe].items():
                parts.append(f"{name}={v * 100:.1f}%" if name in pct else f"{name}={v:.2f}")
            lines.append(f"FY{pe.year}: " + ", ".join(parts))
        return "\n".join(lines)

    def _tool_run_dcf(self, inp: dict[str, Any]) -> str:
        periods = self._store.periods("revenue")
        if not periods:
            return "No data to run DCF."
        pe = periods[-1]
        fcf = self._store.derived("free_cash_flow").get(pe)
        if fcf is None:
            return "Cannot compute latest free cash flow."
        cash = self._store.value("cash_and_equivalents", pe) or 0.0
        debt = self._store.value("long_term_debt", pe) or 0.0
        shares = self._store.value("shares_outstanding", pe) or self._store.value("shares_diluted", pe)
        base = DCFAssumptions(
            base_fcf=fcf.value,
            growth_rate=float(inp["base_growth"]),
            terminal_growth=float(inp.get("terminal_growth", 0.025)),
            discount_rate=float(inp["discount_rate"]),
            net_cash=cash - debt,
            shares_outstanding=shares,
        )
        scenarios = scenario_range(
            base, bear_growth=float(inp["bear_growth"]), bull_growth=float(inp["bull_growth"])
        )
        out = [
            f"DCF from FY{pe.year} free cash flow {_fmt(fcf.value)} "
            f"(source: {fcf.formula}; "
            f"{','.join(sorted({p.accession for p in fcf.sources if p.accession}))}).",
            f"Assumptions: discount={base.discount_rate:.1%}, terminal={base.terminal_growth:.1%}, "
            f"net_cash={_fmt(base.net_cash)}, shares={_fmt(shares) if shares else 'n/a'}.",
        ]
        for name in ["bear", "base", "bull"]:
            r = scenarios[name]
            vps = f"${r.value_per_share:,.2f}/sh" if r.value_per_share else "n/a"
            out.append(f"{name}: equity {_fmt(r.equity_value)}, {vps}")
        return "\n".join(out)

    def _tool_search_filings(self, inp: dict[str, Any]) -> str:
        if self.ctx.chunks is None:
            return "Filing search unavailable (no documents indexed; run `equity index`)."
        hits = self.ctx.chunks.search(
            inp["query"], cik=self.ctx.company.cik,
            form=inp.get("form"), section_like=inp.get("section"), limit=4,
        )
        if not hits:
            return "No matching passages found."
        out = []
        for h in hits:
            snippet = " ".join(h.text[:700].split())
            out.append(f"[{h.citation()}]\n{snippet}…")
        return "\n\n".join(out)
