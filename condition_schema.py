"""Versioned acquisition condition and modernization labels."""

PHYSICAL_CONDITIONS = (
    "C1_NEW",
    "C2_LIKE_NEW",
    "C3_WELL_MAINTAINED",
    "C4_AVERAGE_FUNCTIONAL",
    "C5_REHAB_NEEDED",
    "C6_SEVERE_DISTRESS",
    "UNKNOWN",
)
MODERNIZATION_STATES = (
    "ORIGINAL",
    "PARTIALLY_UPDATED",
    "UPDATED",
    "FULLY_REMODELED",
    "UNKNOWN",
)
TARGET_LABELS = ("TARGET", "NOT_TARGET", "UNKNOWN")
LABEL_POLICY_VERSION = "acquisition-labels-v1"

LEGACY_CONDITION_MAP = {
    "updated": ("C3_WELL_MAINTAINED", "UPDATED"),
    "maintained_original": ("C3_WELL_MAINTAINED", "ORIGINAL"),
    "slightly_dated": ("C4_AVERAGE_FUNCTIONAL", "ORIGINAL"),
    "dated": ("C4_AVERAGE_FUNCTIONAL", "ORIGINAL"),
    "mixed": ("C4_AVERAGE_FUNCTIONAL", "PARTIALLY_UPDATED"),
    "rough": ("C5_REHAB_NEEDED", "ORIGINAL"),
    "major": ("C6_SEVERE_DISTRESS", "UNKNOWN"),
    "unknown": ("UNKNOWN", "UNKNOWN"),
}

GUIDANCE = {
    "C1_NEW": "New construction with no physical depreciation.",
    "C2_LIKE_NEW": "Recently constructed or comprehensively renewed with negligible wear.",
    "C3_WELL_MAINTAINED": "Properly maintained with minimal wear and no meaningful repair requirement.",
    "C4_AVERAGE_FUNCTIONAL": "Functional with normal wear; cosmetic updates or minor repairs are likely.",
    "C5_REHAB_NEEDED": "Significant deferred maintenance or multiple substantial repairs are needed.",
    "C6_SEVERE_DISTRESS": "Severe deterioration, damage, or visible usability/soundness concerns.",
    "UNKNOWN": "Available evidence does not establish physical condition.",
}


def legacy_labels(value):
    return LEGACY_CONDITION_MAP.get(value or "unknown", ("UNKNOWN", "UNKNOWN"))


def validate_labels(physical_condition, modernization_state):
    if physical_condition not in PHYSICAL_CONDITIONS:
        raise ValueError("Invalid physical condition label")
    if modernization_state not in MODERNIZATION_STATES:
        raise ValueError("Invalid modernization state")
    return physical_condition, modernization_state
