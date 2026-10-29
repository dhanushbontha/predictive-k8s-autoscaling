# Prometheus Monitoring Setup

This directory contains configuration, architecture documentation, and verification instructions for the Prometheus monitoring stack supporting the predictive Kubernetes autoscaling project.

---

## 1. Architecture & Monitoring Components

The monitoring pipeline runs inside the `monitoring` namespace using the Prometheus Operator (`kube-prometheus-stack`):

```
+-------------------------------------------------------------------------------------------------+
|                                        Minikube Cluster                                         |
|                                                                                                 |
|  [ Kubelet / cAdvisor ] ---------->  container_cpu_usage_seconds_total,                         |
|                                      container_memory_working_set_bytes                         |
|                                                     |                                           |
|  [ kube-state-metrics ] ---------->  kube_deployment_status_replicas,                           |
|                                      kube_horizontalpodautoscaler_status_current_replicas,      |
|                                      kube_pod_info                                              |
|                                                     |                                           |
|                                                     v                                           |
|                                     [ Prometheus Server Pod ]                                   |
|                                           (Port 9090)                                           |
|                                                     |                                           |
|                                                     v                                           |
|                                  [ Forecasting Service (Prophet) ]                              |
|                                  [ Evaluation & Analysis Scripts ]                              |
+-------------------------------------------------------------------------------------------------+
```

### Components
1. **Prometheus Operator**: Manages Prometheus instances and custom resources (`ServiceMonitor`, `PodMonitor`, `PrometheusRule`).
2. **Prometheus Server**: Scrapes, stores, and serves time-series metrics via PromQL queries.
3. **kube-state-metrics**: Listens to the Kubernetes API server and exposes object-level state metrics (Deployment replicas, HPA current/desired replicas, Pod statuses).
4. **Node Exporter & Kubelet/cAdvisor**: Exposes node and container-level resource utilization (CPU, memory).

---

## 2. Helm Installation (Reproducibility Reference)

If setting up on a clean cluster, install using Helm:

```powershell
# 1. Add and update Prometheus community Helm repository
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 2. Install lightweight kube-prometheus-stack in 'monitoring' namespace
helm install prometheus prometheus-community/kube-prometheus-stack `
  --namespace monitoring `
  --create-namespace `
  --set alertmanager.enabled=false `
  --set grafana.enabled=false
```

---

## 3. Verification & Metric Queries

### Step 1: Verify Pod Readiness
```powershell
kubectl get pods -n monitoring
```
Ensure all pods (`prometheus-prometheus-kube-prometheus-prometheus-0`, `prometheus-kube-state-metrics-*`) are `Running` and ready (`2/2` or `1/1`).

### Step 2: Access Prometheus Web UI
Forward port 9090 to localhost:
```powershell
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
```
Open **[http://localhost:9090](http://localhost:9090)** in your browser.
Navigate to **Status -> Targets** to check active scrape endpoints.

### Step 3: Verified PromQL Queries

Run these queries in the Prometheus expression browser or via API:

| Metric Category | PromQL Query | Description | Status |
| :--- | :--- | :--- | :--- |
| **HTTP Request Count** | `http_requests_total{job="target-app-service"}` | Total requests by handler and status code | ✅ Verified |
| **Workload Request Rate** | `rate(http_requests_total{job="target-app-service", handler="/api/work"}[1m])` | Requests per second on `/api/work` | ✅ Verified |
| **All Routes Request Rate** | `sum by (handler, status) (rate(http_requests_total{job="target-app-service"}[1m]))` | Real-time throughput grouped by route and status | ✅ Verified |
| **95th Percentile Latency** | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_highr_seconds_bucket{job="target-app-service"}[1m])))` | p95 request duration in seconds | ✅ Verified |
| **Pod CPU Usage** | `rate(container_cpu_usage_seconds_total{pod=~"target-app.*"}[1m])` | Real-time CPU cores utilized per target pod | ✅ Verified |
| **Pod Memory Usage**| `container_memory_working_set_bytes{pod=~"target-app.*"}` | Memory working set in bytes | ✅ Verified |
| **Target Pod Count**| `count(kube_pod_info{pod=~"target-app.*"})` | Current count of target application pods | ✅ Verified |
| **Deployment Replicas** | `kube_deployment_status_replicas{deployment="target-app"}` | Replicas declared in target-app deployment | ✅ Verified |
| **HPA Current Replicas** | `kube_horizontalpodautoscaler_status_current_replicas{horizontalpodautoscaler="target-app-hpa"}` | Current replica count tracked by HPA | ✅ Verified |
| **HPA Desired Replicas** | `kube_horizontalpodautoscaler_status_desired_replicas{horizontalpodautoscaler="target-app-hpa"}` | Desired replica count calculated by HPA | ✅ Verified |
| **HPA Min/Max Bounds** | `kube_horizontalpodautoscaler_spec_min_replicas`, `kube_horizontalpodautoscaler_spec_max_replicas` | Configured scaling boundaries (1 to 5) | ✅ Verified |

---

## 4. Target Application Scrape Configuration

The scrape configuration is managed declaratively by the Prometheus Operator via [`kubernetes/monitoring/target-app-servicemonitor.yaml`](target-app-servicemonitor.yaml):

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: target-app-servicemonitor
  namespace: default
  labels:
    app: target-app
    release: prometheus
spec:
  selector:
    matchLabels:
      app: target-app
  namespaceSelector:
    matchNames:
      - default
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
      scrapeTimeout: 10s
```

### Key Matching Attributes
- **`metadata.labels.release: prometheus`**: Matches the Prometheus resource's `serviceMonitorSelector`.
- **`spec.selector.matchLabels.app: target-app`**: Selects `target-app-service` (`app=target-app`).
- **`spec.namespaceSelector.matchNames: [default]`**: Targets the `default` namespace where `target-app` resides.
- **`spec.endpoints[0].port: http`**: Matches the named port `http` (`8000`) defined on `target-app-service`.
- **`spec.endpoints[0].path: /metrics`**: The Prometheus metrics endpoint exposed by FastAPI.

### Troubleshooting Scrape Targets
1. **Target not appearing in `Status -> Targets`**:
   - Check ServiceMonitor labels: Ensure `release: prometheus` is set on the ServiceMonitor.
   - Check Service port name: Ensure `port: http` in the ServiceMonitor matches `ports[].name: http` in `target-app-service.yaml`.
2. **Target state is `DOWN`**:
   - Verify that the pod is responding to `GET /metrics` on port 8000:
     `kubectl port-forward svc/target-app-service 8000:8000` and curl `http://localhost:8000/metrics`.
   - Inspect Prometheus Operator logs:
     `kubectl logs -n monitoring -l app.kubernetes.io/name=prometheus-operator`


---

## 5. How Prometheus Data Will Be Used in Later Experiments

1. **Workload Forecasting**: Historical `rate(http_requests_total[1m])` or CPU usage data will be exported or queried via Prometheus REST API by the Prophet forecasting service to train predictive models.
2. **Autoscaling Evaluation**: PromQL queries comparing reactive HPA vs predictive scaling will measure:
   - Target vs actual scaling lead times.
   - Resource over-provisioning vs throttling.
   - Request latency percentiles during sudden load spikes.

---

## 6. Cleanup Instructions

To uninstall the Prometheus monitoring release from your cluster:
```powershell
helm uninstall prometheus -n monitoring
kubectl delete namespace monitoring
```
