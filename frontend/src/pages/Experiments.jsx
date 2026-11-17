import React, { useState } from 'react';
import {
  BarChart3,
  TrendingDown,
  Clock,
  Cpu,
  Layers,
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  FileSpreadsheet,
  Info,
} from 'lucide-react';
import StatusBadge from '../components/StatusBadge';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts';

export default function Experiments({ comparisonData }) {
  const [activeProfileTab, setActiveProfileTab] = useState('stable');

  const profiles = comparisonData?.profiles || [];
  const currentProfile = profiles.find((p) => p.profile === activeProfileTab) || profiles[0];

  const hpa = currentProfile?.hpa;
  const predictive = currentProfile?.predictive;

  // Comparison metrics for the chart (if predictive exists or baseline-only)
  const chartData = [
    {
      metric: 'P95 Latency (ms)',
      HPA: hpa?.response_time_p95_ms !== undefined ? Number(hpa.response_time_p95_ms.toFixed(1)) : 0,
      Predictive: predictive?.response_time_p95_ms !== undefined ? Number(predictive.response_time_p95_ms.toFixed(1)) : null,
    },
    {
      metric: 'P99 Latency (ms)',
      HPA: hpa?.response_time_p99_ms !== undefined ? Number(hpa.response_time_p99_ms.toFixed(1)) : 0,
      Predictive: predictive?.response_time_p99_ms !== undefined ? Number(predictive.response_time_p99_ms.toFixed(1)) : null,
    },
    {
      metric: 'Scaling Delay (s)',
      HPA: hpa?.scaling_delay_seconds !== undefined ? Number(hpa.scaling_delay_seconds.toFixed(1)) : 0,
      Predictive: predictive?.scaling_delay_seconds !== undefined ? Number(predictive.scaling_delay_seconds.toFixed(1)) : null,
    },
  ];

  const resourceChartData = [
    {
      metric: 'Avg CPU %',
      HPA: hpa?.avg_cpu_percent !== undefined ? Number(hpa.avg_cpu_percent.toFixed(1)) : 0,
      Predictive: predictive?.avg_cpu_percent !== undefined ? Number(predictive.avg_cpu_percent.toFixed(1)) : null,
    },
    {
      metric: 'Max Replicas',
      HPA: hpa?.max_replicas !== undefined ? hpa.max_replicas : 0,
      Predictive: predictive?.max_replicas !== undefined ? predictive.max_replicas : null,
    },
    {
      metric: 'Core-Seconds',
      HPA: hpa?.total_core_seconds !== undefined ? Number(hpa.total_core_seconds.toFixed(0)) : 0,
      Predictive: predictive?.total_core_seconds !== undefined ? Number(predictive.total_core_seconds.toFixed(0)) : null,
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <BarChart3 className="w-6 h-6" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-slate-100">
              Autoscaling Benchmark & Comparative Evaluation
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Empirical side-by-side performance comparison: Reactive CPU HPA vs Proactive Predictive KEDA
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-xs font-mono flex items-center gap-1.5 text-slate-300">
            <FileSpreadsheet className="w-4 h-4 text-emerald-400" />
            <span>Dataset: </span>
            <span className="text-emerald-400 font-semibold">experiments/hpa_*.csv</span>
          </div>
        </div>
      </div>

      {/* Profile Selector Tabs */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 border-b border-slate-800">
        {profiles.map((p) => {
          const isSelected = p.profile === (currentProfile?.profile || 'stable');
          return (
            <button
              key={p.profile}
              onClick={() => setActiveProfileTab(p.profile)}
              className={`px-4 py-2 text-xs font-medium rounded-lg transition-colors whitespace-nowrap flex items-center gap-2 ${
                isSelected
                  ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              }`}
            >
              <span className="uppercase font-mono">{p.profile}</span>
              {p.predictive ? (
                <span className="w-2 h-2 rounded-full bg-emerald-400" title="Both runs available" />
              ) : (
                <span className="text-[10px] text-slate-400 font-normal">(HPA baseline)</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Status Alert if Predictive Run is Pending */}
      {!predictive && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 flex items-start gap-3">
          <Info className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-amber-200/90 leading-relaxed">
            <strong className="text-amber-300">Predictive Benchmark Data Pending:</strong> Baseline CPU HPA data for{' '}
            <code className="text-cyan-300 font-mono font-semibold uppercase">
              {currentProfile?.profile}
            </code>{' '}
            is loaded from <code className="text-slate-200 font-mono">experiments/hpa_{currentProfile?.profile}.csv</code>.
            The predictive dataset (<code className="text-slate-300 font-mono">predictive_{currentProfile?.profile}.csv</code>) has not yet been collected. In accordance with rigorous scientific evaluation, no synthetic values are fabricated.
          </div>
        </div>
      )}

      {/* Side-by-Side Metric Comparison Table */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <h3 className="text-sm font-semibold text-slate-100 mb-4 flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-cyan-400" />
          Evaluation Matrix: {currentProfile?.profile.toUpperCase()} Profile ({currentProfile?.description})
        </h3>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="border-b border-slate-800 text-slate-400">
              <tr>
                <th className="pb-3 px-3">Performance Metric</th>
                <th className="pb-3 px-3 text-cyan-400">Reactive CPU HPA (Baseline)</th>
                <th className="pb-3 px-3 text-purple-400">Predictive KEDA (Prophet)</th>
                <th className="pb-3 px-3">Variance / Improvement</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Response Time P95
                </td>
                <td className="py-3 px-3 text-cyan-300 font-bold">
                  {hpa?.response_time_p95_ms !== undefined ? `${hpa.response_time_p95_ms.toFixed(1)} ms` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive?.response_time_p95_ms !== undefined ? `${predictive.response_time_p95_ms.toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span className={predictive.response_time_p95_ms < hpa.response_time_p95_ms ? 'text-emerald-400' : 'text-rose-400'}>
                      {((predictive.response_time_p95_ms - hpa.response_time_p95_ms) / hpa.response_time_p95_ms * 100).toFixed(1)}%
                    </span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>

              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Response Time P99
                </td>
                <td className="py-3 px-3 text-cyan-300 font-bold">
                  {hpa?.response_time_p99_ms !== undefined ? `${hpa.response_time_p99_ms.toFixed(1)} ms` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive?.response_time_p99_ms !== undefined ? `${predictive.response_time_p99_ms.toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span className={predictive.response_time_p99_ms < hpa.response_time_p99_ms ? 'text-emerald-400' : 'text-rose-400'}>
                      {((predictive.response_time_p99_ms - hpa.response_time_p99_ms) / hpa.response_time_p99_ms * 100).toFixed(1)}%
                    </span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>

              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  SLO Violation Rate (&gt; 200ms)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa?.slo_violation_percent !== undefined ? `${hpa.slo_violation_percent.toFixed(2)}%` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive?.slo_violation_percent !== undefined ? `${predictive.slo_violation_percent.toFixed(2)}%` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span className={predictive.slo_violation_percent < hpa.slo_violation_percent ? 'text-emerald-400' : 'text-rose-400'}>
                      {(predictive.slo_violation_percent - hpa.slo_violation_percent).toFixed(2)}%
                    </span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>

              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Scaling Reaction Delay
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa?.scaling_delay_seconds !== undefined ? `${hpa.scaling_delay_seconds.toFixed(1)} s` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive?.scaling_delay_seconds !== undefined ? `${predictive.scaling_delay_seconds.toFixed(1)} s` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span className="text-emerald-400 font-semibold">
                      {(hpa.scaling_delay_seconds - predictive.scaling_delay_seconds).toFixed(1)}s faster
                    </span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>

              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Average CPU Utilization
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa?.avg_cpu_percent !== undefined ? `${hpa.avg_cpu_percent.toFixed(1)}%` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive?.avg_cpu_percent !== undefined ? `${predictive.avg_cpu_percent.toFixed(1)}%` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span>{(predictive.avg_cpu_percent - hpa.avg_cpu_percent).toFixed(1)}%</span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>

              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Max Pod Replicas
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa?.max_replicas !== undefined ? `${hpa.max_replicas} pods` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive?.max_replicas !== undefined ? `${predictive.max_replicas} pods` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span>{predictive.max_replicas - hpa.max_replicas} pods</span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>

              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Total Resource Cost (Core-Seconds)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa?.total_core_seconds !== undefined ? `${hpa.total_core_seconds.toFixed(0)} core-s` : 'N/A'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive?.total_core_seconds !== undefined ? `${predictive.total_core_seconds.toFixed(0)} core-s` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa ? (
                    <span className={predictive.total_core_seconds <= hpa.total_core_seconds ? 'text-emerald-400' : 'text-amber-400'}>
                      {((predictive.total_core_seconds - hpa.total_core_seconds) / hpa.total_core_seconds * 100).toFixed(1)}%
                    </span>
                  ) : (
                    <span className="text-slate-400 font-sans text-[11px]">Awaiting predictive run</span>
                  )}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Graphical Benchmark Visualizations */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Latency & Delay Chart */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
          <h3 className="text-sm font-semibold text-slate-100 mb-2">
            Response Time & Scaling Latency
          </h3>
          <p className="text-xs text-slate-400 mb-4">
            Lower is better. Demonstrates prevention of tail-latency spikes.
          </p>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="metric" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                />
                <Legend wrapperStyle={{ fontSize: '12px' }} />
                <Bar dataKey="HPA" fill="#06b6d4" name="Baseline CPU HPA" radius={[4, 4, 0, 0]} />
                {predictive && (
                  <Bar dataKey="Predictive" fill="#a855f7" name="Predictive KEDA" radius={[4, 4, 0, 0]} />
                )}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Resource Consumption Chart */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
          <h3 className="text-sm font-semibold text-slate-100 mb-2">
            Resource Cost & Cluster Utilization
          </h3>
          <p className="text-xs text-slate-400 mb-4">
            Core-seconds and average CPU utilization across the experiment duration.
          </p>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={resourceChartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="metric" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                />
                <Legend wrapperStyle={{ fontSize: '12px' }} />
                <Bar dataKey="HPA" fill="#06b6d4" name="Baseline CPU HPA" radius={[4, 4, 0, 0]} />
                {predictive && (
                  <Bar dataKey="Predictive" fill="#a855f7" name="Predictive KEDA" radius={[4, 4, 0, 0]} />
                )}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
