"""Small property queue index; never loads every photo or model prediction payload."""
import json

from pilot import read_json


def review_queue(studio, args):
    scope = args.get("scope", "acquisitions")
    if scope not in {"acquisitions", "quarantine", "reference"}:
        raise ValueError("Unknown dataset scope")
    queue = args.get("queue", "all")
    if queue not in {"all", "ready", "unscored", "reviewed", "photo_match"}:
        raise ValueError("Unknown review queue")
    offset, limit = max(0, int(args.get("offset", 0))), min(40, max(1, int(args.get("limit", 20))))
    search = args.get("search", "").strip().casefold()
    latest = {}
    for path in studio.assessments.folder.glob("*.json"):
        job = read_json(path)
        previous = latest.get(job["property_id"])
        if previous is None or job["created_at"] > previous["created_at"]:
            latest[job["property_id"]] = job
    legacy = studio.store.legacy()
    with studio.store.connect() as db:
        reviews = {row["id"]: json.loads(row["payload"]) for row in db.execute(
            "SELECT id,payload FROM reviews WHERE kind='property'"
        )}
        rows = db.execute("""
            SELECT p.id,p.metadata,
              (SELECT count(*) FROM images i WHERE i.property_id=p.id) AS image_count,
              (SELECT id FROM images i WHERE i.property_id=p.id ORDER BY sequence,id LIMIT 1) AS hero_image_id
            FROM properties p ORDER BY p.id
        """).fetchall()
    items = []
    for row in rows:
        metadata = json.loads(row["metadata"])
        review = reviews.get(row["id"], legacy.get("properties", {}).get(row["id"], {}))
        job = latest.get(row["id"], {})
        ready = job.get("status") == "completed" and bool(job.get("prediction"))
        reviewed = review.get("status") == "approved"
        status = "reviewed" if reviewed else "ready" if ready else (
            "running" if job.get("status") == "running" else
            "failed" if job.get("status") in {"failed", "interrupted"} else "unscored"
        )
        history = studio.assessments.history(row["id"])
        blocked = bool(history and history.get("blocked"))
        if (scope == "acquisitions" and (not history or blocked)) or (
            scope == "quarantine" and not blocked
        ) or (scope == "reference" and history):
            continue
        needs_era = bool(history and not history["timing_verified"])
        items.append({
            "id": row["id"], "address": metadata.get("UnparsedAddress", metadata.get("address", row["id"])),
            "city": metadata.get("City", metadata.get("city", "")), "listing_id": metadata.get("ListingId"),
            "image_count": row["image_count"], "hero_image_id": row["hero_image_id"],
            "status": status, "needs_photo_match": needs_era,
            "acquisition_status": history.get("acquisition_status") if history else "reference_only",
            "blocked": blocked,
            "source_role": metadata.get("source_role", "imported"),
            "target": job.get("review_target") if ready else None,
            "human_target": review.get("target_fit") if reviewed else None,
            "human_score": review.get("target_score") if reviewed else None,
        })
    counts = {
        "all": len(items), "ready": sum(i["status"] == "ready" for i in items),
        "unscored": sum(i["status"] in {"unscored", "failed"} for i in items),
        "reviewed": sum(i["status"] == "reviewed" for i in items),
        "photo_match": sum(i["needs_photo_match"] for i in items),
    }
    filtered = [i for i in items if (
        queue == "all" or queue == "photo_match" and i["needs_photo_match"]
        or queue == "unscored" and i["status"] in {"unscored", "failed"} or i["status"] == queue
    ) and (not search or search in " ".join(str(i[k] or "") for k in ("id", "address", "city", "listing_id")).casefold())]
    priority = {"ready": 0, "running": 1, "unscored": 2, "failed": 3, "reviewed": 4}
    filtered.sort(key=lambda i: (not i["image_count"], priority[i["status"]], i["source_role"] == "comp", i["address"]))
    return {"items": filtered[offset:offset+limit], "counts": counts,
            "total": len(filtered), "offset": offset, "limit": limit, "token": studio.app.token}
