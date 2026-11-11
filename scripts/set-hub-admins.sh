#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# set-hub-admins.sh — choose who becomes a JupyterHub admin at sign-in. The
# emails go in a Secret, not in git, so the repository holds no personal
# addresses.
#
#   scripts/set-hub-admins.sh \
#     --namespace airflow-waas \
#     --emails first.person@example.com,second.person@example.com
#
# Matching is on the verified email in the identity provider's ID token. It
# only grants admin: an email removed from this list is not demoted (do that
# in the Hub's /hub/admin page). The Hub reads the list when it starts, so the
# script restarts it if it is already running.
#
# Clear the list with --emails "".
# ---------------------------------------------------------------------------
set -euo pipefail

NAMESPACE=""
EMAILS=""
EMAILS_SET="false"
SECRET_NAME="airflow-platform-admins"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--namespace) NAMESPACE="$2"; shift 2 ;;
        --emails)       EMAILS="$2"; EMAILS_SET="true"; shift 2 ;;
        --name)         SECRET_NAME="$2"; shift 2 ;;
        -h|--help)      sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$NAMESPACE" ]]; then
    echo "missing required argument: --namespace" >&2
    exit 1
fi
if [[ "$EMAILS_SET" != "true" ]]; then
    echo "missing required argument: --emails (use --emails \"\" to clear the list)" >&2
    exit 1
fi

# Normalise: lower-case, strip spaces, drop empties; reject anything that is
# not shaped like an address so a typo does not silently grant nothing.
CLEAN=""
IFS=',' read -ra PARTS <<< "$EMAILS"
for part in "${PARTS[@]:-}"; do
    email="$(printf '%s' "$part" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
    [[ -z "$email" ]] && continue
    if [[ ! "$email" =~ ^[^@,]+@[^@,]+\.[^@,]+$ ]]; then
        echo "not an email address: ${part}" >&2
        exit 1
    fi
    CLEAN="${CLEAN:+${CLEAN},}${email}"
done

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
    --from-literal=emails="$CLEAN" \
    --dry-run=client -o yaml | kubectl apply -f -

COUNT=0
[[ -n "$CLEAN" ]] && COUNT="$(printf '%s' "$CLEAN" | tr ',' '\n' | wc -l | tr -d ' ')"
echo "  ✓ secret ${NAMESPACE}/${SECRET_NAME} holds ${COUNT} admin email(s)"

if kubectl -n "$NAMESPACE" get deploy hub >/dev/null 2>&1; then
    kubectl -n "$NAMESPACE" rollout restart deploy/hub
    echo "  ✓ Hub restarted; the list applies at each person's next sign-in"
fi
