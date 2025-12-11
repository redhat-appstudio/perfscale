#!/bin/bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <last_num_days> [--text|--color|--csv|--json]"
    exit 1
fi

last_num_days="$1"
shift

# Default output mode
OUTPUT_MODE="--text"
if [ $# -gt 0 ]; then
    case "$1" in
        --text|--color|--csv|--json) OUTPUT_MODE="$1" ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
fi

# Single-context for now (replace with kubectl contexts if needed)
CONTEXTS="`kubectl config get-contexts -o name | xargs`"
# override for testing / single cluster if desired:
#CONTEXTS="default/api-stone-prd-rh01-pg1f-p1-openshiftapps-com:6443/smodak"

task_name="buildah"
container_names="step-build step-push step-sbom-syft-generate step-prepare-sboms step-upload-sbom"

# If CSV mode, print header once
if [ "$OUTPUT_MODE" = "--csv" ]; then
    echo '"cluster","task","step","max_mem_mb","pod","namespace","component","application","p95_mb","p90_mb","median_mb"'
fi

for context in ${CONTEXTS}; do
    kubectl config use-context "${context}" &> /dev/null || continue

    for container in ${container_names}; do
        # call wrapper and let it print one row (or text/color/json)
        ./wrapper_for_promql.sh "${task_name}" "${container}" "${last_num_days}" "${OUTPUT_MODE}"
    done
done

