import React from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  Gauge,
  Clock,
  Layers,
  Sliders,
  Settings,
  CheckCircle2,
  AlertTriangle,
  Info,
  TrendingUp,
} from 'lucide-react';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';
import { formatToIstTime } from '../utils/timezone';
import { resolvePredictedRps, prepareForecastChartData } from '../utils/forecast';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';

export default function Autoscaling({
  autoscalingStatus,
  metrics,
  k8sStatus,
  forecastData,
  forecastError,
}) {
  const isCpuHpaMode = autoscalingStatus?.active_controller === 'cpu-hpa';
  const isKedaMode = autoscalingStatus?.active_controller === 'keda';
  const scaledObjectPresent = autoscalingStatus?.scaledobject_present ?? (isKedaMode || (autoscalingStatus?.scaledobject_state !== 'Standby' && autoscalingStatus?.scaledobject_state !== 'Inactive'));

  const isGateValid = Boolean(autoscalingStatus?.is_fresh);
  const readyStatus = autoscalingStatus?.ready_status ?? false;
  const activeStatus = autoscalingStatus?.active_status ?? (isKedaMode && isGateValid);
  const fallbackActive = autoscalingStatus?.fallback_active ?? false;

  const freshnessAge =
    autoscalingStatus?.prediction_age_seconds !== null &&
    autoscalingStatus?.prediction_age_seconds !== undefined
      ? Math.round(autoscalingStatus.prediction_age_seconds)
      : null;

  // Shared forecast resolution ensures exact match with Overview and live backend
  const predInfo = resolvePredictedRps({
    autoscalingStatus,
    forecastData,
    metrics,
    backendHealthy: true,
  });

  const { chartData, hasPoints, yDomain } = prepareForecastChartData(forecastData, 30);

  const activeQuery =
    autoscalingStatus?.query ||
    'max(predicted_http_work_rps) and (max(prediction_timestamp_seconds) > 0) and ((time() - max(prediction_timestamp_seconds)) < 45)';

  const metricThreshold = autoscalingStatus?.metric_threshold || 10.0;
  const minReplicas = autoscalingStatus?.min_replicas || k8sStatus?.min_replicas || 1;
  const maxReplicas = autoscalingStatus?.max_replicas || k8sStatus?.max_replicas || 5;
  const currentReplicas = k8sStatus?.current_replicas ?? k8sStatus?.available_replicas ?? 1;
  const desiredReplicas = autoscalingStatus?.desired_hpa_replicas ?? k8sStatus?.desired_replicas ?? currentReplicas;

  return (
    <div className="space-y-6">
      {/* Top Header Card */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Gauge className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold text-slate-100">
                KEDA ScaledObject: {autoscalingStatus?.scaledobject_name || 'target-app-predictive-scaledobject'}
              </h2>
              {isCpuHpaMode || !scaledObjectPresent ? (
                <>
                  <StatusBadge
                    status="idle"
                    label="KEDA INACTIVE"
                    size="xs"
                  />
                  <span className="text-xs px-2.5 py-0.5 rounded-full font-medium bg-amber-500/10 text-amber-400 border border-amber-500/30">
                    Standby (CPU HPA Active)
                  </span>
                </>
              ) : (
                <>
                  <StatusBadge
                    status={activeStatus ? 'active' : 'warning'}
                    label={activeStatus ? 'KEDA ACTIVE' : 'KEDA INACTIVE'}
                    size="xs"
                  />
                  <StatusBadge
                    status={readyStatus ? 'healthy' : 'error'}
                    label={readyStatus ? 'READY' : 'NOT READY'}
                    size="xs"
                  />
                  <StatusBadge
                    status={fallbackActive ? 'fallback' : 'healthy'}
                    label={fallbackActive ? 'FALLBACK ACTIVE' : 'FALLBACK STANDBY'}
                    size="xs"
                  />
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
                    isKedaMode
                      ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                      : 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                  }`}>
                    {isKedaMode ? 'Active Controller' : 'Standby Controller'}
                  </span>
                </>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-1">
              KEDA-Managed HPA: <span className="font-mono text-cyan-400 font-semibold">{autoscalingStatus?.hpa_name || 'keda-hpa-target-app-predictive-scaledobject'}</span> &bull; Target: <span className="font-mono text-slate-300">Deployment/{autoscalingStatus?.target_deployment || 'target-app'}</span> &bull; Metric: <span className="font-mono text-emerald-400">s0-prometheus (predicted_http_work_rps)</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-xs font-mono">
            <span className="text-slate-400">State: </span>
            <span className={`font-semibold uppercase ${isCpuHpaMode ? 'text-amber-400' : 'text-cyan-400'}`}>
              {isCpuHpaMode ? 'Inactive (Standby)' : (autoscalingStatus?.scaledobject_state || 'Active')}
            </span>
          </div>
        </div>
      </div>

      {/* Controller Authority Notice Banner */}
      <div
        className={`p-3.5 rounded-xl border flex items-center justify-between gap-3 text-xs ${
          isCpuHpaMode
            ? 'bg-amber-500/10 border-amber-500/30 text-amber-200'
            : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-200'
        }`}
      >
        <div className="flex items-center gap-2.5">
          {isCpuHpaMode ? (
            <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
          ) : (
            <ShieldCheck className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          )}
          <span className="font-medium">
            {isCpuHpaMode
              ? 'KEDA is inactive — CPU HPA controls target-app.'
              : 'KEDA ACTIVE — Predictive Autoscaler actively controls Deployment/target-app based on predicted RPS.'}
          </span>
        </div>
        <span className="text-[11px] font-mono text-slate-400 shrink-0">
          {isCpuHpaMode ? 'Active Controller: Traditional CPU HPA (50% target)' : 'Active Controller: KEDA Predictive Scaler'}
        </span>
      </div>

      {/* KPI Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Predicted RPS Metric"
          value={predInfo.displayValue}
          unit={predInfo.unit}
          badge={
            predInfo.badgeLabel ? (
              <span className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/30">
                {predInfo.badgeLabel}
              </span>
            ) : null
          }
          subtitle={predInfo.subtitle}
          icon={Gauge}
          variant={predInfo.variant}
        />
        <MetricCard
          title="Scaling Threshold"
          value={metricThreshold}
          unit="RPS / pod"
          subtitle="AverageValue target"
          icon={Sliders}
          variant="purple"
        />
        <MetricCard
          title="Current & Desired Replicas"
          value={`${currentReplicas} / ${desiredReplicas}`}
          unit="pods"
          subtitle={`Min: ${minReplicas} · Max: ${maxReplicas} pods`}
          icon={Layers}
          variant="emerald"
        />
        <MetricCard
          title="Freshness & Fallback"
          value={freshnessAge !== null ? `${freshnessAge}s` : 'N/A'}
          unit={isGateValid ? '(Valid)' : '(Stale)'}
          subtitle={
            isCpuHpaMode || !scaledObjectPresent
              ? 'ScaledObject absent · Fallback not applicable'
              : fallbackActive
              ? 'Fallback active: 2 pods'
              : 'Fallback standby: 3 fails'
          }
          icon={Clock}
          variant={isGateValid ? 'emerald' : 'amber'}
        />
      </div>

      {/* Prophet Load Forecast Horizon Chart (Preview on Predictive KEDA page) */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-3">
        <div className="flex items-center justify-between mb-1">
          <div>
            <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2 flex-wrap">
              <TrendingUp className="w-4 h-4 text-cyan-400" />
              <span>Prophet Load Forecast Horizon (Next 30 Steps)</span>
              {isCpuHpaMode && (
                <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/30">
                  Informational only — not used for scaling
                </span>
              )}
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              {isCpuHpaMode
                ? "Informational only — not used for scaling in CPU HPA mode. Monitored for comparison with CPU baseline."
                : "Active workload prediction horizon used by KEDA Prometheus scaler for predictive scaling decisions."}
            </p>
          </div>
        </div>

        <div className="h-64 w-full">
          {hasPoints ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="kedaYhatGlow" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="kedaBoundsGlow" x1="0" y1="0" x2="0" y2="1">
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
                />
                <YAxis
                  stroke="#64748b"
                  fontSize={11}
                  tickLine={false}
                  domain={yDomain}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                  labelStyle={{ color: '#94a3b8' }}
                />
                <Area
                  type="monotone"
                  dataKey="yhat_upper"
                  stroke="#6366f1"
                  strokeDasharray="2 2"
                  fill="none"
                  name="Upper Bound"
                />
                <Area
                  type="monotone"
                  dataKey="yhat"
                  stroke="#06b6d4"
                  strokeWidth={2.5}
                  fillOpacity={1}
                  fill="url(#kedaYhatGlow)"
                  name="Predicted RPS"
                />
                <Area
                  type="monotone"
                  dataKey="yhat_lower"
                  stroke="#6366f1"
                  strokeDasharray="2 2"
                  fill="url(#kedaBoundsGlow)"
                  name="Lower Bound"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full flex flex-col items-center justify-center p-6 text-center text-xs text-slate-400 bg-slate-950/40 rounded-lg border border-dashed border-slate-800">
              <Info className="w-8 h-8 text-amber-400/90 mb-2" />
              <span className="font-semibold text-slate-200 mb-1">
                Waiting for sufficient live Prometheus observations to fit Prophet.
              </span>
              <span className="max-w-md text-slate-400 leading-relaxed">
                At least 5 live observations on /api/work are required to fit the time-series model.
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Live KEDA HPA Conditions & External Metrics Info */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            KEDA HPA Conditions &amp; External Metric
          </h3>
          <span className="text-xs font-mono text-cyan-400 bg-cyan-500/10 border border-cyan-500/30 px-2 py-0.5 rounded">
            Metric: s0-prometheus
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {(isCpuHpaMode || !scaledObjectPresent) ? (
            <div className="col-span-3 text-xs text-slate-400 py-3 px-4 bg-slate-950/60 rounded-lg border border-slate-800 flex items-center gap-2.5">
              <Info className="w-4 h-4 text-amber-400/80 shrink-0" />
              <span>
                KEDA-specific HPA conditions are unavailable: KEDA ScaledObject is absent and CPU HPA is the active controller.
              </span>
            </div>
          ) : (autoscalingStatus?.conditions && autoscalingStatus.conditions.length > 0) ? (
            autoscalingStatus.conditions.map((cond, idx) => (
              <div
                key={idx}
                className="p-3 bg-slate-950/80 rounded-lg border border-slate-800 space-y-1 text-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-200 font-mono">{cond.type}</span>
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded font-mono ${
                      cond.status === 'True'
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                        : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {cond.status}
                  </span>
                </div>
                {cond.reason && (
                  <div className="text-slate-400 text-[11px]">
                    Reason: <span className="text-cyan-300 font-mono">{cond.reason}</span>
                  </div>
                )}
                {cond.message && (
                  <div className="text-slate-400 text-[11px] leading-relaxed line-clamp-2" title={cond.message}>
                    {cond.message}
                  </div>
                )}
              </div>
            ))
          ) : (
            <div className="col-span-3 text-xs text-slate-400 py-2">
              KEDA HPA conditions active: ValidMetricFound &bull; ReadyForNewScale &bull; DesiredWithinRange
            </div>
          )}
        </div>
      </div>

      {/* Scaling Events Log */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-3">
        <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
          <Clock className="w-4 h-4 text-slate-400" />
          KEDA ScaledObject &amp; HPA Scaling Events
        </h3>

        {autoscalingStatus?.scaling_events && autoscalingStatus.scaling_events.length > 0 ? (
          <div className="divide-y divide-slate-800/60 text-xs font-mono max-h-60 overflow-y-auto">
            {autoscalingStatus.scaling_events.map((evt, idx) => (
              <div key={idx} className="py-2.5 flex items-start justify-between gap-3">
                <div className="space-y-0.5 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-[10px] px-1.5 py-0.2 rounded font-bold uppercase ${
                        evt.type === 'Warning'
                          ? 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                          : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                      }`}
                    >
                      {evt.type}
                    </span>
                    <span className="text-slate-200 font-semibold font-sans">{evt.reason}</span>
                    <span className="text-slate-400 text-[10px] font-mono">{evt.object}</span>
                  </div>
                  <div className="text-slate-400 text-[11px] font-sans truncate">{evt.message}</div>
                </div>
                <span className="text-slate-400 text-[10px] shrink-0 font-mono">
                  {evt.timestamp ? formatToIstTime(evt.timestamp) : 'Recent'}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-slate-400 py-3 text-center">
            No active scaling warning events recorded for KEDA ScaledObject.
          </div>
        )}
      </div>

      {/* Advanced Details (Collapsible): Pipeline Architecture, PromQL Query, and Fallback Mechanics */}
      <details className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm group">
        <summary className="cursor-pointer font-semibold text-xs text-slate-300 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Settings className="w-4 h-4 text-slate-400" />
            Advanced Details: Architecture, PromQL Trigger &amp; Stabilization Policies
          </span>
          <span className="text-[11px] font-mono text-cyan-400 group-open:rotate-180 transition-transform">▼</span>
        </summary>

        <div className="mt-5 space-y-5">
          {/* Visual Pipeline Flowchart */}
          <div>
            <h4 className="text-xs font-semibold text-slate-300 mb-2">Autoscaling Architecture Flow</h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
              <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
                <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider block mb-1">Step 1</span>
                <div className="text-xs font-semibold text-slate-200">Prometheus Scrapes</div>
                <p className="text-[11px] text-slate-400 mt-1">Extracts incoming RPS from target-app pods</p>
              </div>

              <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
                <span className="text-[10px] font-mono text-purple-400 uppercase tracking-wider block mb-1">Step 2</span>
                <div className="text-xs font-semibold text-slate-200">Prophet Backend</div>
                <p className="text-[11px] text-slate-400 mt-1">Fits time series &amp; exports predicted_http_work_rps gauge</p>
              </div>

              <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
                <span className="text-[10px] font-mono text-emerald-400 uppercase tracking-wider block mb-1">Step 3</span>
                <div className="text-xs font-semibold text-slate-200">Freshness Gate</div>
                <p className="text-[11px] text-slate-400 mt-1">Enforces 45s cutoff timestamp to prevent stale scaling</p>
              </div>

              <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
                <span className="text-[10px] font-mono text-amber-400 uppercase tracking-wider block mb-1">Step 4</span>
                <div className="text-xs font-semibold text-slate-200">KEDA Scaler</div>
                <p className="text-[11px] text-slate-400 mt-1">Polls every 5s &amp; evaluates 10 RPS/pod threshold</p>
              </div>

              <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
                <span className="text-[10px] font-mono text-rose-400 uppercase tracking-wider block mb-1">Step 5</span>
                <div className="text-xs font-semibold text-slate-200">Kubernetes HPA</div>
                <p className="text-[11px] text-slate-400 mt-1">Adjusts target-app replicas: 0s up, 15s down</p>
              </div>
            </div>
          </div>

          {/* PromQL & Fallback Specs */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="p-4 bg-slate-950 rounded-lg border border-slate-800 space-y-2">
              <span className="text-xs font-semibold text-slate-200">PromQL Trigger Query</span>
              <div className="font-mono text-xs text-cyan-300 break-all bg-slate-900 p-2.5 rounded border border-slate-800 leading-relaxed">
                {activeQuery}
              </div>
              <p className="text-[11px] text-slate-400">
                If prediction age exceeds 45s, the query evaluates to empty vector, preventing stale scaling.
              </p>
            </div>

            <div className="p-4 bg-slate-950 rounded-lg border border-slate-800 space-y-2">
              <span className="text-xs font-semibold text-slate-200">Fallback Configuration</span>
              <div className="flex gap-4 text-xs font-mono">
                <div>Failure Threshold: <span className="text-slate-100 font-bold">{autoscalingStatus?.fallback_threshold || 3} fails</span></div>
                <div>Fallback Replicas: <span className="text-amber-400 font-bold">{autoscalingStatus?.fallback_replicas || 2} pods</span></div>
              </div>
              <p className="text-[11px] text-slate-400">
                Guarantees continuity by pinning deployment to 2 safe replicas if telemetry becomes unavailable.
              </p>
            </div>
          </div>
        </div>
      </details>
    </div>
  );
}
