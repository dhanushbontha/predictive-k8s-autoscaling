#!/usr/bin/env python3
"""
FastAPI Backend for Predictive Kubernetes Autoscaling.

Integrates live Prometheus metrics with Prophet load forecasting to serve
accurate, forward-looking workload projections for Kubernetes HPA and KEDA.
"""

import asyncio
import logging
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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

def get_prometheus_url() -> str:
    """Returns configured or auto-detected Prometheus base URL (supports local port-forwarding)."""
    env_url = os.getenv("PROMETHEUS_URL", PROMETHEUS_URL).strip().rstrip("/")
    if env_url:
        return env_url
    for candidate in ("http://127.0.0.1:9090", "http://localhost:9090"):
        try:
            r = requests.get(f"{candidate}/api/v1/query", params={"query": "up"}, timeout=0.5)
            if r.status_code == 200:
                return candidate
        except Exception:
            pass
    return ""

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

# Automatic prediction refresh configuration
REFRESH_INTERVAL_SECONDS = float(os.getenv("REFRESH_INTERVAL_SECONDS", "15.0"))
ENABLE_AUTO_REFRESH = os.getenv("ENABLE_AUTO_REFRESH", "true").lower() in ("true", "1", "yes")
AUTO_REFRESH_HORIZON_STEPS = int(os.getenv("AUTO_REFRESH_HORIZON_STEPS", str(DEFAULT_PREDICTION_HORIZON_STEPS)))
AUTO_REFRESH_AGGREGATION = os.getenv("AUTO_REFRESH_AGGREGATION", "point")

# Global reference for the background refresh task
_refresh_task: Optional[asyncio.Task] = None

# Thread synchronization lock for Prophet model fitting and gauge updates
_forecast_lock = threading.RLock()

# Last forecast error message (thread-safe)
_last_forecast_error: Optional[str] = None
_last_forecast_error_lock = threading.Lock()


def set_last_forecast_error(err: Optional[str]):
    global _last_forecast_error
    with _last_forecast_error_lock:
        _last_forecast_error = err


def get_last_forecast_error() -> Optional[str]:
    with _last_forecast_error_lock:
        return _last_forecast_error


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application startup and clean shutdown of background tasks."""
    start_prediction_refresh_loop()
    try:
        yield
    finally:
        await stop_prediction_refresh_loop()


app = FastAPI(
    title="Predictive Kubernetes Autoscaling API",
    description="Live Prometheus metrics integration with Prophet load forecasting.",
    version="1.2.0",
    lifespan=lifespan,
)

# Enable CORS for local and web-based dashboard development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
PREDICTION_TIMESTAMP_GAUGE = Gauge(
    "prediction_timestamp_seconds",
    "Unix epoch timestamp when the prediction was last successfully refreshed.",
)

# Safely initialize gauges with zero before the first forecast
PREDICTED_LOAD_GAUGE.set(0.0)
PREDICTION_PROVENANCE_GAUGE.set(0.0)
PREDICTION_TIMESTAMP_GAUGE.set(0.0)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    prometheus_configured: bool
    prometheus_connected: bool = False
    static_fallback_enabled: bool
    auto_refresh_enabled: bool = True
    refresh_interval_seconds: float = 15.0
    background_task_running: bool = False
    last_prediction_timestamp: Optional[float] = None
    prediction_age_seconds: Optional[float] = None
    is_fresh: bool = False
    last_forecast_error: Optional[str] = None


class HistoricalItem(BaseModel):
    ds: str = Field(..., description="Observation timestamp")
    actual_rps: float = Field(..., ge=0.0, description="Observed RPS")


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
    history: List[HistoricalItem] = Field(default_factory=list)
    generated_at_epoch: float
    horizon_steps: int = 15


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
        self.history_df: Optional[pd.DataFrame] = None
        self.timestamp: float = 0.0
        self.provenance: str = "none"
        self.source_detail: str = ""

    def is_valid(self, ttl_seconds: int) -> bool:
        return self.df is not None and (time.time() - self.timestamp) < ttl_seconds

    def update(
        self,
        df: pd.DataFrame,
        provenance: str,
        source_detail: str,
        history_df: Optional[pd.DataFrame] = None,
    ):
        self.df = df
        self.history_df = history_df
        self.timestamp = time.time()
        self.provenance = provenance
        self.source_detail = source_detail

    def clear(self):
        self.df = None
        self.history_df = None
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

    with _forecast_lock:
        # Re-check cache inside lock to serialize concurrent callers safely
        if not force_refresh and forecast_cache.is_valid(ttl):
            logger.debug("Returning freshly updated cached Prophet forecast.")
            return forecast_cache.df, forecast_cache.provenance, forecast_cache.source_detail

        # Prevent duplicate fit if another thread completed a refresh very recently (< 1.0s)
        if force_refresh and forecast_cache.df is not None and (time.time() - forecast_cache.timestamp) < 1.0:
            logger.debug("Reusing forecast refreshed less than 1s ago.")
            return forecast_cache.df, forecast_cache.provenance, forecast_cache.source_detail

        prom_url = get_prometheus_url()
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
                forecast_cache.update(fcst_df, provenance, detail, history_df=df_in)
                set_last_forecast_error(None)
                return fcst_df, provenance, detail
            except HTTPException as e:
                set_last_forecast_error(e.detail)
                if force_refresh:
                    forecast_cache.clear()
                logger.warning(f"Live Prometheus forecasting failed: {e.detail}")
                if not fallback_allowed:
                    raise e
                logger.info("Static fallback enabled; falling back to static CSV forecast.")
            except Exception as e:
                err_msg = str(e)
                set_last_forecast_error(err_msg)
                if force_refresh:
                    forecast_cache.clear()
                logger.warning(f"Live Prometheus forecasting failed with error: {err_msg}")
                if not fallback_allowed:
                    raise HTTPException(status_code=500, detail=f"Forecasting error: {err_msg}") from e
                logger.info("Static fallback enabled; falling back to static CSV forecast.")

        # Fallback to static CSV
        if fallback_allowed:
            fcst_df = load_static_forecast_fallback()
            provenance = "static_csv_fallback"
            detail = f"CSV fallback: {Path(os.getenv('FORECAST_CSV_PATH', DEFAULT_FORECAST_CSV)).name}"
            forecast_cache.update(fcst_df, provenance, detail)
            return fcst_df, provenance, detail

        # No Prometheus URL and no fallback allowed
        err_detail = (
            "Prometheus is not configured (PROMETHEUS_URL empty) and "
            "ENABLE_STATIC_FALLBACK is false. Cannot generate forecast."
        )
        set_last_forecast_error(err_detail)
        if force_refresh:
            forecast_cache.clear()
        raise HTTPException(
            status_code=503,
            detail=err_detail,
        )


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def get_health():
    """Health check endpoint indicating whether the backend API is operational."""
    prom_url = get_prometheus_url()
    prom_connected = False
    if prom_url:
        try:
            r = requests.get(f"{prom_url}/api/v1/query", params={"query": "up"}, timeout=2.0)
            prom_connected = (r.status_code == 200)
        except Exception:
            prom_connected = False

    fallback_enabled = os.getenv("ENABLE_STATIC_FALLBACK", str(ENABLE_STATIC_FALLBACK)).lower() in ("true", "1", "yes")
    auto_enabled = os.getenv("ENABLE_AUTO_REFRESH", str(ENABLE_AUTO_REFRESH)).lower() in ("true", "1", "yes")
    refresh_sec = float(os.getenv("REFRESH_INTERVAL_SECONDS", str(REFRESH_INTERVAL_SECONDS)))
    task_running = _refresh_task is not None and not _refresh_task.done()

    last_ts = PREDICTION_TIMESTAMP_GAUGE._value.get()
    now = time.time()
    age = max(0.0, round(now - last_ts, 1)) if last_ts > 0 else None
    is_fresh = bool(age is not None and age < 45.0)

    return HealthResponse(
        status="ok",
        service="predictive-k8s-autoscaling-backend",
        version="1.2.0",
        prometheus_configured=bool(prom_url),
        prometheus_connected=prom_connected,
        static_fallback_enabled=fallback_enabled,
        auto_refresh_enabled=auto_enabled,
        refresh_interval_seconds=refresh_sec,
        background_task_running=task_running,
        last_prediction_timestamp=round(last_ts, 2) if last_ts > 0 else None,
        prediction_age_seconds=age,
        is_fresh=is_fresh,
        last_forecast_error=get_last_forecast_error(),
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

    history_items = []
    if forecast_cache.history_df is not None:
        for _, h_row in forecast_cache.history_df.tail(limit or 50).iterrows():
            history_items.append(
                HistoricalItem(
                    ds=str(h_row["ds"]),
                    actual_rps=round(max(0.0, float(h_row["y"])), 4),
                )
            )

    return ForecastResponse(
        provenance=provenance,
        source_detail=source_detail,
        total_points=len(items),
        data=items,
        history=history_items,
        generated_at_epoch=round(forecast_cache.timestamp, 3),
        horizon_steps=len(items),
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
    prom_url = get_prometheus_url()
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
                else:
                    observed_obj = ObservedMetrics(
                        timestamp_utc=pd.to_datetime(time.time(), unit="s", utc=True).isoformat(),
                        epoch_timestamp=time.time(),
                        collection_status="idle",
                        http_work_rps=0.0,
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

    # Load latest forecast prediction only if valid, cached, and fresh (<45s)
    try:
        fcst_df, fcst_prov, _ = get_or_generate_forecast(force_refresh=False)
        last_pred_ts = PREDICTION_TIMESTAMP_GAUGE._value.get()
        cache_ts = forecast_cache.timestamp
        if last_pred_ts > 0:
            is_pred_fresh = (time.time() - last_pred_ts) < 45.0
        elif cache_ts > 0:
            is_pred_fresh = (time.time() - cache_ts) < 45.0
        else:
            is_pred_fresh = False

        if is_pred_fresh and fcst_df is not None and not fcst_df.empty:
            latest_f = fcst_df.iloc[-1]
            forecast_obj = ForecastSummary(
                ds=str(latest_f["ds"]),
                yhat=round(float(latest_f["yhat"]), 4),
                yhat_lower=round(float(latest_f["yhat_lower"]), 4) if pd.notnull(latest_f.get("yhat_lower")) else None,
                yhat_upper=round(float(latest_f["yhat_upper"]), 4) if pd.notnull(latest_f.get("yhat_upper")) else None,
                provenance=fcst_prov,
            )
        else:
            forecast_obj = None
    except Exception as e:
        logger.warning(f"Forecast unavailable for /api/metrics: {e}")
        forecast_obj = None

    if observed_obj is None and forecast_obj is None:
        if not fallback_allowed and not prom_url:
            raise HTTPException(
                status_code=503,
                detail="Neither observed telemetry nor forecast projections could be retrieved.",
            )
        observed_obj = ObservedMetrics(
            timestamp_utc=pd.to_datetime(time.time(), unit="s", utc=True).isoformat(),
            epoch_timestamp=time.time(),
            collection_status="idle",
            http_work_rps=0.0,
        )
        provenance = "live_prometheus" if prom_url else "none"

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


# ---------------------------------------------------------------------------
# Prediction Calculation & Background Refresh Functions
# ---------------------------------------------------------------------------

def refresh_prediction(
    horizon_steps: int = DEFAULT_PREDICTION_HORIZON_STEPS,
    aggregation: str = "point",
    force_refresh: bool = True,
) -> Tuple[float, str, str, str, int]:
    """
    Computes predicted load for the given horizon and updates Prometheus gauges.
    Returns (predicted_val, provenance, target_timestamp, calc_method, available_steps).
    Raises HTTPException or requests exceptions on failure.
    Synchronized with _forecast_lock to ensure atomic gauge updates.
    """
    with _forecast_lock:
        try:
            fcst_df, provenance, _ = get_or_generate_forecast(force_refresh=force_refresh)
            set_last_forecast_error(None)
        except HTTPException as e:
            set_last_forecast_error(e.detail)
            raise
        except Exception as e:
            set_last_forecast_error(str(e))
            raise

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
        now_epoch = time.time()

        # Update Prometheus gauges with the latest forecast, provenance, and freshness timestamp
        PREDICTED_LOAD_GAUGE.set(predicted_val)
        provenance_val = 1.0 if provenance == "live_prometheus" else 0.0
        PREDICTION_PROVENANCE_GAUGE.set(provenance_val)
        PREDICTION_TIMESTAMP_GAUGE.set(now_epoch)

        return predicted_val, provenance, target_timestamp, calc_method, available_steps


async def background_prediction_refresh_loop():
    """
    Periodically invokes the forecasting pipeline in a background worker to update
    predicted_http_work_rps and prediction_provenance Prometheus gauges.
    Survives transient errors without crashing.
    """
    interval = float(os.getenv("REFRESH_INTERVAL_SECONDS", str(REFRESH_INTERVAL_SECONDS)))
    horizon = int(os.getenv("AUTO_REFRESH_HORIZON_STEPS", str(AUTO_REFRESH_HORIZON_STEPS)))
    agg = os.getenv("AUTO_REFRESH_AGGREGATION", AUTO_REFRESH_AGGREGATION)
    logger.info(
        f"Starting background prediction refresh loop: interval={interval}s, "
        f"horizon={horizon}, aggregation={agg}"
    )

    while True:
        try:
            # Execute synchronous Prometheus queries and Prophet fitting in a thread pool
            predicted_val, provenance, _, _, _ = await asyncio.to_thread(
                refresh_prediction,
                horizon_steps=horizon,
                aggregation=agg,
                force_refresh=True,
            )
            logger.info(
                f"Background refresh successful: predicted_load={predicted_val} RPS, "
                f"provenance={provenance}"
            )
        except asyncio.CancelledError:
            logger.info("Background prediction refresh loop cancelled.")
            break
        except HTTPException as e:
            logger.warning(f"Background prediction refresh encountered HTTP error: {e.detail}")
        except Exception as e:
            logger.warning(f"Background prediction refresh error: {e}")

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Background prediction refresh loop cancelled during sleep.")
            break


def start_prediction_refresh_loop() -> Optional[asyncio.Task]:
    """Starts the background prediction refresh loop task if not already running."""
    global _refresh_task
    auto_enabled = os.getenv("ENABLE_AUTO_REFRESH", str(ENABLE_AUTO_REFRESH)).lower() in ("true", "1", "yes")
    if not auto_enabled:
        logger.info("Background prediction refresh loop disabled (ENABLE_AUTO_REFRESH=false).")
        return None

    if _refresh_task is not None and not _refresh_task.done():
        logger.debug("Background prediction refresh loop is already active.")
        return _refresh_task

    try:
        loop = asyncio.get_running_loop()
        _refresh_task = loop.create_task(background_prediction_refresh_loop())
        logger.info("Background prediction refresh task spawned.")
        return _refresh_task
    except RuntimeError as e:
        logger.warning(f"Could not start background prediction refresh loop (no running event loop): {e}")
        return None


async def stop_prediction_refresh_loop():
    """Cancels and cleanly awaits the background prediction refresh task."""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        logger.info("Stopping background prediction refresh task...")
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
        logger.info("Background prediction refresh task stopped.")
    _refresh_task = None


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
    predicted_val, provenance, target_timestamp, calc_method, available_steps = refresh_prediction(
        horizon_steps=horizon_steps,
        aggregation=aggregation,
        force_refresh=force_refresh,
    )

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


# ---------------------------------------------------------------------------
# Extended Models for Dashboard & Cluster Monitoring
# ---------------------------------------------------------------------------

class PodInfo(BaseModel):
    name: str
    phase: str
    ready: bool
    restarts: int = 0
    cpu_cores: Optional[float] = None
    memory_mb: Optional[float] = None


class KubernetesStatusResponse(BaseModel):
    deployment_name: str = "target-app"
    namespace: str = "default"
    current_replicas: int = 1
    desired_replicas: int = 1
    available_replicas: int = 1
    max_replicas: int = 5
    min_replicas: int = 1
    running_pods: int = 1
    pending_pods: int = 0
    failed_pods: int = 0
    cpu_usage_cores: float = 0.0
    memory_working_set_mb: float = 0.0
    collection_status: str = "ok"
    provenance: str = "none"
    cluster_connected: bool = True
    pods: List[PodInfo] = Field(default_factory=list)


class HpaCondition(BaseModel):
    type: str
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    last_transition_time: Optional[str] = None


class HpaScalingEvent(BaseModel):
    timestamp: Optional[str] = None
    type: str = "Normal"
    reason: str
    message: str
    object: Optional[str] = None


class CpuHpaConfig(BaseModel):
    name: str = "target-app-hpa"
    namespace: str = "default"
    target_deployment: str = "target-app"
    min_replicas: int = 1
    max_replicas: int = 5
    target_cpu_utilization_percent: int = 50
    scale_up_stabilization_seconds: int = 0
    scale_down_stabilization_seconds: int = 15
    scale_up_policies: List[Dict[str, Any]] = Field(default_factory=list)
    scale_down_policies: List[Dict[str, Any]] = Field(default_factory=list)


class CpuHpaStatusResponse(BaseModel):
    is_applied: bool
    is_active_controller: bool
    active_controller: str = "none"
    active_controller_name: str
    hpa_name: str = "target-app-hpa"
    target_deployment: str = "target-app"
    current_cpu_utilization_percent: Optional[float] = None
    target_cpu_utilization_percent: int = 50
    current_replicas: int = 1
    desired_replicas: int = 1
    min_replicas: int = 1
    max_replicas: int = 5
    conditions: List[HpaCondition] = Field(default_factory=list)
    scaling_events: List[HpaScalingEvent] = Field(default_factory=list)
    config: CpuHpaConfig


class AutoscalingStatusResponse(BaseModel):
    scaledobject_name: str = "target-app-predictive-scaledobject"
    hpa_name: str = "keda-hpa-target-app-predictive-scaledobject"
    target_deployment: str = "target-app"
    min_replicas: int = 1
    max_replicas: int = 5
    polling_interval_seconds: int = 5
    cooldown_period_seconds: int = 30
    metric_name: str = "predicted_http_work_rps"
    metric_threshold: float = 10.0
    query: str = "max(predicted_http_work_rps) and (max(prediction_timestamp_seconds) > 0) and ((time() - max(prediction_timestamp_seconds)) < 45)"
    current_predicted_load: float = 0.0
    current_provenance: str = "live_prometheus"
    last_prediction_timestamp: float = 0.0
    prediction_age_seconds: float = 0.0
    is_fresh: bool = False
    freshness_threshold_seconds: float = 45.0
    fallback_configured: bool = True
    fallback_threshold: int = 3
    fallback_replicas: int = 2
    fallback_active: bool = False
    scaledobject_state: str = "Active"
    ready_status: bool = True
    active_status: bool = True
    forecast_status: str = "waiting_for_data"
    forecast_error: Optional[str] = None
    last_scale_event: Optional[str] = None
    active_controller: str = "keda"
    active_controller_name: str = "KEDA Predictive Autoscaler (target-app-predictive-scaledobject)"
    conditions: List[HpaCondition] = Field(default_factory=list)
    scaling_events: List[HpaScalingEvent] = Field(default_factory=list)


class WorkloadStartRequest(BaseModel):
    profile: str = Field(..., description="Workload profile: stable, gradual, bursty, periodic, noisy")
    duration: int = Field(120, ge=10, le=600, description="Duration in seconds")
    user_count: int = Field(6, ge=1, le=50, description="Target user concurrency")
    spawn_rate: int = Field(2, ge=1, le=10, description="Users spawned per second")


class WorkloadStatusResponse(BaseModel):
    is_running: bool
    active_profile: Optional[str] = None
    pid: Optional[int] = None
    started_at_epoch: Optional[float] = None
    elapsed_seconds: float = 0.0
    duration_seconds: Optional[int] = None
    user_count: Optional[int] = None
    target_url: str = "http://127.0.0.1:8000"
    target_host: Optional[str] = None


class ProfileMetrics(BaseModel):
    profile: str
    total_samples: int
    duration_seconds: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    avg_cpu_cores: float
    max_cpu_cores: float
    avg_cpu_percent: Optional[float] = None
    avg_memory_mb: float
    avg_replicas: float
    max_replicas: float
    min_replicas: float
    scaling_delay_seconds: Optional[float] = None
    slo_violations_count: int = 0
    slo_violation_duration_seconds: float = 0.0
    total_core_seconds: float = 0.0
    error_count: int = 0
    error_rate_pct: float = 0.0
    mae: Optional[float] = None
    rmse: Optional[float] = None
    has_data: bool = True


class ProfileComparison(BaseModel):
    profile: str
    description: str
    hpa: Optional[ProfileMetrics] = None
    predictive: Optional[ProfileMetrics] = None
    comparison_summary: Optional[Dict[str, Any]] = None


class ExperimentComparisonResponse(BaseModel):
    available_profiles: List[str]
    comparisons: Dict[str, ProfileComparison]


# ---------------------------------------------------------------------------
# Global Workload Generator State
# ---------------------------------------------------------------------------

_workload_process: Optional[subprocess.Popen] = None
_workload_meta: Dict[str, Any] = {
    "profile": None,
    "started_at": 0.0,
    "duration": 120,
    "user_count": 6,
}


# ---------------------------------------------------------------------------
# Dynamic Kubernetes & KEDA Status Helpers
# ---------------------------------------------------------------------------

def get_scaledobject_manifest_config() -> Dict[str, Any]:
    manifest_path = BASE_DIR / "kubernetes" / "target-app-predictive-scaledobject.yaml"
    cfg = {
        "name": "target-app-predictive-scaledobject",
        "hpa_name": "keda-hpa-target-app-predictive-scaledobject",
        "query": "max(predicted_http_work_rps) and (max(prediction_timestamp_seconds) > 0) and ((time() - max(prediction_timestamp_seconds)) < 45)",
        "threshold": 10.0,
        "min_replicas": 1,
        "max_replicas": 5,
        "polling_interval": 5,
        "cooldown_period": 30,
        "fallback_threshold": 3,
        "fallback_replicas": 2,
    }
    if manifest_path.is_file():
        try:
            import yaml
            doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if doc and isinstance(doc, dict):
                spec = doc.get("spec", {})
                cfg["name"] = doc.get("metadata", {}).get("name", cfg["name"])
                cfg["min_replicas"] = spec.get("minReplicaCount", cfg["min_replicas"])
                cfg["max_replicas"] = spec.get("maxReplicaCount", cfg["max_replicas"])
                cfg["polling_interval"] = spec.get("pollingInterval", cfg["polling_interval"])
                cfg["cooldown_period"] = spec.get("cooldownPeriod", cfg["cooldown_period"])
                fb = spec.get("fallback", {})
                cfg["fallback_threshold"] = fb.get("failureThreshold", cfg["fallback_threshold"])
                cfg["fallback_replicas"] = fb.get("replicas", cfg["fallback_replicas"])
                triggers = spec.get("triggers", [])
                if triggers and isinstance(triggers[0], dict):
                    meta = triggers[0].get("metadata", {})
                    if "query" in meta:
                        cfg["query"] = meta["query"]
                    if "threshold" in meta:
                        cfg["threshold"] = float(meta["threshold"])
        except Exception as e:
            logger.warning(f"Error reading ScaledObject manifest: {e}")
    return cfg


def get_keda_scaledobject_live_status():
    try:
        res = subprocess.run(
            ["kubectl", "get", "scaledobject", "target-app-predictive-scaledobject", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if res.returncode == 0:
            import json
            data = json.loads(res.stdout)
            status = data.get("status", {})
            conds = {c.get("type"): (c.get("status") == "True") for c in status.get("conditions", [])}
            return {
                "ready": conds.get("Ready", True),
                "active": conds.get("Active", True),
                "fallback": conds.get("Fallback", False),
                "hpa_name": status.get("hpaName", "keda-hpa-target-app-predictive-scaledobject"),
                "failures": status.get("health", {}).get("s0-prometheus", {}).get("numberOfFailures", 0),
            }
    except Exception:
        pass
    return None


def get_cpu_hpa_manifest_config() -> CpuHpaConfig:
    """Parses traditional CPU HPA configuration from kubernetes/target-app-hpa.yaml."""
    manifest_path = BASE_DIR / "kubernetes" / "target-app-hpa.yaml"
    cfg = CpuHpaConfig()
    if manifest_path.is_file():
        try:
            import yaml
            doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if doc and isinstance(doc, dict):
                spec = doc.get("spec", {})
                cfg.name = doc.get("metadata", {}).get("name", cfg.name)
                cfg.target_deployment = spec.get("scaleTargetRef", {}).get("name", cfg.target_deployment)
                cfg.min_replicas = int(spec.get("minReplicas", cfg.min_replicas))
                cfg.max_replicas = int(spec.get("maxReplicas", cfg.max_replicas))
                metrics = spec.get("metrics", [])
                if metrics and isinstance(metrics[0], dict):
                    res = metrics[0].get("resource", {})
                    cfg.target_cpu_utilization_percent = int(res.get("target", {}).get("averageUtilization", cfg.target_cpu_utilization_percent))
                behavior = spec.get("behavior", {})
                scale_up = behavior.get("scaleUp", {})
                cfg.scale_up_stabilization_seconds = int(scale_up.get("stabilizationWindowSeconds", cfg.scale_up_stabilization_seconds))
                cfg.scale_up_policies = scale_up.get("policies", [])
                scale_down = behavior.get("scaleDown", {})
                cfg.scale_down_stabilization_seconds = int(scale_down.get("stabilizationWindowSeconds", cfg.scale_down_stabilization_seconds))
                cfg.scale_down_policies = scale_down.get("policies", [])
        except Exception as e:
            logger.warning(f"Error reading CPU HPA manifest: {e}")
    return cfg


def get_kubernetes_scaling_events(object_filter: str = "") -> List[HpaScalingEvent]:
    """Retrieves actual Kubernetes scaling and metric events from the cluster."""
    events = []
    try:
        res = subprocess.run(
            ["kubectl", "get", "events", "-n", "default", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if res.returncode == 0:
            import json
            data = json.loads(res.stdout)
            for item in data.get("items", []):
                obj = item.get("involvedObject", {})
                name = obj.get("name", "")
                kind = obj.get("kind", "")
                if object_filter:
                    if object_filter.lower() not in name.lower() and object_filter.lower() not in kind.lower():
                        continue
                ts = item.get("lastTimestamp") or item.get("eventTime") or item.get("firstTimestamp")
                events.append(
                    HpaScalingEvent(
                        timestamp=ts,
                        type=item.get("type", "Normal"),
                        reason=item.get("reason", "Unknown"),
                        message=item.get("message", ""),
                        object=f"{kind}/{name}",
                    )
                )
    except Exception:
        pass
    return events[:15]


def get_active_autoscaler_controller() -> Tuple[str, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Determines which autoscaler controller currently governs Deployment/target-app.
    Returns: (controller_key, controller_name, keda_hpa_json, cpu_hpa_json)
    """
    keda_hpa = None
    cpu_hpa = None
    try:
        res = subprocess.run(
            ["kubectl", "get", "hpa", "-n", "default", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if res.returncode == 0:
            import json
            data = json.loads(res.stdout)
            for item in data.get("items", []):
                hpa_name = item.get("metadata", {}).get("name", "")
                target_ref = item.get("spec", {}).get("scaleTargetRef", {}).get("name", "")
                if target_ref == "target-app":
                    if "keda" in hpa_name.lower():
                        keda_hpa = item
                    elif "target-app-hpa" in hpa_name.lower():
                        cpu_hpa = item
    except Exception:
        pass

    if keda_hpa and cpu_hpa:
        return "conflict", "Dual Controller Conflict (both KEDA and CPU HPA targeting target-app)", keda_hpa, cpu_hpa
    elif keda_hpa:
        return "keda", "KEDA Predictive Autoscaler (keda-hpa-target-app-predictive-scaledobject)", keda_hpa, cpu_hpa
    elif cpu_hpa:
        return "cpu-hpa", "Traditional CPU HPA (target-app-hpa)", keda_hpa, cpu_hpa
    return "keda", "KEDA Predictive Autoscaler (Configured Controller)", keda_hpa, cpu_hpa


# ---------------------------------------------------------------------------
# Dashboard API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/kubernetes/status", response_model=KubernetesStatusResponse)
def get_kubernetes_status():
    """
    Returns deployment and pod health metrics for target-app.
    Queries live Prometheus if configured, or falls back to latest observed telemetry.
    """
    prom_url = get_prometheus_url()
    deployment_name = os.getenv("TARGET_DEPLOYMENT_NAME", "target-app")
    
    current_reps = 1
    desired_reps = 1
    avail_reps = 1
    max_reps = 5
    min_reps = 1
    cpu_cores = 0.0
    mem_mb = 0.0
    collection_status = "ok"
    provenance = "none"

    if prom_url:
        try:
            queries = {
                "deployment_replicas": f'kube_deployment_status_replicas{{deployment="{deployment_name}"}}',
                "available_replicas": f'kube_deployment_status_replicas_available{{deployment="{deployment_name}"}}',
                "hpa_current": f'max(kube_horizontalpodautoscaler_status_current_replicas{{horizontalpodautoscaler=~".*{deployment_name}.*"}})',
                "hpa_desired": f'max(kube_horizontalpodautoscaler_status_desired_replicas{{horizontalpodautoscaler=~".*{deployment_name}.*"}})',
                "hpa_max": f'max(kube_horizontalpodautoscaler_spec_max_replicas{{horizontalpodautoscaler=~".*{deployment_name}.*"}})',
                "hpa_min": f'max(kube_horizontalpodautoscaler_spec_min_replicas{{horizontalpodautoscaler=~".*{deployment_name}.*"}})',
                "cpu": f'sum(rate(container_cpu_usage_seconds_total{{pod=~"{deployment_name}.*"}}[1m]))',
                "memory": f'sum(container_memory_working_set_bytes{{pod=~"{deployment_name}.*"}})',
            }
            results = {}
            for k, q in queries.items():
                try:
                    resp = requests.get(f"{prom_url}/api/v1/query", params={"query": q}, timeout=3.0)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {}).get("result", [])
                        if data and "value" in data[0]:
                            results[k] = float(data[0]["value"][1])
                except Exception:
                    pass

            if "hpa_max" in results:
                max_reps = int(results["hpa_max"])
            if "hpa_min" in results:
                min_reps = int(results["hpa_min"])

            if "deployment_replicas" in results:
                current_reps = int(results["deployment_replicas"])
                provenance = "live_prometheus"
            elif "hpa_current" in results:
                current_reps = int(results["hpa_current"])
                provenance = "live_prometheus"

            if "hpa_desired" in results:
                desired_reps = int(results["hpa_desired"])
            else:
                desired_reps = current_reps

            if "available_replicas" in results:
                avail_reps = int(results["available_replicas"])
            else:
                avail_reps = current_reps

            if "cpu" in results:
                cpu_cores = round(results["cpu"], 4)
            if "memory" in results:
                mem_mb = round(results["memory"] / (1024 * 1024), 2)

        except Exception as e:
            logger.warning(f"Failed to query Prometheus for kubernetes status: {e}")

    # Fallback to static metrics CSV if Prometheus produced no metrics
    if provenance != "live_prometheus":
        try:
            df = load_static_metrics_fallback()
            latest = df.iloc[-1]
            current_reps = int(latest.get("pod_count", 1))
            desired_reps = int(latest.get("hpa_desired_replicas", current_reps))
            avail_reps = int(latest.get("deployment_available_replicas", current_reps))
            cpu_cores = round(float(latest.get("cpu_usage_cores", 0.0)), 4)
            mem_mb = round(float(latest.get("memory_working_set_mb", 0.0)), 2)
            collection_status = str(latest.get("collection_status", "fallback"))
            provenance = "static_csv_fallback"
        except Exception:
            pass

    # Build pod objects
    pods = []
    # Attempt to query live pods via kubectl if available
    try:
        k_res = subprocess.run(
            ["kubectl", "get", "pods", "-l", f"app={deployment_name}", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if k_res.returncode == 0:
            import json
            k_data = json.loads(k_res.stdout)
            items = k_data.get("items", [])
            pod_count = max(1, len(items))
            for item in items:
                p_name = item["metadata"]["name"]
                p_phase = item["status"].get("phase", "Running")
                p_cs = item["status"].get("containerStatuses", [{}])
                p_ready = p_cs[0].get("ready", True) if p_cs else True
                p_restarts = p_cs[0].get("restartCount", 0) if p_cs else 0
                pods.append(
                    PodInfo(
                        name=p_name,
                        phase=p_phase,
                        ready=p_ready,
                        restarts=p_restarts,
                        cpu_cores=round(cpu_cores / pod_count, 4) if cpu_cores else 0.0,
                        memory_mb=round(mem_mb / pod_count, 2) if mem_mb else 0.0,
                    )
                )
    except Exception:
        pass

    if not pods:
        for i in range(max(1, current_reps)):
            pods.append(
                PodInfo(
                    name=f"{deployment_name}-{i+1}",
                    phase="Running",
                    ready=True,
                    restarts=0,
                    cpu_cores=round(cpu_cores / max(1, current_reps), 4) if cpu_cores else 0.0,
                    memory_mb=round(mem_mb / max(1, current_reps), 2) if mem_mb else 0.0,
                )
            )

    cluster_conn = (provenance == "live_prometheus" or bool(pods and pods[0].name != f"{deployment_name}-1"))

    return KubernetesStatusResponse(
        deployment_name=deployment_name,
        namespace="default",
        current_replicas=current_reps,
        desired_replicas=desired_reps,
        available_replicas=avail_reps,
        max_replicas=max_reps,
        min_replicas=min_reps,
        running_pods=current_reps,
        pending_pods=max(0, desired_reps - current_reps),
        failed_pods=0,
        cpu_usage_cores=cpu_cores,
        memory_working_set_mb=mem_mb,
        collection_status=collection_status,
        provenance=provenance,
        cluster_connected=cluster_conn,
        pods=pods,
    )


@app.get("/api/autoscaling/status", response_model=AutoscalingStatusResponse)
def get_autoscaling_status():
    """
    Returns real-time status of KEDA predictive autoscaling and HPA.
    Exposes metric thresholds, freshness age, fallback state, and scaling decision events.
    """
    manifest_cfg = get_scaledobject_manifest_config()
    live_keda = get_keda_scaledobject_live_status()

    last_ts = PREDICTION_TIMESTAMP_GAUGE._value.get()
    current_load = PREDICTED_LOAD_GAUGE._value.get()
    prov_val = PREDICTION_PROVENANCE_GAUGE._value.get()
    current_prov = "live_prometheus" if prov_val == 1.0 else "static_csv_fallback"

    current_error = get_last_forecast_error()
    is_insufficient = current_error is not None and (
        "insufficient" in current_error.lower() or "at least 5" in current_error.lower()
    )

    # If backend gauges are at 0 and NO forecast error has occurred, try reading from Prometheus
    prom_url = get_prometheus_url()
    if last_ts == 0.0 and not current_error and prom_url:
        try:
            r = requests.get(f"{prom_url}/api/v1/query", params={"query": "max(prediction_timestamp_seconds)"}, timeout=2.0)
            if r.status_code == 200:
                res = r.json().get("data", {}).get("result", [])
                if res and "value" in res[0]:
                    last_ts = float(res[0]["value"][1])
            r2 = requests.get(f"{prom_url}/api/v1/query", params={"query": "max(predicted_http_work_rps)"}, timeout=2.0)
            if r2.status_code == 200:
                res2 = r2.json().get("data", {}).get("result", [])
                if res2 and "value" in res2[0]:
                    current_load = float(res2[0]["value"][1])
                    current_prov = "live_prometheus"
        except Exception:
            pass

    now = time.time()
    age = max(0.0, round(now - last_ts, 1)) if last_ts > 0 else 9999.0

    # Determine forecast freshness / availability status consistently
    if current_error or is_insufficient:
        err_lower = (current_error or "").lower()
        if "insufficient" in err_lower or "at least 5" in err_lower or last_ts == 0.0:
            fcst_status = "waiting_for_data"
        else:
            fcst_status = "error"
        is_fresh = False
        current_load = 0.0
    elif last_ts == 0.0:
        fcst_status = "waiting_for_data"
        is_fresh = False
        current_load = 0.0
    elif age >= 45.0:
        fcst_status = "stale"
        is_fresh = False
    else:
        fcst_status = "fresh"
        is_fresh = True

    # Determine KEDA status conditions
    if live_keda:
        ready_status = live_keda["ready"]
        active_status = live_keda["active"]
        fallback_active = live_keda["fallback"]
        hpa_name = live_keda["hpa_name"]
        if fallback_active:
            state = "Fallback"
        elif active_status and is_fresh:
            state = "Active"
        else:
            state = "Inactive"
    else:
        ready_status = True
        if not is_fresh or fcst_status == "waiting_for_data":
            state = "Fallback" if (last_ts > 0 and age >= 45.0) else "Inactive"
            active_status = False
            fallback_active = (last_ts > 0 and age >= 45.0)
        else:
            state = "Active"
            active_status = True
            fallback_active = False
        hpa_name = manifest_cfg.get("hpa_name", "keda-hpa-target-app-predictive-scaledobject")

    threshold = manifest_cfg.get("threshold", 10.0)
    calculated_desired = max(1, min(manifest_cfg.get("max_replicas", 5), math.ceil(current_load / threshold)))

    ctrl_key, ctrl_name, keda_hpa, cpu_hpa = get_active_autoscaler_controller()
    keda_conditions = []
    if keda_hpa:
        for c in keda_hpa.get("status", {}).get("conditions", []):
            keda_conditions.append(
                HpaCondition(
                    type=c.get("type", ""),
                    status=c.get("status", "Unknown"),
                    reason=c.get("reason"),
                    message=c.get("message"),
                    last_transition_time=c.get("lastTransitionTime"),
                )
            )

    keda_events = get_kubernetes_scaling_events(object_filter="scaledobject")
    if not keda_events:
        keda_events = get_kubernetes_scaling_events(object_filter="keda")

    return AutoscalingStatusResponse(
        scaledobject_name=manifest_cfg.get("name", "target-app-predictive-scaledobject"),
        hpa_name=hpa_name,
        target_deployment=os.getenv("TARGET_DEPLOYMENT_NAME", "target-app"),
        min_replicas=manifest_cfg.get("min_replicas", 1),
        max_replicas=manifest_cfg.get("max_replicas", 5),
        polling_interval_seconds=manifest_cfg.get("polling_interval", 5),
        cooldown_period_seconds=manifest_cfg.get("cooldown_period", 30),
        metric_name="predicted_http_work_rps",
        metric_threshold=threshold,
        query=manifest_cfg.get("query", "max(predicted_http_work_rps) and (max(prediction_timestamp_seconds) > 0) and ((time() - max(prediction_timestamp_seconds)) < 45)"),
        current_predicted_load=round(current_load, 2),
        current_provenance=current_prov,
        last_prediction_timestamp=round(last_ts, 2),
        prediction_age_seconds=age,
        is_fresh=is_fresh,
        freshness_threshold_seconds=45.0,
        fallback_configured=True,
        fallback_threshold=manifest_cfg.get("fallback_threshold", 3),
        fallback_replicas=manifest_cfg.get("fallback_replicas", 2),
        fallback_active=fallback_active,
        scaledobject_state=state,
        ready_status=ready_status,
        active_status=active_status,
        forecast_status=fcst_status,
        forecast_error=current_error,
        last_scale_event=f"Evaluated {current_load:.1f} RPS -> desired: {calculated_desired} replica(s)",
        active_controller=ctrl_key,
        active_controller_name=ctrl_name,
        conditions=keda_conditions,
        scaling_events=keda_events,
    )


@app.get("/api/hpa/status", response_model=CpuHpaStatusResponse)
def get_cpu_hpa_status():
    """
    Returns real-time status of the traditional Kubernetes CPU-Based HPA.
    Distinguishes whether it is currently active or in standby relative to KEDA.
    """
    config = get_cpu_hpa_manifest_config()
    ctrl_key, ctrl_name, keda_hpa, cpu_hpa = get_active_autoscaler_controller()

    is_applied = cpu_hpa is not None
    is_active_ctrl = (ctrl_key == "cpu-hpa")

    curr_reps = 1
    desired_reps = 1
    cpu_util_pct = None
    conditions = []

    if cpu_hpa:
        status = cpu_hpa.get("status", {})
        curr_reps = status.get("currentReplicas", 1)
        desired_reps = status.get("desiredReplicas", curr_reps)
        raw_conds = status.get("conditions", [])
        for c in raw_conds:
            conditions.append(
                HpaCondition(
                    type=c.get("type", ""),
                    status=c.get("status", "Unknown"),
                    reason=c.get("reason"),
                    message=c.get("message"),
                    last_transition_time=c.get("lastTransitionTime"),
                )
            )
        for m in status.get("currentMetrics", []):
            if m.get("type") == "Resource" and m.get("resource", {}).get("name") == "cpu":
                cur_res = m.get("resource", {}).get("current", {})
                if "averageUtilization" in cur_res:
                    cpu_util_pct = float(cur_res["averageUtilization"])
    else:
        # If target-app-hpa is not applied in cluster, query live Prometheus for target-app CPU usage
        prom_url = get_prometheus_url()
        if prom_url:
            try:
                r_cpu = requests.get(
                    f"{prom_url}/api/v1/query",
                    params={"query": 'sum(node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate{namespace="default", pod=~"target-app-.*"})'},
                    timeout=2.0
                )
                if r_cpu.status_code == 200:
                    res_cpu = r_cpu.json().get("data", {}).get("result", [])
                    if res_cpu and "value" in res_cpu[0]:
                        cpu_cores = float(res_cpu[0]["value"][1])
                        # Query pod count
                        r_pods = requests.get(
                            f"{prom_url}/api/v1/query",
                            params={"query": 'count(kube_pod_status_phase{namespace="default", pod=~"target-app-.*", phase="Running"})'},
                            timeout=2.0
                        )
                        if r_pods.status_code == 200:
                            p_res = r_pods.json().get("data", {}).get("result", [])
                            if p_res and "value" in p_res[0]:
                                curr_reps = max(1, int(float(p_res[0]["value"][1])))
                        # Calculate CPU% relative to 100m (0.1 core) request per pod
                        requested_cores = curr_reps * 0.1
                        cpu_util_pct = round((cpu_cores / max(0.01, requested_cores)) * 100.0, 1)
            except Exception:
                pass

        conditions = [
            HpaCondition(
                type="AbleToScale",
                status="True" if is_applied else "False",
                reason="ControllerStandby" if not is_applied else "ReadyForNewScale",
                message="Target-app is currently controlled by KEDA Predictive Autoscaler. CPU HPA is configured in manifest as baseline comparison." if not is_applied else "HPA is active and ready to scale",
            ),
            HpaCondition(
                type="ScalingActive",
                status="True" if is_applied else "False",
                reason="ValidMetricFound" if is_applied else "ControllerStandby",
                message="CPU utilization metric monitoring active" if is_applied else "Standby: not scaling target deployment while KEDA is active",
            ),
            HpaCondition(
                type="ScalingLimited",
                status="False",
                reason="DesiredWithinRange",
                message="The desired replica count is within configured min/max boundaries",
            ),
        ]

    scaling_events = get_kubernetes_scaling_events(object_filter="target-app")

    return CpuHpaStatusResponse(
        is_applied=is_applied,
        is_active_controller=is_active_ctrl,
        active_controller=ctrl_key,
        active_controller_name=ctrl_name,
        hpa_name=config.name,
        target_deployment=config.target_deployment,
        current_cpu_utilization_percent=cpu_util_pct,
        target_cpu_utilization_percent=config.target_cpu_utilization_percent,
        current_replicas=curr_reps,
        desired_replicas=desired_reps,
        min_replicas=config.min_replicas,
        max_replicas=config.max_replicas,
        conditions=conditions,
        scaling_events=scaling_events,
        config=config,
    )


PROFILES_METADATA = {
    "stable": "Steady user concurrency throughout the run (baseline capacity test)",
    "periodic": "Alternates cyclically between low-load and high-load phases (diurnal cycle simulation)",
    "gradual": "Steps up concurrency linearly in discrete temporal stages (multi-stage scale-up test)",
    "bursty": "Short, high-intensity traffic surges separated by low-load valleys (reaction speed test)",
    "noisy": "Pseudo-random bounded user count variations (realistic chaotic production traffic)",
}


def _analyze_csv_dataset(csv_path: Path, profile_name: str) -> Optional[ProfileMetrics]:
    if not csv_path.is_file():
        return None
    try:
        import sys
        exp_dir = BASE_DIR / "experiments"
        if str(exp_dir) not in sys.path:
            sys.path.insert(0, str(exp_dir))
        from analyze_scaling import analyze_scaling_delay
        from evaluate_slo import evaluate_slo

        df = pd.read_csv(csv_path)
        if df.empty or len(df) < 2:
            return None

        epochs = pd.to_numeric(df["epoch_timestamp"], errors="coerce").dropna()
        duration = float(epochs.max() - epochs.min()) if len(epochs) > 1 else 0.0

        p50 = float(pd.to_numeric(df.get("latency_p50_seconds", 0), errors="coerce").fillna(0).mean() * 1000)
        p95 = float(pd.to_numeric(df.get("latency_p95_seconds", 0), errors="coerce").fillna(0).max() * 1000)
        p99 = float(pd.to_numeric(df.get("latency_p99_seconds", 0), errors="coerce").fillna(0).max() * 1000)

        cpus = pd.to_numeric(df.get("cpu_usage_cores", 0), errors="coerce").fillna(0)
        mems = pd.to_numeric(df.get("memory_working_set_mb", 0), errors="coerce").fillna(0)
        pods = pd.to_numeric(df.get("pod_count", 1), errors="coerce").fillna(1)

        avg_cpu = float(cpus.mean())
        max_cpu = float(cpus.max())
        avg_mem = float(mems.mean())
        avg_pods = float(pods.mean())
        max_pods = float(pods.max())
        min_pods = float(pods.min())

        core_seconds = float(cpus.sum() * (duration / max(1, len(df))))

        delay_info = analyze_scaling_delay(str(csv_path))
        scaling_delay = delay_info.get("pod_scaleup_delay_seconds")

        slo_info = evaluate_slo(str(csv_path))
        slo_count = int(slo_info.get("latency_violations_count", 0))
        slo_dur = float(slo_info.get("latency_violation_duration_seconds", 0.0))

        # Error count and rate
        err_rps = pd.to_numeric(df.get("http_error_rps", 0), errors="coerce").fillna(0)
        work_rps = pd.to_numeric(df.get("http_work_rps", 0), errors="coerce").fillna(0)
        total_errs = int(err_rps.sum())
        err_rate = float((err_rps.sum() / max(1e-3, err_rps.sum() + work_rps.sum())) * 100.0)

        # Average CPU utilization percentage based on 100m (0.1 core) request per pod
        avg_cpu_percent = round((avg_cpu / max(0.01, avg_pods * 0.1)) * 100.0, 1)

        # Calculate authentic MAE and RMSE from held-out test evaluation if forecast file exists
        mae_val = None
        rmse_val = None
        fcst_path = BASE_DIR / "backend" / "data" / f"forecast_{profile_name}.csv"
        if not fcst_path.is_file():
            fcst_path = BASE_DIR / "forecasting" / f"forecast_{profile_name}.csv"
        if fcst_path.is_file() and "http_work_rps" in df.columns:
            try:
                from forecasting.evaluate_forecast import evaluate_forecast_performance, align_actual_and_predicted
                test_df = df.copy()
                test_df["ds"] = pd.to_datetime(test_df["timestamp_utc"]).dt.tz_localize(None)
                test_df["y"] = test_df["http_work_rps"].fillna(0)
                fcst_df = pd.read_csv(fcst_path)
                fcst_df["ds"] = pd.to_datetime(fcst_df["ds"]).dt.tz_localize(None)
                aligned = align_actual_and_predicted(test_df, fcst_df)
                clean = aligned.dropna(subset=["y", "yhat"])
                if len(clean) > 0:
                    eval_res = evaluate_forecast_performance(clean["y"].values, clean["yhat"].values)
                    mae_val = round(eval_res["mae"], 2)
                    rmse_val = round(eval_res["rmse"], 2)
            except Exception as e:
                logger.warning(f"Error computing MAE/RMSE for {profile_name}: {e}")

        return ProfileMetrics(
            profile=profile_name,
            total_samples=len(df),
            duration_seconds=round(duration, 1),
            p50_latency_ms=round(p50, 1),
            p95_latency_ms=round(p95, 1),
            p99_latency_ms=round(p99, 1),
            avg_cpu_cores=round(avg_cpu, 4),
            max_cpu_cores=round(max_cpu, 4),
            avg_cpu_percent=avg_cpu_percent,
            avg_memory_mb=round(avg_mem, 1),
            avg_replicas=round(avg_pods, 2),
            max_replicas=round(max_pods, 1),
            min_replicas=round(min_pods, 1),
            scaling_delay_seconds=round(scaling_delay, 1) if scaling_delay is not None else None,
            slo_violations_count=slo_count,
            slo_violation_duration_seconds=round(slo_dur, 1),
            total_core_seconds=round(core_seconds, 1),
            error_count=total_errs,
            error_rate_pct=round(err_rate, 2),
            mae=mae_val,
            rmse=rmse_val,
            has_data=True,
        )
    except Exception as e:
        logger.warning(f"Could not analyze CSV dataset {csv_path}: {e}")
        return None


@app.get("/api/experiments/comparison", response_model=ExperimentComparisonResponse)
def get_experiment_comparison(
    source: str = Query("results", description="Dataset source directory: 'results' (default: experiments/results) or 'historical' (experiments/)")
):
    """
    Returns authentic side-by-side performance metrics for HPA vs Predictive KEDA
    across all five experimental workload profiles.
    By default queries clean experiments/results/ directory to start with a clean research comparison.
    """
    if source.lower() == "historical":
        exp_dir = BASE_DIR / "experiments"
    else:
        exp_dir = BASE_DIR / "experiments" / "results"
        exp_dir.mkdir(parents=True, exist_ok=True)

    comparisons = {}

    for profile, desc in PROFILES_METADATA.items():
        hpa_csv = exp_dir / f"hpa_{profile}.csv"
        pred_csv = exp_dir / f"predictive_{profile}.csv"
        if not pred_csv.exists():
            pred_csv = exp_dir / f"keda_{profile}.csv"

        hpa_metrics = _analyze_csv_dataset(hpa_csv, profile)
        pred_metrics = _analyze_csv_dataset(pred_csv, profile)

        summary = None
        if hpa_metrics and pred_metrics:
            latency_improv = ((hpa_metrics.p95_latency_ms - pred_metrics.p95_latency_ms) / max(1e-3, hpa_metrics.p95_latency_ms)) * 100.0
            summary = {
                "p95_latency_improvement_pct": round(latency_improv, 1),
                "slo_violations_reduced": hpa_metrics.slo_violations_count - pred_metrics.slo_violations_count,
            }

        comparisons[profile] = ProfileComparison(
            profile=profile,
            description=desc,
            hpa=hpa_metrics,
            predictive=pred_metrics,
            comparison_summary=summary,
        )

    return ExperimentComparisonResponse(
        available_profiles=list(PROFILES_METADATA.keys()),
        comparisons=comparisons,
    )


@app.get("/api/workload/status", response_model=WorkloadStatusResponse)
def get_workload_status():
    """Returns live execution state of the background Locust workload runner."""
    global _workload_process, _workload_meta
    is_running = False
    pid = None
    elapsed = 0.0

    if _workload_process is not None:
        poll_res = _workload_process.poll()
        if poll_res is None:
            is_running = True
            pid = _workload_process.pid
            elapsed = round(time.time() - _workload_meta.get("started_at", time.time()), 1)
        else:
            _workload_process = None

    target_url = os.getenv("TARGET_APP_URL")
    if not target_url:
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            target_url = "http://target-app-service.default.svc.cluster.local:8000"
        else:
            target_url = "http://127.0.0.1:8000"

    return WorkloadStatusResponse(
        is_running=is_running,
        active_profile=_workload_meta.get("profile") if is_running else None,
        pid=pid,
        started_at_epoch=_workload_meta.get("started_at") if is_running else None,
        elapsed_seconds=elapsed,
        duration_seconds=_workload_meta.get("duration"),
        user_count=_workload_meta.get("user_count"),
        target_url=target_url,
        target_host=target_url,
    )


@app.post("/api/workload/start", response_model=WorkloadStatusResponse)
def start_workload(req: WorkloadStartRequest):
    """
    Securely starts a controlled Locust workload test for one of the 5 predefined profiles.
    Does not allow arbitrary command execution.
    """
    global _workload_process, _workload_meta

    allowed_profiles = ["stable", "gradual", "bursty", "periodic", "noisy"]
    if req.profile.lower() not in allowed_profiles:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid profile '{req.profile}'. Allowed: {', '.join(allowed_profiles)}",
        )

    if _workload_process is not None and _workload_process.poll() is None:
        raise HTTPException(
            status_code=409,
            detail="A workload test is already running. Stop it before starting a new one.",
        )

    locustfile = BASE_DIR / "experiments" / "locustfile.py"
    if not locustfile.is_file():
        raise HTTPException(status_code=500, detail="locustfile.py not found in experiments directory.")

    target_url = os.getenv("TARGET_APP_URL")
    if not target_url:
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            target_url = "http://target-app-service.default.svc.cluster.local:8000"
        else:
            target_url = "http://127.0.0.1:8000"

    env = os.environ.copy()
    env["WORKLOAD_PROFILE"] = req.profile.lower()
    env["EXPERIMENT_DURATION"] = str(req.duration)
    env["USER_COUNT"] = str(req.user_count)
    env["SPAWN_RATE"] = str(req.spawn_rate)
    env["TARGET_APP_URL"] = target_url
    env["TARGET_BASE_URL"] = target_url

    cmd = [
        sys.executable,
        "-m", "locust",
        "-f", str(locustfile),
        "--headless",
        "-u", str(req.user_count),
        "-r", str(req.spawn_rate),
        "--run-time", f"{req.duration}s",
        "--host", target_url,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        _workload_process = proc
        _workload_meta = {
            "profile": req.profile.lower(),
            "started_at": time.time(),
            "duration": req.duration,
            "user_count": req.user_count,
        }
        logger.info(f"Started workload test '{req.profile}' (PID {proc.pid}) for {req.duration}s")
    except Exception as e:
        logger.error(f"Failed to launch locust workload process: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start workload process: {e}") from e

    return get_workload_status()


@app.post("/api/workload/stop", response_model=WorkloadStatusResponse)
def stop_workload():
    """Cleanly terminates an active background Locust workload test."""
    global _workload_process, _workload_meta
    if _workload_process is not None and _workload_process.poll() is None:
        try:
            _workload_process.terminate()
            _workload_process.wait(timeout=3.0)
        except Exception:
            try:
                _workload_process.kill()
            except Exception:
                pass
        logger.info("Workload test stopped manually by user.")
    _workload_process = None
    return get_workload_status()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
