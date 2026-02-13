#!/usr/bin/env bash
# ---------------------------------------------------------------
# deploy.sh — Deploy the launcher to Kubernetes
#
# Applies: namespace → launcher (RBAC + Deployment + Service)
# ---------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================================="
echo "  Deploying to Kubernetes"
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
    echo "   Make sure Docker Desktop Kubernetes is enabled,"
    echo "   or your minikube/kind cluster is running."
    exit 1
fi

echo "▸ Creating namespace..."
kubectl apply -f k8s/namespace.yaml
echo ""

echo "▸ Deploying launcher (RBAC + Deployment + Service)..."
kubectl apply -f k8s/launcher.yaml
echo ""

echo "▸ Waiting for launcher pod to be ready..."
kubectl -n airflow-dev rollout status deployment/launcher --timeout=60s
echo ""

echo "=============================================="
echo "  ✓ Deployment complete!"
echo ""
echo "  Open the launcher:"
echo "    http://localhost:30080"
echo ""
echo "  Useful commands:"
echo "    kubectl -n airflow-dev get pods"
echo "    kubectl -n airflow-dev logs -f deploy/launcher"
echo "    kubectl -n airflow-dev delete pod airflow-workspace"
echo "=============================================="
