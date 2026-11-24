import React from 'react';
import { RefreshCw, Activity, Database, Shield } from 'lucide-react';
import StatusBadge from './StatusBadge';
import { formatToIstTime } from '../utils/timezone';

export default function Navbar({
  activeTab,
  backendHealthy,
  prometheusHealthy,
  autoscalingStatus,
  lastUpdated,
  isRefreshing,
  onRefresh,
}) {
  const titles = {
    overview: { title: 'Overview Dashboard', desc: 'Real-time metrics, load predictions & cluster utilization' },
    forecasting: { title: 'Workload Forecasting', desc: 'Prophet time-series load prediction & uncertainty envelope' },
    kubernetes: { title: 'Kubernetes Cluster & Pods', desc: 'Pod resources, deployment replicas & node metrics' },
    autoscaling: { title: 'Predictive Autoscaling (KEDA)', desc: 'Prophet-driven ScaledObject, query freshness gate & fallback' },
    'cpu-hpa': { title: 'Traditional CPU HPA (Baseline)', desc: 'Resource-based autoscaling evaluation, 50% CPU target & scaling conditions' },
    workload: { title: 'Workload Generator', desc: 'Locust benchmark profile executor (bursty, gradual, periodic)' },
    experiments: { title: 'Benchmark & Evaluation', desc: 'Baseline CPU HPA vs Predictive KEDA comparison' },
  };

  const current = titles[activeTab] || titles.overview;

  return (
    <header className="min-h-16 py-3.5 bg-slate-900/80 backdrop-blur border-b border-slate-800/80 px-6 flex items-center justify-between sticky top-0 z-20 shrink-0">
      <div className="min-w-0 pr-4">
        <h2 className="text-base font-semibold text-slate-100">{current.title}</h2>
        <p className="text-xs text-slate-400 hidden sm:block leading-relaxed">{current.desc}</p>
      </div>

      <div className="flex items-center gap-4">
        {/* Status Indicators */}
        <div className="hidden md:flex items-center gap-2">
          {/* Backend Status */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-800/50 border border-slate-700/50">
            <Activity className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs text-slate-400">Backend:</span>
            <StatusBadge
              status={backendHealthy ? 'online' : 'offline'}
              label={backendHealthy ? 'Online' : 'Offline'}
              size="xs"
            />
          </div>

          {/* Prometheus Status */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-800/50 border border-slate-700/50">
            <Database className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs text-slate-400">Prometheus:</span>
            <StatusBadge
              status={prometheusHealthy ? 'healthy' : 'warning'}
              label={prometheusHealthy ? 'Connected' : 'Offline / Mock'}
              size="xs"
            />
          </div>

          {/* Autoscaler Status */}
          {autoscalingStatus && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-800/50 border border-slate-700/50">
              <Shield className="w-3.5 h-3.5 text-slate-400" />
              <span className="text-xs text-slate-400">Autoscaler:</span>
              <StatusBadge
                status={
                  autoscalingStatus.fallback_active
                    ? 'fallback'
                    : (autoscalingStatus.is_fresh ?? autoscalingStatus.freshness_gate_active)
                    ? 'active'
                    : 'warning'
                }
                label={
                  autoscalingStatus.fallback_active
                    ? 'Fallback'
                    : (autoscalingStatus.is_fresh ?? autoscalingStatus.freshness_gate_active)
                    ? 'Active'
                    : 'Stale'
                }
                size="xs"
              />
            </div>
          )}
        </div>

        {/* Refresh button & Timestamp */}
        <div className="flex items-center gap-3 pl-2 border-l border-slate-800">
          {lastUpdated && (
            <span className="text-xs text-slate-400 font-mono hidden lg:inline">
              {formatToIstTime(lastUpdated)} IST
            </span>
          )}
          <button
            onClick={onRefresh}
            disabled={isRefreshing}
            title="Refresh now (auto-polls every 5s)"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 hover:border-slate-600 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin text-cyan-400' : ''}`} />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </div>
    </header>
  );
}
