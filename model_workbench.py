"""Revisioned challenge feedback and immutable dataset versions for the hosted workbench."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from condition_schema import TARGET_LABELS, validate_labels
from studio_data import now, trim_metadata


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def worker_state(store,threshold_seconds=90):
    heartbeat = store.document("workbench-worker") or {}
    try:
        at = datetime.fromisoformat(heartbeat["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        age = max(0,(datetime.now(UTC)-at).total_seconds())
    except (KeyError,TypeError,ValueError):
        age = None
    status = heartbeat.get("status","offline")
    online = age is not None and age<threshold_seconds and status in {
        "ready","running","loading"
    }
    return {
        "online":online,"actionable":online and status=="ready",
        "status":status if online or status=="stopped" else "offline",
        "age_seconds":round(age,1) if age is not None else None,
        "detail":heartbeat.get("detail"),
        "threshold_seconds":threshold_seconds,
    }


def list_properties(store, args):
    offset = max(0, int(args.get("offset", 0)))
    limit = min(40, max(1, int(args.get("limit", 20))))
    search = args.get("search", "").strip().casefold()
    with store.database.connect() as db:
        rows = db.execute('''SELECT DISTINCT ON (e.listing_key)
            e.listing_key,e.group_id,g.protected_test,
            c.item->'listing' AS listing,
            (SELECT count(*) FROM acq_training.photos p
             WHERE (p.workspace_id,p.example_id)=(e.workspace_id,e.id)
             AND p.revoked_at IS NULL
             AND (p.retention_until IS NULL OR p.retention_until>now())) AS photo_count
            FROM acq_training.examples e
            JOIN acq_training.property_groups g
              ON (g.workspace_id,g.id)=(e.workspace_id,e.group_id)
            LEFT JOIN LATERAL (
              SELECT item FROM jsonb_array_elements(e.source_snapshot->'mls_candidates') item
              WHERE item->'listing'->>'ListingKey'=e.listing_key LIMIT 1
            ) c ON true
            WHERE e.workspace_id=%s AND e.listing_key IS NOT NULL
            ORDER BY e.listing_key,e.id''',(store.workspace,)).fetchall()
        feedback = {
            row["payload"].get("property_id"): row["payload"]
            for row in db.execute('''SELECT payload FROM acq_training.studio_state
              WHERE workspace_id=%s AND kind='document'
              AND item_id LIKE 'workbench-feedback:%%'
              ORDER BY updated_at''',(store.workspace,)).fetchall()
        }
        results = {
            row["payload"].get("property_id"): row["payload"]
            for row in db.execute('''SELECT payload FROM acq_training.studio_state
              WHERE workspace_id=%s AND kind='document'
              AND item_id LIKE 'workbench-result:%%'
              ORDER BY updated_at''',(store.workspace,)).fetchall()
        }
    items = []
    for row in rows:
        listing = trim_metadata(row["listing"] or {})
        identifier = str(row["listing_key"])
        result = results.get(identifier, {})
        review = feedback.get(identifier, {})
        item = {
            "id": identifier, "group_id": str(row["group_id"]),
            "protected_test": bool(row["protected_test"]),
            "address": listing.get("UnparsedAddress") or identifier,
            "city": listing.get("City"), "listing_id": listing.get("ListingId"),
            "year_built": listing.get("YearBuilt"),
            "property_type": listing.get("PropertySubType") or listing.get("PropertyType"),
            "photo_count": row["photo_count"],
            "review_label": review.get("target_label"),
            "hard_negative": bool(review.get("hard_negative")),
            "last_score": (result.get("prediction") or {}).get("score"),
            "last_mode": (result.get("prediction") or {}).get("mode_used"),
        }
        text = " ".join(str(item.get(key) or "") for key in
                        ("id","address","city","listing_id","property_type")).casefold()
        if not search or search in text:
            items.append(item)
    items.sort(key=lambda item: (
        item["review_label"] is not None,
        item["last_score"] is None,
        -(item["last_score"] or 0),
        item["address"],
    ))
    return {"items":items[offset:offset+limit],"total":len(items),
            "offset":offset,"limit":limit}


def _training_listing_keys(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT DISTINCT listing_key
            FROM acq_training.examples
            WHERE workspace_id=%s AND listing_key IS NOT NULL''',
            (store.workspace,)).fetchall()
    return {str(row["listing_key"]) for row in rows}


def _public_challenge(item):
    return {
        key:value for key,value in item.items()
        if key!="media"
    } | {
        "photo_count":len(item.get("media",[])),
        "images":[{
            key:value for key,value in media.items() if key!="source_url"
        } for media in item.get("media",[])],
    }


def challenge_properties(store, source, args):
    mode = args.get("mode","active")
    limit = min(20,max(1,int(args.get("limit",5))))
    offset = max(0,int(args.get("offset",0)))
    excluded = _training_listing_keys(store)
    if mode=="active":
        items = source.list_active(
            search=args.get("search",""),limit=limit,offset=offset,exclude=excluded,
        )
    elif mode=="random":
        items = source.random_active(
            seed=args.get("seed","workbench-challenge-v1"),
            limit=limit,exclude=excluded,
        )
    elif mode=="opportunities":
        items = source.current_opportunities(
            limit=limit,offset=offset,exclude=excluded,
        )
    else:
        raise ValueError("Unknown MLS challenge source")
    return {
        "items":[_public_challenge(item) for item in items],
        "mode":mode,"offset":offset,"limit":limit,
        "excluded_training_listings":len(excluded),
        "notice":"MLS opportunity scores are context only, never training ground truth.",
    }


def challenge_property(source, identifier):
    if not isinstance(identifier,str) or not identifier.startswith("mls:"):
        raise ValueError("Unknown MLS challenge property")
    listing_key = identifier.removeprefix("mls:")
    items = source.listings([listing_key])
    if not items:
        raise ValueError("MLS challenge property is unavailable")
    return _public_challenge(items[0])


def freeze_challenge_batch(store, source, payload):
    name = payload.get("name","")
    if not isinstance(name,str) or not 1<=len(name.strip())<=100:
        raise ValueError("A challenge batch name is required")
    keys = payload.get("listing_keys")
    if not isinstance(keys,list) or not 1<=len(keys)<=20:
        raise ValueError("Select between one and 20 challenge properties")
    keys = list(dict.fromkeys(str(key) for key in keys if key))
    overlap = sorted(set(keys)&_training_listing_keys(store))
    if overlap:
        raise ValueError("Challenge batch overlaps the training or protected dataset")
    listings = source.listings(keys)
    if len(listings)!=len(keys):
        raise ValueError("One or more MLS challenge properties are unavailable")
    snapshots = [source.immutable_snapshot(item) for item in listings]
    fingerprint = digest(snapshots)
    identifier = uuid4().hex
    record = {
        "id":identifier,"name":name.strip(),"status":"frozen",
        "created_at":now(),"fingerprint":fingerprint,
        "source_mode":payload.get("source_mode","selected"),
        "seed":payload.get("seed"),"items":snapshots,
        "counts":{
            "properties":len(snapshots),
            "photos":sum(len(item["photos"]) for item in snapshots),
            "metadata_only":sum(not item["photos"] for item in snapshots),
        },
        "policy":"workbench-fixed-challenge-v1",
    }
    store.save_document("workbench-challenge-batch:"+identifier,record,0)
    return record


def challenge_batches(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT payload FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
              AND item_id LIKE 'workbench-challenge-batch:%%'
            ORDER BY updated_at DESC''',(store.workspace,)).fetchall()
    return {"items":[{
        key:value for key,value in row["payload"].items() if key!="items"
    } for row in rows]}


def challenge_batch(store, identifier):
    if not isinstance(identifier,str) or len(identifier)!=32:
        raise ValueError("Unknown fixed challenge batch")
    batch = store.document("workbench-challenge-batch:"+identifier)
    if not batch or batch.get("status")!="frozen":
        raise ValueError("Unknown fixed challenge batch")
    items = []
    for snapshot in batch.get("items",[]):
        listing_key = snapshot["listing_key"]
        metadata = snapshot.get("metadata",{})
        items.append({
            "id":f"challenge:{identifier}:{listing_key}",
            "batch_id":identifier,"listing_key":listing_key,
            "address":metadata.get("UnparsedAddress") or listing_key,
            "city":metadata.get("City"),"postal_code":metadata.get("PostalCode"),
            "metadata":metadata,"metadata_sha256":snapshot.get("metadata_sha256"),
            "images":[{
                **photo,"listing_key":listing_key,"batch_id":identifier,
            } for photo in snapshot.get("photos",[])],
            "photo_count":len(snapshot.get("photos",[])),
            "opportunity":snapshot.get("opportunity"),
            "transaction_history":metadata.get("PriorSales",[]),
            "fixed_challenge":True,
        })
    return {
        **{key:value for key,value in batch.items() if key!="items"},
        "items":items,
    }


def challenge_item(store, identifier):
    if not isinstance(identifier,str) or not identifier.startswith("challenge:"):
        raise ValueError("Unknown fixed challenge property")
    try:
        _,batch_id,listing_key = identifier.split(":",2)
    except ValueError as exc:
        raise ValueError("Unknown fixed challenge property") from exc
    batch = challenge_batch(store,batch_id)
    item = next((
        item for item in batch["items"] if item["listing_key"]==listing_key
    ),None)
    if not item:
        raise ValueError("Unknown fixed challenge property")
    return item


def property_detail(store, identifier):
    from acquisition_metadata import acquisition_time_metadata
    detail = store.property(identifier)
    source = (detail.get("historical_source") or {}).get("source") or {}
    v1_metadata = acquisition_time_metadata(
        detail["property"].get("metadata",{}),source,
    )
    transaction_history = []
    for prefix,label in (("Prior","Source prior sale"),("Last","Source last sale")):
        date = source.get(prefix+" Sale Date")
        amount = source.get(prefix+" Sale Amount")
        if date or amount:
            transaction_history.append({
                "label":label,"date":date,"price":amount,
                "model_use":"review evidence only; V1 uses only events before the listing snapshot",
            })
    with store.database.connect() as db:
        result = db.execute('''SELECT item_id,payload,revision FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'workbench-result:%%'
            AND payload->>'property_id'=%s ORDER BY updated_at DESC LIMIT 1''',
            (store.workspace,identifier)).fetchone()
        feedback = db.execute('''SELECT item_id,payload,revision FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'workbench-feedback:%%'
            AND payload->>'property_id'=%s ORDER BY updated_at DESC LIMIT 1''',
            (store.workspace,identifier)).fetchone()
    return {
        "property":detail["property"],
        "v1_metadata":v1_metadata,
        "images":[{
            "id":image["id"],"room":image.get("effective",{}).get("room"),
            "context":image.get("effective",{}).get("context"),
            "condition_draft":image.get("condition_draft"),
            "included":image.get("selection",{}).get("included",True),
            "sequence":image.get("sequence"),
        } for image in detail["images"][:12]],
        "coverage":detail.get("historical_source",{}).get("photo_coverage","unknown"),
        "transaction_history":transaction_history,
        "result":({**result["payload"],"revision":result["revision"]}
                  if result else None),
        "feedback":({**feedback["payload"],"revision":feedback["revision"]}
                    if feedback else None),
    }


def queue_run(store, payload):
    if not worker_state(store)["actionable"]:
        raise ValueError("Local model worker is offline. Start/restart the model worker before running this action.")
    identifier = payload.get("property_id")
    if not isinstance(identifier, str):
        raise ValueError("Property ID required")
    challenge = None
    if identifier.startswith("challenge:"):
        challenge = challenge_item(store,identifier)
    else:
        store.property(identifier)
    requested_mode = payload.get("requested_mode", "automatic")
    if requested_mode not in {"automatic","images_only","metadata_only","images_and_metadata"}:
        raise ValueError("Unsupported prediction mode")
    run_id = uuid4().hex
    record = {
        "id":run_id,"property_id":identifier,"status":"queued",
        "requested_mode":requested_mode,"blind":payload.get("blind") is True,
        "created_at":now(),"policy":"workbench-run-v1",
    }
    if challenge:
        record.update(
            challenge_batch_id=challenge["batch_id"],
            challenge_listing_key=challenge["listing_key"],
        )
    return store.save_document("workbench-run:"+run_id,record,0)


def run_status(store, identifier):
    if not isinstance(identifier,str) or len(identifier)!=32:
        raise ValueError("Unknown workbench run")
    request = store.document("workbench-run:"+identifier)
    if not request:
        raise ValueError("Unknown workbench run")
    result = store.document("workbench-result:"+identifier)
    feedback = store.document("workbench-feedback:"+identifier)
    return {"request":request,"result":result,"feedback":feedback}


def pending_runs(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT payload FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'workbench-run:%%'
            AND payload->>'status' IN ('queued','running')
            ORDER BY updated_at''',(store.workspace,)).fetchall()
    return [row["payload"] for row in rows]


def save_feedback(store, payload):
    run_id = payload.get("run_id")
    state = run_status(store, run_id)
    if not state["result"] or state["request"].get("status")!="completed":
        raise ValueError("Run must complete before feedback")
    target = payload.get("target_label")
    if target not in TARGET_LABELS:
        raise ValueError("Choose TARGET, NOT_TARGET or UNKNOWN")
    physical, modernization = validate_labels(
        payload.get("physical_condition","UNKNOWN"),
        payload.get("modernization_state","UNKNOWN"),
    )
    hard_negative = payload.get("hard_negative") is True
    if hard_negative and target!="NOT_TARGET":
        raise ValueError("Hard negative requires NOT_TARGET")
    challenge = state["request"]["property_id"].startswith("challenge:")
    promoted = payload.get("promote_to_dataset") is True
    if promoted and not challenge:
        raise ValueError("Only fixed challenge feedback requires explicit dataset promotion")
    if promoted and target=="UNKNOWN":
        raise ValueError("UNKNOWN challenge feedback cannot be promoted to a dataset")
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=100:
        raise ValueError("Reviewer name required")
    result = state["result"]
    photo_ids = {photo.get("image_id") for photo in result.get("photos",[])}
    excluded = payload.get("excluded_photo_ids",[])
    if not isinstance(excluded,list) or not set(excluded)<=photo_ids:
        raise ValueError("Excluded photo is outside this run")
    reason = payload.get("reason","")
    if not isinstance(reason,str) or len(reason)>2000:
        raise ValueError("Feedback reason is too long")
    record = {
        "run_id":run_id,"property_id":state["request"]["property_id"],
        "target_label":target,"hard_negative":hard_negative,
        "physical_condition":physical,"modernization_state":modernization,
        "excluded_photo_ids":list(dict.fromkeys(excluded)),
        "reason":reason.strip(),"reviewer":reviewer.strip(),
        "reviewed_at":now(),"model_version":result.get("model",{}).get("version"),
        "prediction_visible":payload.get("prediction_visible") is True,
        "promoted_to_dataset":promoted if challenge else True,
        "challenge_only":challenge and not promoted,
        "policy":"workbench-feedback-v1",
    }
    previous = state["feedback"] or {}
    return store.save_document(
        "workbench-feedback:"+run_id,record,previous.get("revision",0)
    )


def error_queue(store, args):
    category = args.get("category","all")
    allowed = {"all","false_positive","false_negative","disagreement",
               "metadata_only","limited_visual","hard_negative",
               "high_confidence_wrong","candidate_regression"}
    if category not in allowed:
        raise ValueError("Unknown error queue")
    with store.database.connect() as db:
        rows = db.execute('''SELECT f.payload feedback,r.payload result,
              f.updated_at reviewed_at
            FROM acq_training.studio_state f JOIN acq_training.studio_state r
              ON r.workspace_id=f.workspace_id AND r.kind='document'
              AND r.item_id='workbench-result:'||(f.payload->>'run_id')
            WHERE f.workspace_id=%s AND f.kind='document'
              AND f.item_id LIKE 'workbench-feedback:%%'
            ORDER BY f.updated_at DESC''',(store.workspace,)).fetchall()
    correctness = {}
    for row in rows:
        feedback,prediction = row["feedback"],row["result"].get("prediction",{})
        score,target = prediction.get("score"),feedback.get("target_label")
        if score is None or target not in {"TARGET","NOT_TARGET"}:
            continue
        value = float(score) if target=="TARGET" else 1-float(score)
        correctness.setdefault(feedback["property_id"],[]).append(
            (value,row["result"].get("model",{}).get("version"))
        )
    regressions = {
        property_id for property_id,values in correctness.items()
        if len({version for _,version in values})>1
        and values[0][0]+.1<max(value for value,_ in values[1:])
    }
    items = []
    for row in rows:
        feedback,result = row["feedback"],row["result"]
        prediction = result.get("prediction",{})
        scores = prediction.get("component_scores",{})
        target,score = feedback.get("target_label"),prediction.get("score")
        categories = []
        if target=="NOT_TARGET" and score is not None and score>=.7:
            categories.append("false_positive")
        if target=="TARGET" and score is not None and score<.5:
            categories.append("false_negative")
        available = [value for value in (scores.get("images"),scores.get("metadata"))
                     if value is not None]
        if len(available)==2 and abs(available[0]-available[1])>=.2:
            categories.append("disagreement")
        if prediction.get("mode_used")=="metadata_only":
            categories.append("metadata_only")
        if 0 < int(prediction.get("usable_photos") or 0) <= 2:
            categories.append("limited_visual")
        if feedback.get("hard_negative"):
            categories.append("hard_negative")
        if (
            (target=="NOT_TARGET" and score is not None and score>=.85)
            or (target=="TARGET" and score is not None and score<=.15)
        ):
            categories.append("high_confidence_wrong")
        if feedback["property_id"] in regressions:
            categories.append("candidate_regression")
        if category=="all" or category in categories:
            why = {
                "false_positive":"Model scored a reviewed NO at or above 70%.",
                "false_negative":"Model scored a reviewed YES below 50%.",
                "disagreement":"Image and metadata scores differ by at least 20 points.",
                "metadata_only":"No usable selected photo evidence was available.",
                "limited_visual":"Only one or two usable photos informed the result.",
                "hard_negative":"Reviewer marked this deceptively target-like NO.",
                "high_confidence_wrong":"Model was confidently opposite the human judgment.",
                "candidate_regression":"Latest reviewed model version is materially worse than an earlier one.",
            }
            items.append({
                "run_id":feedback["run_id"],"property_id":feedback["property_id"],
                "target_label":target,"hard_negative":feedback.get("hard_negative",False),
                "physical_condition":feedback.get("physical_condition"),
                "modernization_state":feedback.get("modernization_state"),
                "scores":scores,"score":score,"mode_used":prediction.get("mode_used"),
                "categories":categories,"model_version":result.get("model",{}).get("version"),
                "reason":feedback.get("reason"),
                "why":[why[value] for value in categories],
                "evidence":result.get("evidence",{}),
                "photos":result.get("photos",[])[:3],
                "reviewed_at":str(row.get("reviewed_at") or ""),
            })
    return {"items":items,"total":len(items),"category":category}


def feedback_snapshot(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT payload FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
              AND item_id LIKE 'workbench-feedback:%%'
            ORDER BY updated_at''',(store.workspace,)).fetchall()
    latest = {}
    for row in rows:
        payload = row["payload"]
        latest[payload["property_id"]] = payload
    return latest


def dataset_preview(store):
    from cloud_training import snapshot
    _, properties = snapshot(store)
    def dataset_split(prop):
        if prop["split"]=="test":
            return "test"
        value = int(hashlib.sha256(
            ("workbench-validation-v1:"+str(prop["group_id"])).encode()
        ).hexdigest()[:8],16)/2**32
        return "validation" if value<.15 else "train"
    known = {
        prop["id"]: {
            "property_id":prop["id"],"group_id":prop["group_id"],"split":dataset_split(prop),
            "target_label":"TARGET","hard_negative":False,
            "physical_condition":prop.get("review",{}).get("physical_condition","UNKNOWN"),
            "modernization_state":prop.get("review",{}).get("modernization_state","UNKNOWN"),
            "origin":prop.get("target_origin"),
        } for prop in properties if prop.get("known_target") and not prop.get("label_exclusion")
    }
    feedback = feedback_snapshot(store)
    by_property = {prop["id"]:prop for prop in properties}
    for property_id,review in feedback.items():
        prop = by_property.get(property_id)
        if review["target_label"]=="UNKNOWN":
            continue
        if prop:
            group_id,split = prop["group_id"],dataset_split(prop)
            origin = "workbench-feedback"
            challenge_batch_id = None
        elif property_id.startswith("challenge:") and review.get("promoted_to_dataset") is True:
            item = challenge_item(store,property_id)
            group_id = "mls-challenge:"+item["listing_key"]
            split = dataset_split({"group_id":group_id,"split":"train"})
            origin = "promoted-challenge-feedback"
            challenge_batch_id = item["batch_id"]
        else:
            continue
        known[property_id] = {
            "property_id":property_id,"group_id":group_id,"split":split,
            "target_label":review["target_label"],
            "hard_negative":review.get("hard_negative",False),
            "physical_condition":review.get("physical_condition","UNKNOWN"),
            "modernization_state":review.get("modernization_state","UNKNOWN"),
            "excluded_photo_ids":review.get("excluded_photo_ids",[]),
            "origin":origin,
        }
        if challenge_batch_id:
            known[property_id]["challenge_batch_id"] = challenge_batch_id
    examples = sorted(known.values(),key=lambda row:row["property_id"])
    fingerprint = digest(examples)
    latest = store.document("workbench-dataset-latest")
    previous_examples = {
        row["property_id"]:row for row in (latest or {}).get("examples",[])
    }
    current_examples = {row["property_id"]:row for row in examples}
    added = sorted(set(current_examples)-set(previous_examples))
    removed = sorted(set(previous_examples)-set(current_examples))
    changed = sorted(
        key for key in set(current_examples)&set(previous_examples)
        if current_examples[key]!=previous_examples[key]
    )
    protected_after = sorted(
        [row for row in examples if row["split"]=="test"],
        key=lambda row:row["property_id"],
    )
    protected_before = sorted(
        [
            row for row in (latest or {}).get("examples",[])
            if row["split"]=="test"
        ],
        key=lambda row:row["property_id"],
    )
    if not latest:
        protected_before = protected_after
    protected_unchanged = protected_before==protected_after
    preview = {
        "id":uuid4().hex,"status":"preview","created_at":now(),
        "fingerprint":fingerprint,"examples":examples,
        "counts":{
            "properties":len(examples),
            "targets":sum(row["target_label"]=="TARGET" for row in examples),
            "not_targets":sum(row["target_label"]=="NOT_TARGET" for row in examples),
            "hard_negatives":sum(row["hard_negative"] for row in examples),
            "train":sum(row["split"]=="train" for row in examples),
            "validation":sum(row["split"]=="validation" for row in examples),
            "test":sum(row["split"]=="test" for row in examples),
        },
        "changes":{
            "added":added,"removed":removed,"changed":changed,
        },
        "change_details":[{
            "property_id":key,"change":"added",
            "before":None,"after":current_examples[key],
        } for key in added]+[{
            "property_id":key,"change":"removed",
            "before":previous_examples[key],"after":None,
        } for key in removed]+[{
            "property_id":key,"change":"changed",
            "before":previous_examples[key],"after":current_examples[key],
        } for key in changed],
        "protected_groups_unchanged":protected_unchanged,
        "protected_test_before_sha256":digest(protected_before),
        "protected_test_after_sha256":digest(protected_after),
        "policy":"workbench-dataset-v1",
    }
    class_counts = {
        split:{
            label:sum(row["split"]==split and row["target_label"]==label for row in examples)
            for label in ("TARGET","NOT_TARGET")
        } for split in ("train","validation","test")
    }
    reasons = []
    if min(class_counts["train"].values())<5:
        reasons.append("Train split needs at least five TARGET and five NOT_TARGET properties")
    if min(class_counts["validation"].values())<2:
        reasons.append("Validation split needs at least two properties in each class")
    if min(class_counts["test"].values())<1:
        reasons.append("Protected test needs at least one reviewed property in each class")
    if not protected_unchanged:
        reasons.append("Protected test changed; freeze is blocked")
    preview.update(class_counts=class_counts,trainable=not reasons,reasons=reasons)
    store.save_document("workbench-dataset-preview:"+preview["id"],preview,0)
    return preview


def freeze_dataset(store, payload):
    identifier = payload.get("id")
    if payload.get("confirmed") is not True:
        raise ValueError("Explicit dataset freeze confirmation required")
    preview = store.document("workbench-dataset-preview:"+str(identifier))
    if not preview or preview.get("status")!="preview":
        raise ValueError("Create a current dataset preview")
    if digest(preview["examples"])!=preview["fingerprint"]:
        raise ValueError("Dataset preview fingerprint changed")
    if preview.get("protected_groups_unchanged") is not True:
        raise ValueError("Protected test changed; dataset freeze is blocked")
    current_feedback = feedback_snapshot(store)
    current_reviewed = sorted(
        key for key,value in current_feedback.items()
        if value.get("target_label")!="UNKNOWN"
        and (
            not key.startswith("challenge:")
            or value.get("promoted_to_dataset") is True
        )
    )
    preview_reviewed = sorted(
        row["property_id"] for row in preview["examples"]
        if row.get("origin") in {
            "workbench-feedback","promoted-challenge-feedback",
        }
    )
    if current_reviewed!=preview_reviewed:
        raise ValueError("Feedback changed; create a new dataset preview")
    latest = store.document("workbench-dataset-latest")
    version = int((latest or {}).get("version",0))+1
    frozen = {
        **preview,"status":"frozen","version":version,"frozen_at":now(),
        "dataset_version":f"Dataset V{version}",
    }
    store.save_document("workbench-dataset:"+preview["fingerprint"],frozen,0)
    store.save_document(
        "workbench-dataset-latest",frozen,(latest or {}).get("revision",0)
    )
    return frozen


def summary(store):
    return {
        "baseline":store.document("model-baseline:v0-positive-similarity"),
        "dataset":store.document("workbench-dataset-latest"),
        "candidate":store.document("workbench-candidate-latest"),
        "training":store.document("workbench-training-latest"),
        "worker":worker_state(store),
    }


def candidate_history(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT payload FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
              AND item_id LIKE 'workbench-candidate:%%'
            ORDER BY updated_at DESC''',(store.workspace,)).fetchall()
    baseline = store.document("model-baseline:v0-positive-similarity")
    items = []
    if baseline:
        items.append({
            "id":"v0-positive-similarity",
            "version":baseline.get("version","V0 positive similarity"),
            "kind":"positive_similarity_baseline",
            "dataset_version":baseline.get("review_fingerprint"),
            "metrics":baseline.get("metrics",{}),
            "immutable":True,
        })
    items.extend({
        "id":row["payload"]["id"],
        "version":row["payload"].get("version",row["payload"]["id"]),
        "kind":"target_classifier_v1",
        "dataset_version":row["payload"].get("dataset_version"),
        "dataset_fingerprint":row["payload"].get("dataset_fingerprint"),
        "artifact_sha256":row["payload"].get("artifact_sha256"),
        "completed_at":row["payload"].get("completed_at"),
        "metrics":row["payload"].get("metrics",{}),
        "immutable":True,
    } for row in rows)
    return {"items":items}


def compare_candidates(store, args):
    history = candidate_history(store)["items"]
    by_id = {item["id"]:item for item in history}
    left,right = by_id.get(args.get("left")),by_id.get(args.get("right"))
    if not left or not right:
        raise ValueError("Choose two saved model versions")
    names = (
        "balanced_accuracy","precision","recall","f1","roc_auc","pr_auc",
        "brier","ndcg","precision_at_20","recall_at_20",
    )
    def classifier_metrics(item):
        if item["kind"]!="target_classifier_v1":
            return {}
        return item.get("metrics",{}).get("protected_test",{}).get("fusion",{})
    left_metrics,right_metrics = classifier_metrics(left),classifier_metrics(right)
    deltas = []
    for name in names:
        before,after = left_metrics.get(name),right_metrics.get(name)
        delta = None
        if isinstance(before,(int,float)) and isinstance(after,(int,float)):
            delta = after-before
        deltas.append({
            "metric":name,"left":before,"right":after,"delta":delta,
            "direction":"lower_is_better" if name=="brier" else "higher_is_better",
        })
    return {
        "left":{key:value for key,value in left.items() if key!="metrics"},
        "right":{key:value for key,value in right.items() if key!="metrics"},
        "metrics":deltas,
        "comparable":bool(left_metrics and right_metrics),
        "notice":(
            "V0 positive-similarity metrics are not classifier metrics and cannot "
            "be treated as an A/B accuracy comparison."
            if not left_metrics or not right_metrics else
            "Positive delta is better except for Brier score, where lower is better."
        ),
    }


def queue_training(store, payload):
    if not worker_state(store)["actionable"]:
        raise ValueError("Local model worker is offline. Start/restart the model worker before running this action.")
    dataset = store.document("workbench-dataset-latest")
    if not dataset or dataset.get("status")!="frozen":
        raise ValueError("Freeze a dataset version before training")
    if not dataset.get("trainable"):
        raise ValueError("; ".join(dataset.get("reasons") or ["Dataset class coverage is insufficient"]))
    if payload.get("dataset_fingerprint")!=dataset.get("fingerprint"):
        raise ValueError("Dataset changed; reload before training")
    current = store.document("workbench-training-latest")
    if current and current.get("status") in {"queued","running"}:
        raise RuntimeError("A workbench training job is already active")
    identifier = uuid4().hex
    request = {
        "id":identifier,"status":"queued","dataset_fingerprint":dataset["fingerprint"],
        "dataset_version":dataset["dataset_version"],"created_at":now(),
        "components":["metadata","vision","fusion"],"policy":"workbench-training-v1",
    }
    store.save_document("workbench-training:"+identifier,request,0)
    store.save_document(
        "workbench-training-latest",request,(current or {}).get("revision",0)
    )
    return request


def pending_training(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT payload FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'workbench-training:%%'
            AND payload->>'status' IN ('queued','running')
            ORDER BY updated_at LIMIT 1''',(store.workspace,)).fetchall()
    return rows[0]["payload"] if rows else None
