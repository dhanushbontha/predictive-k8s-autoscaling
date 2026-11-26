/**
 * Shared forecast data resolution and formatting utility.
 * Ensures consistent presentation across Overview, Autoscaling, and Forecasting pages.
 */
import { formatToIstChartTime } from './timezone.js';

/**
 * Resolves predicted RPS value, unit, badge label, subtitle, and visual variant.
 *
 * Rules:
 * - Valid zero prediction -> "0.0 req/s" (not "--")
 * - Valid positive prediction -> e.g. "15.2 req/s"
 * - Missing, unavailable, or stale prediction -> "-- req/s"
 * - CPU HPA mode -> labeled "Informational only — not used for scaling"
 * - KEDA mode -> marked as active predictive scaling metric
 */
export function resolvePredictedRps({
  autoscalingStatus,
  forecastData,
  metrics,
  backendHealthy = true,
}) {
  const isCpuHpaMode = autoscalingStatus?.active_controller === 'cpu-hpa';
  const activeError = autoscalingStatus?.forecast_error;
  const isInsufficient = Boolean(
    activeError &&
      (activeError.toLowerCase().includes('insufficient') ||
        activeError.toLowerCase().includes('waiting for sufficient') ||
        activeError.toLowerCase().includes('at least 5'))
  );

  const forecastStatus = autoscalingStatus?.forecast_status;
  const predictionAge = autoscalingStatus?.prediction_age_seconds;
  const lastTs = autoscalingStatus?.last_prediction_timestamp;
  const isFresh = Boolean(autoscalingStatus?.is_fresh);

  // Extract raw numeric value from autoscalingStatus, forecastData, or metrics
  let rawVal = null;
  if (
    autoscalingStatus?.current_predicted_load !== undefined &&
    autoscalingStatus?.current_predicted_load !== null &&
    !isNaN(Number(autoscalingStatus.current_predicted_load))
  ) {
    rawVal = Number(autoscalingStatus.current_predicted_load);
  } else if (
    forecastData?.data &&
    forecastData.data.length > 0 &&
    forecastData.data[0]?.yhat !== undefined &&
    forecastData.data[0]?.yhat !== null &&
    !isNaN(Number(forecastData.data[0].yhat))
  ) {
    rawVal = Number(forecastData.data[0].yhat);
  } else if (
    metrics?.forecast?.yhat !== undefined &&
    metrics?.forecast?.yhat !== null &&
    !isNaN(Number(metrics.forecast.yhat))
  ) {
    rawVal = Number(metrics.forecast.yhat);
  }

  const isBackendDown = !backendHealthy;
  const isWaiting =
    forecastStatus === 'waiting_for_data' ||
    isInsufficient ||
    (lastTs === 0 && rawVal === null) ||
    (lastTs === null && rawVal === null);

  const isStale =
    (predictionAge !== null && predictionAge !== undefined && predictionAge >= 45.0) ||
    forecastStatus === 'stale' ||
    (!isFresh && lastTs !== null && lastTs > 0);

  let displayValue = '--';
  let unit = 'req/s';
  let isAvailable = false;
  let statusReason = 'unknown';

  if (isBackendDown) {
    displayValue = 'Offline';
    unit = '';
    statusReason = 'offline';
  } else if (isWaiting) {
    displayValue = '--';
    unit = 'req/s';
    statusReason = 'waiting_for_data';
  } else if (isStale) {
    displayValue = '--';
    unit = 'req/s';
    statusReason = 'stale';
  } else if (rawVal !== null && !isNaN(rawVal)) {
    // Valid zero or positive prediction
    displayValue = rawVal.toFixed(1);
    unit = 'req/s';
    isAvailable = true;
    statusReason = 'fresh';
  } else {
    displayValue = '--';
    unit = 'req/s';
    statusReason = 'unavailable';
  }

  let badgeLabel = null;
  let subtitle = '';
  let variant = 'cyan';

  if (isCpuHpaMode) {
    badgeLabel = 'Informational only — not used for scaling';
    variant = isAvailable ? 'purple' : 'amber';
    if (!isAvailable) {
      subtitle =
        statusReason === 'offline'
          ? 'Backend service offline'
          : statusReason === 'stale'
          ? 'Forecast stale (≥45s gate closed)'
          : 'Waiting for sufficient live Prometheus observations to fit Prophet.';
    } else {
      subtitle = 'Informational only — not used for scaling in CPU HPA mode';
    }
  } else {
    variant = isAvailable ? 'cyan' : 'amber';
    if (!isAvailable) {
      subtitle =
        statusReason === 'offline'
          ? 'Backend service offline'
          : statusReason === 'stale'
          ? 'Forecast stale (≥45s gate closed)'
          : 'Waiting for live data';
    } else {
      const ageStr =
        predictionAge !== null && predictionAge !== undefined
          ? `${Math.round(predictionAge)}s ago`
          : 'recently';
      subtitle = `Active metric used for predictive scaling · Refreshed ${ageStr}`;
    }
  }

  return {
    rawVal,
    displayValue,
    unit,
    isAvailable,
    statusReason,
    badgeLabel,
    subtitle,
    variant,
    predictionAge,
    isCpuHpaMode,
  };
}

/**
 * Prepares chart points and handles zero-valued chart domains correctly.
 * When all yhat values are 0.0, sets domain to [0, 5] so Recharts renders the baseline cleanly.
 */
export function prepareForecastChartData(forecastData, horizonLimit = 30) {
  const points =
    forecastData?.data && forecastData.data.length > 0
      ? forecastData.data
      : forecastData?.forecast && forecastData.forecast.length > 0
      ? forecastData.forecast
      : [];

  const displayPoints = points.slice(0, horizonLimit);

  const chartData = displayPoints.map((pt, idx) => {
    let timeLabel = `+${idx * 15}s`;
    const tsStr = pt.ds || pt.timestamp;
    if (tsStr) {
      timeLabel = formatToIstChartTime(tsStr);
    }
    const y = pt.yhat !== null && pt.yhat !== undefined ? Number(pt.yhat) : 0;
    const yLower =
      pt.yhat_lower !== null && pt.yhat_lower !== undefined
        ? Math.max(0, Number(pt.yhat_lower))
        : 0;
    const yUpper =
      pt.yhat_upper !== null && pt.yhat_upper !== undefined
        ? Math.max(0, Number(pt.yhat_upper))
        : y;
    return {
      time: timeLabel,
      yhat: Number(y.toFixed(2)),
      yhat_lower: Number(yLower.toFixed(2)),
      yhat_upper: Number(yUpper.toFixed(2)),
    };
  });

  const maxYhat =
    chartData.length > 0
      ? Math.max(...chartData.map((d) => Math.max(d.yhat, d.yhat_upper)))
      : 0;

  // Handle zero-valued chart domains correctly: when max is 0, provide domain [0, 5]
  const yDomain = [0, maxYhat > 0 ? Math.ceil(maxYhat * 1.15) : 5];

  return {
    chartData,
    hasPoints: chartData.length > 0,
    maxYhat,
    yDomain,
  };
}
