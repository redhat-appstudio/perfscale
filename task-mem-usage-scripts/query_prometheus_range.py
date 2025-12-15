import json
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

token, host, query, start, end = sys.argv[1:6]

url = f"https://{host}/api/v1/query_range"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}
params = {
    "query": query,
    "start": start,
    "end": end,
    "step": 30,
}

response = requests.get(url, headers=headers, params=params, verify=False, timeout=60)
print(json.dumps(response.json()))
