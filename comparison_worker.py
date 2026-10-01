"""Explicit local SigLIP comparison, isolated from the web process."""
import argparse
import hashlib
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

import pilot
from acq_exchange import validate_request
from property_models import normalize_context, predict_property, usable_context

MAX_IMAGE_BYTES = 4_000_000


def public_addresses(host):
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Photo host must resolve only to public addresses")
    return addresses


def download(url, destination):
    parsed = urlsplit(url)
    addresses = public_addresses(parsed.hostname)
    # Pin the checked IP for this request, preserving the original TLS SNI and
    # certificate validation. No redirects, proxy environment, cookies or keys.
    address = addresses[0]
    netloc = f"[{address}]" if ":" in address else address
    pinned = urlunsplit(("https", netloc, parsed.path, parsed.query, ""))
    with httpx.Client(trust_env=False, timeout=30, follow_redirects=False) as client:
        with client.stream("GET", pinned, headers={"Host": parsed.hostname},
                           extensions={"sni_hostname": parsed.hostname.encode()}) as response:
            response.raise_for_status()
            if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
                raise ValueError("Photo URL did not return an image; export a fresh request if it expired")
            size = 0
            with destination.open("wb") as target:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("Photo exceeds the 4 MB comparison limit")
                    target.write(chunk)


def infer(paths, bundle, backbone):
    from PIL import Image, ImageOps
    from transformers import AutoImageProcessor, SiglipVisionModel
    torch = pilot.torch_setup()
    Image.MAX_IMAGE_PIXELS = 25_000_000
    encoder = SiglipVisionModel.from_pretrained(backbone["snapshot_path"], local_files_only=True).eval()
    processor = AutoImageProcessor.from_pretrained(backbone["snapshot_path"], local_files_only=True, use_fast=False)
    predictions = []
    for path in paths:  # One image at a time keeps peak memory bounded.
        with Image.open(path) as source:
            if source.width * source.height > 25_000_000:
                raise ValueError("Photo exceeds the pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
        with torch.inference_mode():
            vector = encoder(**processor(images=[image], return_tensors="pt")).pooler_output
            vector = torch.nn.functional.normalize(vector.float(), dim=-1).cpu().numpy()
        image.close()
        room = str(bundle["room_model"].predict(vector)[0])
        context_model = bundle.get("context_model")
        context = str(context_model.predict(vector)[0]) if context_model is not None else "unknown"
        context = normalize_context(context)
        head = bundle.get("preference_models", {}).get(room)
        score = (
            float(head.predict_proba(vector)[0, list(head.classes_).index(1)])
            if usable_context(context) and head is not None else None
        )
        predictions.append({
            "room": room, "context": context, "usable_for_property": usable_context(context),
            "preference_score": score, "_embedding": vector[0],
        })
    return predictions


def process(folder, downloader=download, predictor=infer):
    import joblib
    frozen = pilot.read_json(folder / "input.json")
    request = frozen["request"]
    validate_request(request)
    file = Path(frozen["model_file"]).resolve()
    if not file.is_relative_to(pilot.ARTIFACTS.resolve()) or pilot.sha(file) != frozen["model"]["heads_sha256"]:
        raise ValueError("Frozen model identity changed")
    # Only operator-owned, checksum-verified local artifacts are loaded. No
    # uploaded model files or request-supplied paths are ever deserialized.
    bundle = joblib.load(file)
    backbone = pilot.read_json(pilot.ARTIFACTS / "backbone.json")
    if bundle["backbone_revision"] != backbone["revision"]:
        raise ValueError("Model and backbone versions differ")
    snapshot = pilot.read_json(Path(frozen["training_snapshot"]))
    from model_loop import fingerprint
    if fingerprint(snapshot["examples"], snapshot.get("properties", [])) != frozen["model"]["review_fingerprint"]:
        raise ValueError("Training snapshot changed")
    known = {row["sha256"] for row in snapshot["examples"] if row["split"] != "test"}
    paths = []
    try:
        for photo in request["photos"]:
            path = folder / (photo["image_id"] + ".image")
            paths.append(path)
            downloader(photo["url"], path)
        scores = predictor(paths, bundle, backbone) if paths else []
        if len(scores) != len(paths):
            raise ValueError("Incomplete photo predictions")
        photos = []
        usable_vectors = []
        for source, path, score in zip(request["photos"], paths, scores):
            image_hash = pilot.sha(path)
            score = dict(score)
            vector = score.pop("_embedding", None)
            if vector is not None and score.get("usable_for_property", True):
                usable_vectors.append(vector)
            photos.append({"image_id": source["image_id"], "image_sha256": image_hash,
                           **score, "training_overlap": "seen_image" if image_hash in known else "unknown"})
        model = {
            "version": frozen["model"]["version"],
            "heads_sha256": frozen["model"]["heads_sha256"],
            "backbone_revision": backbone["revision"],
            "preprocessing": "siglip2-224-exif-rgb-l2-v1",
            "review_fingerprint": frozen["model"]["review_fingerprint"],
            "reviews_current": True,
            "components": frozen["model"].get("components", {}),
            "component_versions": frozen["model"].get("component_versions", {}),
        }
        if request["schema_version"] == "acq-property-request-v2":
            prediction = predict_property(
                bundle, usable_vectors, request["metadata"], request["requested_mode"]
            )
            output = {
                "schema_version": "acq-property-result-v2",
                "comparison_id": request["comparison_id"],
                "property_id": request["property_id"],
                "request_sha256": request["request_sha256"],
                "photos": photos,
                "prediction": prediction,
                "model": model,
            }
        else:
            output = {
                "schema_version": "acq-siglip-result-v1",
                "comparison_id": request["comparison_id"],
                "request_sha256": request["request_sha256"],
                "photos": photos,
                "model": model,
            }
        pilot.write_json(folder / "result.json", output)
        return output
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    folder = parser.parse_args().job.resolve()
    if not folder.is_relative_to((pilot.ARTIFACTS / "acq_comparisons").resolve()):
        raise ValueError("Unexpected comparison folder")
    process(folder)
