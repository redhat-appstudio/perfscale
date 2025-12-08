Scripts for extracting Memory Usage
===================================
This directory contains a bunch of loosely stitched scripts to generate Memory usage (Max and 95 percentil) information per step/container for each tass passed to the script. We created these initially to address: https://issues.redhat.com/browse/KONFLUX-6712.

The parent script is invoked as:
`# ./wrapper_for_promql_for_all_clusters.sh <num_of_days>`
e.g
`# ./wrapper_for_promql_for_all_clusters.sh 7`
This extracts the data for the last 7 days. The sampling time delta (step) inside the PromQL query is adjusted accordingly.

It would be ideal to create a Python virtual environment first before executing the script. E.g:
`python -m venv promql_for_mem_metrics`

Highligths:
1. As of now we are passing the TASK name and the STEP(s) name(s) manually to the script, but that can be automated in future using something like:
```
$ yq '.metadata.name as $aaa | .spec.steps[] | [$aaa, .name, .computeResources.requests.cpu, .computeResources.requests.memory, .computeResources.limits.cpu, .computeResources.limits.memory] | @csv' task/sast-coverity-check-oci-ta/0.3/sast-coverity-check-oci-ta.yaml
sast-coverity-check-oci-ta,use-trusted-artifact,null,null,null,null
sast-coverity-check-oci-ta,prepare,null,null,null,null
sast-coverity-check-oci-ta,build,4,4Gi,null,16Gi
sast-coverity-check-oci-ta,postprocess,4,4Gi,null,16Gi
```
2. The parent script `wrapper_for_promql_for_all_clusters.sh` loops around by switching context (different Konflux clusters) and then calling `./wrapper_for_promql.sh` since. The `./wrapper_for_promql.sh` calls 2 more Python scripts `list_pods_for_a_particular_task.py` to list the PODs being used by a particular Task and then pass those PODs to `list_container_mem_usage_for_a_particular_pod.py` script to generate the Mem Usage data (As of now Max and 95 percentile)
3. A sample run looks like the below:
```
% ./wrapper_for_promql_for_all_clusters.sh 7
"default/api-kflux-prd-rh02-0fk9-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-prepare",  max_memory_usage="10 MBytes", 95_percentile_memory_usage="7 MBytes"
"default/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-prepare",  max_memory_usage="0 MBytes", 95_percentile_memory_usage="0 MBytes"
"default/api-stone-prod-p02-hjvn-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-prepare",  max_memory_usage="12 MBytes", 95_percentile_memory_usage="10 MBytes"

"default/api-kflux-prd-rh02-0fk9-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-use-trusted-artifact",  max_memory_usage="1587 MBytes", 95_percentile_memory_usage="480 MBytes"
"default/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-use-trusted-artifact",  max_memory_usage="12 MBytes", 95_percentile_memory_usage="10 MBytes"
"default/api-stone-prod-p02-hjvn-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-use-trusted-artifact",  max_memory_usage="4098 MBytes", 95_percentile_memory_usage="4096 MBytes"

"default/api-kflux-prd-rh02-0fk9-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-build",  max_memory_usage="9386 MBytes", 95_percentile_memory_usage="7645 MBytes"
"default/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-build",  max_memory_usage="15994 MBytes", 95_percentile_memory_usage="15052 MBytes"
"default/api-stone-prod-p02-hjvn-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-build",  max_memory_usage="16386 MBytes", 95_percentile_memory_usage="16384 MBytes"

"default/api-kflux-prd-rh02-0fk9-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-postprocess",  max_memory_usage="15411 MBytes", 95_percentile_memory_usage="13381 MBytes"
"default/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-postprocess",  max_memory_usage="12 MBytes", 95_percentile_memory_usage="10 MBytes"
"default/api-stone-prod-p02-hjvn-p1-openshiftapps-com:6443/smodak", "sast-coverity-check-oci-ta", "step-postprocess",  max_memory_usage="8964 MBytes", 95_percentile_memory_usage="7974 MBytes"
```

Logging to various Konflux clusters
-------------------------------------
'Jan Hutar' shared with me a script 'oclogin', after which I wrote a wrapper called 'oclogin-all' to login to all the Konflux clusters, both the scripts placed under /usr/local/bin/ of my Macbook. Executing 'oclogin-all' causes the script to prompt Pressing any Key for one time so that the user's Redhat SSO authentication can be done for the first cluster. The remaining authentication automatically happens after that. When all authentication is done the Konflux clusters' entries are created automatically in `~/.kube/config`.
