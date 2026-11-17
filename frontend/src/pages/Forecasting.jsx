import React, { useState } from 'react';
import {
  TrendingUp,
  RefreshCw,
  Clock,
  Database,
  Layers,
  Activity,
  Info,
} from 'lucide-react';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';

export default function Forecasting({
  forecastData,
  forecastError,
  metrics,
  autoscalingStatus,
  onForceRefresh,
  isRefreshing,
}) {
  const [horizonLimit, setHorizonLimit] = useState(50);
  const [tableSearch, setTableSearch] = useState('');

  const isFresh = Boolean(autoscalingStatus?.is_fresh);
  const ageSeconds = autoscalingStatus?.prediction_age_seconds;
  const forecastStatus = autoscalingStatus?.forecast_status || (isFresh ? 'fresh' : 'waiting_for_data');
  const activeError = forecastError || autoscalingStatus?.forecast_error;

  // Determine currentPredicted presentation
  let predictedDisplay = '--';
  let predictedUnit = 'req/s';
  let predictedBadge = null;
  let predictedSubtitle = 'Served to KEDA ScaledObject';
  let predictedVariant = 'cyan';

  const isInsufficient = Boolean(
    activeError && (activeError.toLowerCase().includes('insufficient') || activeError.toLowerCase().includes('at least 5'))
  );
  const isWaiting =
    forecastStatus === 'waiting_for_data' ||
    isInsufficient ||
    !isFresh ||
    ageSeconds === null ||
    autoscalingStatus?.last_prediction_timestamp === 0 ||
    autoscalingStatus?.last_prediction_timestamp === null;

  if (isWaiting || isInsufficient) {
    predictedDisplay = '--';
    predictedUnit = 'req/s';
    predictedBadge = null;
    predictedSubtitle = (
      <span
        className="flex items-center gap-1.5 text-amber-400 text-xs font-medium"
        title={activeError || 'Awaiting 5 traffic samples on /api/work'}
      >
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0 animate-pulse" />
        <span>Waiting for live data</span>
      </span>
    );
    predictedVariant = 'amber';
  } else if (ageSeconds !== null && ageSeconds !== undefined && (ageSeconds >= 45 || forecastStatus === 'stale')) {
    predictedDisplay = '--';
    predictedUnit = 'req/s';
    predictedBadge = null;
    predictedSubtitle = (
      <span className="flex items-center gap-1.5 text-amber-400 text-xs font-medium">
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
        <span>Stale (&ge;45s gate)</span>
      </span>
    );
    predictedVariant = 'amber';
  } else if (isFresh && autoscalingStatus?.current_predicted_load !== undefined && autoscalingStatus?.current_predicted_load !== null && autoscalingStatus.current_predicted_load > 0) {
    predictedDisplay = Number(autoscalingStatus.current_predicted_load).toFixed(2);
    predictedUnit = 'req/s';
    predictedBadge = null;
    predictedSubtitle = `Fresh prediction (${Math.round(ageSeconds || 0)}s old)`;
    predictedVariant = 'cyan';
  } else {
    predictedDisplay = '--';
    predictedUnit = 'req/s';
    predictedBadge = null;
    predictedSubtitle = activeError || 'Waiting for live data';
    predictedVariant = 'amber';
  }

  const historyPoints = (isFresh || forecastData) ? (forecastData?.history || []) : [];
  const forecastPoints = (isFresh && forecastData) ? (forecastData?.data || forecastData?.forecast || []) : [];
  const displayForecastPoints = forecastPoints.slice(0, horizonLimit);

  // Compute max / min bounds from display points
  const yhatValues = displayForecastPoints.map((p) => p.yhat || 0);
  const maxYhat = yhatValues.length > 0 ? Math.max(...yhatValues).toFixed(1) : '--';
  const minYhat = yhatValues.length > 0 ? Math.min(...yhatValues).toFixed(1) : '--';

  // Build combined chart timeline: Historical observations first, followed by future forecast
  const chartData = [];

  // Add historical points
  historyPoints.forEach((h, idx) => {
    let timeLabel = `H-${historyPoints.length - idx}`;
    let fullTime = `History Point ${idx + 1}`;
    if (h.ds) {
      const d = new Date(h.ds);
      if (!isNaN(d.getTime())) {
        timeLabel = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        fullTime = d.toLocaleTimeString();
      }
    }
    chartData.push({
      time: timeLabel,
      fullTime,
      actual_rps: Number((h.actual_rps || 0).toFixed(2)),
      yhat: null,
      yhat_lower: null,
      yhat_upper: null,
    });
  });

  // Add future forecast points
  displayForecastPoints.forEach((pt, idx) => {
    let timeLabel = `+${idx * 15}s`;
    let fullTime = `Forecast Step ${idx + 1}`;
    const tsStr = pt.ds || pt.timestamp;
    if (tsStr) {
      const d = new Date(tsStr);
      if (!isNaN(d.getTime())) {
        timeLabel = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        fullTime = d.toLocaleTimeString();
      }
    }
    chartData.push({
      time: timeLabel,
      fullTime,
      actual_rps: null,
      yhat: Number((pt.yhat || 0).toFixed(2)),
      yhat_lower: Number(Math.max(0, pt.yhat_lower || 0).toFixed(2)),
      yhat_upper: Number((pt.yhat_upper || 0).toFixed(2)),
    });
  });

  const generatedEpoch = forecastData?.generated_at_epoch;
  const generatedTimeStr = generatedEpoch
    ? new Date(generatedEpoch * 1000).toLocaleTimeString()
    : 'N/A';

  return (
    <div className="space-y-6">
      {/* Top Controls & Status Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-purple-500/10 text-purple-400 border border-purple-500/20">
            <TrendingUp className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-slate-100">
                Prophet Workload Forecasting Engine
              </h2>
              <StatusBadge
                status={
                  forecastStatus === 'fresh'
                    ? 'healthy'
                    : forecastStatus === 'waiting_for_data'
                    ? 'warning'
                    : forecastStatus === 'stale'
                    ? 'warning'
                    : 'error'
                }
                label={
                  forecastStatus === 'fresh'
                    ? 'Fresh Forecast'
                    : forecastStatus === 'waiting_for_data'
                    ? 'Waiting for Live Data'
                    : forecastStatus === 'stale'
                    ? 'Stale (>45s Gate Closed)'
                    : 'Forecast Error'
                }
              />
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                Generated: {isFresh ? generatedTimeStr : 'Pending live data'}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Fits live Prometheus request rates, computes Bayesian piecewise trend &amp; generates prediction horizon.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => onForceRefresh(true)}
            disabled={isRefreshing}
            className="flex items-center gap-2 px-3.5 py-2 text-xs font-semibold rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-950 transition-colors shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
            Force Model Retrain
          </button>
        </div>
      </div>

      {/* Warning/Error Banner when waiting for live observations or model fitting error */}
      {(!isFresh || activeError) && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 flex items-start gap-3">
          <div className="p-2 rounded-lg bg-amber-500/20 text-amber-400 shrink-0 mt-0.5">
            <Info className="w-5 h-5" />
          </div>
          <div className="space-y-1">
            <div className="text-sm font-semibold text-amber-200">
              {forecastStatus === 'waiting_for_data'
                ? 'Forecasting Standby: Awaiting Live Prometheus Traffic Observations'
                : forecastStatus === 'stale'
                ? 'Forecast Validity Expired (>45s): KEDA Fallback Safeguard Triggered'
                : 'Forecast Refresh Notice'}
            </div>
            <div className="text-xs text-slate-300 leading-relaxed">
              {activeError ||
                'Prophet requires at least 5 consecutive request-rate observations on /api/work to train the time-series model. When observations are insufficient, old predictions are withheld to protect autoscaling integrity.'}
            </div>
            <div className="text-[11px] text-slate-400">
              Tip: Go to the <span className="text-cyan-400 font-medium">Workload Generator</span> tab and start a traffic profile to generate live application metrics.
            </div>
          </div>
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Current Predicted RPS"
          value={predictedDisplay}
          unit={predictedUnit}
          badge={predictedBadge}
          subtitle={predictedSubtitle}
          icon={TrendingUp}
          variant={predictedVariant}
        />
        <MetricCard
          title="Prediction Freshness"
          value={ageSeconds !== null && ageSeconds !== undefined ? `${Math.round(ageSeconds)}s` : 'N/A'}
          subtitle={
            isFresh
              ? 'Within 45s validity gate'
              : 'Stale (Fallback candidate)'
          }
          icon={Clock}
          variant={isFresh ? 'emerald' : 'amber'}
        />
        <MetricCard
          title="Horizon Peak (yhat_upper)"
          value={maxYhat}
          unit="req/s"
          subtitle={`Max predicted in next ${horizonLimit} steps`}
          icon={Layers}
          variant="purple"
        />
        <MetricCard
          title="Horizon Floor (yhat_lower)"
          value={minYhat}
          unit="req/s"
          subtitle={`Min predicted in next ${horizonLimit} steps`}
          icon={Database}
          variant="emerald"
        />
      </div>

      {/* Main Forecasting Recharts Graph */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
          <div>
            <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-cyan-400" />
              Actual Historical Observations vs Predicted Horizon
            </h3>
            <p className="text-xs text-slate-400">
              Green line represents observed requests; cyan line indicates central forecast estimate with 80% credible interval [yhat_lower, yhat_upper]
            </p>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <span className="text-slate-400">Horizon:</span>
            {[30, 50, 100].map((count) => (
              <button
                key={count}
                onClick={() => setHorizonLimit(count)}
                className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                  horizonLimit === count
                    ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 font-semibold'
                    : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                }`}
              >
                {count} pts
              </button>
            ))}
          </div>
        </div>

        <div className="h-80 w-full">
          {chartData.length > 0 && isFresh ? (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
                <defs>
                  <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="boundsGlow" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis
                  dataKey="time"
                  stroke="#64748b"
                  fontSize={11}
                  tickLine={false}
                  angle={-15}
                  textAnchor="end"
                />
                <YAxis
                  stroke="#64748b"
                  fontSize={11}
                  tickLine={false}
                  domain={[0, 'auto']}
                  label={{ value: 'RPS (req/s)', angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 11 }}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const data = payload[0].payload;
                      return (
                        <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 shadow-xl font-mono text-xs space-y-1">
                          <div className="text-slate-400 font-sans font-semibold border-b border-slate-800 pb-1 mb-1">
                            {data.fullTime}
                          </div>
                          {data.actual_rps !== null && data.actual_rps !== undefined && (
                            <div className="text-emerald-400 flex justify-between gap-4">
                              <span>Actual Load:</span>
                              <span className="font-bold">{data.actual_rps} req/s</span>
                            </div>
                          )}
                          {data.yhat !== null && data.yhat !== undefined && (
                            <>
                              <div className="text-cyan-400 flex justify-between gap-4">
                                <span>Predicted (yhat):</span>
                                <span className="font-bold">{data.yhat} req/s</span>
                              </div>
                              <div className="text-indigo-400 flex justify-between gap-4">
                                <span>Upper (yhat_upper):</span>
                                <span>{data.yhat_upper} req/s</span>
                              </div>
                              <div className="text-indigo-400 flex justify-between gap-4">
                                <span>Lower (yhat_lower):</span>
                                <span>{data.yhat_lower} req/s</span>
                              </div>
                            </>
                          )}
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Legend
                  verticalAlign="top"
                  height={36}
                  wrapperStyle={{ fontSize: '12px' }}
                />
                {/* Historical Observed Load Line */}
                <Line
                  type="monotone"
                  dataKey="actual_rps"
                  stroke="#10b981"
                  strokeWidth={2.5}
                  dot={{ r: 2, fill: '#10b981' }}
                  activeDot={{ r: 5 }}
                  name="Actual Observed RPS"
                />
                {/* Upper bound dashed line */}
                <Line
                  type="monotone"
                  dataKey="yhat_upper"
                  stroke="#818cf8"
                  strokeDasharray="3 3"
                  dot={false}
                  strokeWidth={1}
                  name="Upper Bound (80%)"
                />
                {/* Main forecast prediction line */}
                <Line
                  type="monotone"
                  dataKey="yhat"
                  stroke="#06b6d4"
                  strokeWidth={2.5}
                  dot={{ r: 2, fill: '#06b6d4' }}
                  activeDot={{ r: 5 }}
                  name="Predicted Load (yhat)"
                />
                {/* Lower bound dashed line */}
                <Line
                  type="monotone"
                  dataKey="yhat_lower"
                  stroke="#818cf8"
                  strokeDasharray="3 3"
                  dot={false}
                  strokeWidth={1}
                  name="Lower Bound (80%)"
                />
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full flex flex-col items-center justify-center p-8 text-center text-xs text-slate-400 bg-slate-950/40 rounded-xl border border-dashed border-slate-800">
              <Info className="w-10 h-10 text-amber-400/90 mb-3" />
              <h4 className="text-sm font-semibold text-slate-200 mb-1">
                {forecastStatus === 'stale'
                  ? 'Forecast Stale (Validity Gate Closed)'
                  : 'Forecast Projections Awaiting Traffic'}
              </h4>
              <p className="max-w-md text-slate-400 leading-relaxed mb-4">
                {activeError ||
                  'The forecasting backend requires at least 5 consecutive live Prometheus traffic observations on target-app (/api/work) before fitting a Prophet time-series model.'}
              </p>
              <div className="flex items-center gap-2 text-[11px] text-slate-400">
                <span>Tip: Go to the <strong className="text-cyan-400">Workload Generator</strong> tab and launch any profile to generate live traffic.</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Model Points Inspector Table */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">
              Raw Forecast Horizon Points Inspector
            </h3>
            <p className="text-xs text-slate-400">
              Direct point-by-point values computed by Prophet model
            </p>
          </div>
          <input
            type="text"
            placeholder="Filter by time..."
            value={tableSearch}
            onChange={(e) => setTableSearch(e.target.value)}
            className="px-3 py-1.5 text-xs bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-cyan-500 font-mono w-48"
          />
        </div>

        <div className="max-h-64 overflow-y-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="sticky top-0 bg-slate-900 border-b border-slate-800 text-slate-400">
              <tr>
                <th className="py-2 px-3">#</th>
                <th className="py-2 px-3">Horizon Timestamp (ds)</th>
                <th className="py-2 px-3 text-cyan-400">Predicted Load (yhat)</th>
                <th className="py-2 px-3 text-slate-400">Lower Bound (yhat_lower)</th>
                <th className="py-2 px-3 text-slate-400">Upper Bound (yhat_upper)</th>
                <th className="py-2 px-3">Confidence Range</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {displayForecastPoints
                .filter((p) => (p.ds || p.timestamp || '').toLowerCase().includes(tableSearch.toLowerCase()))
                .map((pt, idx) => {
                  const yhat = Number(pt.yhat || 0).toFixed(2);
                  const lower = Number(Math.max(0, pt.yhat_lower || 0)).toFixed(2);
                  const upper = Number(pt.yhat_upper || 0).toFixed(2);
                  const spread = (upper - lower).toFixed(2);

                  return (
                    <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                      <td className="py-2 px-3 text-slate-400">{idx + 1}</td>
                      <td className="py-2 px-3 text-slate-300">{pt.ds || pt.timestamp || `+${idx * 15}s`}</td>
                      <td className="py-2 px-3 text-cyan-400 font-semibold">{yhat} req/s</td>
                      <td className="py-2 px-3 text-slate-400">{lower}</td>
                      <td className="py-2 px-3 text-slate-400">{upper}</td>
                      <td className="py-2 px-3 text-slate-400">&plusmn;{(spread / 2).toFixed(2)} req/s</td>
                    </tr>
                  );
                })}
              {displayForecastPoints.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-slate-400 font-sans">
                    No forecast points currently generated.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
