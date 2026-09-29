"""One resolved tag set for grouping, display and editing."""
from pilot import FEATURE_ROOMS, FEATURES


def effective_photo(image, ai=None):
    ai = ai or {}
    review = image.get("review") or {}
    has_human_room = bool(review.get("room") and (
        review.get("status") in {"approved", "draft"} or review.get("room_confirmed")))
    room = review["room"] if has_human_room else ai.get("room") or image.get("room") or "other"
    context = review.get("context") or ai.get("context") or image.get("provider_context") or "unknown"
    base_features = ai.get("features", image.get("features", {}))
    features = {name: base_features.get(name) for name in FEATURES}
    features.update(review.get("features") or {})
    for name in features:
        if name in FEATURE_ROOMS and room not in FEATURE_ROOMS[name]:
            features[name] = None
        if context in {"shared_amenity", "floor_plan", "unrelated"}:
            features[name] = None
    local = (image.get("local_model") or {}).get("preferences", {}).get(room)
    score = local.get("score") if isinstance(local, dict) else local
    if context in {"shared_amenity", "floor_plan", "unrelated"}:
        score = None
    gpt_score = ai.get("renovation_scope_score") if ai.get("room") == room and ai.get("context") == context else None
    if context != "subject":
        gpt_score = None
    pref = review.get("preference")
    if review.get("preference_room") and review["preference_room"] != room:
        pref = None
    if context in {"shared_amenity", "floor_plan", "unrelated"}:
        pref = None
    return {"room": room, "context": context, "features": features,
            "preference": pref, "local_score": score, "gpt_scope_score": gpt_score,
            "room_source": "Your review" if has_human_room else "GPT draft" if ai.get("room") else image.get("room_source", "Unknown"),
            "context_source": "Your review" if review.get("context") else "GPT draft" if ai.get("context") else "MLS description" if image.get("provider_context") else "Unknown",
            "label_status": review.get("status", "unreviewed")}
