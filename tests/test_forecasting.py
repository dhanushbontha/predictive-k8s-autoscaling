import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

# Ensure root workspace is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

forecasting_dir = root_dir / "forecasting"
if str(forecasting_dir) not in sys.path:
    sys.path.insert(0, str(forecasting_dir))

from forecast import (
    load_and_validate_csv,
    split_chronological,
    infer_frequency,
    train_prophet_model,
    generate_forecast,
)
from evaluate_forecast import (
    evaluate_forecast_performance,
    align_actual_and_predicted,
)


@pytest.fixture
def synthetic_timeseries_csv(tmp_path):
    """
    Generates a synthetic time-series CSV for testing purposes.
    Explicitly labeled as synthetic fixture data.
    """
    timestamps = pd.date_range("2026-01-01 00:00:00", periods=20, freq="2s", tz="UTC")
    # Synthetic request rate ramping from 5 to 15
    y_values = np.linspace(5.0, 15.0, 20)
    df = pd.DataFrame(
        {
            "timestamp_utc": timestamps.astype(str),
            "http_work_rps": y_values,
            "http_rps": y_values + 0.5,
            "cpu_usage_cores": 0.05,
        }
    )
    csv_file = tmp_path / "synthetic_metrics.csv"
    df.to_csv(csv_file, index=False)
    return str(csv_file)


def test_load_and_validate_valid_csv(synthetic_timeseries_csv):
    df = load_and_validate_csv(synthetic_timeseries_csv, metric_col="http_work_rps")
    assert len(df) == 20
    assert list(df.columns) == ["ds", "y"]
    # Check timezone naivety (required by Prophet)
    assert df["ds"].dt.tz is None
    # Check chronological ordering
    assert df["ds"].is_monotonic_increasing


def test_load_and_validate_missing_file():
    with pytest.raises(FileNotFoundError):
        load_and_validate_csv("non_existent_file.csv")


def test_load_and_validate_missing_metric_column(synthetic_timeseries_csv):
    with pytest.raises(ValueError, match="Target request-rate column 'invalid_metric' not found"):
        load_and_validate_csv(synthetic_timeseries_csv, metric_col="invalid_metric")


def test_load_and_validate_missing_timestamp_column(synthetic_timeseries_csv):
    with pytest.raises(ValueError, match="Timestamp column 'invalid_ts' not found"):
        load_and_validate_csv(synthetic_timeseries_csv, timestamp_col="invalid_ts")


def test_load_and_validate_non_numeric_values(tmp_path):
    bad_df = pd.DataFrame(
        {
            "timestamp_utc": ["2026-01-01 00:00:00", "2026-01-01 00:00:02"],
            "http_work_rps": ["invalid_str", "10.5"],
        }
    )
    csv_file = tmp_path / "bad_numeric.csv"
    bad_df.to_csv(csv_file, index=False)
    with pytest.raises(ValueError, match="non-numeric or missing values"):
        load_and_validate_csv(str(csv_file), metric_col="http_work_rps")


def test_load_and_validate_insufficient_samples(tmp_path):
    small_df = pd.DataFrame(
        {
            "timestamp_utc": [
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:02",
                "2026-01-01 00:00:04",
            ],
            "http_work_rps": [1.0, 2.0, 3.0],
        }
    )
    csv_file = tmp_path / "small.csv"
    small_df.to_csv(csv_file, index=False)
    with pytest.raises(ValueError, match="Insufficient observations"):
        load_and_validate_csv(str(csv_file))


def test_chronological_split():
    dates = pd.date_range("2026-01-01 00:00:00", periods=10, freq="2s")
    df = pd.DataFrame({"ds": dates, "y": range(10)})

    train_df, test_df = split_chronological(df, test_size=0.3)
    assert len(train_df) == 7
    assert len(test_df) == 3
    # Verify no data leakage: max train timestamp < min test timestamp
    assert train_df["ds"].max() < test_df["ds"].min()
    assert list(train_df["y"]) == [0, 1, 2, 3, 4, 5, 6]
    assert list(test_df["y"]) == [7, 8, 9]


def test_infer_frequency():
    dates = pd.date_range("2026-01-01 00:00:00", periods=5, freq="2s")
    assert infer_frequency(pd.Series(dates)) == "2s"


def test_forecast_training_and_generation(synthetic_timeseries_csv):
    df = load_and_validate_csv(synthetic_timeseries_csv)
    train_df, test_df = split_chronological(df, test_size=0.25)

    model = train_prophet_model(train_df, random_seed=42)
    forecast = generate_forecast(model, periods=5, freq="2s", history=True)

    # Check forecast columns
    for col in ["ds", "yhat", "yhat_lower", "yhat_upper"]:
        assert col in forecast.columns

    # Non-negativity constraint
    assert (forecast["yhat"] >= 0).all()
    assert (forecast["yhat_lower"] >= 0).all()

    # Total forecast rows = train rows (15) + horizon (5) = 20
    assert len(forecast) == len(train_df) + 5


def test_evaluate_forecast_performance_metrics():
    actual = np.array([10.0, 12.0, 14.0, 16.0])
    pred = np.array([10.0, 11.0, 15.0, 18.0])

    metrics = evaluate_forecast_performance(actual, pred)
    # Differences: [0, -1, 1, 2] -> Abs diffs: [0, 1, 1, 2] -> MAE = 4/4 = 1.0
    assert pytest.approx(metrics["mae"], 0.001) == 1.0
    # Sq diffs: [0, 1, 1, 4] -> MSE = 6/4 = 1.5 -> RMSE = sqrt(1.5) ~= 1.2247
    assert pytest.approx(metrics["rmse"], 0.001) == np.sqrt(1.5)
    assert metrics["samples"] == 4


def test_align_actual_and_predicted():
    dates_test = pd.date_range("2026-01-01 00:00:10", periods=3, freq="2s")
    test_df = pd.DataFrame({"ds": dates_test, "y": [10.0, 12.0, 14.0]})

    dates_fcst = pd.date_range("2026-01-01 00:00:10", periods=3, freq="2s")
    fcst_df = pd.DataFrame(
        {
            "ds": dates_fcst,
            "yhat": [10.1, 12.2, 13.9],
            "yhat_lower": [9.0, 11.0, 12.0],
            "yhat_upper": [11.0, 13.0, 15.0],
        }
    )

    aligned = align_actual_and_predicted(test_df, fcst_df)
    assert len(aligned) == 3
    assert list(aligned["y"]) == [10.0, 12.0, 14.0]
    assert list(aligned["yhat"]) == [10.1, 12.2, 13.9]
