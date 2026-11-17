import React from 'react';
import {
  ShieldCheck,
  Gauge,
  Clock,
  Layers,
  Sliders,
  Settings,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';

export default function Autoscaling({ autoscalingStatus, metrics, k8sStatus }) {
  const currentVal =
    autoscalingStatus?.current_predicted_load !== undefined &&
    autoscalingStatus?.current_predicted_load !== null
      ? Number(autoscalingStatus.current_predicted_load).toFixed(2)
      : metrics?.forecast?.yhat !== undefined
      ? Number(metrics.forecast.yhat).toFixed(2)
      : '--';

  const freshnessAge =
    autoscalingStatus?.prediction_age_seconds !== null &&
    autoscalingStatus?.prediction_age_seconds !== undefined
      ? Math.round(autoscalingStatus.prediction_age_seconds)
      : null;

  const isGateValid = autoscalingStatus?.is_fresh ?? (freshnessAge !== null && freshnessAge < 45);

  const readyStatus = autoscalingStatus?.ready_status ?? true;
  const activeStatus = autoscalingStatus?.active_status ?? isGateValid;
  const fallbackActive = autoscalingStatus?.fallback_active ?? false;

  const activeQuery =
    autoscalingStatus?.query ||
    'max(predicted_http_work_rps) and (max(prediction_timestamp_seconds) > 0) and ((time() - max(prediction_timestamp_seconds)) < 45)';

  const metricThreshold = autoscalingStatus?.metric_threshold || 10.0;
  const minReplicas = autoscalingStatus?.min_replicas || k8sStatus?.min_replicas || 1;
  const maxReplicas = autoscalingStatus?.max_replicas || k8sStatus?.max_replicas || 5;

  return (
    <div className="space-y-6">
      {/* Top Header Card */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Gauge className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-slate-100">
                KEDA ScaledObject: {autoscalingStatus?.scaledobject_name || 'target-app-predictive-scaledobject'}
              </h2>
              {/* ScaledObject READY Status */}
              <StatusBadge
                status={readyStatus ? 'healthy' : 'error'}
                label={readyStatus ? 'READY' : 'NOT READY'}
                size="xs"
              />
              {/* ScaledObject ACTIVE Status */}
              <StatusBadge
                status={activeStatus ? 'active' : 'warning'}
                label={activeStatus ? 'ACTIVE' : 'INACTIVE'}
                size="xs"
              />
              {/* ScaledObject FALLBACK Status */}
              <StatusBadge
                status={fallbackActive ? 'fallback' : 'healthy'}
                label={fallbackActive ? 'FALLBACK ACTIVE' : 'FALLBACK STANDBY'}
                size="xs"
              />
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Associated HPA:{' '}
              <span className="font-mono text-cyan-400">
                {autoscalingStatus?.hpa_name || 'keda-hpa-target-app-predictive-scaledobject'}
              </span>{' '}
              &bull; Target:{' '}
              <span className="font-mono text-slate-300">
                {autoscalingStatus?.target_deployment || 'target-app'}
              </span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-xs font-mono">
            <span className="text-slate-400">State: </span>
            <span className="text-cyan-400 font-semibold uppercase">
              {autoscalingStatus?.scaledobject_state || 'Active'}
            </span>
          </div>
        </div>
      </div>

      {/* KPI Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Predicted RPS Metric"
          value={currentVal}
          unit="req/s"
          subtitle="Queried live by KEDA scaler"
          icon={Gauge}
          variant="cyan"
        />
        <MetricCard
          title="Scaler Target Threshold"
          value={metricThreshold}
          unit="RPS / pod"
          subtitle="AverageValue metric type"
          icon={Sliders}
          variant="purple"
        />
        <MetricCard
          title="Prediction Freshness"
          value={freshnessAge !== null ? `${freshnessAge}s` : 'N/A'}
          subtitle={
            isGateValid
              ? 'Within 45-second freshness window'
              : 'Stale query: returns empty vector'
          }
          icon={Clock}
          variant={isGateValid ? 'emerald' : 'amber'}
        />
        <MetricCard
          title="Replica Boundaries"
          value={`${minReplicas} - ${maxReplicas}`}
          unit="pods"
          subtitle={`Min: ${minReplicas} pod &bull; Max: ${maxReplicas} pods`}
          icon={Layers}
          variant="emerald"
        />
      </div>

      {/* Visual Pipeline Flowchart */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <h3 className="text-sm font-semibold text-slate-100 mb-4 flex items-center gap-2">
          <Layers className="w-4 h-4 text-cyan-400" />
          Predictive Autoscaling Pipeline Architecture
        </h3>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
            <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider block mb-1">Step 1</span>
            <div className="text-xs font-semibold text-slate-200">Prometheus Scrapes</div>
            <p className="text-[11px] text-slate-400 mt-1">
              Extracts incoming RPS from target-app pods
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
            <span className="text-[10px] font-mono text-purple-400 uppercase tracking-wider block mb-1">Step 2</span>
            <div className="text-xs font-semibold text-slate-200">Prophet Backend</div>
            <p className="text-[11px] text-slate-400 mt-1">
              Fits time series &amp; exports predicted_http_work_rps gauge
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
            <span className="text-[10px] font-mono text-emerald-400 uppercase tracking-wider block mb-1">Step 3</span>
            <div className="text-xs font-semibold text-slate-200">Freshness Gate</div>
            <p className="text-[11px] text-slate-400 mt-1">
              Enforces 45s cutoff timestamp to prevent stale scaling
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
            <span className="text-[10px] font-mono text-amber-400 uppercase tracking-wider block mb-1">Step 4</span>
            <div className="text-xs font-semibold text-slate-200">KEDA Scaler</div>
            <p className="text-[11px] text-slate-400 mt-1">
              Polls every 5s &amp; evaluates 10 RPS/pod AverageValue threshold
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80">
            <span className="text-[10px] font-mono text-rose-400 uppercase tracking-wider block mb-1">Step 5</span>
            <div className="text-xs font-semibold text-slate-200">Kubernetes HPA</div>
            <p className="text-[11px] text-slate-400 mt-1">
              Adjusts target-app replicas: 0s up-stabilization, 15s down
            </p>
          </div>
        </div>
      </div>

      {/* Freshness Gate & Fallback Specification Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Freshness Gate Card */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
              <Clock className="w-4 h-4 text-emerald-400" />
              KEDA Freshness Gate Validation
            </h3>
            <span
              className={`text-xs px-2 py-0.5 rounded font-mono ${
                isGateValid
                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                  : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
              }`}
            >
              {isGateValid ? 'Gate Valid (<45s)' : 'Gate Expired'}
            </span>
          </div>

          <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
            <div className="text-xs text-slate-400 mb-1">
              Live PromQL Trigger Query (Retrieved from ScaledObject Manifest):
            </div>
            <div className="font-mono text-xs text-cyan-300 break-all bg-slate-900 p-2.5 rounded border border-slate-800 leading-relaxed">
              {activeQuery}
            </div>
          </div>

          <div className="space-y-2 text-xs text-slate-300 leading-relaxed">
            <p>
              <strong>Why this is essential:</strong> Without a timestamp gate, if the forecasting backend stops updating, Prometheus holds the last gauge value indefinitely. KEDA would continuously scale based on that stale value.
            </p>
            <p>
              <strong>How it works:</strong> If <code className="text-cyan-400 font-mono">time() - max(prediction_timestamp_seconds) &gt;= 45</code>, the PromQL query evaluates to an empty vector, signaling to KEDA that live predictions are unavailable.
            </p>
          </div>
        </div>

        {/* Fallback Configuration Card */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-purple-400" />
              Automated Fallback Protection
            </h3>
            <span
              className={`text-xs px-2 py-0.5 rounded font-mono ${
                fallbackActive
                  ? 'bg-amber-500/10 text-amber-400 border border-amber-500/30 animate-pulse'
                  : 'bg-slate-800 text-slate-400'
              }`}
            >
              {fallbackActive ? 'Active' : 'Standby'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 font-mono text-xs">
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
              <span className="text-slate-400 font-sans block mb-1">Failure Threshold</span>
              <span className="text-slate-100 text-base font-bold">
                {autoscalingStatus?.fallback_threshold || 3}
              </span>
              <span className="text-slate-400 font-sans block text-[11px] mt-0.5">Consecutive query fails</span>
            </div>
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
              <span className="text-slate-400 font-sans block mb-1">Fallback Replicas</span>
              <span className="text-amber-400 text-base font-bold">
                {autoscalingStatus?.fallback_replicas || 2}
              </span>
              <span className="text-slate-400 font-sans block text-[11px] mt-0.5">Guaranteed safe pods</span>
            </div>
          </div>

          <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/80 text-xs text-slate-300 leading-relaxed">
            <p>
              When Prometheus or the forecasting backend experiences downtime, KEDA activates fallback after 3 failed polling cycles (15s), pinning the deployment to <strong>2 replicas</strong> to prevent outages.
            </p>
          </div>
        </div>
      </div>

      {/* HPA Behavior Specifications */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <h3 className="text-sm font-semibold text-slate-100 mb-3 flex items-center gap-2">
          <Settings className="w-4 h-4 text-slate-400" />
          HPA Scaling Behavior Rules (Scale-Up &amp; Scale-Down)
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
          <div className="p-3.5 bg-slate-950/80 rounded-lg border border-slate-800 space-y-2">
            <div className="text-emerald-400 font-sans font-semibold flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Scale-Up Policy (Aggressive Predictive Scale)
            </div>
            <div className="text-slate-300">
              <span className="text-slate-400">stabilizationWindowSeconds:</span> 0 (Immediate reaction)
            </div>
            <div className="text-slate-300">
              <span className="text-slate-400">policy:</span> Up to 4 pods every 15 seconds
            </div>
          </div>
          <div className="p-3.5 bg-slate-950/80 rounded-lg border border-slate-800 space-y-2">
            <div className="text-cyan-400 font-sans font-semibold flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5" />
              Scale-Down Policy (Controlled Cooldown)
            </div>
            <div className="text-slate-300">
              <span className="text-slate-400">stabilizationWindowSeconds:</span> 15 (Smooth recovery)
            </div>
            <div className="text-slate-300">
              <span className="text-slate-400">policy:</span> Up to 100% allowed scale-down per 15s window
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
