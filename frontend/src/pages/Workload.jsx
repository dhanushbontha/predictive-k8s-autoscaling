import React, { useState } from 'react';
import {
  Zap,
  Play,
  Square,
  Clock,
  Users,
  Activity,
  AlertCircle,
  CheckCircle2,
  Sliders,
  ShieldAlert,
} from 'lucide-react';
import StatusBadge from '../components/StatusBadge';

const PROFILES = [
  {
    id: 'stable',
    name: 'Stable',
    badge: 'Baseline',
    desc: 'Uniform, constant traffic simulating steady predictable production load.',
    defaultUsers: 10,
    defaultSpawnRate: 2,
    defaultDuration: 120,
    color: 'border-cyan-500/30 text-cyan-400 bg-cyan-500/10',
  },
  {
    id: 'gradual',
    name: 'Gradual',
    badge: 'Ramp Up/Down',
    desc: 'Smooth linear traffic increase followed by controlled ramp-down.',
    defaultUsers: 25,
    defaultSpawnRate: 2,
    defaultDuration: 180,
    color: 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10',
  },
  {
    id: 'bursty',
    name: 'Bursty',
    badge: 'Flash Crowds',
    desc: 'Sudden, aggressive spikes testing autoscaler reaction delay and headroom.',
    defaultUsers: 40,
    defaultSpawnRate: 10,
    defaultDuration: 120,
    color: 'border-rose-500/30 text-rose-400 bg-rose-500/10',
  },
  {
    id: 'periodic',
    name: 'Periodic',
    badge: 'Diurnal Wave',
    desc: 'Sinusoidal oscillatory wave representing repeating cyclical daily patterns.',
    defaultUsers: 20,
    defaultSpawnRate: 3,
    defaultDuration: 240,
    color: 'border-purple-500/30 text-purple-400 bg-purple-500/10',
  },
  {
    id: 'noisy',
    name: 'Noisy',
    badge: 'Stochastic',
    desc: 'Random Poisson-distributed variations challenging trend forecasting.',
    defaultUsers: 20,
    defaultSpawnRate: 5,
    defaultDuration: 180,
    color: 'border-amber-500/30 text-amber-400 bg-amber-500/10',
  },
];

export default function Workload({
  workloadStatus,
  onStartWorkload,
  onStopWorkload,
  actionLoading,
}) {
  const [selectedProfile, setSelectedProfile] = useState('stable');
  const [duration, setDuration] = useState(120);
  const [users, setUsers] = useState(10);
  const [spawnRate, setSpawnRate] = useState(2);
  const [errorMsg, setErrorMsg] = useState(null);

  const isRunning = workloadStatus?.is_running;
  const elapsed = workloadStatus?.elapsed_seconds || 0;
  const totalDuration = workloadStatus?.duration_seconds || duration;
  const progressPercent = Math.min(100, Math.round((elapsed / (totalDuration || 1)) * 100));

  const handleSelectProfile = (p) => {
    setSelectedProfile(p.id);
    setUsers(p.defaultUsers);
    setSpawnRate(p.defaultSpawnRate);
    setDuration(p.defaultDuration);
    setErrorMsg(null);
  };

  const handleStart = async () => {
    setErrorMsg(null);
    try {
      await onStartWorkload({
        profile: selectedProfile,
        duration_seconds: duration,
        users,
        spawn_rate: spawnRate,
      });
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || err.message || 'Failed to start workload');
    }
  };

  const handleStop = async () => {
    setErrorMsg(null);
    try {
      await onStopWorkload();
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || err.message || 'Failed to stop workload');
    }
  };

  return (
    <div className="space-y-6">
      {/* Workload Status Banner */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div
              className={`p-3 rounded-xl border ${
                isRunning
                  ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30'
                  : 'bg-slate-800 text-slate-400 border-slate-700'
              }`}
            >
              <Zap className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-slate-100">
                  Locust Workload Generator
                </h2>
                <StatusBadge
                  status={isRunning ? 'running' : 'stopped'}
                  label={isRunning ? 'Active Execution' : 'Idle'}
                />
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Target URL:{' '}
                <span className="font-mono text-cyan-400 font-semibold">
                  {workloadStatus?.target_url || workloadStatus?.target_host || 'http://127.0.0.1:8000'}
                </span>
                <span className="text-slate-400 ml-2">
                  &bull; K8s Service: <span className="font-mono text-slate-300">target-app-service:8000 (HTTP)</span>
                </span>
                {isRunning && workloadStatus?.pid && (
                  <span className="ml-2">
                    &bull; Process PID: <span className="font-mono text-cyan-400">{workloadStatus.pid}</span>
                  </span>
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {isRunning ? (
              <button
                onClick={handleStop}
                disabled={actionLoading}
                className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-rose-500 hover:bg-rose-600 text-white transition-colors disabled:opacity-50"
              >
                <Square className="w-4 h-4 fill-current" />
                Stop Benchmark
              </button>
            ) : (
              <button
                onClick={handleStart}
                disabled={actionLoading}
                className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-950 transition-colors disabled:opacity-50 shadow-md shadow-cyan-500/20"
              >
                <Play className="w-4 h-4 fill-current" />
                Launch {PROFILES.find((p) => p.id === selectedProfile)?.name} Profile
              </button>
            )}
          </div>
        </div>

        {/* Live Progress Bar when Running */}
        {isRunning && (
          <div className="mt-5 pt-4 border-t border-slate-800 space-y-2">
            <div className="flex justify-between text-xs font-mono">
              <span className="text-slate-400">
                Running <strong className="text-cyan-400 uppercase">{workloadStatus.profile}</strong> ({workloadStatus.users} users)
              </span>
              <span className="text-cyan-400 font-semibold">
                {Math.round(elapsed)}s / {totalDuration}s ({progressPercent}%)
              </span>
            </div>
            <div className="w-full bg-slate-800 rounded-full h-2.5 overflow-hidden">
              <div
                className="bg-gradient-to-r from-cyan-500 to-indigo-500 h-full rounded-full transition-all duration-300"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>
        )}

        {/* Error Message if any */}
        {errorMsg && (
          <div className="mt-4 p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-xs text-rose-400 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
      </div>

      {/* Profile Selector Cards */}
      <div>
        <h3 className="text-sm font-semibold text-slate-100 mb-3 flex items-center gap-2">
          <Sliders className="w-4 h-4 text-cyan-400" />
          Select Workload Profile
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {PROFILES.map((profile) => {
            const isSelected = selectedProfile === profile.id;
            return (
              <button
                key={profile.id}
                onClick={() => handleSelectProfile(profile)}
                disabled={isRunning}
                className={`text-left p-4 rounded-xl border transition-all duration-200 flex flex-col justify-between ${
                  isSelected
                    ? 'bg-slate-800/90 border-cyan-500 shadow-md shadow-cyan-500/10'
                    : 'bg-slate-900/60 border-slate-800 hover:border-slate-700 hover:bg-slate-800/40'
                } ${isRunning ? 'opacity-60 cursor-not-allowed' : ''}`}
              >
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-bold text-slate-100">{profile.name}</span>
                    <span
                      className={`text-[10px] font-mono px-2 py-0.5 rounded border ${profile.color}`}
                    >
                      {profile.badge}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 leading-relaxed mb-4">
                    {profile.desc}
                  </p>
                </div>
                <div className="pt-2 border-t border-slate-800/60 flex justify-between text-[11px] font-mono text-slate-400">
                  <span>{profile.defaultUsers} users</span>
                  <span>{profile.defaultDuration}s</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Parameter Adjustment Panel */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <h3 className="text-sm font-semibold text-slate-100 mb-4 flex items-center gap-2">
          <Sliders className="w-4 h-4 text-purple-400" />
          Execution Parameters for &quot;{PROFILES.find((p) => p.id === selectedProfile)?.name}&quot;
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Duration slider */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-300 font-medium">Test Duration</span>
              <span className="font-mono text-cyan-400 font-bold">{duration} seconds</span>
            </div>
            <input
              type="range"
              min={30}
              max={600}
              step={15}
              value={duration}
              disabled={isRunning}
              onChange={(e) => setDuration(Number(e.target.value))}
              className="w-full accent-cyan-400 cursor-pointer"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono">
              <span>30s</span>
              <span>300s</span>
              <span>600s</span>
            </div>
          </div>

          {/* Concurrent Users slider */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-300 font-medium">Concurrent Users</span>
              <span className="font-mono text-cyan-400 font-bold">{users} users</span>
            </div>
            <input
              type="range"
              min={1}
              max={100}
              step={1}
              value={users}
              disabled={isRunning}
              onChange={(e) => setUsers(Number(e.target.value))}
              className="w-full accent-cyan-400 cursor-pointer"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono">
              <span>1 user</span>
              <span>50 users</span>
              <span>100 users</span>
            </div>
          </div>

          {/* Spawn Rate slider */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-300 font-medium">Spawn Rate</span>
              <span className="font-mono text-cyan-400 font-bold">{spawnRate} users/s</span>
            </div>
            <input
              type="range"
              min={1}
              max={20}
              step={1}
              value={spawnRate}
              disabled={isRunning}
              onChange={(e) => setSpawnRate(Number(e.target.value))}
              className="w-full accent-cyan-400 cursor-pointer"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono">
              <span>1 /s</span>
              <span>10 /s</span>
              <span>20 /s</span>
            </div>
          </div>
        </div>
      </div>

      {/* Execution Summary Footer */}
      <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-cyan-400 flex-shrink-0" />
          <span>Locust runs in headless background mode targeting <code className="text-slate-300 font-mono">target-app-service:8000</code>.</span>
        </div>
        <span className="font-mono text-[11px] text-slate-400">HTTP Port 8000</span>
      </div>
    </div>
  );
}
