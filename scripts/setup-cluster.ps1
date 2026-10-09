<#
.SYNOPSIS
    Sets up the Minikube cluster for the predictive-k8s-autoscaling project.

.DESCRIPTION
    1. Starts Minikube with the Docker driver at pinned Kubernetes version.
    2. Enables the metrics-server addon.
    3. Adds Helm repos and installs KEDA (in namespace 'keda') and
       kube-prometheus-stack (in namespace 'monitoring') at pinned chart versions.

.PARAMETER Cpus
    Number of CPUs for Minikube. Default: 4.

.PARAMETER Memory
    Memory (MB) for Minikube. Default: 6144 (6 GB).

.PARAMETER KubernetesVersion
    Kubernetes version to pin. Default: v1.36.1.

.PARAMETER KedaVersion
    KEDA Helm chart version to install. Default: 2.21.0.

.PARAMETER PrometheusVersion
    kube-prometheus-stack Helm chart version to install. Default: 92.1.1.

.EXAMPLE
    .\scripts\setup-cluster.ps1
    .\scripts\setup-cluster.ps1 -Cpus 6 -Memory 8192

.NOTES
    Requires: Docker Desktop (WSL2 backend), minikube, kubectl, helm — all on PATH.
    Run from the repo root on Windows PowerShell / PowerShell 7+.
    See docs/versions.md for the pinned component versions.
#>

[CmdletBinding()]
param(
    [int]    $Cpus              = 4,
    [int]    $Memory            = 6144,
    [string] $KubernetesVersion = "v1.36.1",
    [string] $KedaVersion       = "2.21.0",
    [string] $PrometheusVersion = "92.1.1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── helpers ────────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host ">>> $Message" -ForegroundColor Cyan
}

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Error "Required command not found: '$Name'. See docs/versions.md for install instructions."
    }
}

function Invoke-Checked {
    param([string]$Description, [scriptblock]$Block)
    Write-Host "    $Description ..." -ForegroundColor Gray
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Error "FAILED: $Description (exit code $LASTEXITCODE)"
    }
}

# ─── preflight ──────────────────────────────────────────────────────────────

Write-Step "Preflight checks"
Assert-Command "docker"
Assert-Command "minikube"
Assert-Command "kubectl"
Assert-Command "helm"

# Verify Docker daemon is reachable before we try to start Minikube
Invoke-Checked "docker info" { docker info | Out-Null }

Write-Host "    All required tools found." -ForegroundColor Green

# ─── start minikube ─────────────────────────────────────────────────────────

Write-Step "Starting Minikube (driver=docker, k8s=$KubernetesVersion, cpus=$Cpus, memory=${Memory}MB)"

# Check if a cluster already exists and is running
$minikubeStatus = minikube status --format "{{.Host}}" 2>$null
if ($minikubeStatus -eq "Running") {
    Write-Host "    Minikube already running. Skipping start." -ForegroundColor Yellow
} else {
    Invoke-Checked "minikube start" {
        minikube start `
            --driver=docker `
            --kubernetes-version=$KubernetesVersion `
            --cpus=$Cpus `
            --memory=$Memory `
            --addons=metrics-server `
            --embed-certs
    }
}

# ─── enable metrics-server ──────────────────────────────────────────────────

Write-Step "Enabling metrics-server addon"
# Safe to run even if already enabled
Invoke-Checked "minikube addons enable metrics-server" {
    minikube addons enable metrics-server
}

# ─── wait for metrics-server ────────────────────────────────────────────────

Write-Step "Waiting for metrics-server to be ready (up to 120 s)"
Invoke-Checked "kubectl rollout status -n kube-system deployment/metrics-server" {
    kubectl rollout status deployment/metrics-server -n kube-system --timeout=120s
}

# ─── helm repos ─────────────────────────────────────────────────────────────

Write-Step "Adding / updating Helm repositories"
Invoke-Checked "helm repo add kedacore" {
    helm repo add kedacore https://kedacore.github.io/charts
}
Invoke-Checked "helm repo add prometheus-community" {
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
}
Invoke-Checked "helm repo update" {
    helm repo update
}

# ─── install KEDA ───────────────────────────────────────────────────────────

Write-Step "Installing KEDA $KedaVersion in namespace 'keda'"
kubectl create namespace keda --dry-run=client -o yaml | kubectl apply -f -

$kedaInstalled = helm list -n keda --filter "^keda$" --short 2>$null
if ($kedaInstalled -eq "keda") {
    Write-Host "    KEDA already installed. Running upgrade instead." -ForegroundColor Yellow
    Invoke-Checked "helm upgrade keda" {
        helm upgrade keda kedacore/keda `
            --namespace keda `
            --version $KedaVersion `
            --wait `
            --timeout 5m
    }
} else {
    Invoke-Checked "helm install keda" {
        helm install keda kedacore/keda `
            --namespace keda `
            --version $KedaVersion `
            --wait `
            --timeout 5m
    }
}

# ─── install kube-prometheus-stack ──────────────────────────────────────────

Write-Step "Installing kube-prometheus-stack $PrometheusVersion in namespace 'monitoring'"
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

$promInstalled = helm list -n monitoring --filter "^prometheus$" --short 2>$null
if ($promInstalled -eq "prometheus") {
    Write-Host "    prometheus already installed. Running upgrade instead." -ForegroundColor Yellow
    Invoke-Checked "helm upgrade prometheus" {
        helm upgrade prometheus prometheus-community/kube-prometheus-stack `
            --namespace monitoring `
            --version $PrometheusVersion `
            --set grafana.adminPassword=admin `
            --set prometheus.prometheusSpec.scrapeInterval=15s `
            --set prometheus.prometheusSpec.evaluationInterval=15s `
            --wait `
            --timeout 10m
    }
} else {
    Invoke-Checked "helm install prometheus" {
        helm install prometheus prometheus-community/kube-prometheus-stack `
            --namespace monitoring `
            --version $PrometheusVersion `
            --set grafana.adminPassword=admin `
            --set prometheus.prometheusSpec.scrapeInterval=15s `
            --set prometheus.prometheusSpec.evaluationInterval=15s `
            --wait `
            --timeout 10m
    }
}

# ─── summary ────────────────────────────────────────────────────────────────

Write-Step "Cluster setup complete!"
Write-Host ""
Write-Host "Installed versions:" -ForegroundColor Green
Write-Host "  Kubernetes : $KubernetesVersion"
Write-Host "  KEDA chart : $KedaVersion"
Write-Host "  Prometheus : $PrometheusVersion"
Write-Host ""
Write-Host "Next steps — run these to verify:" -ForegroundColor Cyan
Write-Host "  kubectl get nodes"
Write-Host "  kubectl top nodes"
Write-Host "  kubectl get pods -n keda"
Write-Host "  kubectl get pods -n monitoring"
Write-Host "  kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090"
Write-Host "  # then open http://localhost:9090 in your browser"
Write-Host ""
Write-Host "Grafana (admin / admin):"
Write-Host "  kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80"
Write-Host "  # then open http://localhost:3000 in your browser"
