#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# create-oidc-secret.sh — create the Secret the Hub reads its IdP credentials
# from. Nothing about the client secret is ever written to this repository.
#
#   scripts/create-oidc-secret.sh \
#     --namespace airflow-prod \
#     --issuer https://login.example.com/oauth2/default \
#     --client-id 0oa1b2c3d4e5f6g7h8i9
#
# The client secret is read from the OIDC_CLIENT_SECRET environment variable,
# or prompted for without echo. It is never passed on the command line, where
# it would land in shell history and in `ps` output.
#
# For a GitOps deployment, keep the credential in Secret Manager and sync it
# with External Secrets Operator instead -- see docs/gcp-setup.md.
# ---------------------------------------------------------------------------
set -euo pipefail

NAMESPACE=""
ISSUER=""
CLIENT_ID=""
SECRET_NAME="airflow-platform-oidc"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--namespace) NAMESPACE="$2"; shift 2 ;;
        --issuer)       ISSUER="$2"; shift 2 ;;
        --client-id)    CLIENT_ID="$2"; shift 2 ;;
        --name)         SECRET_NAME="$2"; shift 2 ;;
        -h|--help)      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

for required in NAMESPACE ISSUER CLIENT_ID; do
    if [[ -z "${!required}" ]]; then
        flag="--${required,,}"
        echo "missing required argument: ${flag//_/-}" >&2
        exit 1
    fi
done

if [[ -z "${OIDC_CLIENT_SECRET:-}" ]]; then
    read -rsp "OIDC client secret for ${CLIENT_ID}: " OIDC_CLIENT_SECRET
    echo
fi
if [[ -z "${OIDC_CLIENT_SECRET}" ]]; then
    echo "client secret is empty" >&2
    exit 1
fi

# Verify the issuer publishes a usable discovery document before storing
# anything; a typo here otherwise surfaces as a Hub CrashLoopBackOff.
echo "==> checking OIDC discovery at ${ISSUER%/}/.well-known/openid-configuration"
if ! DISCOVERY="$(curl -fsS --max-time 15 "${ISSUER%/}/.well-known/openid-configuration")"; then
    echo "could not fetch the discovery document; check the issuer URL" >&2
    exit 1
fi
printf '%s' "$DISCOVERY" | python3 -c '
import json
import sys

metadata = json.load(sys.stdin)
missing = [
    key
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint")
    if not metadata.get(key)
]
if missing:
    sys.exit("discovery document is missing required endpoints: " + ", ".join(missing))
print("  \u2713 discovery document looks usable")
'    

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
    --from-literal=issuer="$ISSUER" \
    --from-literal=client-id="$CLIENT_ID" \
    --from-literal=client-secret="$OIDC_CLIENT_SECRET" \
    --dry-run=client -o yaml | kubectl apply -f -

unset OIDC_CLIENT_SECRET

echo
echo "  ✓ secret ${NAMESPACE}/${SECRET_NAME} created or updated"
echo
echo "Register this redirect URI with the identity provider:"
echo "    https://<your-domain>/hub/oauth_callback"
