"""Local scorer for queued hosted workbench runs."""
from __future__ import annotations

import hashlib
import io
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from PIL import Image, ImageOps

import pilot
from condition_schema import legacy_labels
from model_loop import candidate
from property_models import predict_property
from studio_data import now


class WorkbenchScorer:
    def __init__(self, store, siglip):
        self.store,self.siglip = store,siglip
        self.pointer = self.bundle = None
        self.load_current()

    def load_current(self):
        workbench = self.store.document("workbench-candidate-latest")
        if workbench and workbench.get("status")=="completed":
            path = Path(workbench["artifact_path"]).resolve()
            root = (pilot.ARTIFACTS/"workbench_candidates").resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("Saved V1 candidate artifact is unavailable")
            if pilot.sha(path)!=workbench.get("artifact_sha256"):
                raise ValueError("Saved V1 candidate artifact hash changed")
            if not self.pointer or self.pointer.get("version")!=workbench["version"]:
                self.pointer = {
                    "version":workbench["version"],
                    "heads_sha256":workbench["artifact_sha256"],
                    "review_fingerprint":workbench["dataset_fingerprint"],
                    "components":{"metadata":True,"vision":True,"fusion":True},
                }
                self.bundle = joblib.load(path)
            return
        pointer,file = candidate(pilot.ROOT)
        if not self.pointer or self.pointer.get("version")!=pointer["version"]:
            self.pointer = pointer
            self.bundle = joblib.load(file)

    def embeddings(self, paths):
        images = []
        for path in paths:
            with Image.open(path) as source:
                images.append(ImageOps.exif_transpose(source).convert("RGB"))
        return self._embed_images(images)

    def embedding_blobs(self, blobs):
        images = []
        for blob in blobs:
            with Image.open(io.BytesIO(blob)) as source:
                images.append(ImageOps.exif_transpose(source).convert("RGB"))
        return self._embed_images(images)

    def _embed_images(self, source_images):
        vectors = []
        for start in range(0,len(source_images),4):
            images = source_images[start:start+4]
            inputs = self.siglip.processor(images=images,return_tensors="pt")
            with self.siglip.torch.inference_mode():
                values = self.siglip.model.get_image_features(
                    pixel_values=inputs["pixel_values"]
                )
                values = self.siglip.torch.nn.functional.normalize(
                    values.float(),dim=-1
                ).cpu().numpy()
            vectors.extend(values)
        return vectors

    def score(self, identifier, requested_mode="automatic"):
        self.load_current()
        if identifier.startswith("challenge:"):
            from mls_source import ExistingMLSSupabaseSource
            from model_workbench import challenge_item
            item = challenge_item(self.store,identifier)
            if not hasattr(self,"mls_source"):
                self.mls_source = ExistingMLSSupabaseSource.from_env()
            eligible = [{
                "id":identifier+":"+photo["media_key"],
                "effective":{"room":photo.get("room"),"context":"interior_or_listing"},
                "selection":{"included":True},
                "condition_draft":"unknown",
                "challenge_photo":photo,
            } for photo in item.get("images",[])][:12]
            blobs = []
            for image in eligible:
                photo = image["challenge_photo"]
                blob = self.mls_source.image_bytes(
                    item["listing_key"],photo["media_key"],
                )
                if hashlib.sha256(blob).hexdigest()!=photo.get("sha256"):
                    raise ValueError(
                        "MLS photo changed after the challenge batch was frozen"
                    )
                blobs.append(blob)
            vectors = self.embedding_blobs(blobs) if blobs else []
            metadata = item.get("metadata",{})
            detail = {
                "property":{
                    "metadata":metadata,
                    "mls_remarks":metadata.get("PublicRemarks") or "",
                },
                "historical_source":{
                    "photo_coverage":"selected_mls_media" if eligible else "metadata_only",
                },
            }
            group = None
        else:
            detail = self.store.property(identifier)
            with self.store.database.connect() as db:
                group = db.execute('''SELECT group_id FROM acq_training.examples
                    WHERE workspace_id=%s AND listing_key=%s ORDER BY id LIMIT 1''',
                    (self.store.workspace,identifier)).fetchone()
            eligible = [
                image for image in detail["images"]
                if image.get("selection",{}).get("included",True)
                and image.get("effective",{}).get("context")
                    not in {"shared_amenity","floor_plan","unrelated"}
            ][:12]
            paths = [self.store.image_path(image["id"]) for image in eligible]
            vectors = self.embeddings(paths) if paths else []
        if self.bundle.get("metadata_model") and self.bundle.get("vision_model"):
            metadata_score = float(self.bundle["metadata_model"].predict(
                [detail["property"].get("metadata",{})],
                [detail["property"].get("mls_remarks") or ""],
            )[0])
            vision_values = self.bundle["vision_model"].predict([vectors])
            vision_score = (
                float(vision_values[0]) if np.isfinite(vision_values[0]) else None
            )
            combined_score = float(self.bundle["fusion_model"].predict(
                [metadata_score],
                [vision_score if vision_score is not None else np.nan],
                [len(vectors)],
            )[0])
            available = {
                "metadata_only":metadata_score,
                "images_only":vision_score,
                "images_and_metadata":combined_score if vision_score is not None else None,
            }
            if requested_mode=="automatic":
                mode = "images_and_metadata" if vision_score is not None else "metadata_only"
            else:
                mode = requested_mode if available.get(requested_mode) is not None else "insufficient_evidence"
            score = available.get(mode)
            prediction = {
                "requested_mode":requested_mode,"mode_used":mode,"score":score,
                "component_scores":{
                    "metadata":metadata_score,"images":vision_score,
                    "combined":combined_score if vision_score is not None else None,
                },
                "score_kind":"target_probability_v1",
                "evidence_confidence":"low","usable_photos":len(vectors),
                "metadata_explanation":self.bundle["metadata_model"].explain(
                    detail["property"].get("metadata",{}),
                    detail["property"].get("mls_remarks") or "",
                ),
                "warnings":["Candidate output requires human review; no production promotion."],
                "decision":"NEEDS_REVIEW",
            }
            influences = self.bundle["vision_model"].image_influence(vectors)
        elif self.bundle.get("target_similarity"):
            from target_similarity import predict_similarity
            prediction = predict_similarity(
                self.bundle["target_similarity"],vectors,
                detail["property"].get("metadata"),requested_mode,
                exclude_group=str(group["group_id"]) if group else None,
            )
            influences = [
                predict_similarity(
                    self.bundle["target_similarity"],[vector],{},"images_only",
                    exclude_group=str(group["group_id"]) if group else None,
                ).get("score")
                for vector in vectors
            ]
        else:
            prediction = predict_property(
                self.bundle,vectors,detail["property"].get("metadata"),requested_mode
            )
            influences = [None]*len(vectors)
        photos = []
        for image,influence in zip(eligible,influences):
            photos.append({
                "image_id":image["id"],
                "room":image.get("effective",{}).get("room"),
                "context":image.get("effective",{}).get("context"),
                "single_photo_target_score":float(influence) if influence is not None else None,
                "condition_draft":image.get("condition_draft","unknown"),
            })
        photos.sort(key=lambda row:(
            row["single_photo_target_score"] is None,
            -(row["single_photo_target_score"] or 0),row["image_id"]
        ))
        conditions = [
            image.get("condition_draft") for image in eligible
            if image.get("condition_draft") not in {None,"unknown"}
        ]
        legacy = Counter(conditions).most_common(1)[0][0] if conditions else "unknown"
        physical,modernization = legacy_labels(legacy)
        return {
            "property_id":identifier,"completed_at":now(),
            "prediction":prediction,"photos":photos,
            "condition":{
                "physical_condition":physical,
                "modernization_state":modernization,
                "source":"draft aggregation; human correction required",
            },
            "evidence":{
                "eligible_images":len(eligible),
                "photo_coverage":detail.get("historical_source",{}).get("photo_coverage"),
                "metadata_fields":sorted(detail["property"].get("metadata",{})),
                "remarks":detail["property"].get("mls_remarks"),
            },
            "model":{
                "version":self.pointer["version"],
                "heads_sha256":self.pointer["heads_sha256"],
                "review_fingerprint":self.pointer.get("review_fingerprint"),
                "components":self.pointer.get("components",{}),
            },
            "policy":"workbench-result-v1",
        }


def process_pending_runs(store, scorer):
    from model_workbench import pending_runs
    processed = 0
    for request in pending_runs(store):
        key = "workbench-run:"+request["id"]
        current = store.document(key)
        if not current or current.get("status")!="queued":
            continue
        claimed = store.save_document(
            key,{**current,"status":"running","started_at":now()},current["revision"]
        )
        try:
            result = scorer.score(
                claimed["property_id"],claimed.get("requested_mode","automatic")
            )
            previous = store.document("workbench-result:"+claimed["id"]) or {}
            store.save_document(
                "workbench-result:"+claimed["id"],
                {**result,"run_id":claimed["id"]},previous.get("revision",0)
            )
            latest = store.document(key)
            store.save_document(
                key,{**{k:v for k,v in latest.items() if k!="revision"},
                     "status":"completed","completed_at":now(),
                     "model_version":result["model"]["version"]},
                latest["revision"],
            )
            processed += 1
        except Exception:
            latest = store.document(key)
            store.save_document(
                key,{**{k:v for k,v in latest.items() if k!="revision"},
                     "status":"failed","completed_at":now(),
                     "error":"Model run failed; verify local candidate and evidence."},
                latest["revision"],
            )
    return processed
