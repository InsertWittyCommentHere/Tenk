"""HTTP API + dashboard surface (Phase 5).

FastAPI backend that serves the v1 product surfaces confirmed for the retail tier:
a **dashboard** (company overview, statements, ratios, valuation, alerts) and
**chat Q&A** (the Supervisor agent). All financial numbers come from the
deterministic layers; the chat endpoint requires ANTHROPIC_API_KEY, the rest do not.
"""

from equity_research.api.app import create_app

__all__ = ["create_app"]
