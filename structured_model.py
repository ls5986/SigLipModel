"""Typed acquisition-time structured baseline, isolated from listing language."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

from actvision_contract import unknown_result
from condition_schema import PHYSICAL_CONDITIONS, MODERNIZATION_STATES, TARGET_LABELS

FEATURE_SCHEMA_VERSION = "actvision-structured-v2"
NUMERIC = (
    "YearBuilt", "PhotosCount", "LivingArea", "BedroomsTotal", "BathroomsTotalInteger",
    "ListPrice", "OriginalListPrice", "DaysOnMarket", "PriorSaleCount",
    "MonthsSinceMostRecentPriorSale", "MostRecentPriorSalePrice",
)
CATEGORICAL = ("PropertyType", "PropertySubType", "PostalCode", "City")
ALLOWED_FIELDS = frozenset((*NUMERIC, *CATEGORICAL))


def structured_features(metadata):
    """No remarks, identities or outcome fields can enter this matrix."""
    result = {}
    for key in (*NUMERIC, *CATEGORICAL):
        value = metadata.get(key)
        result[key + "_missing"] = 1.0
        if value is None or value == "":
            continue
        if key in NUMERIC:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a JSON number or null")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
            result[key] = float(value)
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string or null")
            if not value.strip():
                continue
            result[key] = value.strip().casefold()[:100]
        result[key + "_missing"] = 0.0
    original, price = result.get("OriginalListPrice"), result.get("ListPrice")
    if original is not None and original > 0 and price is not None:
        result["price_reduction_pct"] = max(0.0, (original - price) / original)
    return result


def completeness(metadata):
    values = structured_features(metadata)
    return sum(values[key + "_missing"] == 0 for key in ALLOWED_FIELDS) / len(ALLOWED_FIELDS)


@dataclass
class EvidenceHeads:
    """Independently supervised evidence axes; UNKNOWN remains unassessed."""
    heads: dict

    @classmethod
    def fit(cls, matrix, labels):
        heads = {}
        for task, allowed in (("physical_condition", PHYSICAL_CONDITIONS), ("modernization", MODERNIZATION_STATES), ("acquisition_fit", TARGET_LABELS)):
            values = [row.get(task, "UNKNOWN") for row in labels]
            if any(value not in allowed for value in values):
                raise ValueError(f"Invalid {task} training label")
            indices = [i for i, value in enumerate(values) if value != "UNKNOWN"]
            if len({values[i] for i in indices}) < 2:
                continue
            heads[task] = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=20261002).fit(
                matrix[indices], [values[i] for i in indices]
            )
        if not heads:
            raise ValueError("Need reviewed non-UNKNOWN examples from at least two classes on an evidence axis")
        return cls(heads)

    def predict(self, matrix):
        results = [unknown_result() for _ in range(matrix.shape[0])]
        for task, model in self.heads.items():
            key = {"physical_condition": "condition_probabilities", "modernization": "modernization_probabilities", "acquisition_fit": "acquisition_fit_probabilities"}[task]
            for result, probabilities in zip(results, model.predict_proba(matrix)):
                result[key] = dict(zip(model.classes_, map(float, probabilities)))
                result[task] = str(model.classes_[np.argmax(probabilities)])
        for result in results:
            probs = result["acquisition_fit_probabilities"]
            result["value_add_score"] = probs.get("TARGET") if probs else None
        # Confidence remains null: softmax/max probability is not calibrated confidence.
        return results


@dataclass
class StructuredModel:
    vectorizer: DictVectorizer
    heads: EvidenceHeads
    feature_schema_version: str = FEATURE_SCHEMA_VERSION

    @classmethod
    def fit(cls, metadata, labels):
        if len(metadata) != len(labels):
            raise ValueError("Structured evidence and labels must align")
        indices = [i for i, value in enumerate(metadata) if completeness(value) > 0]
        if not indices:
            raise ValueError("No structured evidence to train")
        vectorizer = DictVectorizer()
        matrix = vectorizer.fit_transform([structured_features(metadata[i]) for i in indices])
        return cls(vectorizer, EvidenceHeads.fit(matrix, [labels[i] for i in indices]))

    def predict(self, metadata):
        matrix = self.vectorizer.transform([structured_features(value) for value in metadata])
        results = self.heads.predict(matrix)
        return [result if completeness(value) > 0 else unknown_result()
                for value, result in zip(metadata, results)]
