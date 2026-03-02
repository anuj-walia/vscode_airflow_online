#!/usr/bin/env bash
# =============================================================================
# build.sh — Build Docker images for all supported Airflow + Python combos
#
# Builds images from the unified Dockerfile using build args.
# Each combo produces: airflow-jupyter:{airflow_version}-py{python_version}
# Also builds the JupyterHub image: airflow-hub:latest
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# =============================================================================
# Supported Version Combos — Add new entries here
# Format: "AIRFLOW_VERSION:PYTHON_VERSION"
# =============================================================================
VERSIONS=(
    "2.10.5:3.11"
    "2.11.0:3.11"
    "3.0.1:3.11"
    "3.1.7:3.11"
    "3.1.7:3.12"
)

# .vscode/launch.json may not be committed; create placeholder if missing
ensure_vscode_dir() {
    if [ ! -f .vscode/launch.json ]; then
        echo "  (creating placeholder .vscode/launch.json for Docker build)"
        mkdir -p .vscode
        echo '{"version":"0.2.0","configurations":[]}' > .vscode/launch.json
        CREATED_VSCODE=true
    else
        CREATED_VSCODE=false
    fi
}

cleanup_vscode_dir() {
    if [ "${CREATED_VSCODE:-false}" = true ]; then
        rm -f .vscode/launch.json
        rmdir .vscode 2>/dev/null || true
    fi
}

trap cleanup_vscode_dir EXIT

echo "=============================================="
echo "  Building Airflow Kubernetes Images"
echo "=============================================="
echo ""
echo "  Versions to build:"
for combo in "${VERSIONS[@]}"; do
    IFS=':' read -r AF_VER PY_VER <<< "$combo"
    echo "    • Airflow ${AF_VER} / Python ${PY_VER}"
done
echo ""

# ---------------------------------------------------------------
# Build Airflow images from version matrix
# ---------------------------------------------------------------
ensure_vscode_dir

for combo in "${VERSIONS[@]}"; do
    IFS=':' read -r AF_VER PY_VER <<< "$combo"
    TAG="airflow-jupyter:${AF_VER}-py${PY_VER}"

    echo "▸ Building ${TAG}..."
    docker build \
        --build-arg AIRFLOW_VERSION="${AF_VER}" \
        --build-arg PYTHON_VERSION="${PY_VER}" \
        -t "${TAG}" .
    echo "  ✓ ${TAG} built"
    echo ""
done

cleanup_vscode_dir

# ---------------------------------------------------------------
# Build the JupyterHub image
# ---------------------------------------------------------------
echo "▸ Building airflow-hub:latest (JupyterHub + KubeSpawner)..."
docker build -t airflow-hub:latest ./hub/
echo "  ✓ airflow-hub:latest built"
echo ""

echo "=============================================="
echo "  All images built successfully!"
echo ""
echo "  Images:"
for combo in "${VERSIONS[@]}"; do
    IFS=':' read -r AF_VER PY_VER <<< "$combo"
    echo "    • airflow-jupyter:${AF_VER}-py${PY_VER}"
done
echo "    • airflow-hub:latest (JupyterHub)"
echo ""
echo "  Next: run ./deploy.sh"
echo "=============================================="
