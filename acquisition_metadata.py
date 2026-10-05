"""Sale-history metadata enrichment for review pages and legacy models.

Uses only the standard library so hosted review does not load the training stack.
"""
from __future__ import annotations

from datetime import datetime


def _date(value):
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.fromisoformat(value[:10])
    except ValueError:
        return None


def acquisition_time_metadata(metadata, source_transactions=None):
    result = dict(metadata or {})
    if result.get("PriorSaleCount") is not None:
        return result
    cutoff = next((
        _date(result.get(key)) for key in (
            "ListDate", "OnMarketDate", "ListingContractDate", "listed_at",
        ) if _date(result.get(key)) is not None
    ), None)
    sales = []
    explicit = result.get("PriorSales")
    if isinstance(explicit, list):
        candidates = explicit
    else:
        source = source_transactions if isinstance(source_transactions, dict) else {}
        candidates = [{
            "date": source.get(prefix + " Sale Date"),
            "price": source.get(prefix + " Sale Amount"),
        } for prefix in ("Prior", "Last")]
    for sale in candidates:
        if not isinstance(sale, dict):
            continue
        date = _date(sale.get("date"))
        if not date or not cutoff or date >= cutoff:
            continue
        try:
            price = float(sale.get("price")) if sale.get("price") not in {None, ""} else None
        except (TypeError, ValueError):
            price = None
        sales.append((date, price))
    sales = sorted(set(sales), reverse=True)
    result["PriorSaleCount"] = len(sales)
    if sales:
        result["MonthsSinceMostRecentPriorSale"] = (
            cutoff - sales[0][0]
        ).days / 30.4375
        if sales[0][1] is not None:
            result["MostRecentPriorSalePrice"] = sales[0][1]
    return result
