"""Prior-acquisition matching only; a later resale is not a positive acquisition example."""
from collections import Counter


def sale_date(value):
    from datetime import date
    try: return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError): return None


def first_sale_policy(candidate, source=None, candidates=None):
    """Actual sale/close dates only. Recording dates never identify acquisition photos."""
    listing, match = candidate.get("listing", {}), candidate.get("match", {})
    agreements = match.get("sale_agreements", [])
    identity = bool(match.get("exact_apn") and match.get("street_number_matches")
                    and not match.get("unit_conflict") and listing.get("StandardStatus") == "Closed")
    transactions = [sale_date(a.get("sale_date")) for a in agreements]
    source = source or {}
    transactions += [sale_date(source.get(k)) for k in ("Prior Sale Date", "Last Sale Date")]
    for other in candidates or []:
        transactions.extend(sale_date(a.get("sale_date")) for a in other.get("match",{}).get("sale_agreements",[]))
    transactions.append(sale_date(match.get("first_actual_sale_date_2026")))
    dates = sorted({d for d in transactions if d and d.year==2026})
    if len(dates)<2:
        dates = sorted({sale_date(a.get("sale_date")) for a in agreements
                        if a.get("source_sale")=="prior" and sale_date(a.get("sale_date"))})
        prior = sale_date(source.get("Prior Sale Date"))
        if prior: dates = sorted(set(dates+[prior]))
    target = dates[0] if dates else None
    closed = sale_date(listing.get("CloseDate"))
    if target:
        gap = abs((closed-target).days) if closed else None
        later_is_closer = bool(closed and any(abs((closed-d).days)<gap for d in dates[1:]))
        supported = bool(identity and closed and closed.year==target.year and gap<=45 and not later_is_closer)
        reason = None if supported else "Selected listing does not match the first actual sale; recover its listing/photos."
        return {"policy":"first-actual-sale-2026-v2","target_sale_date":target.isoformat(),
                "selected_close_date":closed.isoformat() if closed else None,
                "actual_date_gap_days":gap,"supported":supported,"reason":reason}
    # Legacy undated fixtures/imports retain their old checks; real dated records use the rule above.
    prior = next((a for a in agreements if a.get("source_sale")=="prior"), {})
    gap = prior.get("minimum_date_gap_days")
    supported = bool(identity and prior.get("price_agrees") is True and gap is not None and gap<=45)
    return {"policy":"legacy-undated-match","target_sale_date":None,
            "selected_close_date":closed.isoformat() if closed else None,
            "supported":supported,"reason":None if supported else "Acquisition date requires review."}


def supports_prior(candidate, source=None, candidates=None):
    return first_sale_policy(candidate, source, candidates)["supported"]


def audit_evidence(data):
    listings, rows = {}, []
    for item in data["properties"]:
        key = item.get("candidate_listing_key")
        if not key:
            rows.append({"source_row": item["source_row"], "status": "unresolved"})
            continue
        selected = next(c for c in item["candidates"] if str(c["listing"]["ListingKey"]) == str(key))
        supported = supports_prior(selected, item.get("source"), item.get("candidates"))
        state = "prior_acquisition_candidate" if supported else "needs_prior_listing"
        entry = listings.setdefault(str(key), {
            "status": state, "source_rows": [], "block_reason": None,
            "listing": selected["listing"], "sources": [],
        })
        entry["source_rows"].append(item["source_row"])
        entry["sources"].append(item["source"])
        if not supported:
            entry["status"] = "needs_prior_listing"
            entry["block_reason"] = "Selected MLS listing matches the last sale, not the prior acquisition. Earlier listing/photos need rematching."
        rows.append({"source_row": item["source_row"], "listing_key": str(key),
                     "address": item["source"].get("Address"), "status": state})
    return {"policy": "first-actual-sale-2026-v2", "rows": rows, "listings": listings,
            "row_counts": dict(Counter(row["status"] for row in rows)),
            "listing_counts": dict(Counter(row["status"] for row in listings.values()))}


def annotate_first_sales(rows):
    """One first sale per physical group, shared across repeated workbook rows/listings."""
    dates_by_group = {}
    for row in rows:
        group = str(row.get('group_id') or row.get('listing_key') or row.get('id'))
        snapshot = row.get('source_snapshot',{})
        source = snapshot.get('spreadsheet',row.get('source',{}))
        candidates = snapshot.get('mls_candidates', [row.get('candidate',{})])
        dates = [sale_date(source.get(k)) for k in ('Prior Sale Date','Last Sale Date')]
        for candidate in candidates:
            match = candidate.get('match',{})
            dates += [sale_date(a.get('sale_date')) for a in match.get('sale_agreements',[])]
        dates_by_group.setdefault(group,[]).extend(d for d in dates if d and d.year==2026)
    for row in rows:
        group = str(row.get('group_id') or row.get('listing_key') or row.get('id'))
        if dates_by_group[group]:
            first = min(dates_by_group[group]).isoformat()
            row['first_sale_date'] = first
            if row.get('candidate'):
                row['candidate'] = {**row['candidate'],'match':{**row['candidate'].get('match',{}),'first_actual_sale_date_2026':first}}
    return rows
