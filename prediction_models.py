"""Backend prediction models built on retrieved marine forecast records.

The models never create missing ocean observations. They score only values
returned by the configured data client and report unavailable inputs clearly.
"""

from __future__ import annotations

from typing import Any, Iterable


class MarineRiskModel:
    """Transparent operational risk model for forecast rows."""

    thresholds = {
        "wind": (12.0, 24.0),
        "wave height": (2.0, 4.0),
        "swell": (1.5, 3.0),
        "rainfall": (15.0, 50.0),
        "currents": (0.8, 1.5),
    }

    def _component(self, name: str, value: float) -> float:
        limits = self.thresholds.get(name)
        if not limits:
            return 0.0
        caution, danger = limits
        if value <= caution:
            return 0.0
        if value >= danger:
            return 100.0
        return round((value - caution) * 100 / (danger - caution), 1)

    def predict_row(self, row: dict[str, Any]) -> dict[str, Any]:
        features = row.get("features", {})
        components = {}
        for name, value in features.items():
            try:
                if value is not None:
                    components[name] = self._component(name, abs(float(value)))
            except (TypeError, ValueError):
                continue
        score = round(max(components.values(), default=0.0), 1)
        label = "high" if score >= 70 else "moderate" if score >= 35 else "low"
        return {
            "timestamp": row.get("timestamp"),
            "risk_score": score,
            "risk_level": label,
            "components": components,
            "source_datasets": row.get("source_dataset", {}),
            "missing_values": row.get("missing_values", []),
        }

    def predict(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.predict_row(row) for row in rows]


class FishingWindowModel:
    """Select lower-risk windows from the risk model output."""

    def predict(self, risk_predictions: Iterable[dict[str, Any]]) -> dict[str, Any]:
        predictions = list(risk_predictions)
        usable = [item for item in predictions if item.get("risk_score") is not None]
        if not usable:
            return {"status": "NOT AVAILABLE", "best_window": None, "windows": []}
        ordered = sorted(usable, key=lambda item: item["risk_score"])
        return {
            "status": "AVAILABLE",
            "best_window": ordered[0],
            "windows": ordered,
        }


def build_predictions(feature_dataset: dict[str, Any]) -> dict[str, Any]:
    """Run all backend prediction models over one real feature dataset."""
    risk = MarineRiskModel().predict(feature_dataset.get("records", []))
    fishing = FishingWindowModel().predict(risk)
    return {
        "model_version": "salty-operational-v1",
        "input_status": "AVAILABLE" if feature_dataset.get("records") else "NOT AVAILABLE",
        "source_datasets": feature_dataset.get("source_datasets", {}),
        "unavailable_parameters": feature_dataset.get("unavailable_parameters", []),
        "forecast_timestamps": feature_dataset.get("forecast_timestamps", []),
        "marine_risk": risk,
        "fishing_window": fishing,
    }
