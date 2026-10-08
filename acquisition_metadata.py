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
    """Derive prior-sale facts only when an acquisition-time cutoff is known.

    Unknown history stays unknown. Previously derived values are trusted only when
    they explicitly identify the same cutoff date.
    """
    result = dict(metadata or {})
    cutoff = next((
        _date(result.get(key)) for key in (
            "ListDate", "OnMarketDate", "ListingContractDate", "listed_at",
        ) if _date(result.get(key)) is not None
    ), None)

    derived_fields = ("PriorSaleCount", "MonthsSinceMostRecentPriorSale", "MostRecentPriorSalePrice")
    if cutoff is None:
        for key in derived_fields:
            result[key] = None
        result["PriorSaleFeaturesAsOf"] = None
        return result

    cutoff_key = cutoff.date().isoformat()
    if result.get("PriorSaleFeaturesAsOf") == cutoff_key and result.get("PriorSaleCount") is not None:
        return result

    explicit = result.get("PriorSales")
    source = source_transactions if isinstance(source_transactions, dict) else {}
    if isinstance(explicit, list):
        candidates = explicit
        history_observed = True
    else:
        prefixes = ("Prior", "Last")
        candidates = [{
            "date": source.get(prefix + " Sale Date"),
            "price": source.get(prefix + " Sale Amount"),
        } for prefix in prefixes]
        history_observed = any(
            source.get(prefix + field) not in {None, ""}
            for prefix in prefixes for field in (" Sale Date", " Sale Amount")
        )

    sales = []
    for sale in candidates:
        if not isinstance(sale, dict):
            continue
        date = _date(sale.get("date"))
        if not date or date >= cutoff:
            continue
        try:
            price = float(sale.get("price")) if sale.get("price") not in {None, ""} else None
        except (TypeError, ValueError):
            price = None
        sales.append((date, price))

    sales = sorted(set(sales), reverse=True)
    for key in derived_fields:
        result[key] = None
    result["PriorSaleFeaturesAsOf"] = cutoff_key
    if not history_observed:
        return result

    result["PriorSaleCount"] = len(sales)
    if sales:
        result["MonthsSinceMostRecentPriorSale"] = (cutoff - sales[0][0]).days / 30.4375
        if sales[0][1] is not None:
            result["MostRecentPriorSalePrice"] = sales[0][1]
    return result
