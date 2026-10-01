import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from property_models import (
    metadata_features,
    predict_property,
    target_class,
    vision_vector,
)


def fitted_bundle():
    negative = [np.asarray([0.0, 0.1]), np.asarray([0.1, 0.0])]
    positive = [np.asarray([0.9, 1.0]), np.asarray([1.0, 0.9])]
    vision_x = np.stack([
        vision_vector(negative),
        vision_vector([negative[0]]),
        vision_vector(positive),
        vision_vector([positive[0]]),
    ])
    y = np.asarray([0, 0, 1, 1])
    vision = LogisticRegression().fit(vision_x, y)
    metadata_rows = [
        metadata_features({"YearBuilt": 2020, "PropertySubType": "Condo"}),
        metadata_features({"YearBuilt": 2018, "PropertySubType": "Condo"}),
        metadata_features({"YearBuilt": 1960, "PropertySubType": "Single Family"}),
        metadata_features({"YearBuilt": 1970, "PropertySubType": "Single Family"}),
    ]
    metadata = Pipeline([
        ("vectorize", DictVectorizer()),
        ("classifier", LogisticRegression()),
    ]).fit(metadata_rows, y)
    vision_scores = vision.predict_proba(vision_x)[:, list(vision.classes_).index(1)]
    metadata_scores = metadata.predict_proba(metadata_rows)[:, list(metadata.classes_).index(1)]
    fusion_x = np.column_stack([
        vision_scores, metadata_scores, np.ones(4), np.ones(4),
    ])
    fusion = LogisticRegression().fit(fusion_x, y)
    return {"property_models": {"vision": vision, "metadata": metadata, "fusion": fusion}}


def test_automatic_router_supports_images_metadata_and_both():
    bundle = fitted_bundle()
    vectors = [np.asarray([0.95, 0.9]), np.asarray([0.9, 1.0])]
    metadata = {"YearBuilt": 1965, "PropertySubType": "Single Family"}
    combined = predict_property(bundle, vectors, metadata)
    assert combined["mode_used"] == "images_and_metadata"
    assert combined["component_scores"]["images"] is not None
    assert combined["component_scores"]["metadata"] is not None
    assert combined["score"] == combined["component_scores"]["combined"]
    assert predict_property(bundle, vectors, {}, "automatic")["mode_used"] == "images_only"
    assert predict_property(bundle, [], metadata, "automatic")["mode_used"] == "metadata_only"


def test_unavailable_explicit_mode_is_not_silently_replaced():
    result = predict_property(fitted_bundle(), [], {"YearBuilt": 1970}, "images_only")
    assert result["mode_used"] == "insufficient_evidence"
    assert result["score"] is None
    assert any("unavailable" in warning for warning in result["warnings"])


def test_target_class_uses_anchored_rating_and_keeps_maybe_unknown():
    assert target_class({"status": "approved", "target_score": 5}) == 1
    assert target_class({"status": "approved", "target_score": 1}) == 0
    assert target_class({"status": "approved", "target_score": 3}) is None
    assert target_class({"status": "approved", "target_fit": "target"}) == 1


def test_invalid_numeric_metadata_stays_explicitly_missing():
    features = metadata_features({"YearBuilt": "not-a-year"})
    assert "year_built" not in features
    assert features["year_built_missing"] == 1.0
