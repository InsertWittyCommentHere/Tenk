"""Supervisor agent — orchestrates the deterministic tools via Claude.

Implements a manual agentic loop (per the Anthropic tool-use guide) so we keep
fine-grained control: we log every tool call, enforce that the model only gets
numbers from tools, and can later insert the Critic/citation-coverage gate.

Model + thinking config follow current guidance: default `claude-opus-4-8` with
adaptive thinking and `effort: high`. The whole agent layer is optional — if no
`ANTHROPIC_API_KEY` is set, the rest of the platform (data spine, analytics, RAG)
still works; only this orchestration needs the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from equity_research.agents.citations import CitationReport, check_citations
from equity_research.agents.critic import CritiqueResult
from equity_research.agents.tools import TOOL_SCHEMAS, ResearchContext, ResearchTools

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are an equity research analyst assistant for {name} ({ticker}).

Hard rules:
- You NEVER state a financial number from memory. Every number must come from a
  tool call (get_financials, get_ratios, run_dcf). If you haven't fetched it, fetch it.
- Cite the source for every material claim using the accession/filing the tool
  returned, in brackets, e.g. [10-K 0001467373-25-000217].
- For qualitative claims (risks, strategy, guidance), call search_filings and quote
  the cited passage.
- Be direct and balanced: present the bull case and the bear case. Distinguish
  facts (from filings) from your interpretation.
- When valuing the company, use run_dcf, state your assumptions and why, and give a
  value RANGE, not a single number. Note this is analysis, not investment advice.

Work by calling tools to gather evidence, then synthesize a structured, cited answer."""


@dataclass
class ResearchResult:
    answer: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    stop_reason: str | None = None
    citation_report: CitationReport | None = None
    critique: CritiqueResult | None = None


class Supervisor:
    def __init__(
        self,
        ctx: ResearchContext,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        max_iterations: int = 8,
    ) -> None:
        self.ctx = ctx
        self.tools = ResearchTools(ctx)
        self.model = model
        self.effort = effort
        self.max_iterations = max_iterations

    def _client(self):
        # Imported lazily so the package doesn't require the SDK/key unless used.
        import anthropic

        return anthropic.Anthropic()

    def research(self, question: str, *, verify: bool = False) -> ResearchResult:
        """Run the agentic loop. If `verify`, also run the citation gate + Critic."""
        client = self._client()
        system = SYSTEM_PROMPT.format(
            name=self.ctx.company.name, ticker=self.ctx.company.ticker
        )
        messages: list[dict] = [{"role": "user", "content": question}]
        calls: list[tuple[str, dict]] = []
        evidence: list[str] = []  # tool outputs, for the Critic and audit log
        stop_reason = None
        answer = "(Reached tool-call limit without a final answer.)"

        for _ in range(self.max_iterations):
            resp = client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            stop_reason = resp.stop_reason
            if resp.stop_reason != "tool_use":
                answer = next((b.text for b in resp.content if b.type == "text"), "")
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    calls.append((block.name, dict(block.input)))
                    output = self.tools.dispatch(block.name, dict(block.input))
                    evidence.append(f"[{block.name}({dict(block.input)})]\n{output}")
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": output}
                    )
            messages.append({"role": "user", "content": results})

        result = ResearchResult(answer=answer, tool_calls=calls, stop_reason=stop_reason)

        # Always run the cheap, deterministic citation gate on the final answer.
        result.citation_report = check_citations(answer)

        if verify:
            from equity_research.agents.critic import Critic

            result.critique = Critic().review(question, answer, "\n\n".join(evidence))

        return result
