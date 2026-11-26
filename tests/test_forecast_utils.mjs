import { resolvePredictedRps, prepareForecastChartData } from '../frontend/src/utils/forecast.js';

// Test 1: Zero prediction
const zeroRes = resolvePredictedRps({
  autoscalingStatus: {
    active_controller: 'cpu-hpa',
    current_predicted_load: 0.0,
    is_fresh: true,
    prediction_age_seconds: 5,
    last_prediction_timestamp: 100,
  },
});
if (zeroRes.displayValue !== '0.0') throw new Error(`Expected 0.0, got ${zeroRes.displayValue}`);
if (zeroRes.unit !== 'req/s') throw new Error(`Expected req/s, got ${zeroRes.unit}`);
if (zeroRes.badgeLabel !== 'Informational only — not used for scaling') {
  throw new Error(`Expected informational badge, got ${zeroRes.badgeLabel}`);
}
if (!zeroRes.subtitle.includes('Informational only — not used for scaling')) {
  throw new Error(`Expected informational subtitle, got ${zeroRes.subtitle}`);
}

// Test 2: Positive prediction in KEDA mode
const posRes = resolvePredictedRps({
  autoscalingStatus: {
    active_controller: 'keda',
    current_predicted_load: 14.8,
    is_fresh: true,
    prediction_age_seconds: 12,
    last_prediction_timestamp: 100,
  },
});
if (posRes.displayValue !== '14.8') throw new Error(`Expected 14.8, got ${posRes.displayValue}`);
if (!posRes.subtitle.includes('Active metric used for predictive scaling')) {
  throw new Error(`Expected active subtitle, got ${posRes.subtitle}`);
}

// Test 3: Missing prediction
const missRes = resolvePredictedRps({
  autoscalingStatus: {
    active_controller: 'cpu-hpa',
    forecast_status: 'waiting_for_data',
    current_predicted_load: 0.0,
    last_prediction_timestamp: 0,
  },
});
if (missRes.displayValue !== '--') throw new Error(`Expected --, got ${missRes.displayValue}`);

// Test 4: Stale prediction
const staleRes = resolvePredictedRps({
  autoscalingStatus: {
    active_controller: 'cpu-hpa',
    current_predicted_load: 10.0,
    is_fresh: false,
    prediction_age_seconds: 55,
    last_prediction_timestamp: 100,
  },
});
if (staleRes.displayValue !== '--') throw new Error(`Expected --, got ${staleRes.displayValue}`);

// Test 5: Chart data with all zero yhat
const chartRes = prepareForecastChartData({
  data: [{ ds: '2026-11-26T11:00:00Z', yhat: 0.0, yhat_lower: 0.0, yhat_upper: 0.0 }],
});
if (chartRes.hasPoints !== true) throw new Error('Expected hasPoints=true');
if (chartRes.yDomain[1] !== 5) throw new Error(`Expected yDomain[1]=5, got ${chartRes.yDomain[1]}`);

// Test 6: Live Kubernetes + Prophet cluster state verification
const liveAutoscaling = {
  current_predicted_load: 0.0,
  is_fresh: true,
  forecast_status: 'fresh',
  active_controller: 'cpu-hpa',
  prediction_age_seconds: 7.8,
  last_prediction_timestamp: 1795687692,
};
const liveForecast = {
  provenance: 'live_prometheus',
  data: Array(76).fill({
    ds: '2026-11-26T11:45:24.658999920+00:00',
    yhat: 0.0,
    yhat_lower: 0.0,
    yhat_upper: 0.0,
  }),
};
const liveRps = resolvePredictedRps({
  autoscalingStatus: liveAutoscaling,
  forecastData: liveForecast,
});
if (liveRps.displayValue !== '0.0') throw new Error(`Expected 0.0, got ${liveRps.displayValue}`);
if (liveRps.unit !== 'req/s') throw new Error(`Expected req/s, got ${liveRps.unit}`);
if (liveRps.badgeLabel !== 'Informational only — not used for scaling') {
  throw new Error(`Expected informational badge, got ${liveRps.badgeLabel}`);
}
const liveChart = prepareForecastChartData(liveForecast, 30);
if (liveChart.hasPoints !== true) throw new Error('Expected liveChart hasPoints=true');
if (liveChart.chartData.length !== 30) throw new Error(`Expected 30 points, got ${liveChart.chartData.length}`);
if (liveChart.yDomain[0] !== 0 || liveChart.yDomain[1] !== 5) {
  throw new Error(`Expected domain [0, 5], got ${JSON.stringify(liveChart.yDomain)}`);
}

console.log('All frontend forecast utility assertions passed successfully!');
