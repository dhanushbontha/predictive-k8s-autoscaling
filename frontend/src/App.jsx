import React, { useState, useEffect, useCallback, useRef } from 'react';
import Sidebar from './components/Sidebar';
import Navbar from './components/Navbar';
import Overview from './pages/Overview';
import Forecasting from './pages/Forecasting';
import Kubernetes from './pages/Kubernetes';
import Autoscaling from './pages/Autoscaling';
import CpuHpa from './pages/CpuHpa';
import Workload from './pages/Workload';
import Experiments from './pages/Experiments';
import api from './services/api';

export default function App() {
  const [activeTab, setActiveTab] = useState('overview');
  const [health, setHealth] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [forecastData, setForecastData] = useState(null);
  const [forecastError, setForecastError] = useState(null);
  const [k8sStatus, setK8sStatus] = useState(null);
  const [autoscalingStatus, setAutoscalingStatus] = useState(null);
  const [cpuHpaStatus, setCpuHpaStatus] = useState(null);
  const [workloadStatus, setWorkloadStatus] = useState(null);
  const [comparisonData, setComparisonData] = useState(null);
  const [experimentSource, setExperimentSource] = useState('results');

  const [backendHealthy, setBackendHealthy] = useState(false);
  const [prometheusHealthy, setPrometheusHealthy] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const isMountedRef = useRef(true);
  const forecastFetchCount = useRef(0);

  // Fetch live metrics (every 5 seconds)
  const fetchLiveMetrics = useCallback(async () => {
    try {
      const [healthRes, metricsRes, k8sRes, autoRes, workloadRes, cpuHpaRes] =
        await Promise.allSettled([
          api.getHealth(),
          api.getMetrics(),
          api.getKubernetesStatus(),
          api.getAutoscalingStatus(),
          api.getWorkloadStatus(),
          api.getCpuHpaStatus(),
        ]);

      if (!isMountedRef.current) return;

      // Backend health: verified if /health or any core API endpoint returns data
      if (healthRes.status === 'fulfilled' && healthRes.value?.status === 'ok') {
        setHealth(healthRes.value);
        setBackendHealthy(true);
        setPrometheusHealthy(Boolean(healthRes.value?.prometheus_connected));
      } else if (
        metricsRes.status === 'fulfilled' ||
        k8sRes.status === 'fulfilled' ||
        autoRes.status === 'fulfilled'
      ) {
        setBackendHealthy(true);
        if (healthRes.status === 'fulfilled') {
          setHealth(healthRes.value);
          setPrometheusHealthy(Boolean(healthRes.value?.prometheus_connected));
        }
      } else {
        setBackendHealthy(false);
      }

      if (metricsRes.status === 'fulfilled') {
        setMetrics(metricsRes.value);
        if (metricsRes.value?.provenance === 'live_prometheus') {
          setPrometheusHealthy(true);
        }
      }

      if (k8sRes.status === 'fulfilled') {
        setK8sStatus(k8sRes.value);
      }

      if (autoRes.status === 'fulfilled') {
        setAutoscalingStatus(autoRes.value);
        if (autoRes.value?.forecast_status === 'waiting_for_data') {
          setForecastData(null);
          if (autoRes.value?.forecast_error) {
            setForecastError(autoRes.value.forecast_error);
          }
        } else if (autoRes.value?.forecast_error && !autoRes.value?.is_fresh) {
          setForecastError(autoRes.value.forecast_error);
        }
      }

      if (workloadRes.status === 'fulfilled') {
        setWorkloadStatus(workloadRes.value);
      }

      if (cpuHpaRes.status === 'fulfilled') {
        setCpuHpaStatus(cpuHpaRes.value);
      }

      setLastUpdated(new Date());
    } catch (err) {
      console.error('Error fetching live metrics:', err);
      if (isMountedRef.current) setBackendHealthy(false);
    }
  }, []);

  // Fetch forecast data (periodically every 15s or on manual refresh)
  const fetchForecast = useCallback(async (forceRefresh = false) => {
    try {
      const res = await api.getForecast(100, forceRefresh);
      if (!isMountedRef.current) return;
      setForecastData(res);
      setForecastError(null);
    } catch (err) {
      if (!isMountedRef.current) return;
      const detail =
        err.response?.data?.detail ||
        err.message ||
        'Forecast unavailable (insufficient traffic observations)';
      setForecastData(null);
      setForecastError(detail);
    }
  }, []);

  // Fetch experiment comparison (clean results by default, or historical backup)
  const fetchExperiments = useCallback(async (source = 'results') => {
    try {
      const res = await api.getExperimentComparison(source);
      if (isMountedRef.current) {
        setComparisonData(res);
        setExperimentSource(source);
      }
    } catch (err) {
      console.warn('Could not load experiment benchmarks:', err);
    }
  }, []);

  // Manual refresh handler
  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    await Promise.allSettled([
      fetchLiveMetrics(),
      fetchForecast(true),
      activeTab === 'experiments' ? fetchExperiments(experimentSource) : Promise.resolve(),
    ]);
    if (isMountedRef.current) setIsRefreshing(false);
  };

  // Initial load
  useEffect(() => {
    isMountedRef.current = true;
    fetchLiveMetrics();
    fetchForecast(false);
    fetchExperiments();

    const interval = setInterval(() => {
      fetchLiveMetrics();
      // Forecast refetched every 3rd cycle (15s) to avoid excessive Prophet fitting
      forecastFetchCount.current += 1;
      if (forecastFetchCount.current % 3 === 0) {
        fetchForecast(false);
      }
    }, 5000);

    return () => {
      isMountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchLiveMetrics, fetchForecast, fetchExperiments]);

  // Load experiments when switching to experiments tab if not loaded
  useEffect(() => {
    if (activeTab === 'experiments' && !comparisonData) {
      fetchExperiments();
    }
  }, [activeTab, comparisonData, fetchExperiments]);

  // Workload actions
  const handleStartWorkload = async (params) => {
    setActionLoading(true);
    try {
      await api.startWorkload(params);
      await fetchLiveMetrics();
    } finally {
      setActionLoading(false);
    }
  };

  const handleStopWorkload = async () => {
    setActionLoading(true);
    try {
      await api.stopWorkload();
      await fetchLiveMetrics();
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100 font-sans selection:bg-cyan-500/30 selection:text-cyan-200">
      {/* Fixed Left Navigation Sidebar */}
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Sticky Header */}
        <Navbar
          activeTab={activeTab}
          backendHealthy={backendHealthy}
          prometheusHealthy={prometheusHealthy}
          autoscalingStatus={autoscalingStatus}
          lastUpdated={lastUpdated}
          isRefreshing={isRefreshing}
          onRefresh={handleManualRefresh}
        />

        {/* Dynamic Page Views */}
        <main className="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">
          {activeTab === 'overview' && (
            <Overview
              metrics={metrics}
              forecastData={forecastData}
              forecastError={forecastError}
              k8sStatus={k8sStatus}
              autoscalingStatus={autoscalingStatus}
              workloadStatus={workloadStatus}
              backendHealthy={backendHealthy}
              prometheusHealthy={prometheusHealthy}
              onNavigate={setActiveTab}
            />
          )}

          {activeTab === 'forecasting' && (
            <Forecasting
              forecastData={forecastData}
              forecastError={forecastError}
              metrics={metrics}
              autoscalingStatus={autoscalingStatus}
              onForceRefresh={() => fetchForecast(true)}
              isRefreshing={isRefreshing}
            />
          )}

          {activeTab === 'kubernetes' && (
            <Kubernetes k8sStatus={k8sStatus} />
          )}

          {activeTab === 'autoscaling' && (
            <Autoscaling
              autoscalingStatus={autoscalingStatus}
              metrics={metrics}
              k8sStatus={k8sStatus}
            />
          )}

          {activeTab === 'cpu-hpa' && (
            <CpuHpa cpuHpaStatus={cpuHpaStatus} k8sStatus={k8sStatus} />
          )}

          {activeTab === 'workload' && (
            <Workload
              workloadStatus={workloadStatus}
              onStartWorkload={handleStartWorkload}
              onStopWorkload={handleStopWorkload}
              actionLoading={actionLoading}
            />
          )}

          {activeTab === 'experiments' && (
            <Experiments
              comparisonData={comparisonData}
              experimentSource={experimentSource}
              onNavigate={setActiveTab}
              onFetchExperiments={fetchExperiments}
            />
          )}
        </main>
      </div>
    </div>
  );
}
