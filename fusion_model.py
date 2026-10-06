"""Explicit missing-modality fusion features for reviewed physical evidence."""
from dataclasses import dataclass

from sklearn.feature_extraction import DictVectorizer

from actvision_contract import unknown_result
from structured_model import EvidenceHeads

FEATURE_SCHEMA_VERSION = "actvision-fusion-v2"


def fusion_features(components, coverage):
    row = {}
    for name in ("vision", "text", "structured"):
        component = components[name]
        available = component["status"] == "available"
        row[name + "_available"] = float(available)
        if available:
            if not component["calibration_version"]:
                raise ValueError("Fusion requires pinned component calibration")
            for axis in ("condition_probabilities", "modernization_probabilities", "acquisition_fit_probabilities"):
                for label, score in component["result"][axis].items():
                    row[f"{name}:{axis}:{label}"] = score
    row.update({
        "usable_photo_count": coverage["usable_photo_count"],
        "text_available": float(coverage["text_available"]),
        "structured_completeness": coverage["structured_completeness"],
    })
    labels = [component["result"]["physical_condition"] for name, component in components.items()
              if name != "fusion" and component["status"] == "available"
              and component["result"]["physical_condition"] != "UNKNOWN"]
    row["condition_disagreement"] = float(len(set(labels)) > 1)
    return row


@dataclass
class FusionModel:
    vectorizer: DictVectorizer
    heads: EvidenceHeads
    feature_schema_version: str = FEATURE_SCHEMA_VERSION

    @classmethod
    def fit(cls, components, coverage, labels, *, prediction_source):
        if prediction_source != "grouped_oof_training":
            raise ValueError("Fusion requires grouped out-of-fold TRAIN predictions, never protected test or in-sample scores")
        if not len(components) == len(coverage) == len(labels):
            raise ValueError("Fusion evidence and labels must align")
        vectorizer = DictVectorizer()
        matrix = vectorizer.fit_transform([fusion_features(c, v) for c, v in zip(components, coverage)])
        return cls(vectorizer, EvidenceHeads.fit(matrix, labels))

    def predict(self, components, coverage):
        if not len(components) == len(coverage):
            raise ValueError("Fusion evidence and coverage must align")
        matrix = self.vectorizer.transform([fusion_features(c, v) for c, v in zip(components, coverage)])
        results = self.heads.predict(matrix)
        return [result if any(c[name]["status"] == "available" for name in ("vision", "text", "structured"))
                else unknown_result() for c, result in zip(components, results)]
