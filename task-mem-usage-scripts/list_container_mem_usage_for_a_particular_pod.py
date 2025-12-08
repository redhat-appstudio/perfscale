import sys
import requests
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if len(sys.argv) <= 6:
	sys.exit(1)

token = sys.argv[1]
#url = "https://prometheus-k8s-openshift-monitoring.apps.stone-prd-rh01.pg1f.p1.openshiftapps.com/api/v1/query_range"
url = "https://" + sys.argv[2] + "/api/v1/query_range"
step_name = sys.argv[3]
end_time_in_secs = int(sys.argv[4])
pod_name = sys.argv[5]
last_num_days = int(sys.argv[6])

headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
}
params = {
            #"query": 'kube_pod_labels{label_tekton_dev_task="' + step_name + '", namespace=~".*-tenant"}',
            "query": 'container_memory_max_usage_bytes{namespace=~".*-tenant", container="'+ step_name + '", pod=~"(' + pod_name + ')"}',
            "step": (15 * last_num_days),
            "start": (end_time_in_secs - (last_num_days * 24 * 60 * 60)),
            "end": end_time_in_secs,
        }

#print ("Inside Script ", sys.argv[0])
#print ("token = ", token)
#print ("Prometheuss Url = ", url)
#print ("Container Name = ", step_name)
#print ("Pod Name = ", pod_name)
#print ("Last Num Days = ", last_num_days)
#print ("Start Time in Secs = ", (end_time_in_secs - (last_num_days * 24 * 60 * 60)))
#print ("End Time in Secs = ", end_time_in_secs)
#print ("Headers = ", headers)
#print ("Params to HTTP Request = ", params)

response = requests.get(
            url, headers=headers, params=params, verify=False, timeout=60
        )

if response.status_code == 200:
	#print("Request successful.", file=sys.stderr)
	#print(response.text)
	print(json.dumps(response.json(), indent=4))
else:
	print(f"Request failed with status code: {response.status_code}")

