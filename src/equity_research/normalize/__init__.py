"""Normalization: raw XBRL facts -> canonical, comparable metrics."""

from equity_research.normalize.concepts import CANONICAL_CONCEPTS, canonical_metrics
from equity_research.normalize.xbrl import build_metric_series, reconstruct_statements

__all__ = [
    "CANONICAL_CONCEPTS",
    "canonical_metrics",
    "build_metric_series",
    "reconstruct_statements",
]
