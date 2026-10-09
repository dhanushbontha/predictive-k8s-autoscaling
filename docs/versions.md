# Pinned Component Versions

All versions in this file are pinned for reproducibility. Update this file whenever
a component is intentionally upgraded, and update the corresponding scripts too.

| Component | Version | Source / Notes |
|---|---|---|
| minikube | v1.38.1 | Installed; v1.39.0 is latest — upgrade when convenient |
| Kubernetes (--kubernetes-version) | v1.36.1 | Latest stable minor supported by KEDA 2.21 |
| kubectl | v1.36.1 | Installed |
| Helm | v4.2.4 | Installed |
| Docker Engine | 29.7.2 | Installed |
| KEDA (Helm chart) | 2.21.0 | kedacore/keda; latest stable (Sep 2026) |
| kube-prometheus-stack (Helm chart) | 92.1.1 | prometheus-community/kube-prometheus-stack; latest stable (Oct 2026) |

## Helm Repositories

| Alias | URL |
|---|---|
| kedacore | https://kedacore.github.io/charts |
| prometheus-community | https://prometheus-community.github.io/helm-charts |

## Kubernetes Namespaces

| Component | Namespace |
|---|---|
| KEDA | `keda` |
| Prometheus / Grafana | `monitoring` |

## Notes

- KEDA 2.21 is compatible with Kubernetes 1.34-1.36.
- `kube-prometheus-stack` 92.x bundles Prometheus Operator, Prometheus, Alertmanager,
  Grafana, and kube-state-metrics in a single chart.
- The minikube cluster uses the **Docker** driver (WSL2-backed on Windows with Docker Desktop).
- Resource defaults: 4 CPUs, 6 GB RAM -- sufficient for the app + KEDA + Prometheus + metrics-server.
