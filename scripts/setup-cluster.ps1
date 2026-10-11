<#
.SYNOPSIS
    Sets up the local Minikube cluster, installs metrics-server, KEDA, and Prometheus stack.
    Pinned strictly per docs/versions.md and PLAN.md.
#>
[CmdletBinding()]
param(
    [int]    $Cpus              = 4,
    [int]    $Memory            = 6144,
    [string] $KubernetesVersion = "v1.35.1",
    [string] $KedaVersion       = "2.21.0",
    [string] $PrometheusVersion = "92.1.1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host ">>> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param([string]$Description, [scriptblock]$Block)
    Write-Host "    $Description ..." -ForegroundColor Gray
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Error "FAILED: $Description (exit code $LASTEXITCODE)"
    }
}

Write-Step "Checking Minikube status"
$status = minikube status --format "{{.Host}}" 2>$null
if ($status -ne "Running") {
    Write-Step "Starting Minikube (k8s: $KubernetesVersion, cpus: $Cpus, mem: ${Memory}MB)"
    Invoke-Checked "minikube start" {
        minikube start `
            --driver=docker `
            --kubernetes-version=$KubernetesVersion `
            --cpus=$Cpus `
            --memory=$Memory `
            --addons=metrics-server `
            --embed-certs
    }
} else {
    Write-Host "    Minikube already running." -ForegroundColor Green
}

Write-Step "Ensuring metrics-server addon is enabled"
Invoke-Checked "minikube addons enable metrics-server" {
    minikube addons enable metrics-server
}

Write-Step "Waiting for metrics-server rollout"
Invoke-Checked "metrics-server ready" {
    kubectl rollout status deployment/metrics-server -n kube-system --timeout=120s
}

Write-Step "Adding and updating Helm repositories"
Invoke-Checked "helm repo add kedacore" {
    helm repo add kedacore https://kedacore.github.io/charts
}
Invoke-Checked "helm repo add prometheus-community" {
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
}
Invoke-Checked "helm repo update" {
    helm repo update
}

Write-Step "Installing KEDA $KedaVersion"
kubectl create namespace keda --dry-run=client -o yaml | kubectl apply -f -
$kedaInstalled = helm list -n keda --filter "^keda$" --short 2>$null
if ($kedaInstalled -eq "keda") {
    Invoke-Checked "helm upgrade keda" {
        helm upgrade keda kedacore/keda --namespace keda --version $KedaVersion --wait --timeout 5m
    }
} else {
    Invoke-Checked "helm install keda" {
        helm install keda kedacore/keda --namespace keda --version $KedaVersion --wait --timeout 5m
    }
}

Write-Step "Installing kube-prometheus-stack $PrometheusVersion"
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
$promInstalled = helm list -n monitoring --filter "^prometheus$" --short 2>$null
if ($promInstalled -eq "prometheus") {
    Invoke-Checked "helm upgrade prometheus" {
        helm upgrade prometheus prometheus-community/kube-prometheus-stack `
            --namespace monitoring `
            --version $PrometheusVersion `
            --set grafana.adminPassword=admin `
            --set prometheus.prometheusSpec.scrapeInterval=15s `
            --set prometheus.prometheusSpec.evaluationInterval=15s `
            --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false `
            --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false `
            --wait --timeout 10m
    }
} else {
    Invoke-Checked "helm install prometheus" {
        helm install prometheus prometheus-community/kube-prometheus-stack `
            --namespace monitoring `
            --version $PrometheusVersion `
            --set grafana.adminPassword=admin `
            --set prometheus.prometheusSpec.scrapeInterval=15s `
            --set prometheus.prometheusSpec.evaluationInterval=15s `
            --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false `
            --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false `
            --wait --timeout 10m
    }
}

Write-Step "Cluster setup complete and verified!"
