"""Separate supervised text baseline; not a pretrained semantic encoder."""
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from actvision_contract import unknown_result
from condition_schema import TEXT_SIGNALS, validate_labels
from structured_model import EvidenceHeads

FEATURE_SCHEMA_VERSION = "actvision-text-tfidf-v2"


def text_vectorizer():
    return TfidfVectorizer(max_features=2000, ngram_range=(1, 2), min_df=1, strip_accents="unicode")


@dataclass
class TextModel:
    vectorizer: TfidfVectorizer
    heads: EvidenceHeads | None
    signals: dict
    feature_schema_version: str = FEATURE_SCHEMA_VERSION

    @classmethod
    def fit(cls, remarks, labels, *, encoder=None):
        if len(remarks) != len(labels) or any(not isinstance(text, str) for text in remarks):
            raise ValueError("Text-only evidence and labels must align")
        indices = [i for i, text in enumerate(remarks) if text.strip()]
        if not indices:
            raise ValueError("No listing remarks to train")
        vectorizer = encoder if encoder is not None else text_vectorizer()
        matrix = vectorizer.fit_transform([remarks[i] for i in indices])
        reviews = [labels[i] for i in indices]
        for review in reviews:
            validate_labels(review.get("physical_condition", "UNKNOWN"), review.get("modernization", "UNKNOWN"))
        evidence_axes_available = any(
            len({row.get(task, "UNKNOWN") for row in reviews} - {"UNKNOWN"}) >= 2
            for task in ("physical_condition", "modernization", "acquisition_fit")
        )
        heads = EvidenceHeads.fit(matrix, reviews) if evidence_axes_available else None
        signals = {}
        for row in reviews:
            if set(row.get("text_signals", {})) - set(TEXT_SIGNALS):
                raise ValueError("Unknown semantic tag")
        for signal in TEXT_SIGNALS:
            states = [row.get("text_signals", {}).get(signal, "UNKNOWN") for row in reviews]
            if any(state not in {"PRESENT", "ABSENT", "UNKNOWN"} for state in states):
                raise ValueError("Text signal labels require PRESENT, ABSENT or UNKNOWN")
            selected = [i for i, value in enumerate(states) if value != "UNKNOWN"]
            if {states[i] for i in selected} != {"PRESENT", "ABSENT"}:
                continue
            signals[signal] = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=20261002).fit(
                matrix[selected], [int(states[i] == "PRESENT") for i in selected]
            )
        if not heads and not signals:
            raise ValueError("Text training needs explicit reviewed positive AND negative classes; UNKNOWN is unassessed")
        return cls(vectorizer, heads, signals, getattr(vectorizer, "feature_schema_version", FEATURE_SCHEMA_VERSION))

    def predict(self, remarks):
        if any(not isinstance(text, str) for text in remarks):
            raise ValueError("Text model accepts remarks strings, not structured fields")
        matrix = self.vectorizer.transform(remarks)
        results = self.heads.predict(matrix) if self.heads else [unknown_result() for _ in remarks]
        for signal, model in self.signals.items():
            for result, probability in zip(results, model.predict_proba(matrix)[:, list(model.classes_).index(1)]):
                result["text_signals"].append({
                    "signal": signal, "state": "PRESENT" if probability >= .5 else "ABSENT",
                    "probability": float(probability), "snippet": None, "start": None, "end": None,
                })
        return [result if text.strip() else unknown_result() for text, result in zip(remarks, results)]

