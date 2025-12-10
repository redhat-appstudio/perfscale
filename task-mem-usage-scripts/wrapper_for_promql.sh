#!/bin/bash

set -eu -o pipefail

task_name="$1"
step_name="$2"
last_num_days="$3"
batch_size="${4:-50}"   # default batch size = 50

cluster_name="$(oc config current-context | sed -E 's#.*/api-([^-]+-[^-]+-[^-]+).*#\1#')"
user_token="$(oc whoami --show-token)"
prometheus_url="$(oc -n openshift-monitoring get route | grep -w 'prometheus-k8s' | grep -v 'federate' | awk '{print $2}')"
end_time_in_secs="$(date +%s)"

###############################################
# GET POD LIST
###############################################

pod_list_raw="$(
    python3 list_pods_for_a_particular_task.py \
        "${user_token}" "${prometheus_url}" "${task_name}" \
        "${end_time_in_secs}" "${last_num_days}" |
    jq -r '.data.result[].metric.pod' |
    sort -u
)"

pods=()
while IFS= read -r line; do
    [ -n "$line" ] && pods+=("$line")
done <<<"$pod_list_raw"

if [ "${#pods[@]}" -eq 0 ]; then
    # No Pods found for the task
    exit 0
fi

###############################################
# INITIALIZE GLOBAL ACCUMULATORS
###############################################

max_overall=0
perc95_overall=0
perc90_overall=0
median_overall=0

###############################################
# BATCH PROCESSING
###############################################

total="${#pods[@]}"
start=0

while [ $start -lt $total ]; do
    end=$((start + batch_size))
    [ $end -gt $total ] && end=$total

    batch_pods=""
    for ((i=start; i<end; i++)); do
        if [ -z "$batch_pods" ]; then
            batch_pods="${pods[$i]}"
        else
            batch_pods="${batch_pods}|${pods[$i]}"
        fi
    done

    ###############################################
    # RUN PYTHON → GET RAW VALUES IN BYTES
    ###############################################

    result_json="$(
        python3 list_container_mem_usage_for_a_particular_pod.py \
            "${user_token}" "${prometheus_url}" "${step_name}" \
            "${end_time_in_secs}" "${batch_pods}" "${last_num_days}" |
        grep -v "Enter any Key" |
        sed '0,/^{/d'
    )"

    ###############################################
    # CALCULATE METRICS
    ###############################################

    max_memory_usage="$(echo "$result_json" | jq '[.data.result[].values[][1] | tonumber | select(. != 0)] | max')"

    perc95="$(echo "$result_json" | jq '
        [.data.result[].values[][1] | tonumber | select(. != 0)] as $data |
        ($data | length) as $N |
        if $N == 0 then 0 else
            (($N * 0.95) | round) as $i |
            ($data | sort)[$i - 1]
        end
    ')"

    perc90="$(echo "$result_json" | jq '
        [.data.result[].values[][1] | tonumber | select(. != 0)] as $data |
        ($data | length) as $N |
        if $N == 0 then 0 else
            (($N * 0.90) | round) as $i |
            ($data | sort)[$i - 1]
        end
    ')"

    median="$(echo "$result_json" | jq '
        [.data.result[].values[][1] | tonumber | select(. != 0)] as $data |
        ($data | length) as $N |
        if $N == 0 then 0 else
            ($data | sort) as $s |
            if ($N % 2 == 1) then
                $s[($N/2 | floor)]
            else
                (($s[($N/2 - 1)] + $s[($N/2)]) / 2)
            end
        end
    ')"

    ###############################################
    # NULL → 0 FIX
    ###############################################

    for var in max_memory_usage perc95 perc90 median; do
        eval "v=\${$var}"
        [ "$v" = "null" ] && eval "$var=0"
    done

    ###############################################
    # CONVERT BYTES → MB
    ###############################################

    max_memory_usage=$((max_memory_usage/1024/1024))
    perc95=$((perc95/1024/1024))
    perc90=$((perc90/1024/1024))
    median=$((median/1024/1024))

    ###############################################
    # GLOBAL ACCUMULATION (MAX ACROSS BATCHES)
    ###############################################

    (( max_memory_usage > max_overall )) && max_overall="$max_memory_usage"
    (( perc95 > perc95_overall )) && perc95_overall="$perc95"
    (( perc90 > perc90_overall )) && perc90_overall="$perc90"
    (( median > median_overall )) && median_overall="$median"

    start=$((start + batch_size))
done

###############################################
# FINAL OUTPUT
###############################################

echo "cluster_name=\"${cluster_name}\", task_name=\"${task_name}\", step_name=\"${step_name}\", \
Max_mem=\"${max_overall} MB\", 95_Pctl_Mem=\"${perc95_overall} MB\", \
90_Pctl_Mem=\"${perc90_overall} MB\", Median_Mem=\"${median_overall} MB\""

