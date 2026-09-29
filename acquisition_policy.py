"""Prior-acquisition matching only; a later resale is not a positive acquisition example."""
from collections import Counter


def supports_prior(candidate):
    match = candidate.get("match", {})
    prior = next((a for a in match.get("sale_agreements", []) if a.get("source_sale") == "prior"), {})
    gap = prior.get("minimum_date_gap_days")
    return bool(match.get("exact_apn") and match.get("street_number_matches")
                and not match.get("unit_conflict")
                and candidate.get("listing", {}).get("StandardStatus") == "Closed"
                and prior.get("price_agrees") is True and gap is not None and gap <= 45)


def audit_evidence(data):
    listings, rows = {}, []
    for item in data["properties"]:
        key = item.get("candidate_listing_key")
        if not key:
            rows.append({"source_row": item["source_row"], "status": "unresolved"})
            continue
        selected = next(c for c in item["candidates"] if str(c["listing"]["ListingKey"]) == str(key))
        supported = supports_prior(selected)
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
    return {"policy": "prior-acquisition-only-v1", "rows": rows, "listings": listings,
            "row_counts": dict(Counter(row["status"] for row in rows)),
            "listing_counts": dict(Counter(row["status"] for row in listings.values()))}
