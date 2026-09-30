"""Agent layer (Phase 4).

A Supervisor agent (Claude) orchestrates the deterministic Phase 2/3 tools to
answer research questions about a company. The hard rules from DESIGN.md hold:
the LLM never computes numbers (the tools do, in tested Python), and every tool
result carries provenance so the thesis can cite its sources.

The tool surface (`ResearchTools`) is plain Python and fully testable without any
API calls; the LLM loop (`Supervisor`) is thin orchestration on top.
"""

from equity_research.agents.citations import CitationReport, check_citations
from equity_research.agents.tools import ResearchContext, ResearchTools

__all__ = ["ResearchContext", "ResearchTools", "CitationReport", "check_citations"]
