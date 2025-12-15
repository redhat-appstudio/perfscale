#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <last_num_days> [--text|--color|--csv|--json]" >&2
    exit 1
fi

last_num_days="$1"
shift

OUTPUT_MODE="--text"
if [ "$#" -gt 0 ]; then
    case "$1" in
        --text|--color|--csv|--json)
            OUTPUT_MODE="$1"
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
fi

CONTEXTS="$(kubectl config get-contexts -o name)"
CONTEXTS="default/api-stone-prd-rh01-pg1f-p1-openshiftapps-com:6443/smodak"

task_name="buildah"
container_names=(
    step-build
    step-push
    step-sbom-syft-generate
    step-prepare-sboms
    step-upload-sbom
)

if [ "$OUTPUT_MODE" = "--csv" ]; then
    echo '"cluster","task","step","max_mem_mb","pod","namespace","component","application","p95_mb","p90_mb","median_mb"'
fi

for context in ${CONTEXTS}; do
    if ! kubectl config use-context "$context" >/dev/null 2>&1; then
        continue
    fi

    for container in "${container_names[@]}"; do
        ./wrapper_for_promql.sh \
            "$task_name" \
            "$container" \
            "$last_num_days" \
            "$OUTPUT_MODE"
    done
done

