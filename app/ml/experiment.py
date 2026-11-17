"""Experimental, scope-limited logistic model with chronological holdout.

No real historical dataset ships with FootScore. Only explicitly verified
rows are accepted; the returned artifact is NEVER marked production-approved.
"""
import hashlib
import json
from datetime import date
from math import exp, isfinite

from app.ml.features import FEATURE_NAMES, TARGET_NAME, validate_cutoff


class DatasetError(ValueError):
    """Invalid provenance, cohort, labels or time split."""


def _as_date(value: object, key: str) -> date:
    if not isinstance(value, str):
        raise DatasetError(f"{key} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DatasetError(f"{key} must be a valid ISO date") from exc


def validate_labeled_rows(rows: list[dict]) -> list[dict]:
    """Reject unverified negative labels, wrong windows, duplicates and leaks."""
    if not rows:
        raise DatasetError("No labeled examples provided")
    result = []
    unique = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DatasetError("Each record must be an object")
        try:
            cutoff = _as_date(row.get("feature_cutoff"), "feature_cutoff")
            validate_cutoff(cutoff)
            start = _as_date(row.get("target_window_start"), "target_window_start")
            end = _as_date(row.get("target_window_end"), "target_window_end")
            verified = _as_date(row.get("label_verified_at"), "label_verified_at")
        except ValueError as exc:
            raise DatasetError(str(exc)) from exc
        if start != date(cutoff.year, 7, 1) or end != date(cutoff.year + 1, 6, 30):
            raise DatasetError("Target window must be the NEXT July-June season")
        if verified <= end or verified > date.today():
            raise DatasetError("Future outcome must be verified after the full target season")
        if cutoff > date.today():
            raise DatasetError("Cannot train on a future cutoff")
        if row.get("target_name") != TARGET_NAME:
            raise DatasetError("Unrecognized target definition")
        if row.get("coverage_verified") is not True:
            raise DatasetError("Incomplete next-season provider coverage: no label allowed")
        if row.get("point_in_time_verified") is not True:
            raise DatasetError("Point-in-time feature snapshot not audited")
        source = row.get("label_source")
        if not isinstance(source, str) or len(source.strip()) < 8:
            raise DatasetError("Auditable label source required")
        if type(row.get("target")) is not int or row["target"] not in (0, 1):
            raise DatasetError("Target must be an explicit verified 0 or 1")
        competition_id = row.get("competition_id")
        player_id = row.get("player_id")
        position = row.get("position_group")
        if type(competition_id) is not int or competition_id < 1:
            raise DatasetError("Invalid competition ID")
        if type(player_id) is not int or player_id < 1:
            raise DatasetError("Invalid player ID")
        if position not in {"attacker", "midfielder", "defender", "goalkeeper"}:
            raise DatasetError("Invalid position group")
        identity = (cutoff, competition_id, position, player_id)
        if identity in unique:
            raise DatasetError("Duplicate player/cohort/cutoff")
        unique.add(identity)

        features = row.get("features")
        if not isinstance(features, dict) or set(features) != set(FEATURE_NAMES):
            raise DatasetError("Feature keys differ from the approved cutoff schema")
        checked = {}
        for key in FEATURE_NAMES:
            value = features[key]
            if value is None:
                checked[key] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DatasetError(f"{key} must be numerical or null")
            number = float(value)
            if not isfinite(number) or number < 0:
                raise DatasetError(f"{key} is nonfinite or negative")
            checked[key] = number
        if (checked["age"] is None or checked["age"] >= 22
                or checked["minutes"] is None or checked["minutes"] < 30
                or checked["matches"] is None or checked["matches"] < 1
                or checked["rated_minutes"] is None
                or checked["rated_minutes"] > checked["minutes"]
                or checked["rating_coverage"] is None
                or checked["rating_coverage"] > 1
                or (checked["average_rating"] is not None
                    and checked["average_rating"] > 10)):
            raise DatasetError("Inconsistent age, minutes, coverage or rating")
        if checked["rated_minutes"] == 0 and checked["average_rating"] is not None:
            raise DatasetError("Rating without rated minutes")
        result.append({**row, "features": checked})
    return result


def train_chronological_experiment(
    rows: list[dict], *, min_train: int = 30, min_validation: int = 10,
) -> dict:
    """Train on EARLIER cutoffs, evaluate on latest cutoff (no random split).

    Requires separately audited point-in-time snapshots and target labels.
    Evaluates one competition and one position only; no pooled global model.
    """
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    valid = validate_labeled_rows(rows)
    groups = {(row["competition_id"], row["position_group"]) for row in valid}
    if len(groups) != 1:
        raise DatasetError("Train one competition/position at a time")
    cutoffs = sorted({row["feature_cutoff"] for row in valid})
    if len(cutoffs) < 2:
        raise DatasetError("At least two annual cutoffs are required")
    holdout = cutoffs[-1]
    training = [row for row in valid if row["feature_cutoff"] < holdout]
    validation = [row for row in valid if row["feature_cutoff"] == holdout]
    if len(training) < min_train or len(validation) < min_validation:
        raise DatasetError("Insufficient training or later holdout examples")
    if any(row["label_verified_at"] >= holdout for row in training):
        raise DatasetError("Earlier labels were not available at holdout cutoff")
    if any(row["target_window_end"] >= holdout for row in training):
        raise DatasetError("Training outcomes overlap the validation feature window")
    y_train = [row["target"] for row in training]
    y_val = [row["target"] for row in validation]
    if len(set(y_train)) < 2 or len(set(y_val)) < 2:
        raise DatasetError("Both outcome classes required in train and validation")

    def matrix(source: list[dict]) -> list[list[float | None]]:
        return [[row["features"][field] for field in FEATURE_NAMES] for row in source]

    estimator = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        LogisticRegression(max_iter=1000, random_state=42),
    )
    estimator.fit(matrix(training), y_train)
    probabilities = estimator.predict_proba(matrix(validation))[:, 1]
    p_baseline = sum(y_train) / len(y_train)
    baseline_brier = sum((label - p_baseline) ** 2 for label in y_val) / len(y_val)
    brier = float(brier_score_loss(y_val, probabilities))
    scaler = estimator.named_steps["standardscaler"]
    imputer = estimator.named_steps["simpleimputer"]
    classifier = estimator.named_steps["logisticregression"]
    competition_id, position = next(iter(groups))
    return {
        "schema": "footscore-experimental-ml-v1",
        "target": TARGET_NAME,
        "model_type": "logistic_regression",
        "approved_for_production": False,
        "dataset_fingerprint": hashlib.sha256(
            json.dumps(valid, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "competition_id": competition_id,
        "position_group": position,
        "feature_names": list(FEATURE_NAMES),
        "imputer_medians": [float(x) for x in imputer.statistics_],
        "scaler_mean": [float(x) for x in scaler.mean_],
        "scaler_scale": [float(x) for x in scaler.scale_],
        "coefficients": [float(x) for x in classifier.coef_[0]],
        "intercept": float(classifier.intercept_[0]),
        "train_cutoffs": cutoffs[:-1],
        "validation_cutoff": holdout,
        "train_count": len(training),
        "validation_count": len(validation),
        "metrics": {
            "validation_roc_auc": round(float(roc_auc_score(y_val, probabilities)), 4),
            "validation_average_precision": round(
                float(average_precision_score(y_val, probabilities)), 4
            ),
            "validation_brier": round(brier, 4),
            "constant_baseline_brier": round(baseline_brier, 4),
            "validation_prevalence": round(sum(y_val) / len(y_val), 4),
            "train_prevalence": round(p_baseline, 4),
        },
        "warning": (
            "Experimental and uncalibrated; predicts only next-season >=900 "
            "minutes in the SAME tracked competition, not player talent. "
            "Data rights, leakage audit, external validation and group fairness "
            "must be checked before any public prediction."
        ),
    }


def score_experiment(artifact: dict, features: dict) -> float:
    """Inference from auditable JSON coefficients; no pickle loading."""
    if artifact.get("schema") != "footscore-experimental-ml-v1":
        raise DatasetError("Unsupported artifact")
    if artifact.get("feature_names") != list(FEATURE_NAMES):
        raise DatasetError("Feature schema mismatch")
    if not isinstance(features, dict) or set(features) != set(FEATURE_NAMES):
        raise DatasetError("Invalid inference features")
    total = artifact["intercept"]
    for idx, field in enumerate(FEATURE_NAMES):
        number = features[field]
        if number is not None:
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise DatasetError("Invalid feature number")
            if not isfinite(number) or number < 0:
                raise DatasetError("Nonfinite or negative feature number")
        x = artifact["imputer_medians"][idx] if number is None else float(number)
        total += (
            (x - artifact["scaler_mean"][idx]) /
            artifact["scaler_scale"][idx] * artifact["coefficients"][idx]
        )
    if total >= 0:
        return 1 / (1 + exp(-total))
    value = exp(total)
    return value / (1 + value)
