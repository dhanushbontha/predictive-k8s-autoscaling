import React, { useState } from 'react';
import {
  BarChart3,
  TrendingDown,
  Clock,
  Cpu,
  Layers,
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  Info,
  Activity,
  Zap,
  Target,
  RefreshCw,
  Download,
  FolderArchive,
  FileDown,
} from 'lucide-react';
import StatusBadge from '../components/StatusBadge';
import api from '../services/api';
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

export default function Experiments({
  comparisonData,
  experimentSource = 'results',
  onNavigate,
  onFetchExperiments,
}) {
  const [activeProfileTab, setActiveProfileTab] = useState('stable');
  const [downloadStatus, setDownloadStatus] = useState({ type: 'idle', message: '' });

  const handleDownloadCsv = async (autoscaler, profile) => {
    try {
      setDownloadStatus({ type: 'loading', message: `Downloading ${autoscaler.toUpperCase()} (${profile}) CSV...` });
      const res = await api.downloadExperimentCsv(autoscaler, profile, experimentSource);
      const blob = new Blob([res.data], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${autoscaler.toLowerCase()}_${profile.toLowerCase()}.csv`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      setDownloadStatus({ type: 'success', message: `Downloaded ${autoscaler.toUpperCase()} (${profile}) CSV successfully.` });
      setTimeout(() => setDownloadStatus({ type: 'idle', message: '' }), 4000);
    } catch (err) {
      let errMsg = `No experiment results available for ${autoscaler.toUpperCase()} (${profile}). Run an experiment first.`;
      if (err.response?.data) {
        try {
          if (err.response.data instanceof Blob) {
            const txt = await err.response.data.text();
            const parsed = JSON.parse(txt);
            if (parsed.detail) errMsg = parsed.detail;
          } else if (err.response.data.detail) {
            errMsg = err.response.data.detail;
          }
        } catch (_) {}
      }
      setDownloadStatus({ type: 'error', message: errMsg });
      setTimeout(() => setDownloadStatus({ type: 'idle', message: '' }), 6000);
    }
  };

  const handleDownloadAll = async () => {
    try {
      setDownloadStatus({ type: 'loading', message: 'Generating and downloading archive of all completed experiments...' });
      const res = await api.downloadAllExperimentsZip(experimentSource);
      const blob = new Blob([res.data], { type: 'application/zip' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `all_completed_experiments_${experimentSource}.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      setDownloadStatus({ type: 'success', message: 'Downloaded all completed experiment results (.zip).' });
      setTimeout(() => setDownloadStatus({ type: 'idle', message: '' }), 4000);
    } catch (err) {
      let errMsg = 'No completed experiment results available to download in experiments/results/. Run an experiment first.';
      if (err.response?.data) {
        try {
          if (err.response.data instanceof Blob) {
            const txt = await err.response.data.text();
            const parsed = JSON.parse(txt);
            if (parsed.detail) errMsg = parsed.detail;
          } else if (err.response.data.detail) {
            errMsg = err.response.data.detail;
          }
        } catch (_) {}
      }
      setDownloadStatus({ type: 'error', message: errMsg });
      setTimeout(() => setDownloadStatus({ type: 'idle', message: '' }), 6000);
    }
  };

  // Workload profiles in canonical order
  const canonicalProfiles = ['stable', 'gradual', 'bursty', 'periodic', 'noisy'];
  const availableKeys =
    comparisonData?.available_profiles ||
    (comparisonData?.comparisons ? Object.keys(comparisonData.comparisons) : canonicalProfiles);

  function getProfileDescription(prof) {
    const descMap = {
      stable: 'Steady user concurrency throughout the run (baseline capacity test)',
      gradual: 'Steps up concurrency progressively across duration (multi-stage scale-up test)',
      bursty: 'Short, high-intensity traffic surges separated by low-load valleys (reaction speed test)',
      periodic: 'Alternates cyclically between low-load and high-load phases (diurnal cycle simulation)',
      noisy: 'Pseudo-random bounded user count variations (realistic chaotic production traffic)',
    };
    return descMap[prof] || 'Workload evaluation profile';
  }

  const profilesList = canonicalProfiles.map((key) => {
    if (comparisonData?.comparisons?.[key]) {
      return comparisonData.comparisons[key];
    }
    return {
      profile: key,
      description: getProfileDescription(key),
      hpa: null,
      predictive: null,
      comparison_summary: null,
    };
  });

  const currentProfile =
    profilesList.find((p) => p.profile.toLowerCase() === activeProfileTab.toLowerCase()) ||
    profilesList[0] ||
    { profile: 'stable', description: '', hpa: null, predictive: null };

  const hpa = currentProfile?.hpa;
  const predictive = currentProfile?.predictive;

  // Helpers to extract metrics cleanly with fallbacks
  const getP95 = (item) => item?.p95_latency_ms ?? item?.response_time_p95_ms;
  const getP99 = (item) => item?.p99_latency_ms ?? item?.response_time_p99_ms;
  const getP50 = (item) => item?.p50_latency_ms ?? item?.response_time_p50_ms;
  const getDelay = (item) => item?.scaling_delay_seconds;
  const getCpuPct = (item) => item?.avg_cpu_percent;
  const getCpuCores = (item) => item?.avg_cpu_cores;
  const getMaxCores = (item) => item?.max_cpu_cores;
  const getMem = (item) => item?.avg_memory_mb;
  const getAvgReps = (item) => item?.avg_replicas;
  const getMaxReps = (item) => item?.max_replicas;
  const getCoreSec = (item) => item?.total_core_seconds;
  const getSloCount = (item) => item?.slo_violations_count;
  const getSloDur = (item) => item?.slo_violation_duration_seconds;
  const getErrors = (item) => item?.error_count ?? 0;
  const getErrorRate = (item) => item?.error_rate_pct ?? 0.0;
  const getMae = (item) => item?.mae;
  const getRmse = (item) => item?.rmse;

  const hasAnyData = hpa !== null || predictive !== null;

  // Chart 1: Latency & Delay Chart (only populate non-null values)
  const chartData = [
    {
      metric: 'P95 Latency (ms)',
      ...(hpa && getP95(hpa) !== undefined ? { 'CPU HPA': Number(getP95(hpa).toFixed(1)) } : {}),
      ...(predictive && getP95(predictive) !== undefined ? { 'Predictive KEDA': Number(getP95(predictive).toFixed(1)) } : {}),
    },
    {
      metric: 'P99 Latency (ms)',
      ...(hpa && getP99(hpa) !== undefined ? { 'CPU HPA': Number(getP99(hpa).toFixed(1)) } : {}),
      ...(predictive && getP99(predictive) !== undefined ? { 'Predictive KEDA': Number(getP99(predictive).toFixed(1)) } : {}),
    },
    {
      metric: 'Scaling Delay (s)',
      ...(hpa && getDelay(hpa) !== undefined && getDelay(hpa) !== null ? { 'CPU HPA': Number(getDelay(hpa).toFixed(1)) } : {}),
      ...(predictive && getDelay(predictive) !== undefined && getDelay(predictive) !== null ? { 'Predictive KEDA': Number(getDelay(predictive).toFixed(1)) } : {}),
    },
  ];

  // Chart 2: Resource Cost & Replicas Chart
  const resourceChartData = [
    {
      metric: 'Avg CPU %',
      ...(hpa && getCpuPct(hpa) !== undefined ? { 'CPU HPA': Number(getCpuPct(hpa).toFixed(1)) } : {}),
      ...(predictive && getCpuPct(predictive) !== undefined ? { 'Predictive KEDA': Number(getCpuPct(predictive).toFixed(1)) } : {}),
    },
    {
      metric: 'Max Replicas',
      ...(hpa && getMaxReps(hpa) !== undefined ? { 'CPU HPA': getMaxReps(hpa) } : {}),
      ...(predictive && getMaxReps(predictive) !== undefined ? { 'Predictive KEDA': getMaxReps(predictive) } : {}),
    },
    {
      metric: 'Core-Seconds (x10)',
      ...(hpa && getCoreSec(hpa) !== undefined ? { 'CPU HPA': Number((getCoreSec(hpa) / 10).toFixed(0)) } : {}),
      ...(predictive && getCoreSec(predictive) !== undefined ? { 'Predictive KEDA': Number((getCoreSec(predictive) / 10).toFixed(0)) } : {}),
    },
  ];

  const handleSourceChange = (newSource) => {
    if (onFetchExperiments) {
      onFetchExperiments(newSource);
    }
  };

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
              Autoscaling Benchmark &amp; Comparative Evaluation
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Empirical side-by-side performance comparison: Reactive CPU HPA vs Proactive Predictive KEDA
            </p>
          </div>
        </div>

        {/* Clean Research Datasets Source Badge */}
        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-xs font-mono flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-cyan-400" />
            <span className="text-slate-400">Dataset Source: </span>
            <span className="text-cyan-400 font-semibold">Clean Research Datasets (experiments/results/)</span>
          </div>
        </div>
      </div>

      {/* Dataset Provenance Banner */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-slate-400">Dataset Source:</span>
          <span className="font-mono text-cyan-400 font-semibold">
            Clean Research Datasets (experiments/results/)
          </span>
          <span className="text-slate-600">&bull;</span>
          <span className="text-slate-400">
            Loads only newly executed experiment runs. Zero fabricated metrics.
          </span>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto flex-wrap">
          <button
            onClick={handleDownloadAll}
            className="text-xs px-2.5 py-1 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 font-medium flex items-center gap-1.5 transition-colors"
            title="Download all genuine completed experiment CSVs as a ZIP archive"
          >
            <FolderArchive className="w-3.5 h-3.5" />
            Download All Results (.ZIP)
          </button>

          {onNavigate && (
            <button
              onClick={() => onNavigate('workload')}
              className="text-xs px-2.5 py-1 rounded-lg bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-medium flex items-center gap-1.5 transition-colors"
            >
              <Zap className="w-3.5 h-3.5" />
              Workload Runner
            </button>
          )}
        </div>
      </div>

      {/* CSV Export & Download Feedback Notification */}
      {downloadStatus.type !== 'idle' && (
        <div
          className={`p-3 rounded-xl border text-xs flex items-center justify-between gap-2.5 transition-all ${
            downloadStatus.type === 'error'
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-300'
              : downloadStatus.type === 'loading'
                ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-300'
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {downloadStatus.type === 'error' && <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0" />}
            {downloadStatus.type === 'loading' && <RefreshCw className="w-4 h-4 text-cyan-400 animate-spin flex-shrink-0" />}
            {downloadStatus.type === 'success' && <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />}
            <span>{downloadStatus.message}</span>
          </div>
          <button
            onClick={() => setDownloadStatus({ type: 'idle', message: '' })}
            className="text-slate-400 hover:text-slate-200 text-xs px-1.5"
          >
            &times;
          </button>
        </div>
      )}

      {/* Profile Selector Tabs (Supports all 5 patterns) */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 border-b border-slate-800">
        {profilesList.map((p) => {
          const isSelected = p.profile.toLowerCase() === activeProfileTab.toLowerCase();
          const hasBoth = p.hpa !== null && p.predictive !== null;
          const hasHpa = p.hpa !== null;
          const hasPred = p.predictive !== null;

          return (
            <button
              key={p.profile}
              onClick={() => setActiveProfileTab(p.profile)}
              className={`px-4 py-2 text-xs font-medium rounded-lg transition-colors whitespace-nowrap flex items-center gap-2 ${isSelected
                ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 font-semibold'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
            >
              <span className="uppercase font-mono">{p.profile}</span>
              {hasBoth ? (
                <span className="w-2 h-2 rounded-full bg-emerald-400" title="Both HPA & Predictive datasets available" />
              ) : hasHpa ? (
                <span className="text-[10px] text-cyan-400 font-normal">(HPA ready)</span>
              ) : hasPred ? (
                <span className="text-[10px] text-purple-400 font-normal">(KEDA ready)</span>
              ) : (
                <span className="text-[10px] text-slate-500 font-normal">(Awaiting data)</span>
              )}
            </button>
          );
        })}
      </div>

      {/* 3 Prominent Autoscaler Benchmark Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Card 1: Traditional CPU HPA */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 backdrop-blur-sm space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-cyan-400 flex items-center gap-1.5">
              <Cpu className="w-4 h-4" />
              Traditional CPU HPA
            </span>
            <StatusBadge
              variant={hpa ? 'success' : 'neutral'}
              label={hpa ? 'Dataset Ready' : 'Awaiting data'}
            />
          </div>
          {hpa ? (
            <div className="space-y-3">
              <div>
                <div className="text-lg font-bold text-slate-100 font-mono">
                  {getP95(hpa) !== undefined ? `${getP95(hpa).toFixed(1)} ms` : '--'}
                </div>
                <p className="text-[11px] text-slate-400 mt-1">
                  P95 Latency &bull; {hpa.total_samples} samples ({hpa.duration_seconds}s)
                </p>
              </div>
              <button
                onClick={() => handleDownloadCsv('hpa', currentProfile?.profile)}
                className="w-full text-xs py-1.5 px-3 rounded-lg bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-medium flex items-center justify-center gap-1.5 transition-colors"
                title={`Download experiments/results/hpa_${currentProfile?.profile}.csv`}
              >
                <Download className="w-3.5 h-3.5" />
                Download HPA CSV ({currentProfile?.profile})
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <div className="text-sm font-medium text-slate-400">
                  Awaiting experiment data
                </div>
                <p className="text-[11px] text-slate-500 font-mono mt-1">
                  Target: experiments/results/hpa_{currentProfile?.profile}.csv
                </p>
              </div>
              <button
                disabled
                className="w-full text-xs py-1.5 px-3 rounded-lg bg-slate-950 border border-slate-800 text-slate-500 font-medium flex items-center justify-center gap-1.5 cursor-not-allowed opacity-60"
                title="No completed experiment data available yet for CPU HPA"
              >
                <Download className="w-3.5 h-3.5" />
                No CSV Available
              </button>
            </div>
          )}
        </div>

        {/* Card 2: Predictive KEDA */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 backdrop-blur-sm space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-purple-400 flex items-center gap-1.5">
              <Zap className="w-4 h-4" />
              Predictive KEDA (Prophet)
            </span>
            <StatusBadge
              variant={predictive ? 'success' : 'neutral'}
              label={predictive ? 'Dataset Ready' : 'Awaiting data'}
            />
          </div>
          {predictive ? (
            <div className="space-y-3">
              <div>
                <div className="text-lg font-bold text-slate-100 font-mono">
                  {getP95(predictive) !== undefined ? `${getP95(predictive).toFixed(1)} ms` : '--'}
                </div>
                <p className="text-[11px] text-slate-400 mt-1">
                  P95 Latency &bull; {predictive.total_samples} samples ({predictive.duration_seconds}s)
                </p>
              </div>
              <button
                onClick={() => handleDownloadCsv('predictive', currentProfile?.profile)}
                className="w-full text-xs py-1.5 px-3 rounded-lg bg-purple-500/10 hover:bg-purple-500/20 text-purple-400 border border-purple-500/30 font-medium flex items-center justify-center gap-1.5 transition-colors"
                title={`Download experiments/results/predictive_${currentProfile?.profile}.csv`}
              >
                <Download className="w-3.5 h-3.5" />
                Download KEDA CSV ({currentProfile?.profile})
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <div className="text-sm font-medium text-slate-400">
                  Awaiting experiment data
                </div>
                <p className="text-[11px] text-slate-500 font-mono mt-1">
                  Target: experiments/results/predictive_{currentProfile?.profile}.csv
                </p>
              </div>
              <button
                disabled
                className="w-full text-xs py-1.5 px-3 rounded-lg bg-slate-950 border border-slate-800 text-slate-500 font-medium flex items-center justify-center gap-1.5 cursor-not-allowed opacity-60"
                title="No completed experiment data available yet for Predictive KEDA"
              >
                <Download className="w-3.5 h-3.5" />
                No CSV Available
              </button>
            </div>
          )}
        </div>

        {/* Card 3: Research Comparison State */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 backdrop-blur-sm space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
              <Target className="w-4 h-4 text-emerald-400" />
              Empirical Comparison
            </span>
            <StatusBadge
              variant={hpa && predictive ? 'success' : 'neutral'}
              label={hpa && predictive ? 'Comparison Ready' : 'No results available yet'}
            />
          </div>
          {hpa && predictive && getP95(hpa) && getP95(predictive) ? (
            <div>
              <div className="text-lg font-bold text-emerald-400 font-mono">
                {(((getP95(hpa) - getP95(predictive)) / getP95(hpa)) * 100).toFixed(1)}% P95 Delta
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                {getSloCount(hpa) - getSloCount(predictive)} SLO violations prevented
              </p>
            </div>
          ) : (
            <div>
              <div className="text-sm font-medium text-slate-400">
                No results available yet
              </div>
              <p className="text-[11px] text-slate-500 mt-1">
                {!hpa && !predictive
                  ? 'Awaiting both CPU HPA and KEDA datasets'
                  : !hpa
                    ? 'Awaiting CPU HPA dataset to compare'
                    : 'Awaiting Predictive KEDA dataset to compare'}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Side-by-Side Comprehensive Metric Comparison Table */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-cyan-400" />
            Empirical Evaluation Matrix: {currentProfile?.profile.toUpperCase()} Pattern
          </h3>
          <span className="text-xs text-slate-400 font-mono">
            {currentProfile?.description}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="border-b border-slate-800 text-slate-400">
              <tr>
                <th className="pb-3 px-3">Evaluation Metric</th>
                <th className="pb-3 px-3 text-cyan-400">Traditional CPU HPA (Baseline)</th>
                <th className="pb-3 px-3 text-purple-400">Predictive KEDA (Prophet)</th>
                <th className="pb-3 px-3">Improvement / Delta</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {/* P95 Response Time */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Response Time P95
                </td>
                <td className="py-3 px-3 text-cyan-300 font-bold">
                  {hpa && getP95(hpa) !== undefined ? `${getP95(hpa).toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive && getP95(predictive) !== undefined ? `${getP95(predictive).toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa && getP95(hpa) && getP95(predictive) ? (
                    <span className={getP95(predictive) < getP95(hpa) ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
                      {(((getP95(hpa) - getP95(predictive)) / getP95(hpa)) * 100).toFixed(1)}% improvement
                    </span>
                  ) : (
                    <span className="text-slate-500 font-sans text-[11px]">--</span>
                  )}
                </td>
              </tr>

              {/* P99 Response Time */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Response Time P99 (Tail Latency)
                </td>
                <td className="py-3 px-3 text-cyan-300 font-bold">
                  {hpa && getP99(hpa) !== undefined ? `${getP99(hpa).toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive && getP99(predictive) !== undefined ? `${getP99(predictive).toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa && getP99(hpa) && getP99(predictive) ? (
                    <span className={getP99(predictive) < getP99(hpa) ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
                      {(((getP99(hpa) - getP99(predictive)) / getP99(hpa)) * 100).toFixed(1)}% improvement
                    </span>
                  ) : (
                    <span className="text-slate-500 font-sans text-[11px]">--</span>
                  )}
                </td>
              </tr>

              {/* P50 Median Latency */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Median Latency (P50)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa && getP50(hpa) !== undefined ? `${getP50(hpa).toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive && getP50(predictive) !== undefined ? `${getP50(predictive).toFixed(1)} ms` : '--'}
                </td>
                <td className="py-3 px-3 text-slate-400 font-sans text-[11px]">
                  {predictive && hpa && getP50(hpa) && getP50(predictive)
                    ? `${(getP50(hpa) - getP50(predictive)).toFixed(1)} ms`
                    : '--'}
                </td>
              </tr>

              {/* Request Failures */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Request Failures &amp; Error Rate
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa ? `${getErrors(hpa)} errors (${getErrorRate(hpa).toFixed(2)}%)` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive ? `${getErrors(predictive)} errors (${getErrorRate(predictive).toFixed(2)}%)` : '--'}
                </td>
                <td className="py-3 px-3">
                  {hpa || predictive ? (
                    <span className="text-emerald-400 font-semibold">0 HTTP 5xx errors</span>
                  ) : (
                    <span className="text-slate-500 font-sans text-[11px]">--</span>
                  )}
                </td>
              </tr>

              {/* SLO Violations */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  SLO Violations (Threshold: &gt; 200ms)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa && getSloCount(hpa) !== undefined ? `${getSloCount(hpa)} violations (${getSloDur(hpa).toFixed(1)}s)` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive && getSloCount(predictive) !== undefined ? `${getSloCount(predictive)} violations (${getSloDur(predictive).toFixed(1)}s)` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa && getSloCount(hpa) !== undefined && getSloCount(predictive) !== undefined ? (
                    <span className="text-emerald-400 font-semibold">
                      {getSloCount(hpa) - getSloCount(predictive)} violations prevented
                    </span>
                  ) : (
                    <span className="text-slate-500 font-sans text-[11px]">--</span>
                  )}
                </td>
              </tr>

              {/* Scaling Reaction Delay */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Scaling Reaction Delay
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa && getDelay(hpa) !== undefined && getDelay(hpa) !== null ? `${getDelay(hpa).toFixed(1)} s` : (hpa ? 'Not observed' : '--')}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive && getDelay(predictive) !== undefined && getDelay(predictive) !== null ? `${getDelay(predictive).toFixed(1)} s` : (predictive ? 'Not observed' : '--')}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa && getDelay(hpa) !== null && getDelay(predictive) !== null ? (
                    <span className="text-emerald-400 font-semibold">
                      {(getDelay(hpa) - getDelay(predictive)).toFixed(1)}s faster reaction
                    </span>
                  ) : (
                    <span className="text-slate-500 font-sans text-[11px]">--</span>
                  )}
                </td>
              </tr>

              {/* Average CPU Utilization */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Average CPU Utilization (Cores / %)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa && getCpuCores(hpa) !== undefined ? `${getCpuCores(hpa).toFixed(4)} cores (${getCpuPct(hpa)?.toFixed(1) || '0.0'}%)` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive && getCpuCores(predictive) !== undefined ? `${getCpuCores(predictive).toFixed(4)} cores (${getCpuPct(predictive)?.toFixed(1) || '0.0'}%)` : '--'}
                </td>
                <td className="py-3 px-3 text-slate-400 font-sans text-[11px]">
                  {hpa && getMaxCores(hpa) ? `Peak: ${getMaxCores(hpa).toFixed(4)} cores` : '--'}
                </td>
              </tr>

              {/* Memory Working Set */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Working Set Memory (Avg)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa && getMem(hpa) !== undefined ? `${getMem(hpa).toFixed(1)} MiB` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive && getMem(predictive) !== undefined ? `${getMem(predictive).toFixed(1)} MiB` : '--'}
                </td>
                <td className="py-3 px-3 text-slate-400 font-sans text-[11px]">
                  {hpa || predictive ? 'Allocated: 128 MiB request / pod' : '--'}
                </td>
              </tr>

              {/* Pod Replicas */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Pod Replicas (Average / Max)
                </td>
                <td className="py-3 px-3 text-cyan-300">
                  {hpa && getAvgReps(hpa) !== undefined ? `${getAvgReps(hpa).toFixed(2)} avg / ${getMaxReps(hpa)} max` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300">
                  {predictive && getAvgReps(predictive) !== undefined ? `${getAvgReps(predictive).toFixed(2)} avg / ${getMaxReps(predictive)} max` : '--'}
                </td>
                <td className="py-3 px-3 text-slate-400 font-sans text-[11px]">
                  {hpa ? `Min: ${hpa.min_replicas || 1} • Max: ${getMaxReps(hpa) || 5}` : '--'}
                </td>
              </tr>

              {/* Resource Cost: Core-Seconds */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Total Resource Cost (Core-Seconds)
                </td>
                <td className="py-3 px-3 text-cyan-300 font-bold">
                  {hpa && getCoreSec(hpa) !== undefined ? `${getCoreSec(hpa).toFixed(0)} core-s` : '--'}
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive && getCoreSec(predictive) !== undefined ? `${getCoreSec(predictive).toFixed(0)} core-s` : '--'}
                </td>
                <td className="py-3 px-3">
                  {predictive && hpa && getCoreSec(hpa) && getCoreSec(predictive) ? (
                    <span className={getCoreSec(predictive) <= getCoreSec(hpa) ? 'text-emerald-400' : 'text-amber-400'}>
                      {(((getCoreSec(predictive) - getCoreSec(hpa)) / getCoreSec(hpa)) * 100).toFixed(1)}% resource variance
                    </span>
                  ) : (
                    <span className="text-slate-500 font-sans text-[11px]">--</span>
                  )}
                </td>
              </tr>

              {/* Forecast Accuracy: MAE */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Prophet Forecast MAE (Mean Absolute Error)
                </td>
                <td className="py-3 px-3 text-slate-500">
                  N/A (CPU HPA has no forecast)
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive && getMae(predictive) !== null && getMae(predictive) !== undefined
                    ? `${getMae(predictive)} req/s`
                    : (hpa && getMae(hpa) !== null && getMae(hpa) !== undefined ? `${getMae(hpa)} req/s` : '--')}
                </td>
                <td className="py-3 px-3 text-slate-400 text-[11px] font-sans">
                  {(predictive && getMae(predictive) !== null) || (hpa && getMae(hpa) !== null)
                    ? 'Evaluated strictly on held-out test data (evaluate_forecast.py)'
                    : '--'}
                </td>
              </tr>

              {/* Forecast Accuracy: RMSE */}
              <tr className="hover:bg-slate-800/30 transition-colors">
                <td className="py-3 px-3 font-semibold text-slate-200 font-sans">
                  Prophet Forecast RMSE (Root Mean Squared Error)
                </td>
                <td className="py-3 px-3 text-slate-500">
                  N/A (CPU HPA has no forecast)
                </td>
                <td className="py-3 px-3 text-purple-300 font-bold">
                  {predictive && getRmse(predictive) !== null && getRmse(predictive) !== undefined
                    ? `${getRmse(predictive)} req/s`
                    : (hpa && getRmse(hpa) !== null && getRmse(hpa) !== undefined ? `${getRmse(hpa)} req/s` : '--')}
                </td>
                <td className="py-3 px-3 text-slate-400 text-[11px] font-sans">
                  {(predictive && getRmse(predictive) !== null) || (hpa && getRmse(hpa) !== null)
                    ? 'Penalizes large prediction deviations'
                    : '--'}
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
            Response Time &amp; Scaling Reaction Delay
          </h3>
          <p className="text-xs text-slate-400 mb-4">
            Lower is better. Demonstrates prevention of tail-latency spikes through proactive scaling.
          </p>

          <div className="h-64 w-full">
            {hasAnyData ? (
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
                  {hpa && (
                    <Bar dataKey="CPU HPA" fill="#06b6d4" name="Baseline CPU HPA" radius={[4, 4, 0, 0]} />
                  )}
                  {predictive && (
                    <Bar dataKey="Predictive KEDA" fill="#a855f7" name="Predictive KEDA" radius={[4, 4, 0, 0]} />
                  )}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full w-full flex flex-col items-center justify-center border border-dashed border-slate-800 rounded-lg text-slate-500 text-xs gap-2">
                <Clock className="w-6 h-6 text-slate-600" />
                <span className="font-medium text-slate-400">
                  Awaiting experiment data for {currentProfile?.profile.toUpperCase()} workload
                </span>
                <span className="text-[11px] text-slate-600 font-mono">
                  Run experiments to populate side-by-side charts
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Resource Consumption Chart */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
          <h3 className="text-sm font-semibold text-slate-100 mb-2">
            Resource Cost &amp; Cluster Utilization
          </h3>
          <p className="text-xs text-slate-400 mb-4">
            Core-seconds (scaled x10) and average CPU utilization across the evaluation duration.
          </p>

          <div className="h-64 w-full">
            {hasAnyData ? (
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
                  {hpa && (
                    <Bar dataKey="CPU HPA" fill="#06b6d4" name="Baseline CPU HPA" radius={[4, 4, 0, 0]} />
                  )}
                  {predictive && (
                    <Bar dataKey="Predictive KEDA" fill="#a855f7" name="Predictive KEDA" radius={[4, 4, 0, 0]} />
                  )}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full w-full flex flex-col items-center justify-center border border-dashed border-slate-800 rounded-lg text-slate-500 text-xs gap-2">
                <Clock className="w-6 h-6 text-slate-600" />
                <span className="font-medium text-slate-400">
                  Awaiting experiment data for {currentProfile?.profile.toUpperCase()} workload
                </span>
                <span className="text-[11px] text-slate-600 font-mono">
                  Run experiments to populate side-by-side charts
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
