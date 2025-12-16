#!/bin/bash
# ShellCheck-clean, bash 3.2 compatible

set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <last_num_days> [--csv] [--debug]" >&2
    exit 1
fi

LAST_DAYS="$1"
shift

OUTPUT_MODE="--csv"
DEBUG=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --csv) OUTPUT_MODE="--csv" ;;
        --debug) DEBUG=1 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

TASK_NAME="buildah"
STEPS="step-build step-push step-sbom-syft-generate step-prepare-sboms step-upload-sbom"
#STEPS="step-build"

# Get all contexts or use specific one for testing
#CONTEXTS="$(kubectl config get-contexts -o name 2>/dev/null | xargs || echo 'default/api-stone-prd-rh01-pg1f-p1-openshiftapps-com:6443/smodak')"
CONTEXTS="default/api-stone-prd-rh01-pg1f-p1-openshiftapps-com:6443/smodak"


# CSV header matching the output format: cluster,task,step,pod_max_memory,pod_namespace_mem,component,application,mem_max_mb,mem_p95_mb,mem_p90_mb,mem_median_mb,pod_max_cpu,pod_namespace_cpu,cpu_max,cpu_p95,cpu_p90,cpu_median
echo '"cluster","task","step","pod_max_memory","pod_namespace_mem","component","application","mem_max_mb","mem_p95_mb","mem_p90_mb","mem_median_mb","pod_max_cpu","pod_namespace_cpu","cpu_max","cpu_p95","cpu_p90","cpu_median"'

for ctx in ${CONTEXTS}; do
    kubectl config use-context "$ctx" >/dev/null 2>&1 || continue
    [ "$DEBUG" -eq 1 ] && echo "DEBUG(parent): context=$ctx" >&2

    for step in ${STEPS}; do
        ./wrapper_for_promql.sh \
            "$TASK_NAME" \
            "$step" \
            "$LAST_DAYS" \
            "$OUTPUT_MODE" \
            "$DEBUG"
    done
done

