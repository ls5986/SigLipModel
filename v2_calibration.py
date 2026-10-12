"""Temperature calibration for ActVision component and fusion probability outputs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import minimize_scalar

from actvision_contract import unknown_result

AXES = {
    "physical_condition": "condition_probabilities",
    "modernization": "modernization_probabilities",
    "acquisition_fit": "acquisition_fit_probabilities",
}


def _temperature_probabilities(probabilities, temperature):
    values = np.asarray(probabilities, dtype=float)
    values = np.clip(values, 1e-9, 1.0)
    logits = np.log(values) / float(temperature)
    logits -= logits.max()
    exp = np.exp(logits)
    return exp / exp.sum()


@dataclass
class TemperatureCalibrator:
    temperatures: dict
    version: str

    @classmethod
    def fit(cls, results, labels, *, identity):
        temperatures = {}
        for task, probability_key in AXES.items():
            samples = []
            classes = None
            for result, label in zip(results, labels):
                truth = label.get(task, "UNKNOWN")
                probabilities = result.get(probability_key) or {}
                if truth == "UNKNOWN" or truth not in probabilities:
                    continue
                ordered = tuple(sorted(probabilities))
                if classes is None:
                    classes = ordered
                if ordered != classes:
                    continue
                samples.append(([probabilities[name] for name in classes], classes.index(truth)))
            if len(samples) < 6 or len({target for _, target in samples}) < 2:
                temperatures[task] = 1.0
                continue
            x = np.asarray([row for row, _ in samples], dtype=float)
            y = np.asarray([target for _, target in samples], dtype=int)

            def objective(log_temperature):
                temperature = math.exp(float(log_temperature))
                calibrated = np.stack([
                    _temperature_probabilities(row, temperature) for row in x
                ])
                return float(-np.mean(np.log(np.clip(calibrated[np.arange(len(y)), y], 1e-12, 1.0))))

            optimum = minimize_scalar(objective, bounds=(-2.5, 2.5), method="bounded")
            temperatures[task] = float(math.exp(optimum.x)) if optimum.success else 1.0
        return cls(temperatures, "temperature-v1:" + identity[:16])

    def apply(self, result):
        if result == unknown_result():
            return deepcopy(result)
        output = deepcopy(result)
        confidences = []
        for task, probability_key in AXES.items():
            probabilities = output.get(probability_key) or {}
            if not probabilities:
                continue
            classes = sorted(probabilities)
            calibrated = _temperature_probabilities(
                [probabilities[name] for name in classes],
                self.temperatures.get(task, 1.0),
            )
            output[probability_key] = dict(zip(classes, map(float, calibrated)))
            best = int(np.argmax(calibrated))
            output[task] = classes[best]
            confidences.append(float(calibrated[best]))
        output["value_add_score"] = output["acquisition_fit_probabilities"].get("TARGET")
        output["confidence"] = float(np.mean(confidences)) if confidences else None
        return output


@dataclass
class CalibratedModel:
    model: object
    calibrator: TemperatureCalibrator
    feature_schema_version: str

    @classmethod
    def wrap(cls, model, calibrator):
        return cls(model, calibrator, model.feature_schema_version)

    def predict(self, evidence, coverage=None):
        if coverage is None:
            results = self.model.predict(evidence)
        else:
            results = self.model.predict(evidence, coverage)
        return [self.calibrator.apply(result) for result in results]


def expected_calibration_error(results, labels, task, bins=10):
    probability_key = AXES[task]
    observations = []
    for result, label in zip(results, labels):
        truth = label.get(task, "UNKNOWN")
        probabilities = result.get(probability_key) or {}
        if truth == "UNKNOWN" or not probabilities:
            continue
        predicted = max(probabilities, key=probabilities.get)
        observations.append((float(probabilities[predicted]), float(predicted == truth)))
    if not observations:
        return None
    error = 0.0
    values = np.asarray(observations)
    for lower in np.linspace(0, 1, bins, endpoint=False):
        upper = lower + 1 / bins
        mask = (values[:, 0] >= lower) & (values[:, 0] < upper if upper < 1 else values[:, 0] <= upper)
        if mask.any():
            error += float(mask.mean()) * abs(float(values[mask, 0].mean()) - float(values[mask, 1].mean()))
    return error
