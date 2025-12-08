import sys
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

token = sys.argv[1]

#url = "https://prometheus-k8s-openshift-monitoring.apps.stone-prd-rh01.pg1f.p1.openshiftapps.com/api/v1/query_range"
url = "https://" + sys.argv[2] + "/api/v1/query_range"
task_name = sys.argv[3]
end_time_in_secs= int(sys.argv[4])
last_num_days = int(sys.argv[5])

headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
}
params = {
            "query": 'kube_pod_labels{label_tekton_dev_task="' + task_name + '", namespace=~".*-tenant"}',
            "step": (15 * last_num_days),
            "start": (end_time_in_secs - (last_num_days * 24 * 60 * 60)),
            "end": end_time_in_secs,
        }

#print ("Inside Script ", sys.argv[0])
#print ("token = ", token)
#print ("Prometheuss Url = ", url)
#print ("Task Name = ", task_name)
#print ("Last Num Days = ", last_num_days)
#print ("Start Time in Secs = ", (end_time_in_secs - (last_num_days * 24 * 60 * 60)))
#print ("End Time in Secs = ", end_time_in_secs)
#print ("step = ", (15 * last_num_days))
#print ("Headers = ", headers)
#print ("Params to HTTP Request = ", params)
#sys.exit(0)

response = requests.get(
            url, headers=headers, params=params, verify=False, timeout=60
        )
print(response.text)

