#!/usr/bin/env python3
"""
oc_get_ooms.py

Detect OOMKilled and CrashLoopBackOff pods across multiple OpenShift/Kubernetes contexts,
parallelized at cluster and namespace levels, with Prometheus fallback and artifact collection.

New in this version:
- When a pod is detected as OOMKilled or CrashLoopBackOff, save:
    - `oc describe pod <pod>` output
    - `oc logs <pod>` (or `oc logs --previous <pod>` if no current logs)
  into per-cluster directories under /tmp/<cluster>/
  Filenames include namespace, pod name, and timestamp to avoid collisions.
- CSV and JSON now include the absolute paths to the description and pod log files:
    description_file, pod_log_file

All previously requested features retained:
- cluster batching, namespace batching, Prometheus fallback (parallelized by namespace batches),
  --skip-prometheus flag, include/exclude regex, retries, timeouts, colorized output, etc.
"""

from __future__ import annotations

import subprocess
import json
import csv
import re
import sys
import time
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Set, Pattern

# Try to import requests, but make it optional
try:
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ---------------------------
# Color output
# ---------------------------
RED = "\033[1;31m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[1;34m"
RESET = "\033[0m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{RESET}"


# ---------------------------
# Time windows (kept for context; not used directly for artifact collection)
# ---------------------------
TIME_WINDOWS = {
    "last_1h": 1,
    "last_3h": 3,
    "last_6h": 6,
    "last_24h": 24,
    "last_48h": 48,
    "last_3d": 72,
    "last_5d": 120,
    "last_7d": 168,
}

# Prometheus queries for OOM and CrashLoopBackOff detection
# Note: These metrics may not be available in all clusters or may require
# specific Prometheus configuration. The queries are used as a fallback
# when oc events don't provide sufficient historical data.
# If these queries don't work, consider using alternative metrics or
# querying Prometheus directly to verify metric availability.
PROMQL_OOM = (
    "kube_pod_container_status_last_terminated_reason"
    '{{reason="OOMKilled",namespace="{namespace}"}}'
)
PROMQL_CRASH = (
    "kube_pod_container_status_waiting_reason"
    '{{reason="CrashLoopBackOff",namespace="{namespace}"}}'
)

# ---------------------------
# Defaults
# ---------------------------
DEFAULT_RETRIES = 3
DEFAULT_OC_TIMEOUT = 45  # seconds
RETRY_DELAY_SECONDS = 3
DEFAULT_NS_BATCH_SIZE = 10
DEFAULT_NS_WORKERS = 5
DEFAULT_BATCH_SIZE = 2

# Prometheus constants
PROMETHEUS_NAMESPACE = "openshift-monitoring"
PROMETHEUS_ROUTE_NAME = "prometheus-k8s"  # Common route name in OpenShift


# ---------------------------
# Command runner with retries
# ---------------------------
def run_cmd_with_retries(
    cmd: List[str], retries: int = DEFAULT_RETRIES, timeout: Optional[int] = None
) -> Tuple[int, str, str]:
    attempt = 0
    last_err = ""
    while attempt < max(1, retries):
        attempt += 1
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()
            return completed.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            last_err = f"TimeoutExpired after {timeout}s"
            time.sleep(RETRY_DELAY_SECONDS * attempt)
        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_DELAY_SECONDS * attempt)
    return 1, "", last_err


def run_shell_cmd_with_retries(
    cmd: str, retries: int = DEFAULT_RETRIES, timeout: Optional[int] = None
) -> Tuple[int, str, str]:
    return run_cmd_with_retries(
        ["/bin/sh", "-c", cmd], retries=retries, timeout=timeout
    )


# ---------------------------
# oc helpers
# ---------------------------
def oc_cmd_parts(
    context: str, oc_timeout_seconds: int, subcommand: List[str]
) -> List[str]:
    parts = ["oc", f"--request-timeout={oc_timeout_seconds}s"]
    if context:
        parts += ["--context", context]
    parts += subcommand
    return parts


def run_oc_subcommand(
    context: str, subcommand: List[str], retries: int, oc_timeout_seconds: int
) -> Tuple[int, str, str]:
    cmd = oc_cmd_parts(context, oc_timeout_seconds, subcommand)
    return run_cmd_with_retries(cmd, retries=retries, timeout=oc_timeout_seconds + 5)


# ---------------------------
# context utilities
# ---------------------------
def get_all_contexts(retries: int, oc_timeout_seconds: int) -> List[str]:
    """Get all available OpenShift contexts."""
    cmd = ["oc", "config", "get-contexts", "-o", "name"]
    rc, out, err = run_cmd_with_retries(
        cmd, retries=retries, timeout=oc_timeout_seconds + 5
    )
    if rc != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def get_current_context(retries: int, oc_timeout_seconds: int) -> str:
    """Get the current OpenShift context."""
    cmd = ["oc", "config", "current-context"]
    rc, out, err = run_cmd_with_retries(
        cmd, retries=retries, timeout=oc_timeout_seconds + 5
    )
    return out.strip() if rc == 0 else ""


def short_cluster_name(full_ctx: str) -> str:
    m = re.search(r"api-([^-]+-[^-]+-[^-]+)", full_ctx)
    if m:
        return m.group(1)
    if "/" in full_ctx:
        return full_ctx.split("/")[-1]
    return full_ctx.replace("/", "_").replace(":", "_")


# ---------------------------
# timestamp utilities
# ---------------------------
def parse_timestamp_to_iso(ts: str) -> str:
    """Parse Kubernetes timestamp to ISO format."""
    if not ts:
        return ""
    try:
        base = ts.split(".")[0].rstrip("Z")
        dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError) as e:
        logging.debug(f"Failed to parse timestamp '{ts}': {e}")
        return ts


def now_ts_for_filename() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


# ---------------------------
# connectivity
# ---------------------------
def check_cluster_connectivity(
    context: str, retries: int, oc_timeout_seconds: int
) -> Tuple[bool, str]:
    rc, out, err = run_oc_subcommand(
        context, ["whoami"], retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if rc == 0:
        return True, ""
    return False, err or out or "unknown error"


# ---------------------------
# oc-only namespace workers (no Prometheus here)
# ---------------------------
def get_all_events_oc(
    context: str,
    namespace: str,
    retries: int,
    oc_timeout_seconds: int,
) -> List[Dict[str, Any]]:
    """Get all events for a namespace (single API call for efficiency)."""
    subcmd = ["-n", namespace, "get", "events", "--ignore-not-found", "-o", "json"]
    rc, out, err = run_oc_subcommand(
        context, subcmd, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if rc != 0 or not out:
        return []
    try:
        obj = json.loads(out)
    except json.JSONDecodeError as e:
        logging.warning(f"Failed to parse events JSON for {namespace}: {e}")
        return []
    return obj.get("items", [])


def find_events_by_reason_oc(
    context: str,
    namespace: str,
    reason_substring: str,
    retries: int,
    oc_timeout_seconds: int,
) -> List[Dict[str, str]]:
    """Find events matching a reason substring in a namespace."""
    events = get_all_events_oc(context, namespace, retries, oc_timeout_seconds)
    res: List[Dict[str, str]] = []
    for ev in events:
        reason = ev.get("reason", "")
        if reason_substring.lower() not in reason.lower():
            continue
        pod = ev.get("involvedObject", {}).get("name")
        ts = ev.get("eventTime") or ev.get("lastTimestamp") or ev.get("firstTimestamp")
        if pod and ts:
            res.append(
                {"pod": pod, "reason": reason, "timestamp": parse_timestamp_to_iso(ts)}
            )
    return res


def crashloop_via_pods_oc(
    context: str, namespace: str, retries: int, oc_timeout_seconds: int
) -> List[Dict[str, str]]:
    """Find pods in CrashLoopBackOff state by querying pod status."""
    subcmd = ["-n", namespace, "get", "pods", "-o", "json", "--ignore-not-found"]
    rc, out, err = run_oc_subcommand(
        context, subcmd, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if rc != 0 or not out:
        return []
    try:
        obj = json.loads(out)
    except json.JSONDecodeError as e:
        logging.warning(f"Failed to parse pods JSON for {namespace}: {e}")
        return []
    res: List[Dict[str, str]] = []
    for item in obj.get("items", []):
        pod_name = item.get("metadata", {}).get("name")
        statuses = item.get("status", {}).get("containerStatuses", []) or []
        for cs in statuses:
            waiting = cs.get("state", {}).get("waiting")
            if waiting and waiting.get("reason") == "CrashLoopBackOff":
                res.append(
                    {"pod": pod_name, "reason": "CrashLoopBackOff", "timestamp": ""}
                )
    return res


# ---------------------------
# Prometheus fallback (can be parallelized in batches)
# ---------------------------
def get_prometheus_route_url(
    context: str, retries: int, oc_timeout_seconds: int
) -> Optional[str]:
    """Get Prometheus route URL from openshift-monitoring namespace."""
    subcmd = [
        "-n",
        PROMETHEUS_NAMESPACE,
        "get",
        "route",
        PROMETHEUS_ROUTE_NAME,
        "-o",
        "jsonpath={.spec.host}",
        "--ignore-not-found",
    ]
    rc, out, err = run_oc_subcommand(
        context, subcmd, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if rc != 0 or not out:
        # Try alternative: get all routes and filter
        subcmd2 = [
            "-n",
            PROMETHEUS_NAMESPACE,
            "get",
            "route",
            "-o",
            "json",
        ]
        rc2, out2, err2 = run_oc_subcommand(
            context, subcmd2, retries=retries, oc_timeout_seconds=oc_timeout_seconds
        )
        if rc2 == 0 and out2:
            try:
                routes = json.loads(out2)
                for route in routes.get("items", []):
                    name = route.get("metadata", {}).get("name", "")
                    if PROMETHEUS_ROUTE_NAME in name and "federate" not in name.lower():
                        host = route.get("spec", {}).get("host", "")
                        if host:
                            return f"https://{host}"
            except json.JSONDecodeError:
                pass
        return None
    host = out.strip()
    if host:
        return f"https://{host}"
    return None


def get_oc_token(context: str, retries: int, oc_timeout_seconds: int) -> Optional[str]:
    """Get OpenShift authentication token for the current context."""
    subcmd = ["whoami", "--show-token"]
    rc, out, err = run_oc_subcommand(
        context, subcmd, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if rc == 0 and out:
        return out.strip()
    return None


def prom_query_from_prometheus_route(
    context: str, namespace: str, promql: str, retries: int, oc_timeout_seconds: int
) -> List[Dict[str, str]]:
    """
    Query Prometheus via HTTP API using route (no exec permissions required).

    This method uses the Prometheus route exposed in openshift-monitoring namespace
    and authenticates using the OpenShift token. This is more reliable and doesn't
    require exec permissions on pods.
    """
    if not HAS_REQUESTS:
        logging.warning(
            "requests library not available. Install with: pip install requests"
        )
        return []

    # Get Prometheus route URL
    prom_url = get_prometheus_route_url(context, retries, oc_timeout_seconds)
    if not prom_url:
        logging.debug(
            f"Could not find Prometheus route in {PROMETHEUS_NAMESPACE} namespace"
        )
        return []

    # Get authentication token
    token = get_oc_token(context, retries, oc_timeout_seconds)
    if not token:
        logging.debug("Could not get OpenShift authentication token")
        return []

    # Build query URL
    query_url = f"{prom_url}/api/v1/query"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    params = {"query": promql}

    # Make HTTP request
    try:
        response = requests.get(
            query_url,
            headers=headers,
            params=params,
            verify=False,
            timeout=oc_timeout_seconds + 10,
        )
    except requests.exceptions.RequestException as e:
        logging.debug(f"Prometheus HTTP request failed for {namespace}: {e}")
        return []

    if response.status_code != 200:
        logging.debug(
            f"Prometheus returned status {response.status_code} for {namespace}: "
            f"{response.text[:200]}"
        )
        return []

    try:
        resp = response.json()
    except json.JSONDecodeError as e:
        logging.warning(f"Failed to parse Prometheus response for {namespace}: {e}")
        return []

    # Validate response structure
    if resp.get("status") != "success":
        error_msg = resp.get("error", {}).get("error", "unknown error")
        logging.debug(
            f"Prometheus query returned error for {namespace}: {error_msg}. "
            f"Query: {promql}"
        )
        return []

    res: List[Dict[str, str]] = []
    for r in resp.get("data", {}).get("result", []):
        metric = r.get("metric", {})
        pod = metric.get("pod") or metric.get("instance") or metric.get("container")
        if pod:
            res.append({"pod": pod, "reason": "Prometheus", "timestamp": ""})
    return res


# Alias for backward compatibility (in case it's called elsewhere)
prom_query_from_prometheus_pod = prom_query_from_prometheus_route


# ---------------------------
# get namespaces and apply include/exclude regex lists
# ---------------------------
def get_namespaces_for_context(
    context: str,
    retries: int,
    oc_timeout_seconds: int,
    include_patterns: Optional[List[Pattern]] = None,
    exclude_patterns: Optional[List[Pattern]] = None,
) -> List[str]:
    """Get namespaces for a context, optionally filtered by include/exclude patterns."""
    subcmd = ["get", "ns", "-o", "json"]
    rc, out, err = run_oc_subcommand(
        context, subcmd, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if rc != 0 or not out:
        return []
    try:
        obj = json.loads(out)
    except json.JSONDecodeError as e:
        logging.warning(f"Failed to parse namespaces JSON: {e}")
        return []
    all_ns = [
        it.get("metadata", {}).get("name")
        for it in obj.get("items", [])
        if it.get("metadata", {}).get("name")
    ]
    filtered: List[str] = []
    for ns in all_ns:
        include = True
        if include_patterns:
            include = any(p.search(ns) for p in include_patterns)
        if not include:
            continue
        if exclude_patterns and any(p.search(ns) for p in exclude_patterns):
            continue
        filtered.append(ns)
    return filtered


# ---------------------------
# Save pod artifacts (describe + logs) into per-cluster directory under /tmp/<cluster>/
# Returns (description_path, log_path)
# ---------------------------
def save_pod_artifacts(
    context: str,
    cluster: str,
    namespace: str,
    pod: str,
    retries: int,
    oc_timeout_seconds: int,
) -> Tuple[str, str]:
    """
    Save 'oc describe pod' and 'oc logs pod' (or --previous) into files under /tmp/<cluster>/
    Filenames include namespace, pod name and timestamp to avoid collisions.
    Returns absolute file paths (description_file, pod_log_file)
    """
    ts = now_ts_for_filename()
    cluster_dir = Path("/tmp") / cluster
    cluster_dir.mkdir(parents=True, exist_ok=True)

    # safe filename parts
    ns_safe = re.sub(r"[^A-Za-z0-9_.-]", "_", namespace)
    pod_safe = re.sub(r"[^A-Za-z0-9_.-]", "_", pod)

    desc_fname = f"{ns_safe}__{pod_safe}__{ts}__desc.txt"
    log_fname = f"{ns_safe}__{pod_safe}__{ts}__log.txt"

    desc_path = cluster_dir / desc_fname
    log_path = cluster_dir / log_fname

    # oc describe pod
    try:
        rc, out, err = run_oc_subcommand(
            context,
            ["-n", namespace, "describe", "pod", pod],
            retries=retries,
            oc_timeout_seconds=oc_timeout_seconds,
        )
        content_desc = (
            out
            if rc == 0 and out
            else (err if err else "Failed to fetch pod description")
        )
    except Exception as e:
        logging.error(f"Error fetching pod description for {namespace}/{pod}: {e}")
        content_desc = f"Error fetching pod description: {e}"

    try:
        desc_path.write_text(content_desc)
    except Exception as e:
        # fallback to best-effort path
        desc_path = cluster_dir / f"{ns_safe}__{pod_safe}__{ts}__desc.failed.txt"
        try:
            desc_path.write_text(
                f"Failed to write description: {e}\nOriginal content:\n{content_desc}"
            )
        except Exception:
            pass

    # oc logs pod (current)
    log_content = ""
    try:
        rc2, out2, err2 = run_oc_subcommand(
            context,
            ["-n", namespace, "logs", pod],
            retries=retries,
            oc_timeout_seconds=oc_timeout_seconds,
        )
        if rc2 == 0 and out2:
            log_content = out2
        else:
            # try previous
            rc3, out3, err3 = run_oc_subcommand(
                context,
                ["-n", namespace, "logs", pod, "--previous"],
                retries=retries,
                oc_timeout_seconds=oc_timeout_seconds,
            )
            if rc3 == 0 and out3:
                log_content = out3
            else:
                # if both empty, record the stderr or note
                log_content = (
                    err2 or err3 or "No logs found (both current and previous empty)"
                )
    except Exception as e:
        logging.error(f"Error fetching logs for {namespace}/{pod}: {e}")
        log_content = f"Error fetching logs: {e}"

    try:
        log_path.write_text(log_content)
    except Exception as e:
        log_path = cluster_dir / f"{ns_safe}__{pod_safe}__{ts}__log.failed.txt"
        try:
            log_path.write_text(
                f"Failed to write logs: {e}\nOriginal logs content:\n{log_content}"
            )
        except Exception:
            pass

    return str(desc_path.resolve()), str(log_path.resolve())


# ---------------------------
# namespace worker (oc-only)
# ---------------------------
def namespace_worker_oc(
    context: str, namespace: str, retries: int, oc_timeout_seconds: int
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Process namespace to find OOMKilled and CrashLoopBackOff pods."""
    pod_map: Dict[str, Dict[str, Any]] = {}

    # OPTIMIZATION: Fetch events once instead of 3 separate API calls
    all_events = get_all_events_oc(
        context, namespace, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )

    # Filter events in memory for OOMKilled, CrashLoop, and BackOff
    oom_events: List[Dict[str, str]] = []
    crash_events: List[Dict[str, str]] = []
    backoff_events: List[Dict[str, str]] = []

    for ev in all_events:
        reason = ev.get("reason", "")
        reason_lower = reason.lower()
        pod = ev.get("involvedObject", {}).get("name")
        ts = ev.get("eventTime") or ev.get("lastTimestamp") or ev.get("firstTimestamp")

        if not pod or not ts:
            continue

        event_data = {
            "pod": pod,
            "reason": reason,
            "timestamp": parse_timestamp_to_iso(ts),
        }

        if "oomkilled" in reason_lower:
            oom_events.append(event_data)
        elif "crashloop" in reason_lower:
            crash_events.append(event_data)
        elif "backoff" in reason_lower:
            backoff_events.append(event_data)

    crash_pods = crashloop_via_pods_oc(
        context, namespace, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )

    for e in oom_events:
        p = e["pod"]
        pod_map.setdefault(
            p,
            {"pod": p, "oom_timestamps": [], "crash_timestamps": [], "sources": set()},
        )
        pod_map[p]["oom_timestamps"].append(e.get("timestamp", ""))
        pod_map[p]["sources"].add("events")
    for e in crash_events + backoff_events:
        p = e["pod"]
        pod_map.setdefault(
            p,
            {"pod": p, "oom_timestamps": [], "crash_timestamps": [], "sources": set()},
        )
        pod_map[p]["crash_timestamps"].append(e.get("timestamp", ""))
        pod_map[p]["sources"].add("events")
    for e in crash_pods:
        p = e["pod"]
        pod_map.setdefault(
            p,
            {"pod": p, "oom_timestamps": [], "crash_timestamps": [], "sources": set()},
        )
        pod_map[p]["crash_timestamps"].append(e.get("timestamp", ""))
        pod_map[p]["sources"].add("oc_get_pods")

    if pod_map:
        out_ns: Dict[str, Dict[str, Any]] = {}
        for p, info in pod_map.items():
            out_ns[p] = {
                "pod": p,
                "oom_timestamps": sorted(list(set(info.get("oom_timestamps", [])))),
                "crash_timestamps": sorted(list(set(info.get("crash_timestamps", [])))),
                "sources": sorted(list(info.get("sources", []))),
            }
        return out_ns
    return None


# ---------------------------
# query a single cluster (namespaces in parallel batches)
# ---------------------------
def query_context(
    context: str,
    retries: int,
    oc_timeout_seconds: int,
    ns_batch_size: int = DEFAULT_NS_BATCH_SIZE,
    ns_workers: int = DEFAULT_NS_WORKERS,
    prom_workers: Optional[int] = None,
    skip_prometheus: bool = False,
) -> Tuple[str, Dict[str, Any], Optional[str]]:
    cluster = short_cluster_name(context)
    print(color(f"\n→ Processing cluster: {cluster}", BLUE))

    ok, msg = check_cluster_connectivity(
        context, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    if not ok:
        err_msg = f"Cluster {cluster} unreachable or auth/connectivity failure: {msg}"
        print(color(f"  [SKIP] {err_msg}", RED))
        return cluster, {}, err_msg

    if prom_workers is None:
        prom_workers = ns_workers

    # Access global patterns (set in parse_args)
    namespaces = get_namespaces_for_context(
        context,
        retries=retries,
        oc_timeout_seconds=oc_timeout_seconds,
        include_patterns=_INCLUDE_PATTERNS,
        exclude_patterns=_EXCLUDE_PATTERNS,
    )
    if not namespaces:
        return cluster, {}, None

    cluster_result: Dict[str, Any] = {}
    namespaces_needing_prom: Set[str] = set()

    total_ns = len(namespaces)
    for i in range(0, total_ns, ns_batch_size):
        ns_batch = namespaces[i : i + ns_batch_size]
        print(
            color(
                f"  Namespace batch {i//ns_batch_size + 1}: {len(ns_batch)} namespaces",
                YELLOW,
            )
        )

        with ThreadPoolExecutor(max_workers=min(ns_workers, len(ns_batch))) as ex:
            futures = {
                ex.submit(
                    namespace_worker_oc, context, ns, retries, oc_timeout_seconds
                ): ns
                for ns in ns_batch
            }
            for fut in as_completed(futures):
                ns = futures[fut]
                try:
                    res = fut.result()
                    if res:
                        # Save artifacts for each pod found in this namespace
                        out_ns_with_artifacts: Dict[str, Dict[str, Any]] = {}
                        for p, info in res.items():
                            desc_file, log_file = save_pod_artifacts(
                                context, cluster, ns, p, retries, oc_timeout_seconds
                            )
                            info["description_file"] = desc_file
                            info["pod_log_file"] = log_file
                            out_ns_with_artifacts[p] = info
                        cluster_result[ns] = out_ns_with_artifacts
                        print(
                            color(
                                f"    Namespace {ns}: {len(res)} pod(s) found via oc",
                                YELLOW,
                            )
                        )
                    else:
                        namespaces_needing_prom.add(ns)
                except Exception as e:
                    print(color(f"    Error processing namespace {ns}: {e}", RED))
                    namespaces_needing_prom.add(ns)

    # Prometheus fallback: run for namespaces_needing_prom in batches and parallelized
    if not skip_prometheus and namespaces_needing_prom:
        ns_count = len(namespaces_needing_prom)
        print(
            color(
                f"  Running Prometheus fallback for {ns_count} namespace(s) in batches",
                BLUE,
            )
        )
        prom_list = sorted(list(namespaces_needing_prom))
        for i in range(0, len(prom_list), ns_batch_size):
            prom_batch = prom_list[i : i + ns_batch_size]
            print(
                color(
                    f"    Prom batch {i//ns_batch_size + 1}: {len(prom_batch)} namespaces",
                    YELLOW,
                )
            )
            with ThreadPoolExecutor(
                max_workers=min(prom_workers, len(prom_batch))
            ) as pex:
                pfutures = {}
                # schedule oom and crash queries as separate tasks
                for ns in prom_batch:
                    f1 = pex.submit(
                        prom_query_from_prometheus_pod,
                        context,
                        ns,
                        PROMQL_OOM.format(namespace=ns),
                        retries,
                        oc_timeout_seconds,
                    )
                    pfutures[f1] = (ns, "oom")
                    f2 = pex.submit(
                        prom_query_from_prometheus_pod,
                        context,
                        ns,
                        PROMQL_CRASH.format(namespace=ns),
                        retries,
                        oc_timeout_seconds,
                    )
                    pfutures[f2] = (ns, "crash")
                prom_results_per_ns: Dict[str, Dict[str, Any]] = {}
                for pfut in as_completed(pfutures):
                    ns, typ = pfutures[pfut]
                    try:
                        res = pfut.result()
                        if not res:
                            continue
                        ns_map = prom_results_per_ns.setdefault(ns, {})
                        for item in res:
                            p = item["pod"]
                            ns_map.setdefault(
                                p,
                                {
                                    "pod": p,
                                    "oom_timestamps": [],
                                    "crash_timestamps": [],
                                    "sources": set(),
                                },
                            )
                            ns_map[p]["sources"].add("prometheus")
                    except Exception as e:
                        print(
                            color(
                                f"      Prometheus query failed for ns={ns}: {e}", RED
                            )
                        )
                # after collecting prom batch, attach and save artifacts
                for ns, ns_map in prom_results_per_ns.items():
                    out_ns: Dict[str, Dict[str, Any]] = {}
                    for p, info in ns_map.items():
                        # save artifacts for prom-found pod as well
                        desc_file, log_file = save_pod_artifacts(
                            context, cluster, ns, p, retries, oc_timeout_seconds
                        )
                        info2 = {
                            "pod": p,
                            "oom_timestamps": [],
                            "crash_timestamps": [],
                            "sources": sorted(list(info.get("sources", []))),
                            "description_file": desc_file,
                            "pod_log_file": log_file,
                        }
                        out_ns[p] = info2
                    cluster_result[ns] = out_ns
                    print(
                        color(
                            f"    Prometheus found {len(out_ns)} pod(s) in namespace {ns}",
                            YELLOW,
                        )
                    )

    # write per-cluster log
    try:
        outfile = Path("/tmp") / f"{cluster}.log"
        outfile.write_text(json.dumps(cluster_result, indent=2))
    except Exception:
        pass

    return cluster, cluster_result, None


# ---------------------------
# cluster batch runner
# ---------------------------
def run_batches(
    contexts: List[str],
    batch_size: int,
    retries: int,
    oc_timeout_seconds: int,
    ns_batch_size: int,
    ns_workers: int,
    prom_workers: int,
    skip_prometheus: bool,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    results: Dict[str, Any] = {}
    skipped: Dict[str, str] = {}
    total = len(contexts)
    for i in range(0, total, batch_size):
        batch = contexts[i : i + batch_size]
        print(color(f"\n=== Cluster batch {i//batch_size + 1}: {batch} ===", BLUE))
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            futures = {
                ex.submit(
                    query_context,
                    ctx,
                    retries,
                    oc_timeout_seconds,
                    ns_batch_size,
                    ns_workers,
                    prom_workers,
                    skip_prometheus,
                ): ctx
                for ctx in batch
            }
            for fut in as_completed(futures):
                ctx = futures[fut]
                try:
                    cluster, data, err = fut.result()
                    if err:
                        skipped[cluster] = err
                        print(color(f"Skipped cluster {cluster}: {err}", RED))
                    else:
                        results[cluster] = data
                        print(color(f"Completed cluster {cluster}", GREEN))
                except Exception as e:
                    cluster_guess = short_cluster_name(ctx)
                    skipped[cluster_guess] = str(e)
                    print(color(f"Error processing {cluster_guess}: {e}", RED))
    return results, skipped


# ---------------------------
# exports & pretty print
# ---------------------------
def export_results(results: Dict[str, Any], json_path: Path, csv_path: Path) -> None:
    """Export results to JSON and CSV files."""
    # JSON structure: namespace -> pod -> info with description_file and pod_log_file
    try:
        json_path.write_text(json.dumps(results, indent=2))
        print(color(f"JSON written → {json_path}", GREEN))
    except (IOError, OSError) as e:
        logging.error(f"Failed to write JSON file {json_path}: {e}")
        print(color(f"ERROR: Failed to write JSON file: {e}", RED))

    # CSV: cluster,namespace,pod,type,timestamps,sources,description_file,pod_log_file
    try:
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "cluster",
                    "namespace",
                    "pod",
                    "type",
                    "timestamps",
                    "sources",
                    "description_file",
                    "pod_log_file",
                ]
            )
            for cluster, ns_map in results.items():
                for ns, pods in ns_map.items():
                    for pod_name, info in pods.items():
                        desc = info.get("description_file", "")
                        plog = info.get("pod_log_file", "")
                        sources = (
                            ";".join(info.get("sources", []))
                            if info.get("sources")
                            else ""
                        )
                        # OOM rows
                        if info.get("oom_timestamps"):
                            writer.writerow(
                                [
                                    cluster,
                                    ns,
                                    pod_name,
                                    "OOMKilled",
                                    ";".join(info.get("oom_timestamps")),
                                    sources,
                                    desc,
                                    plog,
                                ]
                            )
                        # Crash rows
                        if info.get("crash_timestamps"):
                            writer.writerow(
                                [
                                    cluster,
                                    ns,
                                    pod_name,
                                    "CrashLoopBackOff",
                                    ";".join(info.get("crash_timestamps")),
                                    sources,
                                    desc,
                                    plog,
                                ]
                            )
                        # If neither timestamps but prom-found, write FoundBy row
                        if not info.get("oom_timestamps") and not info.get(
                            "crash_timestamps"
                        ):
                            writer.writerow(
                                [
                                    cluster,
                                    ns,
                                    pod_name,
                                    "FoundBy",
                                    "",
                                    sources,
                                    desc,
                                    plog,
                                ]
                            )
        print(color(f"CSV written → {csv_path}", GREEN))
    except (IOError, OSError) as e:
        logging.error(f"Failed to write CSV file {csv_path}: {e}")
        print(color(f"ERROR: Failed to write CSV file: {e}", RED))


def pretty_print(results: Dict[str, Any], skipped: Dict[str, str]) -> None:
    for cluster, ns_map in results.items():
        print()
        print(color(f"Cluster: {cluster}", BLUE))
        if not ns_map:
            print(color("  (no namespaces with OOM/CrashLoopBackOff found)", GREEN))
            continue
        for ns, pods in ns_map.items():
            print(color(f"  Namespace: {ns}", YELLOW))
            for pod_name, info in pods.items():
                heading_color = (
                    RED
                    if (info.get("oom_timestamps") or info.get("crash_timestamps"))
                    else GREEN
                )
                print(color(f"    Pod: {pod_name}", heading_color))
                if info.get("oom_timestamps"):
                    for t in info["oom_timestamps"]:
                        print(f"      - OOMKilled at: {t}")
                if info.get("crash_timestamps"):
                    for t in info["crash_timestamps"]:
                        print(f"      - CrashLoopBackOff event at: {t}")
                if not info.get("oom_timestamps") and not info.get("crash_timestamps"):
                    sources_str = ", ".join(info.get("sources", []))
                    print(
                        f"      - Detected (no timestamps) via sources: {sources_str}"
                    )
                # print artifacts paths
                if info.get("description_file") or info.get("pod_log_file"):
                    print(
                        f"      - description_file: {info.get('description_file', '')}"
                    )
                    print(f"      - pod_log_file: {info.get('pod_log_file', '')}")
    if skipped:
        print()
        print(color("Skipped / Unreachable clusters:", RED))
        for c, msg in skipped.items():
            print(color(f"  {c}: {msg}", RED))


# ---------------------------
# global patterns (populated in parse_args)
# ---------------------------
_INCLUDE_PATTERNS: Optional[List[Pattern]] = None
_EXCLUDE_PATTERNS: Optional[List[Pattern]] = None


# ---------------------------
# argument parsing
# ---------------------------
def print_usage_and_exit() -> None:
    print(
        """
Usage:
  oc_get_ooms.py [--current] [--contexts ctxA,ctxB] [--batch N]
                 [--ns-batch-size M] [--ns-workers W] [--prom-workers P]
                 [--skip-prometheus] [--include-ns regex1,regex2] [--exclude-ns regex1,regex2]
                 [--retries R] [--timeout S] [--help]

Options:
  --current                Run only on current-context
  --contexts ctxA,ctxB     Comma-separated contexts
  --batch N                Cluster-level batch size (default 2)
  --ns-batch-size M        Number of namespaces in each namespace batch (default 10)
  --ns-workers W           Thread pool size for oc checks per namespace batch (default 5)
  --prom-workers P         Thread pool size for Prometheus queries per prom-batch
                          (default = ns-workers)
  --skip-prometheus        Do not run Prometheus fallback
  --include-ns regex,...   Comma-separated regex patterns to include (namespace must match any)
  --exclude-ns regex,...   Comma-separated regex patterns to exclude (if match any -> excluded)
  --retries R              Number of retries for oc calls (default 3)
  --timeout S              OC request timeout in seconds used as --request-timeout (default 45)
  --help                   Show this help
"""
    )
    sys.exit(1)


def compile_patterns(csv_patterns: Optional[str]) -> Optional[List[Pattern]]:
    if not csv_patterns:
        return None
    parts = [p.strip() for p in csv_patterns.split(",") if p.strip()]
    if not parts:
        return None
    try:
        return [re.compile(p) for p in parts]
    except re.error as e:
        print(color(f"Invalid regex in patterns: {e}", RED))
        sys.exit(1)


def parse_args(argv: List[str]) -> Tuple[List[str], int, int, int, int, int, bool, int]:
    args = list(argv)
    if "--help" in args:
        print_usage_and_exit()

    contexts: List[str] = []
    batch_size = DEFAULT_BATCH_SIZE
    ns_batch_size = DEFAULT_NS_BATCH_SIZE
    ns_workers = DEFAULT_NS_WORKERS
    prom_workers = None
    skip_prometheus = False
    retries = DEFAULT_RETRIES
    oc_timeout_seconds = DEFAULT_OC_TIMEOUT
    include_csv = None
    exclude_csv = None

    if "--current" in args:
        cur = get_current_context(
            retries=retries, oc_timeout_seconds=oc_timeout_seconds
        )
        if cur:
            contexts = [cur]
    elif "--contexts" in args:
        i = args.index("--contexts")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --contexts", RED))
            print_usage_and_exit()
        contexts = [c.strip() for c in args[i + 1].split(",") if c.strip()]
    else:
        contexts = get_all_contexts(
            retries=retries, oc_timeout_seconds=oc_timeout_seconds
        )

    if "--batch" in args:
        i = args.index("--batch")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --batch", RED))
            print_usage_and_exit()
        try:
            batch_size = int(args[i + 1])
            if batch_size < 1:
                raise ValueError("batch size must be >= 1")
        except (ValueError, IndexError) as e:
            print(color(f"ERROR: invalid --batch value: {e}", RED))
            print_usage_and_exit()

    if "--ns-batch-size" in args:
        i = args.index("--ns-batch-size")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --ns-batch-size", RED))
            print_usage_and_exit()
        try:
            ns_batch_size = int(args[i + 1])
            if ns_batch_size < 1:
                raise ValueError("ns-batch-size must be >= 1")
        except (ValueError, IndexError) as e:
            print(color(f"ERROR: invalid --ns-batch-size value: {e}", RED))
            print_usage_and_exit()

    if "--ns-workers" in args:
        i = args.index("--ns-workers")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --ns-workers", RED))
            print_usage_and_exit()
        try:
            ns_workers = int(args[i + 1])
            if ns_workers < 1:
                raise ValueError("ns-workers must be >= 1")
        except (ValueError, IndexError) as e:
            print(color(f"ERROR: invalid --ns-workers value: {e}", RED))
            print_usage_and_exit()

    if "--prom-workers" in args:
        i = args.index("--prom-workers")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --prom-workers", RED))
            print_usage_and_exit()
        try:
            prom_workers = int(args[i + 1])
            if prom_workers < 1:
                raise ValueError("prom-workers must be >= 1")
        except (ValueError, IndexError) as e:
            print(color(f"ERROR: invalid --prom-workers value: {e}", RED))
            print_usage_and_exit()

    if "--skip-prometheus" in args:
        skip_prometheus = True

    if "--include-ns" in args:
        i = args.index("--include-ns")
        include_csv = args[i + 1] if i + 1 < len(args) else None

    if "--exclude-ns" in args:
        i = args.index("--exclude-ns")
        exclude_csv = args[i + 1] if i + 1 < len(args) else None

    if "--retries" in args:
        i = args.index("--retries")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --retries", RED))
            print_usage_and_exit()
        try:
            retries = int(args[i + 1])
            if retries < 1:
                raise ValueError("retries must be >= 1")
        except (ValueError, IndexError) as e:
            print(color(f"ERROR: invalid --retries value: {e}", RED))
            print_usage_and_exit()

    if "--timeout" in args:
        i = args.index("--timeout")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --timeout", RED))
            print_usage_and_exit()
        try:
            oc_timeout_seconds = int(args[i + 1])
            if oc_timeout_seconds < 1:
                raise ValueError("timeout must be >= 1")
        except (ValueError, IndexError) as e:
            print(color(f"ERROR: invalid --timeout value: {e}", RED))
            print_usage_and_exit()

    global _INCLUDE_PATTERNS, _EXCLUDE_PATTERNS
    _INCLUDE_PATTERNS = compile_patterns(include_csv)
    _EXCLUDE_PATTERNS = compile_patterns(exclude_csv)

    if prom_workers is None:
        prom_workers = ns_workers

    return (
        contexts,
        batch_size,
        ns_batch_size,
        ns_workers,
        prom_workers,
        skip_prometheus,
        retries,
        oc_timeout_seconds,
    )


# ---------------------------
# main
# ---------------------------
def main() -> None:
    """Main entry point for the OOM/CrashLoopBackOff detector."""
    # Configure logging (quiet by default, can be enhanced with --verbose flag)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    (
        contexts,
        batch_size,
        ns_batch_size,
        ns_workers,
        prom_workers,
        skip_prometheus,
        retries,
        oc_timeout_seconds,
    ) = parse_args(sys.argv[1:])

    if not contexts:
        print(color("No contexts discovered. Exiting.", RED))
        sys.exit(1)

    print(color(f"Using contexts: {contexts}", BLUE))
    print(
        color(
            f"Cluster-batch: {batch_size}  NS-batch-size: {ns_batch_size}  "
            f"NS-workers: {ns_workers}  Prom-workers: {prom_workers}",
            BLUE,
        )
    )
    print(
        color(
            f"Retries: {retries}  OC timeout(s): {oc_timeout_seconds}s  "
            f"Skip-prometheus: {skip_prometheus}",
            BLUE,
        )
    )
    if _INCLUDE_PATTERNS:
        print(
            color(
                f"Include namespace patterns: {[p.pattern for p in _INCLUDE_PATTERNS]}",
                BLUE,
            )
        )
    if _EXCLUDE_PATTERNS:
        print(
            color(
                f"Exclude namespace patterns: {[p.pattern for p in _EXCLUDE_PATTERNS]}",
                BLUE,
            )
        )

    results, skipped = run_batches(
        contexts,
        batch_size,
        retries,
        oc_timeout_seconds,
        ns_batch_size,
        ns_workers,
        prom_workers,
        skip_prometheus,
    )

    json_path = Path("oom_results.json")
    csv_path = Path("oom_results.csv")
    export_results(results, json_path, csv_path)

    pretty_print(results, skipped)

    if skipped:
        print(
            color(
                "\nSome clusters were skipped due to connectivity errors (see messages above).",
                YELLOW,
            )
        )

    print(
        color(
            "\nPer-cluster logs written to /tmp/<cluster>/ (if any findings were found)",
            GREEN,
        )
    )


if __name__ == "__main__":
    main()
