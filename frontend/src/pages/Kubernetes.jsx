import React from 'react';
import {
  Server,
  Layers,
  Cpu,
  Database,
  CheckCircle2,
  AlertTriangle,
  Boxes,
  FileCode,
} from 'lucide-react';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';

export default function Kubernetes({ k8sStatus }) {
  const pods = k8sStatus?.pods || [];
  const currentReplicas = k8sStatus?.current_replicas ?? k8sStatus?.available_replicas ?? pods.length ?? 1;
  const desiredReplicas = k8sStatus?.desired_replicas ?? 1;
  const availableReplicas = k8sStatus?.available_replicas ?? currentReplicas;
  const maxReplicas = k8sStatus?.max_replicas ?? 5;
  const minReplicas = k8sStatus?.min_replicas ?? 1;

  const totalCpuCores = k8sStatus?.cpu_usage_cores ?? 0.0;
  const totalCpuMillicores = (totalCpuCores * 1000).toFixed(1);
  const totalMemoryMib = (k8sStatus?.memory_working_set_mb ?? 0.0).toFixed(1);

  return (
    <div className="space-y-6">
      {/* Top Deployment Info Header */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <Boxes className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-slate-100">
                Deployment: {k8sStatus?.deployment_name || 'target-app'}
              </h2>
              <StatusBadge
                status={k8sStatus?.cluster_connected ? 'healthy' : 'warning'}
                label={k8sStatus?.cluster_connected ? 'Cluster Connected' : 'Simulated / Offline'}
              />
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Namespace: <span className="font-mono text-slate-300">{k8sStatus?.namespace || 'default'}</span> &bull; Container:{' '}
              <span className="font-mono text-slate-300">target-app:latest</span> &bull; Provenance:{' '}
              <span className="font-mono text-cyan-400">{k8sStatus?.provenance || 'live_prometheus'}</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <div className="px-3 py-2 bg-slate-950/60 rounded-lg border border-slate-800 font-mono">
            <span className="text-slate-400">Request: </span>
            <span className="text-cyan-400 font-bold">100m CPU / 128Mi RAM</span>
          </div>
          <div className="px-3 py-2 bg-slate-950/60 rounded-lg border border-slate-800 font-mono">
            <span className="text-slate-400">Limit: </span>
            <span className="text-emerald-400 font-bold">500m CPU / 256Mi RAM</span>
          </div>
        </div>
      </div>

      {/* Cluster Metrics Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Current Replicas"
          value={currentReplicas}
          unit="pods"
          subtitle={`Available: ${availableReplicas} ready pods`}
          icon={Server}
          variant="emerald"
        />
        <MetricCard
          title="Desired Replicas"
          value={desiredReplicas}
          unit="pods"
          subtitle="HPA Target (distinct from Max)"
          icon={Layers}
          variant="cyan"
        />
        <MetricCard
          title="Maximum Replicas"
          value={maxReplicas}
          unit="pods"
          subtitle={`Min: ${minReplicas} &bull; Max capacity cap`}
          icon={Layers}
          variant="amber"
        />
        <MetricCard
          title="Total Resource Usage"
          value={`${totalCpuMillicores}m`}
          unit={`/ ${totalMemoryMib} MiB`}
          subtitle="Across all target-app replicas"
          icon={Cpu}
          variant="purple"
        />
      </div>

      {/* Pod Status Table */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
              <Server className="w-4 h-4 text-emerald-400" />
              Active Target Application Pods
            </h3>
            <p className="text-xs text-slate-400">
              Live pod status, container readiness, restarts, and resource consumption
            </p>
          </div>
          <span className="text-xs text-slate-400 font-mono">
            {pods.length} pod{pods.length !== 1 ? 's' : ''} monitored
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="border-b border-slate-800 text-slate-400">
              <tr>
                <th className="pb-3 px-3">Pod Identifier</th>
                <th className="pb-3 px-3">Phase</th>
                <th className="pb-3 px-3">Readiness</th>
                <th className="pb-3 px-3">Restarts</th>
                <th className="pb-3 px-3">CPU Usage</th>
                <th className="pb-3 px-3">Memory</th>
                <th className="pb-3 px-3">Utilization Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {pods.map((pod) => {
                const cpuValMillicores = (pod.cpu_cores ? pod.cpu_cores * 1000 : 0);
                const memValMb = pod.memory_mb || 0;
                // Ratio against 100m CPU request
                const cpuRatioPct = Math.min(100, Math.round((cpuValMillicores / 100) * 100));
                const cpuBarColor = cpuRatioPct > 80 ? 'bg-rose-500' : cpuRatioPct > 50 ? 'bg-amber-500' : 'bg-cyan-500';

                return (
                  <tr key={pod.name} className="hover:bg-slate-800/30 transition-colors">
                    <td className="py-3 px-3 font-semibold text-slate-200">
                      {pod.name}
                    </td>
                    <td className="py-3 px-3 font-sans">
                      <StatusBadge
                        status={pod.phase === 'Running' ? 'running' : 'warning'}
                        label={pod.phase}
                        size="xs"
                      />
                    </td>
                    <td className="py-3 px-3">
                      <span
                        className={`inline-flex items-center gap-1 font-sans ${
                          pod.ready ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {pod.ready ? (
                          <CheckCircle2 className="w-3.5 h-3.5" />
                        ) : (
                          <AlertTriangle className="w-3.5 h-3.5" />
                        )}
                        {pod.ready ? '1/1 Ready' : '0/1 Ready'}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-slate-300">{pod.restarts ?? 0}</td>
                    <td className="py-3 px-3 text-cyan-400">
                      {cpuValMillicores.toFixed(1)}m ({cpuRatioPct}% of req)
                    </td>
                    <td className="py-3 px-3 text-slate-300">
                      {memValMb.toFixed(1)} MiB
                    </td>
                    <td className="py-3 px-3 w-40">
                      <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                        <div
                          className={`h-full ${cpuBarColor} transition-all duration-500`}
                          style={{ width: `${Math.max(5, cpuRatioPct)}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
              {pods.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-slate-400 font-sans">
                    No active pods found for deployment &quot;target-app&quot;.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Manifest Specification Card */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 backdrop-blur-sm">
        <div className="flex items-center gap-2 mb-3">
          <FileCode className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-semibold text-slate-100">
            Resource Allocation &amp; Limits (target-app-deployment.yaml)
          </h3>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
          <div className="p-3 bg-slate-950/70 rounded-lg border border-slate-800/80 space-y-1.5">
            <div className="text-slate-400 font-sans font-medium text-xs mb-1">
              Resource Requests (Guaranteed per Pod)
            </div>
            <div className="flex justify-between text-slate-300">
              <span>cpu:</span>
              <span className="text-cyan-400">100m (0.1 core)</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>memory:</span>
              <span className="text-emerald-400">128Mi</span>
            </div>
          </div>
          <div className="p-3 bg-slate-950/70 rounded-lg border border-slate-800/80 space-y-1.5">
            <div className="text-slate-400 font-sans font-medium text-xs mb-1">
              Resource Limits (Hard Caps per Pod)
            </div>
            <div className="flex justify-between text-slate-300">
              <span>cpu:</span>
              <span className="text-cyan-400">500m (0.5 core)</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>memory:</span>
              <span className="text-emerald-400">256Mi</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
