#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build-image.sh — build and push the Airflow 3 workspace image.
#
# Unlike the build script on the k8s branch, this never stashes the worktree
# or checks out another branch: there is exactly one image, built from
# images/workspace in this branch.
#
#   scripts/build-image.sh \
#     --registry europe-west1-docker.pkg.dev/my-project/airflow \
#     (for ECR: --registry 111122223333.dkr.ecr.eu-west-1.amazonaws.com) \
#     --push
#
# Prints the immutable digest reference on success. Deploy that, not the tag.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=../VERSIONS.env
source VERSIONS.env

REGISTRY=""
IMAGE_NAME="airflow-workspace"
TAG=""
PUSH=false
PLATFORM="linux/amd64"   # GKE/EKS x86 nodes; use linux/arm64 for Arm (Tau T2A, Graviton) pools

usage() {
    sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --registry) REGISTRY="$2"; shift 2 ;;
        --name)     IMAGE_NAME="$2"; shift 2 ;;
        --tag)      TAG="$2"; shift 2 ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        --push)     PUSH=true; shift ;;
        -h|--help)  usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

# Default tag ties the image to both the Airflow release and the exact source
# it was built from, so a running pod can be traced back to a commit.
if [[ -z "$TAG" ]]; then
    GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo nogit)"
    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        GIT_SHA="${GIT_SHA}-dirty"
    fi
    TAG="${AIRFLOW_VERSION}-${GIT_SHA}"
fi

if [[ -n "$REGISTRY" ]]; then
    IMAGE_REF="${REGISTRY%/}/${IMAGE_NAME}:${TAG}"
else
    IMAGE_REF="${IMAGE_NAME}:${TAG}"
    if [[ "$PUSH" == true ]]; then
        echo "--push requires --registry" >&2
        exit 1
    fi
fi

echo "==> building ${IMAGE_REF}"
echo "    airflow      ${AIRFLOW_VERSION}"
echo "    python       ${PYTHON_VERSION} (${BASE_IMAGE})"
echo "    code-server  ${CODE_SERVER_VERSION}"
echo "    platform     ${PLATFORM}"
echo

docker build \
    --platform "${PLATFORM}" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "AIRFLOW_VERSION=${AIRFLOW_VERSION}" \
    --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
    --build-arg "CODE_SERVER_VERSION=${CODE_SERVER_VERSION}" \
    --label "org.opencontainers.image.title=airflow-workspace" \
    --label "org.opencontainers.image.version=${AIRFLOW_VERSION}" \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    -t "${IMAGE_REF}" \
    images/workspace

echo "  ✓ built ${IMAGE_REF}"

# Cheap smoke test: the image must be able to import Airflow, report the
# expected version, and parse the seeded example DAG as the non-root user.
echo
echo "==> smoke testing the image"
docker run --rm -i --platform "${PLATFORM}" --entrypoint /opt/venv/bin/python "${IMAGE_REF}" - <<'PYSMOKE'
import os
import subprocess
import sys

import airflow

print("airflow", airflow.__version__)
assert sys.version_info[:2] >= (3, 10), sys.version

# The runtime user must not be root.
assert os.getuid() == 1000, f"expected uid 1000, got {os.getuid()}"

# Image assets must live outside AIRFLOW_HOME, which a PVC shadows in k8s.
for path in (
    "/srv/workspace/bin/start.sh",
    "/srv/workspace/bin/workspace_services.py",
    "/srv/workspace/icons/airflow-api.svg",
    "/opt/venv/etc/jupyter/jupyter_server_config.py",
):
    assert os.path.exists(path), f"missing image asset: {path}"

subprocess.run([sys.executable, "-c", "import jupyter_server_proxy"], check=True)

# Compile in memory rather than with py_compile: the runtime user deliberately
# cannot write a __pycache__ into the image's asset directories.
for example in os.listdir("/srv/workspace/examples"):
    if example.endswith(".py"):
        path = os.path.join("/srv/workspace/examples", example)
        with open(path, encoding="utf-8") as handle:
            compile(handle.read(), path, "exec")

# The image must not be writable by the user it runs as.
for readonly in ("/srv/workspace", "/opt/venv"):
    assert not os.access(readonly, os.W_OK), f"{readonly} is writable by the runtime user"

print("smoke test OK")
PYSMOKE

if [[ "$PUSH" == true ]]; then
    echo
    echo "==> pushing ${IMAGE_REF}"
    docker push "${IMAGE_REF}"
    DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${IMAGE_REF}")"
    echo
    echo "  ✓ pushed"
    echo
    echo "Deploy this immutable reference:"
    echo "    ${DIGEST}"
    echo
    echo "    scripts/deploy.sh --env prod --domain <host> --image '${DIGEST}'"
else
    echo
    echo "Built locally (not pushed): ${IMAGE_REF}"
    echo "Add --registry <host>/<project>/<repo> --push to publish it."
fi
