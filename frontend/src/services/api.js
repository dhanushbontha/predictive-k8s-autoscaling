import axios from 'axios';

// Base API URL: In dev, Vite proxies /api, /health, /metrics to backend
// In preview/standalone, falls back to http://localhost:8080
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (typeof window !== 'undefined' && window.location.port !== '5173'
    ? 'http://localhost:8080'
    : '');

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 8000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const api = {
  // Backend & Metrics Endpoints
  getHealth: async () => {
    const res = await client.get('/health');
    return res.data;
  },

  getMetrics: async () => {
    const res = await client.get('/api/metrics');
    return res.data;
  },

  getForecast: async (limit = 100, forceRefresh = false) => {
    const res = await client.get('/api/forecast', {
      params: { limit, force_refresh: forceRefresh },
    });
    return res.data;
  },

  getPredictedLoad: async (forceRefresh = false) => {
    const res = await client.get('/api/predicted-load', {
      params: { force_refresh: forceRefresh },
    });
    return res.data;
  },

  // Kubernetes & Autoscaling Status
  getKubernetesStatus: async () => {
    const res = await client.get('/api/kubernetes/status');
    return res.data;
  },

  getAutoscalingStatus: async () => {
    const res = await client.get('/api/autoscaling/status');
    return res.data;
  },

  // Workload Generator Controls
  getWorkloadStatus: async () => {
    const res = await client.get('/api/workload/status');
    return res.data;
  },

  startWorkload: async ({ profile, duration_seconds, users, spawn_rate }) => {
    const res = await client.post('/api/workload/start', {
      profile,
      duration: duration_seconds ? Number(duration_seconds) : undefined,
      user_count: users ? Number(users) : undefined,
      spawn_rate: spawn_rate ? Number(spawn_rate) : undefined,
    });
    return res.data;
  },

  stopWorkload: async () => {
    const res = await client.post('/api/workload/stop');
    return res.data;
  },

  // Experiment Comparison
  getExperimentComparison: async () => {
    const res = await client.get('/api/experiments/comparison');
    return res.data;
  },
};

export default api;
