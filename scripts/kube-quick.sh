#!/usr/bin/env bash
# The simple way, in one command.
# Usage: kube-quick.sh <cluster> <zone-or-region> [project]   (project defaults to your current gcloud project)
# Saves .kube/configs/<cluster>.yaml (git-ignored) and prints the one line to paste to use it.
# It writes a local file only. Nothing is changed in Google Cloud, and your default ~/.kube/config is not touched.
set -euo pipefail
[ $# -ge 2 ] || { echo "usage: $0 <cluster> <zone-or-region> [project]"; exit 2; }
project="${3:-$(gcloud config get-value project 2>/dev/null)}"
dir="$(cd "$(dirname "$0")" && pwd)/configs"
mkdir -p "$dir"
f="$dir/$1.yaml"
KUBECONFIG="$f" gcloud container clusters get-credentials "$1" --location "$2" --project "$project"
chmod 600 "$f"
echo
echo "Use it in this terminal:"
echo "  export KUBECONFIG=\"$f\""
