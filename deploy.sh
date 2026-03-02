#!/usr/bin/env bash
# ---------------------------------------------------------------
# deploy.sh — Deploy JupyterHub to Kubernetes
#
# Applies: namespace → hub (RBAC + PVC + Deployment + Service)
# ---------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================================="
echo "  Deploying JupyterHub to Kubernetes"
echo "=============================================="
echo ""

# Check kubectl is available
if ! command -v kubectl &>/dev/null; then
    echo "❌ kubectl not found. Please install kubectl first."
    exit 1
fi

# Check cluster connectivity
if ! kubectl cluster-info &>/dev/null; then
    echo "❌ Cannot connect to Kubernetes cluster."
    echo "   Make sure Docker Desktop / OrbStack Kubernetes is enabled."
    exit 1
fi

echo "▸ Creating namespace..."
kubectl apply -f k8s/namespace.yaml
echo ""

echo "▸ Deploying JupyterHub (RBAC + PVC + Deployment + Service)..."
kubectl apply -f k8s/hub.yaml
echo ""

echo "▸ Waiting for JupyterHub pod to be ready..."
kubectl -n airflow-dev rollout status deployment/hub --timeout=120s
echo ""

echo "=============================================="
echo "  ✓ JupyterHub deployed!"
echo ""
echo "  Open JupyterHub:"
echo "    http://localhost:30080"
echo ""
echo "  First time? Register a new account on the sign-up page."
echo "  Then log in and pick your Airflow version."
echo ""
echo "  Useful commands:"
echo "    kubectl -n airflow-dev get pods"
echo "    kubectl -n airflow-dev logs -f deploy/hub"
echo "    kubectl delete namespace airflow-dev  # tear down"
echo "=============================================="
