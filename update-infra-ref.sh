#!/bin/bash

# Utility to simplify updating 'infra-deployments' refference to our repo

set -euo pipefail

if ! type -p yq >/dev/null; then
    echo "ERROR: No 'yq' utility available" >&2
    exit 1
fi

file="../infra-deployments/components/monitoring/grafana/base/dashboards/performance/kustomization.yaml"
if ! [ -w "$file" ]; then
    echo "ERROR: No 'infra-deployments' checkout on expected location" >&2
    exit 1
fi

commit=$1
update="https://github.com/redhat-appstudio/perfscale/grafana/?ref=$commit"

yq -i '.resources.0 = "'"$update"'"' "$file"

echo "Done updating $file"
