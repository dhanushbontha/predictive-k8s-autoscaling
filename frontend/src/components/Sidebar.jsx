import React from 'react';
  import {
  Activity,
  TrendingUp,
  Server,
  Gauge,
  Zap,
  BarChart3,
  Layers,
  ShieldCheck,
} from 'lucide-react';

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: Activity, desc: 'Live System State' },
  { id: 'forecasting', label: 'Forecasting', icon: TrendingUp, desc: 'Prophet Model & Load' },
  { id: 'kubernetes', label: 'Cluster & Pods', icon: Server, desc: 'Target-App Deployment' },
  { id: 'autoscaling', label: 'Autoscaler (KEDA)', icon: Gauge, desc: 'Freshness & Scaling Rules' },
  { id: 'workload', label: 'Workload Generator', icon: Zap, desc: 'Locust Benchmark Runner' },
  { id: 'experiments', label: 'Benchmarks', icon: BarChart3, desc: 'HPA vs Predictive Eval' },
];

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside className="w-64 bg-slate-900/95 border-r border-slate-800/80 flex flex-col justify-between h-screen sticky top-0 select-none z-20">
      {/* Brand Header */}
      <div>
        <div className="p-5 border-b border-slate-800/80 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-cyan-500/20 text-white font-bold">
            <Layers className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-white flex items-center gap-1.5">
              Predictive K8s
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-mono">
                v2.0
              </span>
            </h1>
            <p className="text-[11px] text-slate-400 tracking-wide font-medium">
              Prophet &bull; KEDA &bull; HPA
            </p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="p-3 space-y-1">
          <div className="px-3 py-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">
            Platform Views
          </div>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all duration-200 group relative ${
                  isActive
                    ? 'bg-cyan-500/10 text-cyan-400 font-semibold border border-cyan-500/30 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
              >
                <Icon
                  className={`w-4 h-4 transition-transform group-hover:scale-110 ${
                    isActive ? 'text-cyan-400' : 'text-slate-400 group-hover:text-slate-300'
                  }`}
                />
                <div className="flex flex-col">
                  <span className="text-xs">{item.label}</span>
                  <span className="text-[10px] text-slate-400 font-normal">
                    {item.desc}
                  </span>
                </div>
                {isActive && (
                  <span className="ml-auto w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-sm shadow-cyan-400" />
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Footer Info Box */}
      <div className="p-4 border-t border-slate-800/80">
        <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800/60 space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span>K8s Autoscaling Engine</span>
          </div>
          <p className="text-[11px] text-slate-400 leading-relaxed">
            Live Prometheus metrics driving Prophet load forecasting & KEDA triggers.
          </p>
        </div>
      </div>
    </aside>
  );
}
