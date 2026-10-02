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


def property_detail(store, identifier):
    detail = store.property(identifier)
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
        "images":[{
            "id":image["id"],"room":image.get("effective",{}).get("room"),
            "context":image.get("effective",{}).get("context"),
            "condition_draft":image.get("condition_draft"),
            "included":image.get("selection",{}).get("included",True),
            "sequence":image.get("sequence"),
        } for image in detail["images"][:12]],
        "coverage":detail.get("historical_source",{}).get("photo_coverage","unknown"),
        "result":({**result["payload"],"revision":result["revision"]}
                  if result else None),
        "feedback":({**feedback["payload"],"revision":feedback["revision"]}
                    if feedback else None),
    }


def queue_run(store, payload):
    identifier = payload.get("property_id")
    if not isinstance(identifier, str):
        raise ValueError("Property ID required")
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
        "policy":"workbench-feedback-v1",
    }
    previous = state["feedback"] or {}
    return store.save_document(
        "workbench-feedback:"+run_id,record,previous.get("revision",0)
    )


def error_queue(store, args):
    category = args.get("category","all")
    allowed = {"all","false_positive","false_negative","disagreement",
               "metadata_only","limited_visual","hard_negative"}
    if category not in allowed:
        raise ValueError("Unknown error queue")
    with store.database.connect() as db:
        rows = db.execute('''SELECT f.payload feedback,r.payload result
            FROM acq_training.studio_state f JOIN acq_training.studio_state r
              ON r.workspace_id=f.workspace_id AND r.kind='document'
              AND r.item_id='workbench-result:'||(f.payload->>'run_id')
            WHERE f.workspace_id=%s AND f.kind='document'
              AND f.item_id LIKE 'workbench-feedback:%%'
            ORDER BY f.updated_at DESC''',(store.workspace,)).fetchall()
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
        if category=="all" or category in categories:
            items.append({
                "run_id":feedback["run_id"],"property_id":feedback["property_id"],
                "target_label":target,"hard_negative":feedback.get("hard_negative",False),
                "physical_condition":feedback.get("physical_condition"),
                "modernization_state":feedback.get("modernization_state"),
                "scores":scores,"score":score,"mode_used":prediction.get("mode_used"),
                "categories":categories,"model_version":result.get("model",{}).get("version"),
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
        if not prop or review["target_label"]=="UNKNOWN":
            continue
        known[property_id] = {
            "property_id":property_id,"group_id":prop["group_id"],"split":dataset_split(prop),
            "target_label":review["target_label"],
            "hard_negative":review.get("hard_negative",False),
            "physical_condition":review.get("physical_condition","UNKNOWN"),
            "modernization_state":review.get("modernization_state","UNKNOWN"),
            "excluded_photo_ids":review.get("excluded_photo_ids",[]),
            "origin":"workbench-feedback",
        }
    examples = sorted(known.values(),key=lambda row:row["property_id"])
    fingerprint = digest(examples)
    latest = store.document("workbench-dataset-latest")
    previous_examples = {
        row["property_id"]:row for row in (latest or {}).get("examples",[])
    }
    current_examples = {row["property_id"]:row for row in examples}
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
            "added":sorted(set(current_examples)-set(previous_examples)),
            "removed":sorted(set(previous_examples)-set(current_examples)),
            "changed":sorted(key for key in set(current_examples)&set(previous_examples)
                             if current_examples[key]!=previous_examples[key]),
        },
        "protected_groups_unchanged":True,
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
    current_feedback = feedback_snapshot(store)
    current_reviewed = sorted(
        key for key,value in current_feedback.items()
        if value.get("target_label")!="UNKNOWN"
    )
    preview_reviewed = sorted(
        row["property_id"] for row in preview["examples"]
        if row.get("origin")=="workbench-feedback"
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
        "worker":store.document("workbench-worker"),
    }


def queue_training(store, payload):
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
