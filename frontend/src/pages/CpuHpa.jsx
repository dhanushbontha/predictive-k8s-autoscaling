import React from 'react';
import {
  Cpu,
  Sliders,
  Layers,
  Settings,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ShieldCheck,
  ShieldAlert,
  Info,
} from 'lucide-react';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';
import { formatToIstTime } from '../utils/timezone';

export default function CpuHpa({ cpuHpaStatus, k8sStatus }) {
  const isApplied = cpuHpaStatus?.is_applied ?? false;
  const isActiveController = cpuHpaStatus?.is_active_controller ?? false;
  const activeControllerName =
    cpuHpaStatus?.active_controller_name ||
    'KEDA Predictive Autoscaler (keda-hpa-target-app-predictive-scaledobject)';

  const currentCpu =
    cpuHpaStatus?.current_cpu_utilization_percent !== null &&
    cpuHpaStatus?.current_cpu_utilization_percent !== undefined
      ? Number(cpuHpaStatus.current_cpu_utilization_percent).toFixed(1)
      : null;

  const targetCpu = cpuHpaStatus?.target_cpu_utilization_percent ?? 50;
  const currentReplicas =
    cpuHpaStatus?.current_replicas ??
    k8sStatus?.current_replicas ??
    k8sStatus?.available_replicas ??
    1;
  const desiredReplicas =
    cpuHpaStatus?.desired_replicas ??
    k8sStatus?.desired_replicas ??
    currentReplicas;
  const minReplicas = cpuHpaStatus?.min_replicas ?? 1;
  const maxReplicas = cpuHpaStatus?.max_replicas ?? 5;

  const conditions = cpuHpaStatus?.conditions || [];
  const events = cpuHpaStatus?.scaling_events || [];
  const config = cpuHpaStatus?.config || {};

  return (
    <div className="space-y-6">
      {/* Top Header Card */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Cpu className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold text-slate-100">
                Traditional CPU HPA: {cpuHpaStatus?.hpa_name || 'target-app-hpa'}
              </h2>
              {/* Cluster Deployment Status */}
              <StatusBadge
                status={isApplied ? 'ready' : 'idle'}
                label={isApplied ? 'APPLIED IN CLUSTER' : 'STANDBY BASELINE'}
                size="xs"
              />
              {/* Active Authority Badge */}
              <span
                className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
                  isActiveController
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                    : 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                }`}
              >
                {isActiveController ? 'Active Controller' : 'Standby / Manifest Configured'}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Scale Target:{' '}
              <span className="font-mono text-cyan-400">
                apps/v1.Deployment/{cpuHpaStatus?.target_deployment || 'target-app'}
              </span>{' '}
              &bull; Namespace:{' '}
              <span className="font-mono text-slate-300">default</span>
              &bull; Manifest:{' '}
              <span className="font-mono text-slate-300">kubernetes/target-app-hpa.yaml</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-xs font-mono">
            <span className="text-slate-400">Target Metric: </span>
            <span className="text-cyan-400 font-semibold">CPU 50% Avg</span>
          </div>
        </div>
      </div>

      {/* Controller Authority Notice Banner */}
      <div
        className={`p-3.5 rounded-xl border flex items-center justify-between gap-3 text-xs ${
          isActiveController
            ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-200'
            : 'bg-amber-500/10 border-amber-500/30 text-amber-200'
        }`}
      >
        <div className="flex items-center gap-2.5">
          {isActiveController ? (
            <ShieldCheck className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          ) : (
            <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
          )}
          <span>
            {isActiveController
              ? 'Traditional CPU HPA is currently actively scaling Deployment/target-app.'
              : `Standby Baseline: Target-app is managed by ${activeControllerName}. This CPU HPA serves as the comparison baseline.`}
          </span>
        </div>
        <span className="text-[11px] font-mono text-slate-400 shrink-0">
          Target: 50% CPU
        </span>
      </div>

      {/* KPI Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Current CPU Utilization"
          value={currentCpu !== null ? currentCpu : '--'}
          unit="%"
          subtitle={`Target threshold: ${targetCpu}%`}
          icon={Cpu}
          variant={currentCpu !== null && Number(currentCpu) > targetCpu ? 'rose' : 'cyan'}
        />
        <MetricCard
          title="Target CPU Threshold"
          value={targetCpu}
          unit="%"
          subtitle="averageUtilization policy"
          icon={Sliders}
          variant="purple"
        />
        <MetricCard
          title="Current & Desired Replicas"
          value={`${currentReplicas} / ${desiredReplicas}`}
          unit="pods"
          subtitle={`Calculated: ceil(CPU% / 50%)`}
          icon={Layers}
          variant="amber"
        />
        <MetricCard
          title="Replica Boundaries"
          value={`${minReplicas} - ${maxReplicas}`}
          unit="pods"
          subtitle={`Min: ${minReplicas} · Max: ${maxReplicas} pods`}
          icon={ShieldCheck}
          variant="emerald"
        />
      </div>

      {/* Kubernetes HPA Conditions & Health Status */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            Kubernetes CPU HPA Conditions &amp; Health
          </h3>
          <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider">
            {isApplied ? 'Cluster State' : 'Manifest Baseline'}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {conditions.length > 0 ? (
            conditions.map((cond, idx) => (
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
                        : cond.status === 'False'
                        ? 'bg-slate-800 text-slate-400'
                        : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
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
            <div className="col-span-3 text-xs text-slate-400 py-3 text-center">
              Conditions available when applied to Kubernetes cluster.
            </div>
          )}
        </div>
      </div>

      {/* Scaling Events & History */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-3">
        <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
          <Clock className="w-4 h-4 text-slate-400" />
          Kubernetes HPA &amp; Scaling Events Log
        </h3>

        {events.length > 0 ? (
          <div className="divide-y divide-slate-800/60 text-xs font-mono max-h-60 overflow-y-auto">
            {events.map((evt, idx) => (
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
            No active scaling warning events recorded in the cluster.
          </div>
        )}
      </div>

      {/* HPA Scaling & Cooldown Rules */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-3">
        <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
          <Settings className="w-4 h-4 text-slate-400" />
          HPA Scaling &amp; Cooldown Rules
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
          <div className="p-3.5 bg-slate-950/80 rounded-lg border border-slate-800 space-y-1.5">
            <div className="text-emerald-400 font-sans font-semibold flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Scale-Up Policy
            </div>
            <div className="text-slate-300 text-[11px]">
              <span className="text-slate-400">stabilizationWindow: </span>
              <span className="text-cyan-300 font-semibold">{config.scale_up_stabilization_seconds ?? 0}s (Immediate reaction)</span>
            </div>
            <div className="text-slate-300 text-[11px]">
              <span className="text-slate-400">policy: </span>
              <span>Up to 4 Pods per 15s period</span>
            </div>
          </div>

          <div className="p-3.5 bg-slate-950/80 rounded-lg border border-slate-800 space-y-1.5">
            <div className="text-cyan-400 font-sans font-semibold flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5" />
              Scale-Down Policy
            </div>
            <div className="text-slate-300 text-[11px]">
              <span className="text-slate-400">stabilizationWindow: </span>
              <span className="text-cyan-300 font-semibold">{config.scale_down_stabilization_seconds ?? 15}s (Cooldown protection)</span>
            </div>
            <div className="text-slate-300 text-[11px]">
              <span className="text-slate-400">policy: </span>
              <span>Up to 100% allowed scale-down per 15s</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
