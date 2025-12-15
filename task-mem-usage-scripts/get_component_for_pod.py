#!/usr/bin/env python3
import json
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if len(sys.argv) < 4:
    print(
        json.dumps(
            {
                "error": (
                    "usage: get_component_for_pod.py "
                    "<token> <prometheus_host> <pod_name>"
                )
            }
        )
    )
    sys.exit(1)

token, prom_host, pod = sys.argv[1:4]

url = f"https://{prom_host}/api/v1/query"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}",
}
params = {"query": f'kube_pod_labels{{pod="{pod}"}}'}

try:
    response = requests.get(
        url, headers=headers, params=params, verify=False, timeout=30
    )
except Exception as exc:
    print(json.dumps({"error": f"request failed: {exc}"}))
    sys.exit(0)

if response.status_code != 200:
    print(json.dumps({"error": f"prometheus returned {response.status_code}"}))
    sys.exit(0)

data = response.json().get("data", {}).get("result", [])
if not data:
    print(json.dumps({"component": "N/A", "application": "N/A", "pod": pod}))
    sys.exit(0)

metric = data[0].get("metric", {})

component_keys = [
    "appstudio_redhat_com_component",
    "label_appstudio_redhat_com_component",
    "appstudio.redhat.com/component",
    "component",
    "app.kubernetes.io/component",
]

application_keys = [
    "appstudio_redhat_com_application",
    "label_appstudio_redhat_com_application",
    "appstudio.redhat.com/application",
    "application",
    "app.kubernetes.io/name",
]


def first_present(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return "N/A"


output = {
    "component": first_present(metric, component_keys),
    "application": first_present(metric, application_keys),
    "pod": pod,
}

print(json.dumps(output))
