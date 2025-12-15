#!/usr/bin/env bash
set -euo pipefail

# usage: wrapper_for_promql.sh <task_name> <step_name> <last_num_days> <mode> [batch_size]

task_name="$1"
step_name="$2"
last_num_days="$3"
OUTPUT_MODE="${4:---text}"
batch_size="${5:-50}"

cluster_name="$(oc config current-context | sed -E 's#.*/api-([^-]+-[^-]+-[^-]+).*#\1#')"
user_token="$(oc whoami --show-token)"
prometheus_url="$(oc -n openshift-monitoring get route | awk '/prometheus-k8s/ && !/federate/ {print $2}')"

end_time_in_secs="$(date +%s)"
start_time="$((end_time_in_secs - last_num_days * 24 * 60 * 60))"
range_window="${last_num_days}d"

pod_list_raw="$(
    python3 list_pods_for_a_particular_task.py \
        "$user_token" \
        "$prometheus_url" \
        "$task_name" \
        "$end_time_in_secs" \
        "$last_num_days" |
    jq -r '.data.result[].metric.pod' 2>/dev/null |
    sort -u
)"

pods=()
while IFS= read -r pod; do
    [ -n "$pod" ] && pods+=("$pod")
done <<< "$pod_list_raw"

if [ "${#pods[@]}" -eq 0 ]; then
    echo "DEBUG: No pods found for task=${task_name} in cluster=${cluster_name}" >&2
    exit 0
fi

max_overall=0
max_overall_pod=""
max_overall_namespace="N/A"
max_overall_component="N/A"
max_overall_app="N/A"

perc95_overall=0
perc90_overall=0
median_overall=0

total="${#pods[@]}"
start=0

while [ "$start" -lt "$total" ]; do
    end="$((start + batch_size))"
    [ "$end" -gt "$total" ] && end="$total"

    batch_pods="$(printf "%s|" "${pods[@]:start:end-start}")"
    batch_pods="${batch_pods%|}"

    max_query="max_over_time(container_memory_max_usage_bytes{namespace=~\".*-tenant\",container=\"${step_name}\",pod=~\"(${batch_pods})\"}[${range_window}])"

    max_json="$(
        python3 query_prometheus_range.py \
            "$user_token" \
            "$prometheus_url" \
            "$max_query" \
            "$start_time" \
            "$end_time_in_secs"
    )"

    max_entry="$(
        echo "$max_json" | jq '
            .data.result[]? as $r
            | ($r.values[][1] | tonumber | floor | select(. > 0)) as $v
            | {value:$v, pod:$r.metric.pod, namespace:$r.metric.namespace}
        ' | jq -s 'if length == 0 then {} else max_by(.value) end'
    )"

    batch_max_bytes="$(echo "$max_entry" | jq -r '.value // 0')"
    batch_max_mb="$((batch_max_bytes / 1024 / 1024))"

    if [ "$batch_max_mb" -gt "$max_overall" ]; then
        max_overall="$batch_max_mb"
        max_overall_pod="$(echo "$max_entry" | jq -r '.pod // ""')"
        max_overall_namespace="$(echo "$max_entry" | jq -r '.namespace // "N/A"')"

        comp_info="$(
            python3 get_component_for_pod.py \
                "$user_token" \
                "$prometheus_url" \
                "$max_overall_pod" 2>/dev/null || echo '{}'
        )"

        max_overall_component="$(echo "$comp_info" | jq -r '.component // "N/A"')"
        max_overall_app="$(echo "$comp_info" | jq -r '.application // "N/A"')"
    fi

    for q in 0.95 0.90 0.50; do
        p_query="quantile_over_time(${q},container_memory_working_set_bytes{namespace=~\".*-tenant\",container=\"${step_name}\",pod=~\"(${batch_pods})\"}[${range_window}])"

        p_json="$(
            python3 query_prometheus_range.py \
                "$user_token" \
                "$prometheus_url" \
                "$p_query" \
                "$start_time" \
                "$end_time_in_secs"
        )"

        p_bytes="$(
            echo "$p_json" |
            jq '[.data.result[].values[][1] | tonumber | floor | select(. > 0)] | max // 0'
        )"

        p_mb="$((p_bytes / 1024 / 1024))"

        case "$q" in
            0.95) [ "$p_mb" -gt "$perc95_overall" ] && perc95_overall="$p_mb" ;;
            0.90) [ "$p_mb" -gt "$perc90_overall" ] && perc90_overall="$p_mb" ;;
            0.50) [ "$p_mb" -gt "$median_overall" ] && median_overall="$p_mb" ;;
        esac
    done

    start="$((start + batch_size))"
done

case "$OUTPUT_MODE" in
    --csv)
        printf '"%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s"\n' \
            "$cluster_name" \
            "$task_name" \
            "$step_name" \
            "$max_overall" \
            "$max_overall_pod" \
            "$max_overall_namespace" \
            "$max_overall_component" \
            "$max_overall_app" \
            "$perc95_overall" \
            "$perc90_overall" \
            "$median_overall"
        ;;
    --json)
        jq -n \
            --arg cluster "$cluster_name" \
            --arg task "$task_name" \
            --arg step "$step_name" \
            --argjson max "$max_overall" \
            --arg pod "$max_overall_pod" \
            --arg ns "$max_overall_namespace" \
            --arg comp "$max_overall_component" \
            --arg app "$max_overall_app" \
            --argjson p95 "$perc95_overall" \
            --argjson p90 "$perc90_overall" \
            --argjson med "$median_overall" \
            '{
                cluster:$cluster,
                task:$task,
                step:$step,
                max_mem_mb:$max,
                max_mem_pod:$pod,
                namespace:$ns,
                component:$comp,
                application:$app,
                p95_mb:$p95,
                p90_mb:$p90,
                median_mb:$med
            }'
        ;;
    *)
        echo "cluster=${cluster_name}, task=${task_name}, step=${step_name}, max=${max_overall}MB, p95=${perc95_overall}MB, p90=${perc90_overall}MB, median=${median_overall}MB"
        ;;
esac

