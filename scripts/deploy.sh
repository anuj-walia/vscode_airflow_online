#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — install or upgrade the platform on GKE or EKS.
#
#   scripts/deploy.sh --env prod \
#     --domain airflow.example.com \
#     --image europe-west1-docker.pkg.dev/proj/airflow/airflow-workspace@sha256:...
#
#   scripts/deploy.sh --cloud aws \
#     --domain airflow.example.com \
#     --certificate-arn arn:aws:acm:eu-west-1:111122223333:certificate/ID \
#     --image 111122223333.dkr.ecr.eu-west-1.amazonaws.com/airflow-workspace:3.3.2-<sha>
#
# Options:
#   --cloud <gke|aws>    target cloud (default: gke)
#   --env <dev|prod|aws-prod>
#                        which environments/<env>.values.yaml to layer on
#                        (default: dev for gke, aws-prod for aws)
#   --certificate-arn <arn>
#                        ACM certificate for the ALB (required with --cloud aws)
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

CLOUD="gke"
ENVIRONMENT=""
CERT_ARN=""
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
        --cloud)     CLOUD="$2"; shift 2 ;;
        --env)       ENVIRONMENT="$2"; shift 2 ;;
        --certificate-arn) CERT_ARN="$2"; shift 2 ;;
        --domain)    DOMAIN="$2"; shift 2 ;;
        --image)     IMAGE="$2"; shift 2 ;;
        -n|--namespace) NAMESPACE="$2"; shift 2 ;;
        --release)   RELEASE="$2"; shift 2 ;;
        --cpu)       CPU="$2"; shift 2 ;;
        --memory)    MEMORY="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=true; shift ;;
        --diff)      DIFF=true; shift ;;
        -h|--help)   sed -n '2,36p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

case "$CLOUD" in
    gke) ENVIRONMENT="${ENVIRONMENT:-dev}" ;;
    aws) ENVIRONMENT="${ENVIRONMENT:-aws-prod}" ;;
    *) echo "--cloud must be gke or aws, got: $CLOUD" >&2; exit 1 ;;
esac
if [[ "$CLOUD" == "aws" && "$ENVIRONMENT" != aws-* ]]; then
    echo "--cloud aws needs an aws-* environment (e.g. aws-prod), got: $ENVIRONMENT" >&2; exit 1
fi
if [[ "$CLOUD" == "gke" && "$ENVIRONMENT" == aws-* ]]; then
    echo "environment $ENVIRONMENT is for AWS; pass --cloud aws" >&2; exit 1
fi
if [[ "$CLOUD" == "gke" && -n "$CERT_ARN" ]]; then
    echo "--certificate-arn only applies to --cloud aws; GKE uses a Google-managed certificate" >&2; exit 1
fi

VALUES_FILE="environments/${ENVIRONMENT}.values.yaml"
[[ -f "$VALUES_FILE" ]] || { echo "no such environment file: $VALUES_FILE" >&2; exit 1; }
# aws-prod -> airflow-prod, matching the GKE naming.
NAMESPACE="${NAMESPACE:-airflow-${ENVIRONMENT#aws-}}"

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

if [[ "$CLOUD" == "aws" ]]; then
    if [[ -z "$CERT_ARN" ]]; then
        note "✗" "--certificate-arn is required with --cloud aws: the ALB terminates TLS with an ACM certificate (docs/aws-setup.md, step 5)"
        fail=1
    elif [[ "$CERT_ARN" != arn:aws*:acm:*:*:certificate/* ]]; then
        note "✗" "--certificate-arn does not look like an ACM certificate ARN: ${CERT_ARN}"
        fail=1
    else
        note "✓" "ACM certificate ${CERT_ARN##*/}"
    fi
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

if [[ "$CLOUD" == "aws" ]]; then
    # No ALB controller means the Ingress is created and never gets an address;
    # no EBS CSI driver means every workspace volume stays Pending.
    if kubectl get ingressclass alb >/dev/null 2>&1; then
        note "✓" "AWS Load Balancer Controller installed (ingressclass alb)"
    else
        note "✗" "no 'alb' IngressClass; install the AWS Load Balancer Controller (docs/aws-setup.md, step 2)"
        fail=1
    fi
    if kubectl get csidriver ebs.csi.aws.com >/dev/null 2>&1; then
        note "✓" "EBS CSI driver installed"
    else
        note "✗" "EBS CSI driver missing; workspace volumes cannot be provisioned (add the aws-ebs-csi-driver EKS add-on)"
        fail=1
    fi
    # NetworkPolicy is what keeps workspaces off IMDS and away from each other.
    # The VPC CNI only enforces it when the network policy agent is enabled.
    if kubectl -n kube-system get daemonset aws-node -o jsonpath='{.spec.template.spec.containers[*].name}' 2>/dev/null | grep -q "aws-eks-nodeagent"; then
        note "✓" "VPC CNI network policy agent present"
    else
        note "!" "could not confirm NetworkPolicy enforcement. Workspace isolation depends on the VPC CNI with enableNetworkPolicy=true (or Calico) -- see docs/aws-setup.md."
    fi
# NetworkPolicy is what keeps workspaces off the metadata server and away from
# each other. Without an enforcing CNI those policies are silently inert.
elif kubectl get crd >/dev/null 2>&1 && \
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

if [[ "$CLOUD" == "aws" ]]; then
    # Dots in an annotation key must be escaped for --set.
    ARGS+=(--set "jupyterhub.ingress.annotations.alb\\.ingress\\.kubernetes\\.io/certificate-arn=${CERT_ARN}")
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

if [[ "$CLOUD" == "aws" ]]; then
    cat <<EOF

==============================================================
  ✓ deployed

  URL        https://${DOMAIN}
  namespace  ${NAMESPACE}
  release    ${RELEASE}

  The ALB takes a few minutes to provision. Once the Ingress has an
  ADDRESS, point ${DOMAIN} at it with a Route 53 alias (or CNAME):

    kubectl -n ${NAMESPACE} get ingress
    # ADDRESS is the ALB hostname, e.g. k8s-...elb.amazonaws.com

  Watch a login and a spawn:

    kubectl -n ${NAMESPACE} logs -f deploy/hub
    kubectl -n ${NAMESPACE} get pods -l component=singleuser-server -w
==============================================================
EOF
    exit 0
fi

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
