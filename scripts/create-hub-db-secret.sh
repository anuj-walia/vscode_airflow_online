#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# create-hub-db-secret.sh — give the Hub a password for its Cloud SQL database
# and store it in the Secret the chart reads (PGPASSWORD). The password is
# generated here, never printed, and never written to this repository.
#
#   scripts/create-hub-db-secret.sh \
#     --namespace airflow-waas \
#     --instance waas-hub-db
#
# Creates the database user (default `hub`) on the instance, or resets its
# password if it already exists, then stores the password in Secret
# airflow-platform-hub-db. Re-running does nothing if the Secret is already
# there; pass --rotate to generate a new password and restart the Hub.
#
# Caveat: `gcloud sql users create` only accepts --password on the command
# line (no prompt or file option), so the generated value is visible in the
# local process list for about a second. It is a random 32 character value,
# not a password a person chose.
# ---------------------------------------------------------------------------
set -euo pipefail

NAMESPACE=""
INSTANCE=""
DB_USER="hub"
PROJECT=""
SECRET_NAME="airflow-platform-hub-db"
ROTATE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--namespace) NAMESPACE="$2"; shift 2 ;;
        --instance)     INSTANCE="$2"; shift 2 ;;
        --db-user)      DB_USER="$2"; shift 2 ;;
        --project)      PROJECT="$2"; shift 2 ;;
        --name)         SECRET_NAME="$2"; shift 2 ;;
        --rotate)       ROTATE="true"; shift ;;
        -h|--help)      sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

for required in NAMESPACE INSTANCE; do
    if [[ -z "${!required}" ]]; then
        echo "missing required argument: --${required,,}" >&2
        exit 1
    fi
done

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "$PROJECT" ]]; then
    echo "no project: pass --project or run 'gcloud config set project <id>'" >&2
    exit 1
fi

echo "==> checking instance ${INSTANCE} in ${PROJECT}"
if ! DESCRIBE="$(gcloud sql instances describe "$INSTANCE" --project "$PROJECT" --format=json 2>/dev/null)"; then
    echo "instance ${INSTANCE} not found (or no permission) in ${PROJECT}" >&2
    exit 1
fi
PRIVATE_IP="$(printf '%s' "$DESCRIBE" | python3 -c '
import json
import sys

addresses = json.load(sys.stdin).get("ipAddresses", [])
print(next((a["ipAddress"] for a in addresses if a.get("type") == "PRIVATE"), ""))
')"
if [[ -z "$PRIVATE_IP" ]]; then
    echo "instance ${INSTANCE} has no private IP; the Hub reaches it over the VPC" >&2
    exit 1
fi
echo "  ✓ private IP ${PRIVATE_IP}"

if kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null 2>&1 && [[ "$ROTATE" != "true" ]]; then
    echo "  ✓ secret ${NAMESPACE}/${SECRET_NAME} already exists; nothing to do (use --rotate to replace the password)"
else
    PASSWORD="$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-32)"
    if [[ "${#PASSWORD}" -ne 32 ]]; then
        echo "could not generate a password" >&2
        exit 1
    fi

    if gcloud sql users list --instance "$INSTANCE" --project "$PROJECT" \
            --filter="name=${DB_USER}" --format="value(name)" | grep -qx "$DB_USER"; then
        echo "==> database user ${DB_USER} exists; setting a new password"
        gcloud sql users set-password "$DB_USER" --instance "$INSTANCE" --project "$PROJECT" \
            --password "$PASSWORD" >/dev/null
    else
        echo "==> creating database user ${DB_USER}"
        gcloud sql users create "$DB_USER" --instance "$INSTANCE" --project "$PROJECT" \
            --password "$PASSWORD" >/dev/null
    fi

    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

    kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
        --from-literal=password="$PASSWORD" \
        --dry-run=client -o yaml | kubectl apply -f -
    unset PASSWORD

    echo "  ✓ secret ${NAMESPACE}/${SECRET_NAME} created or updated"
    if [[ "$ROTATE" == "true" ]] && kubectl -n "$NAMESPACE" get deploy hub >/dev/null 2>&1; then
        kubectl -n "$NAMESPACE" rollout restart deploy/hub
        echo "  ✓ Hub restarted to pick up the new password"
    fi
fi

echo
echo "Point the chart at the database in environments/<env>.values.yaml:"
echo "    jupyterhub.hub.db.type: postgres"
echo "    jupyterhub.hub.db.url:  postgresql+psycopg2://${DB_USER}@${PRIVATE_IP}:5432/jupyterhub?sslmode=require"
