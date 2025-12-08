#!/bin/bash

task_name="$1"
step_name="$2"
last_num_days="$3"
user_token="`oc whoami --show-token`"
prometheus_url="`oc -n openshift-monitoring get route | grep -w 'prometheus-k8s' | grep -v 'federate' | awk '{print $2}'`"
#task_name="sast-coverity-check-oci-ta"
#task_name="sast-coverity-check"
end_time_in_secs=`date +%s`

set -eu -o pipefail

#echo "Current Cluster Context = \"`oc config current-context`\""
#echo "User Token = $user_token"
#echo "Prometheus Url = ${prometheus_url}"
#echo "Querying Task Name = \"${task_name}\""
#echo "last_num_days = $last_num_days"
#echo "\"`date`\": start_time_in_secs = $((end_time_in_secs - (last_num_days * 24 * 60 * 60)))"
#echo "\"`date`\": end_time_in_secs = ${end_time_in_secs}"

#echo " "
#read -s -p "Enter any Key to proceed..."

#python3 list_pods_for_a_particular_task.py "${user_token}" "${prometheus_url}" "${task_name}" ${end_time_in_secs} | jq .
#pod_names="`python3 list_pods_for_a_particular_task.py "${user_token}" "${prometheus_url}" "${task_name}" ${end_time_in_secs} | jq . | grep -i '"pod":' | awk {'print $2'} | tr -d '",' | head -1`" #Just the 1st POD for experiment

pod_names="x"
pod_names="`python3 list_pods_for_a_particular_task.py "${user_token}" "${prometheus_url}" "${task_name}" ${end_time_in_secs} ${last_num_days} | jq . | grep -i '"pod":' | awk {'print $2'} | tr -d '",' | xargs | tr ' ' '|'`" #All the PODs

#echo "Pod Name(s) = \"$pod_names\""
#step_name="step-use-trusted-artifact"
#step_name="step-prepare"
#step_name="step-build"
#step_name="step-postprocess"

#echo "Container Name = ${step_name}"

#python3 list_container_mem_usage_for_a_particular_pod.py "${user_token}" "${prometheus_url}" "${step_name}" ${end_time_in_secs} ${pod_names}

#Print all values of memory used
#python3 list_container_mem_usage_for_a_particular_pod.py "${user_token}" "${prometheus_url}" "${step_name}" ${end_time_in_secs} ${pod_names} | grep -v "Enter any Key" | sed '0,/^{/d' | jq '.data.result[].values[][1]'

if [ pod_names != "x" ]; then
	#Print only the non-zero values
	max_memory_usage=`python3 list_container_mem_usage_for_a_particular_pod.py "${user_token}" "${prometheus_url}" "${step_name}" ${end_time_in_secs} ${pod_names} ${last_num_days} | grep -v "Enter any Key" | sed '0,/^{/d' | jq '[.data.result[].values[][1] | tonumber | select(. != 0)] | max'`
	max_memory_usage=$((max_memory_usage/1024/1024))
	ninety_five_percentile_memory_usage=`python3 list_container_mem_usage_for_a_particular_pod.py "${user_token}" "${prometheus_url}" "${step_name}" ${end_time_in_secs} ${pod_names} ${last_num_days} | grep -v "Enter any Key" | sed '0,/^{/d' | jq '[.data.result[].values[][1] | tonumber | select(. != 0)] as $data | ($data | length) as $length | (($length * 0.95 ) | round) as $index_95thperc | ($data | sort)[$index_95thperc - 1]'`
	ninety_five_percentile_memory_usage=$((ninety_five_percentile_memory_usage/1024/1024))

	#echo "For Cluster=\"`oc config current-context`\", Task=\"${task_name}\", Container=\"${step_name}\", Max Memory usage in last 24 hours=`echo $((max_memory_usage/1024/1024))` Mb"
	echo "\"`oc config current-context`\", \"${task_name}\", \"${step_name}\",  max_memory_usage=\"${max_memory_usage} MBytes\", 95_percentile_memory_usage=\"${ninety_five_percentile_memory_usage} MBytes\""
fi

