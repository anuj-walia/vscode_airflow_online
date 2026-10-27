#!/usr/bin/env bash
# Save one cluster's kubeconfig as .kube/configs/<name>.yaml (private to this folder, git-ignored).
# Usage: kube-add.sh <name> <cluster> <zone-or-region> <gcp-project>
# It writes a local file only. Nothing is changed in Google Cloud.
set -euo pipefail
[ $# -eq 4 ] || { echo "usage: $0 <name> <cluster> <zone-or-region> <gcp-project>"; exit 2; }
dir="$(cd "$(dirname "$0")" && pwd)/configs"
mkdir -p "$dir"
f="$dir/$1.yaml"
KUBECONFIG="$f" gcloud container clusters get-credentials "$2" --location "$3" --project "$4"
chmod 600 "$f"
echo "saved: $f"
echo "use it: source $(cd "$(dirname "$0")" && pwd)/kube-use.sh $1"
