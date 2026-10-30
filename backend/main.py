#!/usr/bin/env python3
"""
FastAPI Backend for Predictive Kubernetes Autoscaling.

Integrates live Prometheus metrics with Prophet load forecasting to serve
accurate, forward-looking workload projections for Kubernetes HPA and KEDA.
"""

import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

# Base repository directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backend")
# Suppress cmdstanpy / prophet chatty log messages
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration & Environment Variables
# ---------------------------------------------------------------------------

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "").strip().rstrip("/")
# e.g., "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090"

# Target PromQL query: computes request rate over 1 minute specifically for /api/work
TARGET_SERVICE_NAME = os.getenv("TARGET_SERVICE_NAME", "target-app-service")
DEFAULT_QUERY = (
    f'sum(rate(http_requests_total{{job="{TARGET_SERVICE_NAME}", handler="/api/work"}}[1m]))'
)
PROMETHEUS_QUERY = os.getenv("PROMETHEUS_QUERY", DEFAULT_QUERY)

# History window and sampling interval for Prometheus query_range
HISTORY_WINDOW_SECONDS = int(os.getenv("HISTORY_WINDOW_SECONDS", "300"))  # 5 minutes history
QUERY_STEP = os.getenv("QUERY_STEP", "5s")
PROMETHEUS_TIMEOUT_SECONDS = float(os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "8.0"))

# Forecasting configuration
FORECAST_HORIZON_STEPS = int(os.getenv("FORECAST_HORIZON_STEPS", "15"))  # steps into the future
MIN_OBSERVATIONS_FOR_FIT = int(os.getenv("MIN_OBSERVATIONS_FOR_FIT", "5"))
FORECAST_CACHE_TTL_SECONDS = int(os.getenv("FORECAST_CACHE_TTL_SECONDS", "30"))  # Cache for 30s
RANDOM_SEED = int(os.getenv("FORECAST_SEED", "42"))

# Fallback configuration
ENABLE_STATIC_FALLBACK = os.getenv("ENABLE_STATIC_FALLBACK", "false").lower() in ("true", "1", "yes")
DEFAULT_FORECAST_CSV = os.getenv(
    "FORECAST_CSV_PATH", str(BASE_DIR / "forecasting" / "forecast_periodic.csv")
)
DEFAULT_METRICS_CSV = os.getenv(
    "METRICS_CSV_PATH", str(BASE_DIR / "experiments" / "hpa_periodic.csv")
)
DEFAULT_PREDICTION_HORIZON_STEPS = int(os.getenv("PREDICTION_HORIZON_STEPS", "5"))

app = FastAPI(
    title="Predictive Kubernetes Autoscaling API",
    description="Live Prometheus metrics integration with Prophet load forecasting.",
    version="1.1.0",
)

# ---------------------------------------------------------------------------
# Prometheus Metric Gauges for Autoscaling & Monitoring
# ---------------------------------------------------------------------------

PREDICTED_LOAD_GAUGE = Gauge(
    "predicted_http_work_rps",
    "Latest predicted workload in requests per second for target application.",
)
PREDICTION_PROVENANCE_GAUGE = Gauge(
    "prediction_provenance",
    "Source of prediction: 1 for live_prometheus, 0 for static_csv_fallback.",
)

# Safely initialize gauges with zero before the first forecast
PREDICTED_LOAD_GAUGE.set(0.0)
PREDICTION_PROVENANCE_GAUGE.set(0.0)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    prometheus_configured: bool
    static_fallback_enabled: bool


class ForecastItem(BaseModel):
    ds: str = Field(..., description="Forecast timestamp (ISO/UTC)")
    yhat: float = Field(..., ge=0.0, description="Predicted request rate (RPS)")
    yhat_lower: Optional[float] = Field(None, ge=0.0, description="Lower prediction bound (RPS)")
    yhat_upper: Optional[float] = Field(None, ge=0.0, description="Upper prediction bound (RPS)")


class ForecastResponse(BaseModel):
    provenance: str = Field(..., description="'live_prometheus' or 'static_csv_fallback'")
    source_detail: str
    total_points: int
    data: List[ForecastItem]
    generated_at_epoch: float


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
    provenance: str


class MetricsResponse(BaseModel):
    status: str
    observed: Optional[ObservedMetrics] = Field(
        None, description="Latest observed measurement (from live Prometheus or historical experiment)."
    )
    forecast: Optional[ForecastSummary] = Field(
        None, description="Latest predicted value from live Prophet model or fallback."
    )
    provenance: str = Field(..., description="Source of metrics: 'live_prometheus' or 'static_csv_fallback'")
    note: str


class PredictedLoadResponse(BaseModel):
    metric_name: str
    unit: str
    predicted_load: float = Field(..., ge=0.0, description="Predicted workload demand (RPS)")
    target_timestamp: str
    horizon_steps: int
    calculation_method: str
    provenance: str = Field(..., description="'live_prometheus' or 'static_csv_fallback'")
    keda_integration_status: str = Field(
        default="ready_for_keda_external_scaler",
        description="States readiness for KEDA external metric consumption; not yet active KEDA scaler.",
    )


# ---------------------------------------------------------------------------
# Global In-Memory Forecast Cache
# ---------------------------------------------------------------------------

class ForecastCache:
    """In-memory cache for Prophet forecast dataframe to prevent redundant model fitting."""
    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.timestamp: float = 0.0
        self.provenance: str = "none"
        self.source_detail: str = ""

    def is_valid(self, ttl_seconds: int) -> bool:
        return self.df is not None and (time.time() - self.timestamp) < ttl_seconds

    def update(self, df: pd.DataFrame, provenance: str, source_detail: str):
        self.df = df
        self.timestamp = time.time()
        self.provenance = provenance
        self.source_detail = source_detail

    def clear(self):
        self.df = None
        self.timestamp = 0.0
        self.provenance = "none"
        self.source_detail = ""


forecast_cache = ForecastCache()


# ---------------------------------------------------------------------------
# Prometheus Client & Parser
# ---------------------------------------------------------------------------

def fetch_prometheus_range_metrics(
    prometheus_url: str,
    query: str,
    start_time: float,
    end_time: float,
    step: str = "5s",
    timeout: float = 8.0,
) -> List[Tuple[float, float]]:
    """
    Executes a range query against the Prometheus HTTP API.
    Returns a list of (epoch_timestamp, float_value) tuples.
    """
    endpoint = f"{prometheus_url}/api/v1/query_range"
    params = {
        "query": query,
        "start": f"{start_time:.3f}",
        "end": f"{end_time:.3f}",
        "step": step,
    }
    try:
        response = requests.get(endpoint, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to query Prometheus at '{endpoint}': {str(e)}",
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Prometheus returned malformed JSON: {str(e)}",
        ) from e

    if payload.get("status") != "success":
        error_msg = payload.get("error", "Unknown Prometheus query error")
        raise HTTPException(
            status_code=502, detail=f"Prometheus query failed: {error_msg}"
        )

    data = payload.get("data", {})
    results = data.get("result", [])
    if not results:
        return []

    # Target app aggregate query produces a single series
    series = results[0]
    values = series.get("values", [])

    clean_pairs = []
    for point in values:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            continue
        raw_ts, raw_val = point
        try:
            ts = float(raw_ts)
            val = float(raw_val)
            if math.isfinite(val) and math.isfinite(ts):
                clean_pairs.append((ts, max(0.0, val)))
        except (ValueError, TypeError):
            continue

    return clean_pairs


def parse_prometheus_to_prophet_dataframe(
    points: List[Tuple[float, float]],
    min_observations: int = 5,
) -> pd.DataFrame:
    """
    Converts list of (timestamp, value) pairs into Prophet DataFrame ['ds', 'y'].
    Validates chronological ordering, naivety, and count.
    """
    if len(points) < min_observations:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Insufficient Prometheus observations: found {len(points)}, "
                f"at least {min_observations} required to fit Prophet."
            ),
        )

    df = pd.DataFrame(points, columns=["epoch", "y"])
    # Convert epoch seconds to UTC datetime, then make timezone-naive for Prophet
    df["ds"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_localize(None)
    df = df[["ds", "y"]].sort_values(by="ds").reset_index(drop=True)

    # Deduplicate timestamps if any exist
    if df["ds"].duplicated().any():
        df = df.groupby("ds", as_index=False)["y"].mean()

    return df


# ---------------------------------------------------------------------------
# Prophet Model Training & Forecasting
# ---------------------------------------------------------------------------

def fit_prophet_and_forecast(
    df: pd.DataFrame,
    horizon_steps: int = 15,
    freq: str = "5s",
    seed: int = 42,
) -> pd.DataFrame:
    """
    Fits Prophet time-series model on input DataFrame and returns projected horizon.
    Clamps predictions to >= 0.0.
    """
    try:
        from prophet import Prophet
    except ImportError as e:
        raise HTTPException(
            status_code=500, detail=f"Prophet library not installed on backend: {str(e)}"
        ) from e

    np.random.seed(seed)
    model = Prophet(
        growth="linear",
        yearly_seasonality=False,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.05,
        interval_width=0.95,
    )

    try:
        model.fit(df)
        future = model.make_future_dataframe(periods=horizon_steps, freq=freq, include_history=True)
        fcst = model.predict(future)
    except Exception as e:
        logger.error(f"Prophet fitting/prediction failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Prophet model fitting error: {str(e)}"
        ) from e

    out_df = fcst[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    out_df["yhat"] = out_df["yhat"].clip(lower=0.0)
    out_df["yhat_lower"] = out_df["yhat_lower"].clip(lower=0.0)
    out_df["yhat_upper"] = out_df["yhat_upper"].clip(lower=0.0)

    return out_df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fallback CSV Loader
# ---------------------------------------------------------------------------

def load_static_forecast_fallback() -> pd.DataFrame:
    """Reads static CSV forecast as fallback when live Prometheus is unavailable."""
    path = Path(os.getenv("FORECAST_CSV_PATH", DEFAULT_FORECAST_CSV))
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Static forecast fallback file not found: {path}",
        )
    try:
        df = pd.read_csv(path)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to read static forecast CSV: {e}"
        ) from e

    if df.empty or "ds" not in df.columns or "yhat" not in df.columns:
        raise HTTPException(
            status_code=422, detail="Static forecast CSV empty or missing required columns ('ds', 'yhat')."
        )

    df["ds_dt"] = pd.to_datetime(df["ds"])
    df["yhat"] = pd.to_numeric(df["yhat"], errors="coerce").fillna(0.0).clip(lower=0.0)
    if "yhat_lower" in df.columns:
        df["yhat_lower"] = pd.to_numeric(df["yhat_lower"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        df["yhat_lower"] = None
    if "yhat_upper" in df.columns:
        df["yhat_upper"] = pd.to_numeric(df["yhat_upper"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        df["yhat_upper"] = None

    return df.sort_values(by="ds_dt").reset_index(drop=True)


def load_static_metrics_fallback() -> pd.DataFrame:
    """Reads static CSV metrics as fallback."""
    path = Path(os.getenv("METRICS_CSV_PATH", DEFAULT_METRICS_CSV))
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail=f"Static metrics fallback file not found: {path}"
        )
    try:
        df = pd.read_csv(path)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to read static metrics CSV: {e}"
        ) from e
    if df.empty or "timestamp_utc" not in df.columns or "http_work_rps" not in df.columns:
        raise HTTPException(
            status_code=422, detail="Static metrics CSV empty or missing required columns."
        )
    return df


# ---------------------------------------------------------------------------
# Core Forecasting Orchestrator
# ---------------------------------------------------------------------------

def get_or_generate_forecast(force_refresh: bool = False) -> Tuple[pd.DataFrame, str, str]:
    """
    Returns (forecast_dataframe, provenance, source_detail).
    Checks cache first unless force_refresh is True.
    Attempts live Prometheus query and Prophet fitting.
    If live Prometheus fails:
      - Uses static CSV if ENABLE_STATIC_FALLBACK is True.
      - Otherwise raises HTTPException.
    """
    ttl = int(os.getenv("FORECAST_CACHE_TTL_SECONDS", str(FORECAST_CACHE_TTL_SECONDS)))
    if not force_refresh and forecast_cache.is_valid(ttl):
        logger.debug("Returning cached Prophet forecast.")
        return forecast_cache.df, forecast_cache.provenance, forecast_cache.source_detail

    prom_url = os.getenv("PROMETHEUS_URL", PROMETHEUS_URL)
    prom_query = os.getenv("PROMETHEUS_QUERY", PROMETHEUS_QUERY)
    history_sec = int(os.getenv("HISTORY_WINDOW_SECONDS", str(HISTORY_WINDOW_SECONDS)))
    step = os.getenv("QUERY_STEP", QUERY_STEP)
    horizon = int(os.getenv("FORECAST_HORIZON_STEPS", str(FORECAST_HORIZON_STEPS)))
    seed = int(os.getenv("FORECAST_SEED", str(RANDOM_SEED)))
    fallback_allowed = os.getenv("ENABLE_STATIC_FALLBACK", str(ENABLE_STATIC_FALLBACK)).lower() in ("true", "1", "yes")

    # Attempt live Prometheus path if URL configured
    if prom_url:
        logger.info(f"Retrieving live metrics from Prometheus ({prom_url})...")
        end_time = time.time()
        start_time = end_time - history_sec
        try:
            points = fetch_prometheus_range_metrics(
                prometheus_url=prom_url,
                query=prom_query,
                start_time=start_time,
                end_time=end_time,
                step=step,
                timeout=float(os.getenv("PROMETHEUS_TIMEOUT_SECONDS", str(PROMETHEUS_TIMEOUT_SECONDS))),
            )
            df_in = parse_prometheus_to_prophet_dataframe(
                points, min_observations=int(os.getenv("MIN_OBSERVATIONS_FOR_FIT", str(MIN_OBSERVATIONS_FOR_FIT)))
            )
            logger.info(f"Fitting Prophet model on {len(df_in)} live Prometheus observations...")
            fcst_df = fit_prophet_and_forecast(df_in, horizon_steps=horizon, freq=step, seed=seed)
            provenance = "live_prometheus"
            detail = f"Prometheus query on '{prom_query}' over {history_sec}s window"
            forecast_cache.update(fcst_df, provenance, detail)
            return fcst_df, provenance, detail
        except HTTPException as e:
            logger.warning(f"Live Prometheus forecasting failed: {e.detail}")
            if not fallback_allowed:
                raise e
            logger.info("Static fallback enabled; falling back to static CSV forecast.")

    # Fallback to static CSV
    if fallback_allowed:
        fcst_df = load_static_forecast_fallback()
        provenance = "static_csv_fallback"
        detail = f"CSV fallback: {Path(os.getenv('FORECAST_CSV_PATH', DEFAULT_FORECAST_CSV)).name}"
        forecast_cache.update(fcst_df, provenance, detail)
        return fcst_df, provenance, detail

    # No Prometheus URL and no fallback allowed
    raise HTTPException(
        status_code=503,
        detail=(
            "Prometheus is not configured (PROMETHEUS_URL empty) and "
            "ENABLE_STATIC_FALLBACK is false. Cannot generate forecast."
        ),
    )


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def get_health():
    """Health check endpoint indicating whether the backend API is operational."""
    prom_configured = bool(os.getenv("PROMETHEUS_URL", PROMETHEUS_URL))
    fallback_enabled = os.getenv("ENABLE_STATIC_FALLBACK", str(ENABLE_STATIC_FALLBACK)).lower() in ("true", "1", "yes")
    return HealthResponse(
        status="ok",
        service="predictive-k8s-autoscaling-backend",
        version="1.1.0",
        prometheus_configured=prom_configured,
        static_fallback_enabled=fallback_enabled,
    )


@app.get("/api/forecast", response_model=ForecastResponse)
def get_forecast(
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Limit returned forecast points"),
    force_refresh: bool = Query(False, description="Force re-querying Prometheus and fitting a new Prophet model"),
):
    """
    Returns the Prophet workload forecast.
    Provenance indicates 'live_prometheus' or 'static_csv_fallback'.
    """
    fcst_df, provenance, source_detail = get_or_generate_forecast(force_refresh=force_refresh)

    if limit is not None:
        fcst_df = fcst_df.tail(limit)

    items = []
    for _, row in fcst_df.iterrows():
        items.append(
            ForecastItem(
                ds=str(row["ds"]),
                yhat=round(float(row["yhat"]), 4),
                yhat_lower=round(float(row["yhat_lower"]), 4) if pd.notnull(row.get("yhat_lower")) else None,
                yhat_upper=round(float(row["yhat_upper"]), 4) if pd.notnull(row.get("yhat_upper")) else None,
            )
        )

    return ForecastResponse(
        provenance=provenance,
        source_detail=source_detail,
        total_points=len(items),
        data=items,
        generated_at_epoch=round(forecast_cache.timestamp, 3),
    )


@app.get("/api/metrics", response_model=MetricsResponse)
def get_metrics():
    """
    Returns the latest observed request-rate measurement and forecast summary.
    Provenance clearly distinguishes live Prometheus measurements from static CSV data.
    """
    observed_obj: Optional[ObservedMetrics] = None
    forecast_obj: Optional[ForecastSummary] = None
    provenance = "none"

    # Attempt latest observed metric
    prom_url = os.getenv("PROMETHEUS_URL", PROMETHEUS_URL)
    prom_query = os.getenv("PROMETHEUS_QUERY", PROMETHEUS_QUERY)
    fallback_allowed = os.getenv("ENABLE_STATIC_FALLBACK", str(ENABLE_STATIC_FALLBACK)).lower() in ("true", "1", "yes")

    if prom_url:
        try:
            # Query instant vector for latest observed rate
            endpoint = f"{prom_url}/api/v1/query"
            resp = requests.get(endpoint, params={"query": prom_query}, timeout=5.0)
            if resp.status_code == 200:
                res_json = resp.json()
                results = res_json.get("data", {}).get("result", [])
                if results and "value" in results[0]:
                    raw_epoch, raw_val = results[0]["value"]
                    observed_obj = ObservedMetrics(
                        timestamp_utc=pd.to_datetime(float(raw_epoch), unit="s", utc=True).isoformat(),
                        epoch_timestamp=float(raw_epoch),
                        collection_status="ok",
                        http_work_rps=round(max(0.0, float(raw_val)), 4),
                    )
                    provenance = "live_prometheus"
        except Exception as e:
            logger.warning(f"Failed to query instant metric from Prometheus: {e}")

    # Fallback to static metrics CSV if observed is not populated and fallback allowed
    if observed_obj is None and fallback_allowed:
        try:
            metrics_df = load_static_metrics_fallback()
            latest_m = metrics_df.iloc[-1]
            observed_obj = ObservedMetrics(
                timestamp_utc=str(latest_m["timestamp_utc"]),
                epoch_timestamp=float(latest_m.get("epoch_timestamp", 0.0)),
                collection_status=str(latest_m.get("collection_status", "unknown")),
                http_work_rps=round(float(latest_m["http_work_rps"]), 4),
                http_rps=round(float(latest_m["http_rps"]), 4) if "http_rps" in latest_m and pd.notnull(latest_m["http_rps"]) else None,
                cpu_usage_cores=round(float(latest_m["cpu_usage_cores"]), 4) if "cpu_usage_cores" in latest_m and pd.notnull(latest_m["cpu_usage_cores"]) else None,
                pod_count=int(latest_m["pod_count"]) if "pod_count" in latest_m and pd.notnull(latest_m["pod_count"]) else None,
                hpa_current_replicas=int(latest_m["hpa_current_replicas"]) if "hpa_current_replicas" in latest_m and pd.notnull(latest_m["hpa_current_replicas"]) else None,
                hpa_desired_replicas=int(latest_m["hpa_desired_replicas"]) if "hpa_desired_replicas" in latest_m and pd.notnull(latest_m["hpa_desired_replicas"]) else None,
            )
            provenance = "static_csv_fallback"
        except Exception as e:
            logger.warning(f"Could not load fallback metrics CSV: {e}")

    # Load latest forecast prediction
    try:
        fcst_df, fcst_prov, _ = get_or_generate_forecast(force_refresh=False)
        latest_f = fcst_df.iloc[-1]
        forecast_obj = ForecastSummary(
            ds=str(latest_f["ds"]),
            yhat=round(float(latest_f["yhat"]), 4),
            yhat_lower=round(float(latest_f["yhat_lower"]), 4) if pd.notnull(latest_f.get("yhat_lower")) else None,
            yhat_upper=round(float(latest_f["yhat_upper"]), 4) if pd.notnull(latest_f.get("yhat_upper")) else None,
            provenance=fcst_prov,
        )
    except Exception as e:
        logger.warning(f"Forecast unavailable for /api/metrics: {e}")

    if observed_obj is None and forecast_obj is None:
        raise HTTPException(
            status_code=503,
            detail="Neither observed telemetry nor forecast projections could be retrieved.",
        )

    return MetricsResponse(
        status="ok",
        observed=observed_obj,
        forecast=forecast_obj,
        provenance=provenance,
        note=(
            "Provenance indicates whether observed telemetry came from 'live_prometheus' "
            "or 'static_csv_fallback'."
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
    force_refresh: bool = Query(False, description="Force refreshing the model prediction"),
):
    """
    Returns a numeric, non-negative predicted load value suitable for consumption
    by a future KEDA external metric scaler.

    Provenance specifies whether the prediction was generated from 'live_prometheus'
    or 'static_csv_fallback'.
    """
    fcst_df, provenance, _ = get_or_generate_forecast(force_refresh=force_refresh)

    if len(fcst_df) == 0:
        raise HTTPException(status_code=422, detail="Forecast series contains no predictions.")

    available_steps = min(horizon_steps, len(fcst_df))
    window_df = fcst_df.tail(available_steps)

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

    predicted_val = max(0.0, round(predicted_val, 4))

    # Update Prometheus gauges with the latest forecast and provenance
    PREDICTED_LOAD_GAUGE.set(predicted_val)
    provenance_val = 1.0 if provenance == "live_prometheus" else 0.0
    PREDICTION_PROVENANCE_GAUGE.set(provenance_val)

    return PredictedLoadResponse(
        metric_name="predicted_http_work_rps",
        unit="requests_per_second",
        predicted_load=predicted_val,
        target_timestamp=target_timestamp,
        horizon_steps=available_steps,
        calculation_method=calc_method,
        provenance=provenance,
        keda_integration_status="ready_for_keda_external_scaler",
    )


@app.get("/metrics")
def get_prometheus_metrics():
    """
    Exposes the most recently calculated Prometheus metrics and Gauges
    for Prometheus scraping and KEDA predictive autoscaling.

    Does not independently fit Prophet or query Prometheus; returns current state instantly.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
