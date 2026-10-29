#!/usr/bin/env python3
"""
Prophet Workload Forecasting Module for Kubernetes Autoscaling.

Trains a Prophet time-series model on historical request rate metrics
(defaulting to 'http_work_rps') and forecasts future demand for a configurable horizon.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from prophet import Prophet

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("forecaster")


def load_and_validate_csv(
    csv_path: str,
    metric_col: str = "http_work_rps",
    timestamp_col: str = "timestamp_utc",
) -> pd.DataFrame:
    """
    Loads experimental metrics CSV and validates schema, timestamps, and metric values.
    Returns a DataFrame sorted chronologically with columns ['ds', 'y'].
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Input CSV file not found: {csv_path}")

    try:
        df = pd.read_csv(path)
    except Exception as e:
        raise ValueError(f"Failed to read CSV file '{csv_path}': {e}") from e

    if df.empty:
        raise ValueError(f"Input CSV file '{csv_path}' is completely empty.")

    # Validate timestamp column
    if timestamp_col not in df.columns:
        raise ValueError(
            f"Timestamp column '{timestamp_col}' not found in CSV. "
            f"Available columns: {list(df.columns)}"
        )

    # Validate target metric column
    if metric_col not in df.columns:
        raise ValueError(
            f"Target request-rate column '{metric_col}' not found in CSV. "
            f"Available columns: {list(df.columns)}"
        )

    # Convert timestamps to tz-naive UTC datetime for Prophet compatibility
    try:
        ds_series = pd.to_datetime(df[timestamp_col], utc=True)
    except Exception as e:
        raise ValueError(f"Failed to parse timestamps in column '{timestamp_col}': {e}") from e

    if ds_series.isnull().any():
        null_count = ds_series.isnull().sum()
        raise ValueError(f"Found {null_count} null or invalid timestamp values in '{timestamp_col}'.")

    # Prophet requires timezone-naive datetime in 'ds'
    ds_series = ds_series.dt.tz_localize(None)

    # Convert metric column to numeric
    y_raw = df[metric_col]
    y_series = pd.to_numeric(y_raw, errors="coerce")
    null_y = y_series.isnull().sum()
    if null_y > 0:
        raise ValueError(
            f"Found {null_y} non-numeric or missing values in target metric column '{metric_col}'."
        )

    clean_df = pd.DataFrame({"ds": ds_series, "y": y_series})

    # Deduplicate timestamps by averaging or keeping latest
    if clean_df["ds"].duplicated().any():
        dup_count = clean_df["ds"].duplicated().sum()
        logger.warning(
            f"Detected {dup_count} duplicate timestamps. Aggregating duplicates by mean."
        )
        clean_df = clean_df.groupby("ds", as_index=False)["y"].mean()

    # Sort strictly chronologically
    clean_df = clean_df.sort_values(by="ds").reset_index(drop=True)

    if len(clean_df) < 5:
        raise ValueError(
            f"Insufficient observations in dataset ({len(clean_df)} valid points). "
            "At least 5 samples are required to fit a time-series model."
        )

    return clean_df


def split_chronological(
    df: pd.DataFrame,
    test_size: float = 0.2,
    test_samples: Optional[int] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Performs strict chronological train/test split to prevent temporal data leakage.
    Never shuffles.
    """
    n = len(df)
    if test_samples is not None:
        if test_samples <= 0:
            raise ValueError(f"test_samples must be positive, got {test_samples}")
        if test_samples >= n:
            raise ValueError(
                f"test_samples ({test_samples}) must be strictly less than total rows ({n})"
            )
        split_idx = n - test_samples
    else:
        if not (0.0 < test_size < 1.0):
            raise ValueError(f"test_size must be between 0.0 and 1.0, got {test_size}")
        split_idx = int(n * (1.0 - test_size))
        # Ensure at least 1 sample in test and 2 in train
        split_idx = max(2, min(n - 1, split_idx))

    train_df = df.iloc[:split_idx].copy().reset_index(drop=True)
    test_df = df.iloc[split_idx:].copy().reset_index(drop=True)

    if len(train_df) < 2:
        raise ValueError(f"Train split has only {len(train_df)} samples; need at least 2.")
    if len(test_df) < 1:
        raise ValueError("Test split has 0 samples.")

    return train_df, test_df


def infer_frequency(ds_series: pd.Series) -> str:
    """
    Infers the median time delta frequency string (e.g., '2s', '5s', '1s') from timestamps.
    """
    if len(ds_series) < 2:
        return "2s"
    deltas = ds_series.diff().dropna().dt.total_seconds()
    median_sec = deltas.median()
    if np.isnan(median_sec) or median_sec <= 0:
        return "2s"
    # Round to nearest integer seconds if close, else millisecond precision
    if abs(median_sec - round(median_sec)) < 0.1:
        return f"{int(round(median_sec))}s"
    return f"{int(round(median_sec * 1000))}ms"


def train_prophet_model(
    train_df: pd.DataFrame,
    random_seed: int = 42,
    changepoint_prior_scale: float = 0.05,
    interval_width: float = 0.95,
) -> Prophet:
    """
    Configures and fits a Prophet model with fixed random seed and suppressed noisy logging.
    """
    # Suppress cmdstanpy / prophet chatty log messages
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    # Set seed in numpy
    np.random.seed(random_seed)

    # Disable daily/weekly/yearly seasonalities since experiments are seconds/minutes scale
    model = Prophet(
        growth="linear",
        yearly_seasonality=False,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=changepoint_prior_scale,
        interval_width=interval_width,
    )

    model.fit(train_df)
    return model


def generate_forecast(
    model: Prophet,
    periods: int,
    freq: str = "2s",
    history: bool = True,
) -> pd.DataFrame:
    """
    Generates forecast dataframe using Prophet for the specified horizon (periods).
    Returns DataFrame containing ['ds', 'yhat', 'yhat_lower', 'yhat_upper'].
    Clamps negative predictions to 0 since request rates cannot be negative.
    """
    future = model.make_future_dataframe(periods=periods, freq=freq, include_history=history)
    forecast = model.predict(future)

    # Extract primary forecast columns
    cols = ["ds", "yhat", "yhat_lower", "yhat_upper"]
    out_df = forecast[cols].copy()

    # Enforce non-negativity constraint for request rate
    out_df["yhat"] = out_df["yhat"].clip(lower=0.0)
    out_df["yhat_lower"] = out_df["yhat_lower"].clip(lower=0.0)
    out_df["yhat_upper"] = out_df["yhat_upper"].clip(lower=0.0)

    return out_df


def main():
    parser = argparse.ArgumentParser(
        description="Train Prophet model on workload metrics and generate load forecasts."
    )
    parser.add_argument(
        "--input-csv",
        type=str,
        required=True,
        help="Path to input metrics CSV (e.g., experiments/hpa_stable.csv).",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default="forecasting/forecast_output.csv",
        help="Path where the forecast CSV will be saved (default: forecasting/forecast_output.csv).",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=15,
        help="Number of future intervals/steps to forecast (default: 15).",
    )
    parser.add_argument(
        "--metric",
        type=str,
        default="http_work_rps",
        help="Column name to forecast (default: http_work_rps).",
    )
    parser.add_argument(
        "--timestamp-col",
        type=str,
        default="timestamp_utc",
        help="Column name containing timestamps (default: timestamp_utc).",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Proportion of the dataset held out for testing (0.0 to 1.0, default: 0.2). Set 0 for 100%% training.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42).",
    )

    args = parser.parse_args()

    logger.info(f"Loading and validating dataset: {args.input_csv}")
    df = load_and_validate_csv(
        csv_path=args.input_csv,
        metric_col=args.metric,
        timestamp_col=args.timestamp_col,
    )
    logger.info(f"Loaded {len(df)} validated observations. Metric: '{args.metric}'.")

    freq = infer_frequency(df["ds"])
    logger.info(f"Inferred data frequency: {freq}")

    if args.test_size > 0.0:
        train_df, test_df = split_chronological(df, test_size=args.test_size)
        logger.info(
            f"Split data chronologically -> Train: {len(train_df)} rows, Test: {len(test_df)} rows."
        )
    else:
        train_df = df
        test_df = pd.DataFrame()
        logger.info(f"Using 100% of data ({len(train_df)} rows) for training.")

    logger.info("Fitting Prophet model...")
    model = train_prophet_model(train_df, random_seed=args.seed)

    # Forecast horizon + test steps if test split exists
    forecast_steps = args.horizon if test_df.empty else (len(test_df) + args.horizon)
    logger.info(f"Forecasting {forecast_steps} future steps (freq={freq})...")
    forecast_df = generate_forecast(model, periods=forecast_steps, freq=freq, history=True)

    # Save output
    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    forecast_df.to_csv(out_path, index=False)
    logger.info(f"Forecast successfully saved to: {out_path} ({len(forecast_df)} rows)")

    # Print summary of latest forecasted points
    print("\n--- Recent & Forecasted Predictions (Sample) ---")
    print(forecast_df.tail(min(10, len(forecast_df))).to_string(index=False))


if __name__ == "__main__":
    main()
