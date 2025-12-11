#!/usr/bin/env python3
import sys
import requests
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if len(sys.argv) < 4:
    print(json.dumps({"error": "usage: get_component_for_pod.py <token> <prometheus_host> <pod_name>"}))
    sys.exit(1)

token = sys.argv[1]
prom_host = sys.argv[2]
pod = sys.argv[3]

url = f"https://{prom_host}/api/v1/query"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}",
}
# query kube_pod_labels for that pod
params = {
    "query": f'kube_pod_labels{{pod="{pod}"}}'
}

try:
    r = requests.get(url, headers=headers, params=params, verify=False, timeout=30)
except Exception as e:
    print(json.dumps({"error": f"request failed: {e}"}))
    sys.exit(0)

if r.status_code != 200:
    print(json.dumps({"error": f"prometheus returned {r.status_code}"}))
    sys.exit(0)

data = r.json().get("data", {}).get("result", [])
if not data:
    print(json.dumps({"component": "N/A", "application": "N/A", "pod": pod}))
    sys.exit(0)

metric = data[0].get("metric", {})

# try common label name variants
candidates = [
    "appstudio_redhat_com_component",
    "label_appstudio_redhat_com_component",
    "appstudio.redhat.com/component",
    "component",
    "app.kubernetes.io/component",
]

a_candidates = [
    "appstudio_redhat_com_application",
    "label_appstudio_redhat_com_application",
    "appstudio.redhat.com/application",
    "application",
    "app.kubernetes.io/name",
]

def first_present(m, keys):
    for k in keys:
        if k in m and m[k] != "":
            return m[k]
    return "N/A"

component = first_present(metric, candidates)
application = first_present(metric, a_candidates)

out = {"component": component, "application": application, "pod": pod}
print(json.dumps(out))

