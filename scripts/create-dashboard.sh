#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# create-dashboard.sh — create the "Airflow platform" Cloud Monitoring
# dashboard from observability/gke-dashboard.json.
#
#   scripts/create-dashboard.sh --project astronomer-success \
#     --labels lab=gcp-pca,owner=someone
#
#   --project <id>        required: the project that will own the dashboard
#   --labels k=v,k2=v2    optional: labels to put on the dashboard
#
# Creates exactly one dashboard and prints its name, then the command that
# deletes it. Run it once: a second run creates a second dashboard. This
# script never deletes anything.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD="${REPO_ROOT}/observability/gke-dashboard.json"

PROJECT=""
LABELS=""

usage() {
    sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)  PROJECT="${2:-}"; shift 2 || usage 2 ;;
        --labels)   LABELS="${2:-}"; shift 2 || usage 2 ;;
        -h|--help)  usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 2 ;;
    esac
done

if [[ -z "$PROJECT" ]]; then
    echo "--project is required" >&2
    exit 2
fi

# Cloud Monitoring label keys start with a lowercase letter; keys and values
# otherwise use lowercase letters, digits, _ and -.
if [[ -n "$LABELS" ]] && ! [[ "$LABELS" =~ ^[a-z][a-z0-9_-]*=[a-z0-9_-]*(,[a-z][a-z0-9_-]*=[a-z0-9_-]*)*$ ]]; then
    echo "--labels must look like key=value,key2=value2 (keys start with a lowercase letter; then lowercase letters, digits, _ and -)" >&2
    exit 2
fi

for tool in gcloud python3; do
    command -v "$tool" >/dev/null || { echo "$tool is required but not on PATH" >&2; exit 1; }
done

CONFIG="$(mktemp)"
trap 'rm -f "$CONFIG"' EXIT

python3 -I - "$DASHBOARD" "$LABELS" > "$CONFIG" <<'PY'
import json
import sys

dashboard = json.load(open(sys.argv[1]))
labels = dict(pair.split("=", 1) for pair in sys.argv[2].split(",") if pair)
if labels:
    dashboard["labels"] = labels
print(json.dumps(dashboard))
PY

NAME="$(gcloud monitoring dashboards create \
    --project="$PROJECT" \
    --config-from-file="$CONFIG" \
    --format='value(name)')"

echo "created: ${NAME}"
echo "to remove it:"
echo "  gcloud monitoring dashboards delete ${NAME} --project=${PROJECT}"
