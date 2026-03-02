#!/usr/bin/env bash
# =============================================================================
# build.sh — Build the single Airflow + JupyterLab Docker image
#
# Builds just 2 images:
#   1. airflow-jupyter:latest  (single image with all Python versions)
#   2. airflow-hub:latest      (JupyterHub + KubeSpawner)
#
# Airflow is NOT installed at build time — it's installed at first
# pod startup based on the user's version selection.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# .vscode/launch.json may not be committed; create placeholder if missing
if [ ! -f .vscode/launch.json ]; then
    echo "  (creating placeholder .vscode/launch.json for Docker build)"
    mkdir -p .vscode
    echo '{"version":"0.2.0","configurations":[]}' > .vscode/launch.json
    CREATED_VSCODE=true
else
    CREATED_VSCODE=false
fi

cleanup() {
    if [ "${CREATED_VSCODE:-false}" = true ]; then
        rm -f .vscode/launch.json
        rmdir .vscode 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "=============================================="
echo "  Building Airflow Kubernetes Images"
echo "=============================================="
echo ""

# ---------------------------------------------------------------
# 1. Build the single Airflow image
# ---------------------------------------------------------------
echo "▸ Building airflow-jupyter:latest..."
echo "  (Includes Python 3.9, 3.10, 3.11, 3.12)"
echo "  (Airflow will be installed at first pod startup)"
docker build -t airflow-jupyter:latest .
echo "  ✓ airflow-jupyter:latest built"
echo ""

# ---------------------------------------------------------------
# 2. Build the JupyterHub image
# ---------------------------------------------------------------
echo "▸ Building airflow-hub:latest (JupyterHub + KubeSpawner)..."
docker build -t airflow-hub:latest ./hub/
echo "  ✓ airflow-hub:latest built"
echo ""

echo "=============================================="
echo "  All images built successfully!"
echo ""
echo "  Images:"
echo "    • airflow-jupyter:latest  (runtime Airflow install)"
echo "    • airflow-hub:latest      (JupyterHub)"
echo ""
echo "  Next: run ./deploy.sh"
echo "=============================================="
