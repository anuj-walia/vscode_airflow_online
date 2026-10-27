# Source this file, do not run it:  source kube-use.sh <name>
# With no name it lists the saved configs. To go back to the default config: unset KUBECONFIG
_kdir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/configs"
if [ -z "${1:-}" ]; then
  echo "saved configs in $_kdir:"
  ls -1 "$_kdir" 2>/dev/null | sed 's/\.yaml$//;s/^/  /'
  echo "current KUBECONFIG: ${KUBECONFIG:-<default ~/.kube/config>}"
elif [ -f "$_kdir/$1.yaml" ]; then
  export KUBECONFIG="$_kdir/$1.yaml"
  echo "KUBECONFIG -> $KUBECONFIG"
  kubectl config current-context
else
  echo "no such config: $_kdir/$1.yaml (create it with kube-add.sh)"
fi
unset _kdir
