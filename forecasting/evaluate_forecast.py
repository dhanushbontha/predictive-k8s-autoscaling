#!/usr/bin/env python3
"""
Prophet Forecast Evaluation Module for Kubernetes Autoscaling.

Evaluates Prophet load forecasts against held-out actual observations using
chronological train/test splitting. Calculates Mean Absolute Error (MAE)
and Root Mean Squared Error (RMSE).
"""

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

# Add parent directory to path to allow imports from forecasting module
curr_dir = Path(__file__).resolve().parent
if str(curr_dir) not in sys.path:
    sys.path.insert(0, str(curr_dir))

from forecast import (
    generate_forecast,
    infer_frequency,
    load_and_validate_csv,
    split_chronological,
    train_prophet_model,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("evaluator")


def evaluate_forecast_performance(
    actual_y: np.ndarray,
    predicted_y: np.ndarray,
) -> Dict[str, float]:
    """
    Computes regression performance metrics: MAE, RMSE, Mean Actual, Mean Predicted.
    """
    if len(actual_y) == 0:
        raise ValueError("Cannot evaluate metrics on empty arrays.")
    if len(actual_y) != len(predicted_y):
        raise ValueError(
            f"Shape mismatch: actual ({len(actual_y)}) vs predicted ({len(predicted_y)})."
        )

    mae = float(mean_absolute_error(actual_y, predicted_y))
    mse = float(mean_squared_error(actual_y, predicted_y))
    rmse = float(math.sqrt(mse))
    mean_actual = float(np.mean(actual_y))
    mean_predicted = float(np.mean(predicted_y))

    # Normalized metrics if mean_actual > 0
    nrmse = (rmse / mean_actual) if mean_actual > 0 else 0.0

    return {
        "samples": len(actual_y),
        "mae": mae,
        "rmse": rmse,
        "nrmse": nrmse,
        "mean_actual": mean_actual,
        "mean_predicted": mean_predicted,
    }


def align_actual_and_predicted(
    test_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    tolerance_seconds: float = 2.5,
) -> pd.DataFrame:
    """
    Merges actual test observations with forecasted points by nearest timestamp
    within a tolerance window to handle small sampling jitter.
    """
    # Sort both by ds
    test_sorted = test_df.sort_values(by="ds").copy()
    fcst_sorted = forecast_df.sort_values(by="ds").copy()

    # Use pd.merge_asof with specified tolerance
    merged = pd.merge_asof(
        test_sorted,
        fcst_sorted[["ds", "yhat", "yhat_lower", "yhat_upper"]],
        on="ds",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=tolerance_seconds),
    )

    valid_aligned = merged.dropna(subset=["y", "yhat"]).copy()
    if len(valid_aligned) == 0:
        raise ValueError(
            "Could not align any actual test points with forecasted timestamps within tolerance."
        )

    return valid_aligned


def run_evaluation_pipeline(
    csv_path: str,
    metric_col: str = "http_work_rps",
    timestamp_col: str = "timestamp_utc",
    test_size: float = 0.25,
    seed: int = 42,
    output_eval_csv: Optional[str] = None,
) -> Tuple[Dict[str, float], pd.DataFrame]:
    """
    End-to-end evaluation:
    1. Loads and validates dataset.
    2. Chronologically splits into Train and Test.
    3. Trains Prophet on Train data only (never evaluates on trained observations).
    4. Predicts across the Test horizon.
    5. Calculates MAE and RMSE on held-out Test data.
    """
    df = load_and_validate_csv(csv_path, metric_col=metric_col, timestamp_col=timestamp_col)
    train_df, test_df = split_chronological(df, test_size=test_size)

    logger.info(
        f"Evaluation Split -> Total: {len(df)}, Train: {len(train_df)}, Held-out Test: {len(test_df)}"
    )

    freq = infer_frequency(df["ds"])
    logger.info(f"Detected series frequency: {freq}. Training model on historical slice...")
    model = train_prophet_model(train_df, random_seed=seed)

    logger.info(f"Forecasting {len(test_df)} steps ahead...")
    fcst_df = generate_forecast(model, periods=len(test_df), freq=freq, history=False)

    aligned = align_actual_and_predicted(test_df, fcst_df)
    metrics = evaluate_forecast_performance(
        actual_y=aligned["y"].to_numpy(),
        predicted_y=aligned["yhat"].to_numpy(),
    )

    if output_eval_csv:
        out_p = Path(output_eval_csv)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        aligned.rename(columns={"y": "actual_y", "yhat": "predicted_y"}).to_csv(out_p, index=False)
        logger.info(f"Saved aligned evaluation points to: {out_p}")

    return metrics, aligned


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Prophet load forecasts against held-out actuals using MAE/RMSE."
    )
    parser.add_argument(
        "--input-csv",
        type=str,
        required=True,
        help="Path to input metrics CSV (e.g. experiments/hpa_periodic.csv).",
    )
    parser.add_argument(
        "--metric",
        type=str,
        default="http_work_rps",
        help="Column to evaluate (default: http_work_rps).",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=0.25,
        help="Proportion of latest observations held out for testing (default: 0.25).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42).",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=None,
        help="Optional path to save aligned actual vs predicted values.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output evaluation results as raw JSON.",
    )

    args = parser.parse_args()

    metrics, aligned = run_evaluation_pipeline(
        csv_path=args.input_csv,
        metric_col=args.metric,
        test_size=args.test_size,
        seed=args.seed,
        output_eval_csv=args.output_csv,
    )

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print("\n=======================================================")
        print("          PROPHET FORECAST EVALUATION REPORT           ")
        print("=======================================================")
        print(f"Dataset             : {args.input_csv}")
        print(f"Target Metric       : {args.metric}")
        print(f"Test Split Ratio    : {args.test_size * 100:.1f}%")
        print(f"Held-Out Samples    : {metrics['samples']}")
        print(f"Mean Actual Value   : {metrics['mean_actual']:.4f}")
        print(f"Mean Predicted Value: {metrics['mean_predicted']:.4f}")
        print("-------------------------------------------------------")
        print(f"MAE  (Mean Absolute Error)     : {metrics['mae']:.4f}")
        print(f"RMSE (Root Mean Squared Error) : {metrics['rmse']:.4f}")
        print(f"NRMSE (Normalized RMSE)        : {metrics['nrmse']:.4f}")
        print("=======================================================\n")


if __name__ == "__main__":
    main()
