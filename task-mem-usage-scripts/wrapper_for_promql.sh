#!/bin/bash
set -eu -o pipefail

# usage: wrapper_for_promql.sh <task_name> <step_name> <last_num_days> <mode> [batch_size]
task_name="$1"
step_name="$2"
last_num_days="$3"
OUTPUT_MODE="${4:---text}"
batch_size="${5:-50}"

cluster_name="$(oc config current-context | sed -E 's#.*/api-([^-]+-[^-]+-[^-]+).*#\1#')"
user_token="$(oc whoami --show-token)"
prometheus_url="$(oc -n openshift-monitoring get route | grep -w 'prometheus-k8s' | grep -v 'federate' | awk '{print $2}')"
end_time_in_secs="$(date +%s)"

# Get pods for task
pod_list_raw="$(
    python3 list_pods_for_a_particular_task.py \
        "${user_token}" "${prometheus_url}" "${task_name}" \
        "${end_time_in_secs}" "${last_num_days}" |
    jq -r '.data.result[].metric.pod' 2>/dev/null |
    sort -u
)"

pods=()
while IFS= read -r line; do
    [ -n "$line" ] && pods+=("$line")
done <<<"$pod_list_raw"

[ "${#pods[@]}" -eq 0 ] && exit 0

# accumulators (units: MB for output vars, bytes internally)
max_overall=0
max_overall_pod=""
max_overall_namespace="N/A"
max_overall_app="N/A"
max_overall_component="N/A"

perc95_overall=0
perc90_overall=0
median_overall=0

total="${#pods[@]}"
start=0

while [ $start -lt $total ]; do
    end=$((start + batch_size))
    [ $end -gt $total ] && end=$total

    # join batch pods with |
    batch_pods=$(printf "%s|" "${pods[@]:$start:$((end-start))}")
    batch_pods="${batch_pods%|}"

    # fetch metrics for this batch (raw JSON)
    result_json="$(
        python3 list_container_mem_usage_for_a_particular_pod.py \
            "${user_token}" "${prometheus_url}" "${step_name}" \
            "${end_time_in_secs}" "${batch_pods}" "${last_num_days}" |
        grep -v "Enter any Key" |
        sed '0,/^{/d' || true
    )"

    # find the single max entry (value, pod, namespace) in this batch (bytes)
    max_entry="$(echo "$result_json" | jq '
        .data.result[]? as $r |
        ($r.values[][1] | tonumber | select(. != 0)) as $v |
        {value: $v, pod: ($r.metric.pod // ""), namespace: ($r.metric.namespace // "")}
    ' | jq -s 'if length==0 then {} else max_by(.value) end')"

    batch_max_value=$(echo "$max_entry" | jq -r '.value // 0')
    batch_max_pod=$(echo "$max_entry" | jq -r '.pod // ""')
    batch_max_namespace=$(echo "$max_entry" | jq -r '.namespace // ""')

    # convert to MB (integer)
    batch_max_value_mb=$(( (batch_max_value+0) / 1024 / 1024 ))

    # try get component/application via helper (uses kube labels if available)
    comp_info="$(python3 get_component_for_pod.py "${user_token}" "${prometheus_url}" "${batch_max_pod}" 2>/dev/null || echo '{}')"
    component="$(echo "$comp_info" | jq -r '.component // "N/A"' 2>/dev/null || echo "N/A")"
    application="$(echo "$comp_info" | jq -r '.application // "N/A"' 2>/dev/null || echo "N/A")"

    # fallback: attempt to parse app/component from pod name pattern if helper didn't find anything
    if [ "$component" = "N/A" ] && [ -n "$batch_max_pod" ]; then
        if [[ "$batch_max_pod" =~ ^([a-z0-9-]+)-([a-z0-9-]+)-([a-f0-9]{8,}) ]]; then
            fallback_app="${BASH_REMATCH[1]}"
            fallback_comp="${BASH_REMATCH[2]}"
            application="${application:-$fallback_app}"
            component="${component:-$fallback_comp}"
        fi
    fi

    # global max update
    if (( batch_max_value_mb > max_overall )); then
        max_overall="$batch_max_value_mb"
        max_overall_pod="$batch_max_pod"
        max_overall_namespace="${batch_max_namespace:-N/A}"
        max_overall_app="${application:-N/A}"
        max_overall_component="${component:-N/A}"
    fi

    # percentiles for the batch (bytes) -> convert -> consider global maxima of those percentiles
    batch_perc95_bytes="$(echo "$result_json" | jq '[.data.result[].values[][1] | tonumber | select(. != 0)] as $d | if ($d|length)==0 then 0 else ($d|sort)[(length*0.95|round)-1] end' 2>/dev/null || echo 0)"
    batch_perc90_bytes="$(echo "$result_json" | jq '[.data.result[].values[][1] | tonumber | select(. != 0)] as $d | if ($d|length)==0 then 0 else ($d|sort)[(length*0.90|round)-1] end' 2>/dev/null || echo 0)"
    batch_median_bytes="$(echo "$result_json" | jq '[.data.result[].values[][1] | tonumber | select(. != 0)] as $d | if ($d|length)==0 then 0 else ($d|sort) as $s | if ((length%2)==1) then $s[(length/2|floor)] else (($s[(length/2-1)] + $s[(length/2)]) / 2) end end' 2>/dev/null || echo 0)"

    # normalize nulls and convert to MB
    [ "$batch_perc95_bytes" = "null" ] && batch_perc95_bytes=0
    [ "$batch_perc90_bytes" = "null" ] && batch_perc90_bytes=0
    [ "$batch_median_bytes" = "null" ] && batch_median_bytes=0

    batch_perc95_mb=$(( (batch_perc95_bytes+0) / 1024 / 1024 ))
    batch_perc90_mb=$(( (batch_perc90_bytes+0) / 1024 / 1024 ))
    batch_median_mb=$(( (batch_median_bytes+0) / 1024 / 1024 ))

    (( batch_perc95_mb > perc95_overall )) && perc95_overall="$batch_perc95_mb"
    (( batch_perc90_mb > perc90_overall )) && perc90_overall="$batch_perc90_mb"
    (( batch_median_mb > median_overall )) && median_overall="$batch_median_mb"

    start=$((start + batch_size))
done

# OUTPUT: depending on mode
case "$OUTPUT_MODE" in
    --json)
        jq -n \
          --arg cluster "$cluster_name" \
          --arg task "$task_name" \
          --arg step "$step_name" \
          --argjson max_mem "$max_overall" \
          --arg pod "$max_overall_pod" \
          --arg ns "$max_overall_namespace" \
          --arg comp "$max_overall_component" \
          --arg app "$max_overall_app" \
          --argjson p95 "$perc95_overall" \
          --argjson p90 "$perc90_overall" \
          --argjson med "$median_overall" \
          '{
            cluster: $cluster,
            task: $task,
            step: $step,
            max_mem_mb: $max_mem,
            max_mem_pod: $pod,
            namespace: $ns,
            component: $comp,
            application: $app,
            p95_mb: $p95,
            p90_mb: $p90,
            median_mb: $med
          }'
    ;;
    --csv)
        # print CSV data row only (no header)
        # ensure values are quoted CSV-safe
        printf '"%s","%s","%s","%s","%s","%s","%s","%s","%s","%s","%s"\n' \
            "$cluster_name" "$task_name" "$step_name" "$max_overall" \
            "$max_overall_pod" "$max_overall_namespace" "$max_overall_component" \
            "$max_overall_app" "$perc95_overall" "$perc90_overall" "$median_overall"
    ;;
    --color)
        RED="\033[1;31m"
        GREEN="\033[1;32m"
        CYAN="\033[1;36m"
        NC="\033[0m"

        echo -e "${CYAN}Cluster:${NC}        $cluster_name"
        echo -e "${CYAN}Task:${NC}           $task_name"
        echo -e "${CYAN}Step:${NC}           $step_name"
        echo -e "${RED}Max Mem:${NC}        ${max_overall} MB"
        echo -e "${GREEN}Pod:${NC}            ${max_overall_pod}"
        echo -e "${CYAN}Namespace:${NC}      ${max_overall_namespace}"
        echo -e "${CYAN}Application:${NC}    ${max_overall_app}"
        echo -e "${CYAN}Component:${NC}      ${max_overall_component}"
        echo -e "${CYAN}95th:${NC}           ${perc95_overall} MB"
        echo -e "${CYAN}90th:${NC}           ${perc90_overall} MB"
        echo -e "${CYAN}Median:${NC}         ${median_overall} MB"
        # blank line between entries (helps separating multiple outputs)
        echo
    ;;
    --text|*)
        echo "cluster_name=\"${cluster_name}\", task_name=\"${task_name}\", step_name=\"${step_name}\", \
Max_mem=\"${max_overall} MB\", Max_mem_pod=\"${max_overall_pod}\", \
Max_mem_namespace=\"${max_overall_namespace}\", \
Max_mem_component=\"${max_overall_component}\", \
Max_mem_application=\"${max_overall_app}\", \
95_Pctl_Mem=\"${perc95_overall} MB\", 90_Pctl_Mem=\"${perc90_overall} MB\", Median_Mem=\"${median_overall} MB\""
    ;;
esac

