"""Frozen SigLIP image/text suggestions. Never writes human training labels."""
import hashlib
import json
import math

POLICY = "siglip-room-context-v1"
TYPE_PROMPTS = {
    "interior": "the interior of a house or apartment",
    "exterior": "the outside facade of a house or apartment building",
    "outdoor": "a yard, garden, patio or balcony outside a home",
    "pool": "a swimming pool, gym or recreation area",
    "floor_plan": "an architectural floor plan drawing",
    "map": "a street map or aerial location map",
    "document": "a document, advertisement, chart or text graphic",
    "other": "an unclear, unrelated or unrecognizable image",
}
ROOM_PROMPTS = {
    "kitchen": "a kitchen with cooking appliances and kitchen cabinets",
    "bathroom": "a bathroom with a toilet, bathtub, shower or bathroom sink",
    "living": "a living room or family room inside a home",
    "bedroom": "a bedroom inside a home",
    "other": "another indoor room such as a dining room, office, hallway, laundry or garage",
}
PROMPTS = [f"This is a photo of {text}.".lower()
           for text in [*TYPE_PROMPTS.values(), *ROOM_PROMPTS.values()]]
PROMPT_HASH = hashlib.sha256(json.dumps(PROMPTS).encode()).hexdigest()


def choice(logits, labels):
    """Conservative abstention heuristic, not a calibrated probability."""
    if len(logits) != len(labels) or not all(math.isfinite(float(v)) for v in logits):
        raise ValueError("Invalid SigLIP scores")
    peak = max(logits)
    values = [math.exp(float(v)-peak) for v in logits]
    scores = {label: value/sum(values) for label, value in zip(labels, values)}
    ranked = sorted(scores, key=scores.get, reverse=True)
    top, second = ranked[:2]
    confident = scores[top] >= .55 and scores[top]-scores[second] >= .15
    return top if confident else None, scores


def resolve(logits):
    photo_type, type_scores = choice(logits[:len(TYPE_PROMPTS)], TYPE_PROMPTS)
    room, room_scores = choice(logits[len(TYPE_PROMPTS):], ROOM_PROMPTS)
    context = "unknown"
    uncertain = photo_type is None or photo_type == "other"
    if photo_type == "interior":
        context = "subject"
        uncertain = uncertain or room is None or room == "other"
    elif photo_type == "exterior":
        room, context = "exterior", "subject"
    elif photo_type in {"outdoor", "pool"}:
        room = "outdoor"
        # A pool/gym image alone does not establish private vs HOA ownership.
        context = "subject" if photo_type == "outdoor" else "unknown"
        uncertain = photo_type == "pool"
    elif photo_type == "floor_plan":
        room, context = "other", "floor_plan"
    elif photo_type in {"map", "document"}:
        room, context = "other", "unrelated"
    else:
        room = "other"
    return {"room": room or "other", "context": context,
            "photo_type": photo_type or "unknown", "uncertain": uncertain,
            "scores_uncalibrated": {"photo_type": type_scores, "room": room_scores},
            "features": {}, "policy": POLICY, "prompt_hash": PROMPT_HASH,
            "provenance": "SigLIP suggestion; not human-approved or calibrated",
            "limitations": ["Room/photo type only; no condition, target or sale verification."]}


class SiglipLabels:
    def __init__(self):
        import pilot
        from transformers import AutoModel, AutoProcessor
        self.torch = pilot.torch_setup()
        path, info = pilot.checkpoint_snapshot()
        self.revision = info["revision"]
        self.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        self.model = AutoModel.from_pretrained(path, local_files_only=True).eval()
        for parameter in self.model.parameters(): parameter.requires_grad_(False)

    def classify(self, paths):
        from PIL import Image, ImageOps
        images = []
        for path in paths:
            with Image.open(path) as image: images.append(ImageOps.exif_transpose(image).convert("RGB"))
        inputs = self.processor(text=PROMPTS, images=images, padding="max_length",
                                truncation=True, max_length=64, return_tensors="pt")
        with self.torch.inference_mode():
            logits = self.model(**inputs).logits_per_image.float().cpu().tolist()
        return [{**resolve(row), "backbone_revision": self.revision} for row in logits]


def active_policy():
    import os
    if os.environ.get('STUDIO_AUTOLABEL_PROVIDER') == 'openai':
        from openai_labels import policy
        return policy()
    return POLICY
