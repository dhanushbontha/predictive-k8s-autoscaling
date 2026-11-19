import React from 'react';
import {
  Activity,
  TrendingUp,
  Server,
  Layers,
  Cpu,
  Database,
  ShieldCheck,
  Play,
  ArrowUpRight,
  Info,
} from 'lucide-react';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';

export default function Overview({
  metrics,
  forecastData,
  forecastError,
  k8sStatus,
  autoscalingStatus,
  workloadStatus,
  backendHealthy = true,
  prometheusHealthy = false,
  onNavigate,
}) {
  // Extract observed metrics from backend /api/metrics or fallback
  const observed = metrics?.observed || metrics?.current_metrics || {};

  // Actual incoming RPS
  const actualRps =
    observed.http_work_rps !== undefined && observed.http_work_rps !== null
      ? Number(observed.http_work_rps).toFixed(1)
      : '--';

  const isFresh = Boolean(autoscalingStatus?.is_fresh);
  const predictionAge = autoscalingStatus?.prediction_age_seconds;
  const forecastStatus = autoscalingStatus?.forecast_status || (isFresh ? 'fresh' : 'waiting_for_data');
  const activeError = forecastError || autoscalingStatus?.forecast_error;

  const provenance =
    autoscalingStatus?.current_provenance ??
    forecastData?.provenance ??
    metrics?.provenance ??
    'live_prometheus';

  // Determine Predicted RPS display values based on backend status and freshness
  let predictedValue = '--';
  let predictedUnit = 'req/s';
  let predictedBadge = null;
  let predictedSubtitle = null;
  let predictedVariant = 'purple';

  const isInsufficient = Boolean(
    activeError && (activeError.toLowerCase().includes('insufficient') || activeError.toLowerCase().includes('at least 5'))
  );
  const isWaiting =
    forecastStatus === 'waiting_for_data' ||
    isInsufficient ||
    !isFresh ||
    predictionAge === null ||
    autoscalingStatus?.last_prediction_timestamp === 0 ||
    autoscalingStatus?.last_prediction_timestamp === null;

  if (!backendHealthy) {
    predictedValue = 'Offline';
    predictedUnit = '';
    predictedBadge = null;
    predictedSubtitle = 'Backend service offline';
    predictedVariant = 'amber';
  } else if (isWaiting || isInsufficient) {
    predictedValue = '--';
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
  } else if (predictionAge !== null && predictionAge !== undefined && (predictionAge >= 45 || forecastStatus === 'stale')) {
    predictedValue = '--';
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
    predictedValue = Number(autoscalingStatus.current_predicted_load).toFixed(1);
    predictedUnit = 'req/s';
    predictedBadge = null;
    const ageStr = predictionAge !== undefined && predictionAge !== null ? `${Math.round(predictionAge)}s ago` : 'just now';
    predictedSubtitle = (
      <span className="flex items-center gap-1.5 text-xs text-slate-400">
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400" />
        <span className="text-emerald-400 font-medium">Updated {ageStr}</span>
        <span className="text-slate-600">&bull;</span>
        <span className="font-mono text-slate-400">{provenance}</span>
      </span>
    );
    predictedVariant = 'cyan';
  } else {
    predictedValue = '--';
    predictedUnit = 'req/s';
    predictedBadge = null;
    predictedSubtitle = activeError || 'Waiting for live data';
    predictedVariant = 'amber';
  }

  // Replicas (accurately distinguishing desired from max)
  const currentReplicas =
    k8sStatus?.current_replicas ??
    k8sStatus?.available_replicas ??
    k8sStatus?.ready_replicas ??
    1;

  const desiredReplicas =
    k8sStatus?.desired_replicas ??
    autoscalingStatus?.desired_hpa_replicas ??
    1;

  const maxReplicas = k8sStatus?.max_replicas ?? autoscalingStatus?.max_replicas ?? 5;

  // CPU and Memory utilization
  const cpuCores = k8sStatus?.cpu_usage_cores ?? 0.0;
  const cpuMillicores = (cpuCores * 1000).toFixed(1);
  const cpuPercent =
    currentReplicas > 0
      ? Math.round((cpuCores / (0.1 * currentReplicas)) * 100)
      : 0;

  const memMb = k8sStatus?.memory_working_set_mb ?? 0.0;
  const memFormatted = memMb.toFixed(1);
  const memPercent =
    currentReplicas > 0
      ? Math.round((memMb / (128 * currentReplicas)) * 100)
      : 0;

  // Format forecast points for the mini-chart (only show if forecast is fresh and active)
  const points = (isFresh && forecastData) ? (forecastData?.data || forecastData?.forecast || []) : [];
  const miniChartData = points.slice(0, 30).map((pt, idx) => {
    let timeLabel = `+${idx * 15}s`;
    const tsStr = pt.ds || pt.timestamp;
    if (tsStr) {
      const d = new Date(tsStr);
      if (!isNaN(d.getTime())) {
        timeLabel = d.toLocaleTimeString([], { minute: '2-digit', second: '2-digit' });
      }
    }
    return {
      time: timeLabel,
      yhat: Number(pt.yhat || 0).toFixed(1),
      yhat_lower: Number(Math.max(0, pt.yhat_lower || 0)).toFixed(1),
      yhat_upper: Number(pt.yhat_upper || 0).toFixed(1),
    };
  });

  return (
    <div className="space-y-6">
      {/* Active Workload Alert Banner if running */}
      {workloadStatus?.is_running && (
        <div className="bg-cyan-500/10 border border-cyan-500/30 rounded-xl p-4 flex items-center justify-between animate-pulse">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-cyan-500/20 text-cyan-400">
              <Play className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-cyan-200">
                Workload Generator Running:{' '}
                <span className="font-mono uppercase text-cyan-400">
                  {workloadStatus.active_profile || workloadStatus.profile}
                </span>{' '}
                profile
              </div>
              <div className="text-xs text-slate-400">
                Target: {workloadStatus.target_url || workloadStatus.target_host} &bull; Users:{' '}
                {workloadStatus.user_count || workloadStatus.users} &bull; Elapsed:{' '}
                {Math.round(workloadStatus.elapsed_seconds || 0)}s /{' '}
                {workloadStatus.duration_seconds}s
              </div>
            </div>
          </div>
          <button
            onClick={() => onNavigate('workload')}
            className="px-3 py-1.5 bg-cyan-500 hover:bg-cyan-400 text-slate-950 text-xs font-semibold rounded-lg flex items-center gap-1 transition-colors"
          >
            Manage <ArrowUpRight className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Main KPI Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        <MetricCard
          title="Actual Incoming RPS"
          value={actualRps}
          unit="req/s"
          subtitle="Real-time traffic rate"
          icon={Activity}
          variant="cyan"
        />
        <MetricCard
          title="Predicted RPS"
          value={predictedValue}
          unit={predictedUnit}
          badge={predictedBadge}
          subtitle={predictedSubtitle}
          icon={TrendingUp}
          variant={predictedVariant}
        />
        <MetricCard
          title="Current Replicas"
          value={currentReplicas}
          unit={`/ ${maxReplicas} max`}
          subtitle={`Available: ${k8sStatus?.available_replicas ?? currentReplicas} pods`}
          icon={Server}
          variant="emerald"
        />
        <MetricCard
          title="Desired Replicas"
          value={desiredReplicas}
          unit="pods"
          subtitle={`Target size (min: ${k8sStatus?.min_replicas || 1})`}
          icon={Layers}
          variant="amber"
        />
        <MetricCard
          title="Active Autoscaler"
          value={autoscalingStatus?.active_controller === 'cpu-hpa' ? 'CPU HPA' : 'Predictive KEDA'}
          subtitle={autoscalingStatus?.active_controller === 'cpu-hpa' ? 'Reactive CPU target (50%)' : 'Proactive request scaler'}
          icon={ShieldCheck}
          variant="purple"
        />
        <MetricCard
          title="Cluster Resources"
          value={`${cpuMillicores}m`}
          unit={`/ ${memFormatted} MiB`}
          subtitle={`${cpuPercent}% CPU · ${memPercent}% RAM`}
          icon={Cpu}
          variant="cyan"
        />
      </div>

      {/* Mid Section: Mini Forecast Chart & Autoscaling Freshness */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Forecast Preview Chart (2 Cols) */}
        <div className="lg:col-span-2 bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-cyan-400" />
                Prophet Load Forecast Horizon (Next 30 Steps)
              </h3>
              <p className="text-xs text-slate-400">
                Forward-looking request projections with uncertainty envelope [yhat_lower, yhat_upper]
              </p>
            </div>
            <button
              onClick={() => onNavigate('forecasting')}
              className="text-xs text-cyan-400 hover:text-cyan-300 font-medium flex items-center gap-1"
            >
              Detailed View <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="h-64 w-full">
            {miniChartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={miniChartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="yhatGlow" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.3} />
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
                  />
                  <YAxis
                    stroke="#64748b"
                    fontSize={11}
                    tickLine={false}
                    domain={[0, 'auto']}
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
                    fill="url(#yhatGlow)"
                    name="Predicted RPS"
                  />
                  <Area
                    type="monotone"
                    dataKey="yhat_lower"
                    stroke="#6366f1"
                    strokeDasharray="2 2"
                    fill="url(#boundsGlow)"
                    name="Lower Bound"
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex flex-col items-center justify-center p-6 text-center text-xs text-slate-400 bg-slate-950/40 rounded-lg border border-dashed border-slate-800">
                <Info className="w-8 h-8 text-amber-400/90 mb-2" />
                <span className="font-semibold text-slate-200 mb-1">
                  {forecastStatus === 'stale'
                    ? 'Forecast Stale (Validity Gate Closed)'
                    : 'Awaiting Application Traffic'}
                </span>
                <span className="max-w-md text-slate-400 leading-relaxed">
                  {activeError ||
                    'Prometheus has not yet observed 5 consecutive /api/work requests needed to fit Prophet. Launch a profile in the Workload tab to begin real-time forecasting.'}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Autoscaler & Freshness Overview Card (1 Col) */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                Autoscaling Guard
              </h3>
              <StatusBadge
                status={
                  autoscalingStatus?.fallback_active
                    ? 'fallback'
                    : isFresh
                    ? 'healthy'
                    : 'warning'
                }
                label={
                  autoscalingStatus?.fallback_active
                    ? 'Fallback Active'
                    : isFresh
                    ? 'Gate Active'
                    : 'Prediction Stale'
                }
              />
            </div>

            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="p-2.5 bg-slate-950/40 rounded-lg border border-slate-800/60">
                  <span className="text-slate-400 block mb-0.5">Scale Target</span>
                  <span className="font-mono text-slate-200 font-semibold">
                    {autoscalingStatus?.metric_threshold || 10} RPS/pod
                  </span>
                </div>
                <div className="p-2.5 bg-slate-950/40 rounded-lg border border-slate-800/60">
                  <span className="text-slate-400 block mb-0.5">Prediction Age</span>
                  <span
                    className={`font-mono font-semibold ${
                      predictionAge !== null && predictionAge !== undefined && predictionAge < 45
                        ? 'text-emerald-400'
                        : 'text-amber-400'
                    }`}
                  >
                    {predictionAge !== null && predictionAge !== undefined
                      ? `${Math.round(predictionAge)}s ago`
                      : 'N/A'}
                  </span>
                </div>
              </div>

              <div className="p-2.5 bg-slate-950/40 rounded-lg border border-slate-800/60 text-xs flex items-center justify-between">
                <span className="text-slate-400">Failover Mode</span>
                <span className="font-mono text-slate-200">
                  Fallback to {autoscalingStatus?.fallback_replicas || 2} pods after{' '}
                  {autoscalingStatus?.fallback_threshold || 3} fails
                </span>
              </div>

              {/* Collapsible PromQL Trigger Details */}
              <details className="p-2.5 bg-slate-950/50 rounded-lg border border-slate-800/80 text-xs group">
                <summary className="cursor-pointer text-slate-400 hover:text-slate-200 font-medium flex items-center justify-between">
                  <span>View PromQL Trigger Query</span>
                  <span className="text-[10px] text-cyan-400 group-open:rotate-180 transition-transform">▼</span>
                </summary>
                <div className="mt-2 font-mono text-[11px] text-cyan-300 break-all bg-slate-900 p-2 rounded border border-slate-800 leading-relaxed">
                  {autoscalingStatus?.query ||
                    'max(predicted_http_work_rps) and (max(prediction_timestamp_seconds) > 0) and ((time() - max(prediction_timestamp_seconds)) < 45)'}
                </div>
              </details>
            </div>
          </div>

          <div className="mt-4 pt-4 border-t border-slate-800 flex justify-end">
            <button
              onClick={() => onNavigate('autoscaling')}
              className="text-xs text-cyan-400 hover:text-cyan-300 font-medium flex items-center gap-1"
            >
              Inspect ScaledObject & HPA <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Cluster Pods Quick Status Summary */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 backdrop-blur-sm flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <Server className="w-5 h-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-slate-200 flex items-center gap-2">
              <span>Target Application Pods</span>
              <span className="text-slate-400 font-normal">({k8sStatus?.deployment_name || 'target-app'})</span>
              <StatusBadge
                status={k8sStatus?.cluster_connected ? 'healthy' : 'warning'}
                label={`${currentReplicas} / ${maxReplicas} Pods Active`}
                size="xs"
              />
            </div>
            <div className="text-[11px] text-slate-400 mt-0.5">
              Available: <span className="text-emerald-400 font-semibold">{k8sStatus?.available_replicas ?? currentReplicas}</span> ready &bull; CPU: <span className="text-cyan-400 font-semibold">{cpuMillicores}m</span> &bull; Memory: <span className="text-slate-300 font-semibold">{memFormatted} MiB</span>
            </div>
          </div>
        </div>
        <button
          onClick={() => onNavigate('kubernetes')}
          className="text-xs text-cyan-400 hover:text-cyan-300 font-medium flex items-center gap-1 self-start sm:self-auto shrink-0 px-3 py-1.5 rounded-lg bg-slate-800/60 border border-slate-700/60 hover:bg-slate-800 transition-colors"
        >
          View Pods & Resource Limits <ArrowUpRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
