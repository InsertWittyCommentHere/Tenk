"""Deterministic analytics engine (Phase 2).

The LLM never computes these numbers — this module does, in tested Python, and
every result carries the source facts it was derived from. Ratios, growth, free
cash flow, DCF (multi-scenario + reverse), and peer comps all live here.
"""

from equity_research.analytics.metrics import Computed, MetricStore
from equity_research.analytics.ratios import compute_ratios
from equity_research.analytics.dcf import DCFAssumptions, DCFResult, reverse_dcf, run_dcf

__all__ = [
    "MetricStore",
    "Computed",
    "compute_ratios",
    "DCFAssumptions",
    "DCFResult",
    "run_dcf",
    "reverse_dcf",
]
