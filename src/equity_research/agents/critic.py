"""Critic / red-team agent.

Adversarially reviews the Supervisor's draft thesis: steelmans the bear case,
checks that claims trace to the gathered evidence, and flags any number that
looks unsupported. Runs as a separate Claude call with its own context so it
isn't anchored on the Supervisor's reasoning.

Pairs with the deterministic `check_citations` gate (citations.py): the gate
catches *missing* citations mechanically; the Critic catches *weak or one-sided*
reasoning that a regex can't. Uses a cheaper model by default (Sonnet) since it
reviews rather than generates.
"""

from __future__ import annotations

from dataclasses import dataclass

CRITIC_MODEL = "claude-sonnet-4-6"

CRITIC_SYSTEM = """You are a skeptical buy-side risk reviewer. You are given a draft
equity-research answer and the raw evidence (tool outputs) it was built from. Your job
is to RED-TEAM it, not to agree with it.

Produce a short critique covering:
1. Bear case: the strongest argument against the draft's conclusion, stated fairly.
2. Unsupported claims: any number or assertion in the draft NOT backed by the evidence
   provided. Quote it.
3. One-sidedness or overconfidence: where the draft overstates certainty.
4. Verdict: one of SOLID / NEEDS_WORK / WEAK, with one sentence why.

Be concise and specific. Do not invent new facts; judge only against the evidence given."""


@dataclass
class CritiqueResult:
    critique: str
    verdict: str  # SOLID | NEEDS_WORK | WEAK | UNKNOWN


def _extract_verdict(text: str) -> str:
    upper = text.upper()
    for v in ("NEEDS_WORK", "WEAK", "SOLID"):
        if v in upper:
            return v
    return "UNKNOWN"


class Critic:
    def __init__(self, *, model: str = CRITIC_MODEL) -> None:
        self.model = model

    def review(self, question: str, draft_answer: str, evidence: str) -> CritiqueResult:
        import anthropic

        client = anthropic.Anthropic()
        user = (
            f"QUESTION:\n{question}\n\n"
            f"DRAFT ANSWER:\n{draft_answer}\n\n"
            f"EVIDENCE (tool outputs the draft had access to):\n{evidence}"
        )
        resp = client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=CRITIC_SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return CritiqueResult(critique=text, verdict=_extract_verdict(text))
