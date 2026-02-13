#!/usr/bin/env bash
# ---------------------------------------------------------------
# build.sh — Build all Docker images for the Kubernetes deployment
#
# Builds 3 images:
#   1. airflow-jupyter:airflow2  (from main branch)
#   2. airflow-jupyter:airflow3  (from airflow3 branch)
#   3. airflow-launcher:latest   (from launcher/)
# ---------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CURRENT_BRANCH=$(git branch --show-current)

# .vscode/launch.json was never committed but Dockerfiles COPY it.
# Create a placeholder if missing so the build succeeds.
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

# Restore the original branch on exit (even on failure)
cleanup() {
    cleanup_vscode_dir
    git checkout "$CURRENT_BRANCH" -q 2>/dev/null || true
    git stash pop -q 2>/dev/null || true
}
trap cleanup EXIT

echo "=============================================="
echo "  Building Airflow Kubernetes Images"
echo "=============================================="
echo ""

# ---------------------------------------------------------------
# 1. Build Airflow 2 image from main branch
# ---------------------------------------------------------------
echo "▸ Building airflow-jupyter:airflow2 (main branch)..."
git stash --include-untracked -q 2>/dev/null || true
git checkout main -q

ensure_vscode_dir
docker build -t airflow-jupyter:airflow2 .
cleanup_vscode_dir

echo "  ✓ airflow-jupyter:airflow2 built"
echo ""

# ---------------------------------------------------------------
# 2. Build Airflow 3 image from airflow3 branch
# ---------------------------------------------------------------
echo "▸ Building airflow-jupyter:airflow3 (airflow3 branch)..."
git checkout airflow3 -q

ensure_vscode_dir
docker build -t airflow-jupyter:airflow3 .
cleanup_vscode_dir

echo "  ✓ airflow-jupyter:airflow3 built"
echo ""

# ---------------------------------------------------------------
# 3. Switch back and build the launcher image
# ---------------------------------------------------------------
git checkout "$CURRENT_BRANCH" -q
git stash pop -q 2>/dev/null || true

echo "▸ Building airflow-launcher:latest..."
docker build -t airflow-launcher:latest ./launcher/
echo "  ✓ airflow-launcher:latest built"
echo ""

echo "=============================================="
echo "  All images built successfully!"
echo ""
echo "  Images:"
echo "    • airflow-jupyter:airflow2"
echo "    • airflow-jupyter:airflow3"
echo "    • airflow-launcher:latest"
echo ""
echo "  Next: run ./deploy.sh"
echo "=============================================="
