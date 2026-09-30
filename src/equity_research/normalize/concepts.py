"""Canonical-metric -> XBRL-concept mapping.

Different filers tag the same economic line with different US-GAAP concepts, and
the same filer changes tags across years (e.g. older `SalesRevenueNet` vs newer
`RevenueFromContractWithCustomerExcludingAssessedTax`). For each canonical
metric we list candidate concepts in **priority order**; the normalizer takes
the first concept that has data for a given period.

This map is intentionally explicit and reviewable — it is the place where
"what counts as revenue" is decided, so it must be auditable, not buried in an
LLM prompt. Extend per-company overrides in the company config when a filer uses
an unusual tag.
"""

from __future__ import annotations

# Ordered candidate concepts per canonical metric. taxonomy is us-gaap unless noted.
CANONICAL_CONCEPTS: dict[str, list[str]] = {
    # --- Income statement ---
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueServicesNet",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfServices",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "rnd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "income_tax_expense": ["IncomeTaxExpenseBenefit"],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "shares_diluted": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted",
    ],
    "shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    # --- Balance sheet (instant) ---
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "total_current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "total_current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",  # e.g. Accenture (lease-heavy, little bond debt)
    ],
    "total_debt": ["DebtLongtermAndShorttermCombinedAmount"],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",  # dei
    ],
    # --- Cash flow ---
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "dividends_paid": [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfOrdinaryDividends",  # Irish-domiciled filers, e.g. Accenture
        "PaymentsOfDividends",
    ],
    "stock_repurchased": ["PaymentsForRepurchaseOfCommonStock"],
}

# Concepts that live in the `dei` taxonomy rather than `us-gaap`.
_DEI_CONCEPTS = {"EntityCommonStockSharesOutstanding"}


def taxonomy_for(concept: str) -> str:
    return "dei" if concept in _DEI_CONCEPTS else "us-gaap"


def canonical_metrics() -> list[str]:
    return list(CANONICAL_CONCEPTS.keys())
