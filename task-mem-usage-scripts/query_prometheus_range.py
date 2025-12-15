import sys, requests, urllib3, json
urllib3.disable_warnings()

token, host, query, start, end = sys.argv[1:6]

url = f"https://{host}/api/v1/query_range"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}
params = {
    "query": query,
    "start": start,
    "end": end,
    "step": 30
}

r = requests.get(url, headers=headers, params=params, verify=False, timeout=60)
print(json.dumps(r.json()))

