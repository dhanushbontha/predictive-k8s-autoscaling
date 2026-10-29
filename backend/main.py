#!/usr/bin/env python3
"""
FastAPI Backend for Predictive Kubernetes Autoscaling.

Exposes endpoints to query generated Prophet forecasts, historical experiment
snapshots, and predicted load values suitable for external metrics consumers like KEDA.
"""

import os
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

# Base repository directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Configurable environment variables
DEFAULT_FORECAST_CSV = os.getenv(
    "FORECAST_CSV_PATH", str(BASE_DIR / "forecasting" / "forecast_periodic.csv")
)
DEFAULT_METRICS_CSV = os.getenv(
    "METRICS_CSV_PATH", str(BASE_DIR / "experiments" / "hpa_periodic.csv")
)
DEFAULT_PREDICTION_HORIZON_STEPS = int(os.getenv("PREDICTION_HORIZON_STEPS", "5"))

app = FastAPI(
    title="Predictive Kubernetes Autoscaling API",
    description="Backend API serving Prophet load forecasts and predicted workload metrics.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ForecastItem(BaseModel):
    ds: str = Field(..., description="Forecast timestamp (ISO/UTC)")
    yhat: float = Field(..., ge=0.0, description="Predicted request rate (RPS)")
    yhat_lower: Optional[float] = Field(None, ge=0.0, description="Lower prediction bound (RPS)")
    yhat_upper: Optional[float] = Field(None, ge=0.0, description="Upper prediction bound (RPS)")


class ForecastResponse(BaseModel):
    source_file: str
    total_points: int
    data: List[ForecastItem]


class ObservedMetrics(BaseModel):
    timestamp_utc: str
    epoch_timestamp: float
    collection_status: str
    http_work_rps: float
    http_rps: Optional[float] = None
    cpu_usage_cores: Optional[float] = None
    pod_count: Optional[int] = None
    hpa_current_replicas: Optional[int] = None
    hpa_desired_replicas: Optional[int] = None


class ForecastSummary(BaseModel):
    ds: str
    yhat: float
    yhat_lower: Optional[float] = None
    yhat_upper: Optional[float] = None


class MetricsResponse(BaseModel):
    status: str
    observed: Optional[ObservedMetrics] = Field(
        None, description="Latest observed historical snapshot from experiment metrics."
    )
    forecast: Optional[ForecastSummary] = Field(
        None, description="Latest available predicted value from generated forecast."
    )
    note: str = Field(
        ...,
        description="Explains that observed metrics and forecasts represent collected/generated data, not live telemetry.",
    )


class PredictedLoadResponse(BaseModel):
    metric_name: str
    unit: str
    predicted_load: float = Field(..., ge=0.0, description="Predicted workload demand (RPS)")
    target_timestamp: str
    horizon_steps: int
    calculation_method: str
    keda_integration_status: str = Field(
        default="ready_for_keda_external_scaler",
        description="States readiness for KEDA external metric consumption; not yet active KEDA scaler.",
    )


# ---------------------------------------------------------------------------
# Helper Functions for CSV Reading and Validation
# ---------------------------------------------------------------------------

def load_forecast_data(file_path: Optional[str] = None) -> pd.DataFrame:
    """
    Reads and validates the forecast CSV.
    Expected columns: ds, yhat, and optionally yhat_lower, yhat_upper.
    """
    path_str = file_path or os.getenv("FORECAST_CSV_PATH", DEFAULT_FORECAST_CSV)
    path = Path(path_str)

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Forecast file not found at: {path_str}. Run forecasting pipeline first.",
        )

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        raise HTTPException(
            status_code=422, detail="Forecast CSV is empty. No predictions available."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to parse forecast CSV: {str(e)}"
        ) from e

    if df.empty:
        raise HTTPException(
            status_code=422, detail="Forecast CSV is empty. No predictions available."
        )

    # Check required columns
    required_cols = ["ds", "yhat"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Forecast CSV missing required columns: {missing}. Found: {list(df.columns)}",
        )

    # Validate timestamps
    try:
        df["ds_dt"] = pd.to_datetime(df["ds"])
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid timestamp format in 'ds' column: {str(e)}",
        ) from e

    # Validate numeric predicted value
    numeric_yhat = pd.to_numeric(df["yhat"], errors="coerce")
    if numeric_yhat.isnull().any():
        raise HTTPException(
            status_code=422,
            detail="Forecast CSV contains non-numeric or null values in 'yhat' column.",
        )
    df["yhat"] = numeric_yhat.clip(lower=0.0)

    # Validate optional lower and upper bounds if present
    if "yhat_lower" in df.columns:
        numeric_lower = pd.to_numeric(df["yhat_lower"], errors="coerce")
        df["yhat_lower"] = numeric_lower.clip(lower=0.0)
    else:
        df["yhat_lower"] = None

    if "yhat_upper" in df.columns:
        numeric_upper = pd.to_numeric(df["yhat_upper"], errors="coerce")
        df["yhat_upper"] = numeric_upper.clip(lower=0.0)
    else:
        df["yhat_upper"] = None

    return df.sort_values(by="ds_dt").reset_index(drop=True)


def load_observed_metrics(file_path: Optional[str] = None) -> pd.DataFrame:
    """
    Reads and validates the observed historical experiment metrics CSV.
    Expected columns: timestamp_utc, epoch_timestamp, collection_status, http_work_rps.
    """
    path_str = file_path or os.getenv("METRICS_CSV_PATH", DEFAULT_METRICS_CSV)
    path = Path(path_str)

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Metrics file not found at: {path_str}. Verify experiment metrics path.",
        )

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        raise HTTPException(
            status_code=422, detail="Metrics CSV is empty. No observed data available."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to parse metrics CSV: {str(e)}"
        ) from e

    if df.empty:
        raise HTTPException(
            status_code=422, detail="Metrics CSV is empty. No observed data available."
        )

    required_cols = ["timestamp_utc", "http_work_rps"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Metrics CSV missing required columns: {missing}. Found: {list(df.columns)}",
        )

    try:
        df["timestamp_dt"] = pd.to_datetime(df["timestamp_utc"])
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid timestamp format in 'timestamp_utc': {str(e)}",
        ) from e

    numeric_rps = pd.to_numeric(df["http_work_rps"], errors="coerce")
    if numeric_rps.isnull().any():
        raise HTTPException(
            status_code=422,
            detail="Metrics CSV contains non-numeric values in 'http_work_rps'.",
        )
    df["http_work_rps"] = numeric_rps

    return df.sort_values(by="timestamp_dt").reset_index(drop=True)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def get_health():
    """Health check endpoint indicating whether the backend API is operational."""
    return HealthResponse(
        status="ok",
        service="predictive-k8s-autoscaling-backend",
        version="1.0.0",
    )


@app.get("/api/forecast", response_model=ForecastResponse)
def get_forecast(
    limit: Optional[int] = Query(
        None, ge=1, le=1000, description="Limit the number of forecast points returned"
    )
):
    """
    Returns available generated Prophet forecast points from CSV including:
    - Timestamps (ds)
    - Predicted load (yhat)
    - Lower and upper prediction intervals (yhat_lower, yhat_upper)
    """
    df = load_forecast_data()
    source_path = os.getenv("FORECAST_CSV_PATH", DEFAULT_FORECAST_CSV)

    if limit is not None:
        df = df.tail(limit)

    items = []
    for _, row in df.iterrows():
        items.append(
            ForecastItem(
                ds=str(row["ds"]),
                yhat=round(float(row["yhat"]), 4),
                yhat_lower=(
                    round(float(row["yhat_lower"]), 4)
                    if pd.notnull(row["yhat_lower"])
                    else None
                ),
                yhat_upper=(
                    round(float(row["yhat_upper"]), 4)
                    if pd.notnull(row["yhat_upper"])
                    else None
                ),
            )
        )

    return ForecastResponse(
        source_file=Path(source_path).name,
        total_points=len(items),
        data=items,
    )


@app.get("/api/metrics", response_model=MetricsResponse)
def get_metrics():
    """
    Returns the latest available observed historical load measurement and latest
    forecast prediction. Clearly distinguishes observed data from forecast predictions.
    """
    observed_obj: Optional[ObservedMetrics] = None
    forecast_obj: Optional[ForecastSummary] = None

    # Load latest observed snapshot
    try:
        metrics_df = load_observed_metrics()
        latest_m = metrics_df.iloc[-1]
        observed_obj = ObservedMetrics(
            timestamp_utc=str(latest_m["timestamp_utc"]),
            epoch_timestamp=float(latest_m.get("epoch_timestamp", 0.0)),
            collection_status=str(latest_m.get("collection_status", "unknown")),
            http_work_rps=round(float(latest_m["http_work_rps"]), 4),
            http_rps=(
                round(float(latest_m["http_rps"]), 4)
                if "http_rps" in latest_m and pd.notnull(latest_m["http_rps"])
                else None
            ),
            cpu_usage_cores=(
                round(float(latest_m["cpu_usage_cores"]), 4)
                if "cpu_usage_cores" in latest_m and pd.notnull(latest_m["cpu_usage_cores"])
                else None
            ),
            pod_count=(
                int(latest_m["pod_count"])
                if "pod_count" in latest_m and pd.notnull(latest_m["pod_count"])
                else None
            ),
            hpa_current_replicas=(
                int(latest_m["hpa_current_replicas"])
                if "hpa_current_replicas" in latest_m and pd.notnull(latest_m["hpa_current_replicas"])
                else None
            ),
            hpa_desired_replicas=(
                int(latest_m["hpa_desired_replicas"])
                if "hpa_desired_replicas" in latest_m and pd.notnull(latest_m["hpa_desired_replicas"])
                else None
            ),
        )
    except HTTPException:
        # If metrics file is unavailable, keep observed as None rather than fabricating
        observed_obj = None

    # Load latest forecast prediction
    try:
        forecast_df = load_forecast_data()
        latest_f = forecast_df.iloc[-1]
        forecast_obj = ForecastSummary(
            ds=str(latest_f["ds"]),
            yhat=round(float(latest_f["yhat"]), 4),
            yhat_lower=(
                round(float(latest_f["yhat_lower"]), 4)
                if pd.notnull(latest_f["yhat_lower"])
                else None
            ),
            yhat_upper=(
                round(float(latest_f["yhat_upper"]), 4)
                if pd.notnull(latest_f["yhat_upper"])
                else None
            ),
        )
    except HTTPException:
        forecast_obj = None

    if observed_obj is None and forecast_obj is None:
        raise HTTPException(
            status_code=404,
            detail="Neither observed experiment metrics nor forecast predictions could be loaded.",
        )

    return MetricsResponse(
        status="ok",
        observed=observed_obj,
        forecast=forecast_obj,
        note=(
            "Observed metrics represent historical experiment recordings; "
            "forecast values are Prophet model projections. "
            "These do not represent live real-time cluster telemetry."
        ),
    )


@app.get("/api/predicted-load", response_model=PredictedLoadResponse)
def get_predicted_load(
    horizon_steps: int = Query(
        DEFAULT_PREDICTION_HORIZON_STEPS,
        ge=1,
        le=50,
        description="Number of steps ahead in the forecast to retrieve for autoscaling decision",
    ),
    aggregation: str = Query(
        "point",
        pattern="^(point|mean|max)$",
        description="Aggregation method: 'point' (horizon step), 'mean' (window average), or 'max' (window peak)",
    ),
):
    """
    Returns a numeric, non-negative predicted load value suitable for consumption
    by a future KEDA external metric scaler (e.g. keda-external-scaler or prometheus-adapter).

    Derivation:
    - Reads the Prophet forecast series.
    - If aggregation='point', selects the predicted load exactly `horizon_steps` ahead of history.
    - If aggregation='max', takes the peak predicted load across the next `horizon_steps`.
    - If aggregation='mean', takes the average predicted load across the next `horizon_steps`.
    - Enforces non-negativity (predicted_load >= 0.0).
    """
    forecast_df = load_forecast_data()

    if len(forecast_df) == 0:
        raise HTTPException(status_code=422, detail="Forecast dataset contains no predictions.")

    # Determine slice of future steps
    # We select the tail `horizon_steps` of the forecast DataFrame
    available_steps = min(horizon_steps, len(forecast_df))
    window_df = forecast_df.tail(available_steps)

    if aggregation == "max":
        predicted_val = float(window_df["yhat"].max())
        target_timestamp = str(window_df.loc[window_df["yhat"].idxmax(), "ds"])
        calc_method = f"maximum predicted load over next {available_steps} forecast steps"
    elif aggregation == "mean":
        predicted_val = float(window_df["yhat"].mean())
        target_timestamp = str(window_df.iloc[-1]["ds"])
        calc_method = f"mean predicted load over next {available_steps} forecast steps"
    else:  # "point"
        target_row = window_df.iloc[-1]
        predicted_val = float(target_row["yhat"])
        target_timestamp = str(target_row["ds"])
        calc_method = f"point prediction at horizon step {available_steps}"

    # Enforce non-negativity
    predicted_val = max(0.0, round(predicted_val, 4))

    return PredictedLoadResponse(
        metric_name="predicted_http_work_rps",
        unit="requests_per_second",
        predicted_load=predicted_val,
        target_timestamp=target_timestamp,
        horizon_steps=available_steps,
        calculation_method=calc_method,
        keda_integration_status="ready_for_keda_external_scaler",
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
