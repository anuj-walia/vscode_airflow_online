#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — install or upgrade the platform on GKE.
#
#   scripts/deploy.sh --env prod \
#     --domain airflow.example.com \
#     --image europe-west1-docker.pkg.dev/proj/airflow/airflow-workspace@sha256:...
#
# Options:
#   --env <dev|prod>     which environments/<env>.values.yaml to layer on
#   --domain <host>      public hostname; sets platform.domain, the ingress
#                        host and the OAuth callback in one place
#   --image <ref>        workspace image, ideally name@sha256:... A tag is
#                        accepted but warned about
#   --namespace <ns>     default: airflow-<env>
#   --release <name>     default: airflow-platform
#   --cpu <n>            workspace CPU request and limit (default from values)
#   --memory <size>      workspace memory request and limit, e.g. 16G, 32G
#   --dry-run            render and validate without applying
#   --diff               show what would change (needs the helm-diff plugin)
#
# Workspace sizing note: --cpu/--memory set request == limit, which puts the
# pod in the Guaranteed QoS class. The committed default is 1 CPU / 16G.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
source VERSIONS.env

ENVIRONMENT="dev"
DOMAIN=""
IMAGE=""
NAMESPACE=""
RELEASE="airflow-platform"
CPU=""
MEMORY=""
DRY_RUN=false
DIFF=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)       ENVIRONMENT="$2"; shift 2 ;;
        --domain)    DOMAIN="$2"; shift 2 ;;
        --image)     IMAGE="$2"; shift 2 ;;
        -n|--namespace) NAMESPACE="$2"; shift 2 ;;
        --release)   RELEASE="$2"; shift 2 ;;
        --cpu)       CPU="$2"; shift 2 ;;
        --memory)    MEMORY="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=true; shift ;;
        --diff)      DIFF=true; shift ;;
        -h|--help)   sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

VALUES_FILE="environments/${ENVIRONMENT}.values.yaml"
[[ -f "$VALUES_FILE" ]] || { echo "no such environment file: $VALUES_FILE" >&2; exit 1; }
NAMESPACE="${NAMESPACE:-airflow-${ENVIRONMENT}}"

for tool in helm kubectl; do
    command -v "$tool" >/dev/null || { echo "$tool is required but not installed" >&2; exit 1; }
done

# ---------------------------------------------------------------------------
# Preflight. These are the mistakes that produce a platform that looks healthy
# but is either unreachable or unprotected.
# ---------------------------------------------------------------------------
fail=0
note() { printf '  %s %s\n' "$1" "$2"; }

echo "==> preflight"

if [[ -z "$DOMAIN" ]]; then
    note "✗" "--domain is required: it sets the ingress host, the managed certificate and the OIDC callback URL"
    fail=1
else
    note "✓" "domain ${DOMAIN}"
fi

if [[ -z "$IMAGE" ]]; then
    note "✗" "--image is required; build one with scripts/build-image.sh --push"
    fail=1
elif [[ "$IMAGE" != *"@sha256:"* ]]; then
    note "!" "image ${IMAGE} is a mutable tag. Production should deploy a digest so a rollback is exact."
else
    note "✓" "image pinned by digest"
fi

if ! kubectl cluster-info >/dev/null 2>&1; then
    note "✗" "cannot reach a Kubernetes cluster; check your kubeconfig context"
    fail=1
else
    note "✓" "cluster $(kubectl config current-context)"
fi

# The Hub cannot start without its OIDC credentials, so catch a missing Secret
# here rather than in a CrashLoopBackOff.
if kubectl -n "$NAMESPACE" get secret airflow-platform-oidc >/dev/null 2>&1; then
    note "✓" "OIDC secret present in ${NAMESPACE}"
else
    note "✗" "secret airflow-platform-oidc missing in ${NAMESPACE}; run scripts/create-oidc-secret.sh first"
    fail=1
fi

# NetworkPolicy is what keeps workspaces off the metadata server and away from
# each other. Without an enforcing CNI those policies are silently inert.
if kubectl get crd >/dev/null 2>&1 && \
   ! kubectl get nodes -o jsonpath='{.items[*].metadata.labels}' 2>/dev/null | grep -q "cloud.google.com/gke-netd\|projectcalico.org\|dataplane"; then
    note "!" "could not confirm NetworkPolicy enforcement on this cluster. Workspace isolation depends on it (GKE Dataplane V2 or Calico) -- see docs/gcp-setup.md."
fi

[[ "$fail" -eq 0 ]] || { echo; echo "preflight failed"; exit 1; }
echo

# ---------------------------------------------------------------------------
# Helm
# ---------------------------------------------------------------------------
echo "==> fetching chart dependencies (zero-to-jupyterhub ${Z2JH_CHART_VERSION})"
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ >/dev/null 2>&1 || true
helm repo update jupyterhub >/dev/null
helm dependency update charts/airflow-dev-platform >/dev/null
echo "  ✓ dependencies ready"
echo

ARGS=(
    upgrade --install "$RELEASE" charts/airflow-dev-platform
    --namespace "$NAMESPACE"
    --create-namespace
    --values "$VALUES_FILE"
    --set "platform.domain=${DOMAIN}"
    --set "jupyterhub.hub.extraEnv.PLATFORM_DOMAIN=${DOMAIN}"
    --set "jupyterhub.ingress.hosts={${DOMAIN}}"
)

# Image reference.
#
# z2jh builds the workspace image string as "{name}:{tag}" and omits the colon
# only when tag is empty (files/hub/jupyterhub_config.py). A digest reference
# therefore has to go in `name` in full, with `tag` empty.
#
# The image pre-puller templates have no such empty-tag handling -- they
# interpolate "{name}:{tag}" unconditionally -- so a digest deployment must
# also turn the pre-puller off, or the DaemonSet renders an unparseable image
# reference. The trade-off is a slower first spawn on a cold node.
#
# If you want both pre-pulling and an immutable reference, push to an Artifact
# Registry repository created with --immutable-tags and deploy the tag: the
# tag can then never be repointed, which is the property the digest was for.
if [[ "$IMAGE" == *"@sha256:"* ]]; then
    ARGS+=(--set "jupyterhub.singleuser.image.name=${IMAGE}")
    ARGS+=(--set "jupyterhub.singleuser.image.tag=")
    ARGS+=(--set "jupyterhub.prePuller.hook.enabled=false")
    ARGS+=(--set "jupyterhub.prePuller.continuous.enabled=false")
    note "!" "digest deployment: image pre-pulling disabled (z2jh cannot template a digest into its puller DaemonSet). First spawn on a cold node will wait for the pull."
else
    ARGS+=(--set "jupyterhub.singleuser.image.name=${IMAGE%:*}")
    ARGS+=(--set "jupyterhub.singleuser.image.tag=${IMAGE##*:}")
fi

if [[ -n "$CPU" ]]; then
    ARGS+=(--set "jupyterhub.singleuser.cpu.limit=${CPU}")
    ARGS+=(--set "jupyterhub.singleuser.cpu.guarantee=${CPU}")
fi
if [[ -n "$MEMORY" ]]; then
    ARGS+=(--set "jupyterhub.singleuser.memory.limit=${MEMORY}")
    ARGS+=(--set "jupyterhub.singleuser.memory.guarantee=${MEMORY}")
fi

if [[ "$DIFF" == true ]]; then
    command -v helm-diff >/dev/null 2>&1 || helm plugin list 2>/dev/null | grep -q '^diff' \
        || { echo "the helm-diff plugin is not installed: helm plugin install https://github.com/databus23/helm-diff" >&2; exit 1; }
    echo "==> helm diff"
    # ARGS starts with "upgrade --install <release> <chart> ..."; `helm diff`
    # takes the same words after its own subcommand.
    helm diff "${ARGS[@]}"
    exit 0
fi

if [[ "$DRY_RUN" == true ]]; then
    echo "==> rendering (dry run)"
    helm "${ARGS[@]}" --dry-run --debug >/dev/null
    echo "  ✓ templates render and validate"
    exit 0
fi

echo "==> helm upgrade --install ${RELEASE} -> ${NAMESPACE}"
helm "${ARGS[@]}" --timeout 15m --wait

cat <<EOF

==============================================================
  ✓ deployed

  URL        https://${DOMAIN}
  namespace  ${NAMESPACE}
  release    ${RELEASE}

  A Google-managed certificate takes up to ~20 minutes and needs
  ${DOMAIN} pointing at the ingress address first:

    kubectl -n ${NAMESPACE} get ingress
    kubectl -n ${NAMESPACE} describe managedcertificate airflow-dev-platform-cert

  Watch a login and a spawn:

    kubectl -n ${NAMESPACE} logs -f deploy/hub
    kubectl -n ${NAMESPACE} get pods -l component=singleuser-server -w
==============================================================
EOF
