import React from 'react';

export default function MetricCard({
  title,
  value,
  unit = '',
  subtitle,
  icon: Icon,
  variant = 'cyan', // 'cyan', 'emerald', 'amber', 'purple', 'rose'
  badge,
  loading = false,
}) {
  const variantStyles = {
    cyan: {
      border: 'border-cyan-500/20 hover:border-cyan-500/40',
      iconBg: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
      glow: 'from-cyan-500/5 to-transparent',
    },
    emerald: {
      border: 'border-emerald-500/20 hover:border-emerald-500/40',
      iconBg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
      glow: 'from-emerald-500/5 to-transparent',
    },
    amber: {
      border: 'border-amber-500/20 hover:border-amber-500/40',
      iconBg: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
      glow: 'from-amber-500/5 to-transparent',
    },
    purple: {
      border: 'border-purple-500/20 hover:border-purple-500/40',
      iconBg: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
      glow: 'from-purple-500/5 to-transparent',
    },
    rose: {
      border: 'border-rose-500/20 hover:border-rose-500/40',
      iconBg: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
      glow: 'from-rose-500/5 to-transparent',
    },
  };

  const style = variantStyles[variant] || variantStyles.cyan;

  if (loading) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 animate-pulse">
        <div className="flex justify-between items-start mb-4">
          <div className="h-4 bg-slate-800 rounded w-24"></div>
          <div className="h-8 w-8 bg-slate-800 rounded-lg"></div>
        </div>
        <div className="h-8 bg-slate-800 rounded w-32 mb-2"></div>
        <div className="h-3 bg-slate-800 rounded w-20"></div>
      </div>
    );
  }

  return (
    <div
      className={`relative bg-slate-900/60 backdrop-blur-sm border ${style.border} rounded-xl p-5 transition-all duration-300 hover:shadow-lg hover:shadow-black/40 overflow-hidden group`}
    >
      {/* Subtle background glow */}
      <div
        className={`absolute inset-0 bg-gradient-to-br ${style.glow} opacity-50 pointer-events-none`}
      />

      <div className="relative z-10 flex items-start justify-between mb-3">
        <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
          {title}
        </span>
        {Icon && (
          <div
            className={`p-2.5 rounded-lg border ${style.iconBg} flex items-center justify-center transition-transform group-hover:scale-105 duration-200`}
          >
            <Icon className="w-5 h-5" />
          </div>
        )}
      </div>

      <div className="relative z-10 flex items-baseline gap-2">
        <span className="text-3xl font-bold tracking-tight text-slate-100 font-mono">
          {value !== undefined && value !== null ? value : '--'}
        </span>
        {unit && <span className="text-sm font-semibold text-slate-400">{unit}</span>}
        {badge && <div className="ml-auto">{badge}</div>}
      </div>

      {subtitle && (
        <div className="relative z-10 mt-2 text-xs text-slate-400 flex items-center gap-1.5">
          {subtitle}
        </div>
      )}
    </div>
  );
}
