#!/bin/bash

if [ "$#" -ne 1 ]; then
	echo "Wrong Arguments..."
	echo "$0 <last_num_days>"
	exit 1
fi

last_num_days=$1
CONTEXTS="`kubectl config get-contexts -o name | xargs`"
task_name="sast-coverity-check-oci-ta"
container_names="step-use-trusted-artifact step-prepare step-build step-postprocess"
#container_names="step-build"

for context in ${CONTEXTS}
do
	kubectl config use-context ${context} &> /dev/null
	#echo "Switching to context = `kubectl config current-context`"
	#echo "Task Name = \"${task_name}\""
	for container in ${container_names}
	do
		#echo "Container = ${container}"
		./wrapper_for_promql.sh ${task_name} ${container} ${last_num_days}
	done
done

