#!/usr/bin/env python3
"""
oc_get_ooms.py

Detect OOMKilled and CrashLoopBackOff pods across multiple OpenShift/Kubernetes contexts,
parallelized at cluster and namespace levels, with artifact collection.

New in this version:
- When a pod is detected as OOMKilled or CrashLoopBackOff, save:
    - `oc describe pod <pod>` output
    - `oc logs <pod>` (or `oc logs --previous <pod>` if no current logs)
  into per-cluster directories under /private/tmp/<cluster>/
  Filenames include namespace, pod name, and timestamp to avoid collisions.
- CSV and JSON now include the absolute paths to the description and pod log files:
    description_file, pod_log_file

All previously requested features retained:
- cluster parallelism, namespace batching, include/exclude regex, retries, timeouts,
  time range filtering, colorized output, etc.
"""

from __future__ import annotations

import subprocess
import json
import csv
import re
import sys
import time
import logging
import glob
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Set, Pattern

# Import HTML export module
try:
    from html_export import generate_html_report
except ImportError:
    # Fallback if module not found
    generate_html_report = None


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


# ---------------------------
# Defaults
# ---------------------------
DEFAULT_RETRIES = 3
DEFAULT_OC_TIMEOUT = 45  # seconds
RETRY_DELAY_SECONDS = 3
DEFAULT_NS_BATCH_SIZE = 10
DEFAULT_NS_WORKERS = 5
DEFAULT_BATCH_SIZE = 2



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
# CLI tool detection and helpers
# ---------------------------
_CLI_TOOL: Optional[str] = None  # Cached CLI tool (kubectl or oc)


def detect_cli_tool() -> str:
    """
    Detect which CLI tool to use: kubectl (preferred) or oc (fallback).
    
    Returns:
        "kubectl" if available, "oc" if kubectl not available, or raises error if neither found
    """
    global _CLI_TOOL
    if _CLI_TOOL:
        return _CLI_TOOL
    
    # Try kubectl first (works with any Kubernetes cluster)
    rc, _, _ = run_cmd_with_retries(["kubectl", "version", "--client", "--short"], retries=1, timeout=5)
    if rc == 0:
        _CLI_TOOL = "kubectl"
        return _CLI_TOOL
    
    # Fallback to oc (OpenShift)
    rc, _, _ = run_cmd_with_retries(["oc", "version", "--client"], retries=1, timeout=5)
    if rc == 0:
        _CLI_TOOL = "oc"
        return _CLI_TOOL
    
    # Neither found
    raise RuntimeError(
        "Neither 'kubectl' nor 'oc' CLI tool found. "
        "Please install kubectl (for Kubernetes) or oc (for OpenShift)."
    )


def cli_cmd_parts(
    context: str, cli_timeout_seconds: int, subcommand: List[str]
) -> List[str]:
    """Build command parts for kubectl or oc."""
    cli_tool = detect_cli_tool()
    parts = [cli_tool, f"--request-timeout={cli_timeout_seconds}s"]
    if context:
        parts += ["--context", context]
    parts += subcommand
    return parts


def run_cli_subcommand(
    context: str, subcommand: List[str], retries: int, cli_timeout_seconds: int
) -> Tuple[int, str, str]:
    """Run a kubectl or oc subcommand."""
    cmd = cli_cmd_parts(context, cli_timeout_seconds, subcommand)
    return run_cmd_with_retries(cmd, retries=retries, timeout=cli_timeout_seconds + 5)


# Backward compatibility aliases
def oc_cmd_parts(
    context: str, oc_timeout_seconds: int, subcommand: List[str]
) -> List[str]:
    """Backward compatibility alias for cli_cmd_parts."""
    return cli_cmd_parts(context, oc_timeout_seconds, subcommand)


def run_oc_subcommand(
    context: str, subcommand: List[str], retries: int, oc_timeout_seconds: int
) -> Tuple[int, str, str]:
    """Backward compatibility alias for run_cli_subcommand."""
    return run_cli_subcommand(context, subcommand, retries, oc_timeout_seconds)


# ---------------------------
# context utilities
# ---------------------------
def get_all_contexts(retries: int, oc_timeout_seconds: int) -> List[str]:
    """Get all available Kubernetes/OpenShift contexts."""
    cli_tool = detect_cli_tool()
    cmd = [cli_tool, "config", "get-contexts", "-o", "name"]
    rc, out, err = run_cmd_with_retries(
        cmd, retries=retries, timeout=oc_timeout_seconds + 5
    )
    if rc != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def match_contexts_by_substring(
    substrings: List[str],
    available_contexts: List[str],
) -> List[str]:
    """
    Match context substrings against available contexts.

    Args:
        substrings: List of substrings to match (e.g., ['kflux-prd-rh02'])
        available_contexts: List of all available context names

    Returns:
        List of matched full context names

    Raises:
        SystemExit: If no match or multiple matches found for a substring
    """
    matched_contexts = []
    for substring in substrings:
        matches = [
            ctx for ctx in available_contexts if substring.lower() in ctx.lower()
        ]
        if not matches:
            print(
                color(
                    f"ERROR: No context found matching substring '{substring}'",
                    RED,
                )
            )
            print(color(f"Available contexts:", YELLOW))
            for ctx in available_contexts:
                print(f"  - {ctx}")
            sys.exit(1)
        elif len(matches) > 1:
            print(
                color(
                    f"ERROR: Multiple contexts match substring '{substring}':",
                    RED,
                )
            )
            for ctx in matches:
                print(f"  - {ctx}")
            print(
                color(
                    "Please use a more specific substring to uniquely identify the context.",
                    YELLOW,
                )
            )
            sys.exit(1)
        else:
            matched_contexts.append(matches[0])
            print(
                color(
                    f"Matched '{substring}' -> '{matches[0]}'",
                    GREEN,
                )
            )
    return matched_contexts


def get_current_context(retries: int, oc_timeout_seconds: int) -> str:
    """Get the current Kubernetes/OpenShift context."""
    cli_tool = detect_cli_tool()
    cmd = [cli_tool, "config", "current-context"]
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
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def timestamp_for_backup() -> str:
    """Generate a readable timestamp string for backup filenames.
    
    Returns format like: '12-Jan-2026_12-05-57-EST'
    """
    now = datetime.now()
    # Get timezone abbreviation (EST, PST, etc.)
    tz_abbr = "UTC"
    try:
        # Try strftime first
        tz_str = now.strftime("%Z")
        if tz_str and tz_str.strip():
            tz_abbr = tz_str
        else:
            # Fallback: use time.tzname
            import time
            if time.tzname and len(time.tzname) > 0:
                tz_abbr = time.tzname[0] if time.daylight == 0 else time.tzname[1]
    except Exception:
        # If all else fails, use UTC
        tz_abbr = "UTC"
    
    # Format: DD-MMM-YYYY_HH-MM-SS-TZ
    return now.strftime(f"%d-%b-%Y_%H-%M-%S-{tz_abbr}")


# ---------------------------
# connectivity
# ---------------------------
def check_cluster_connectivity(
    context: str, retries: int, oc_timeout_seconds: int
) -> Tuple[bool, str]:
    """Check cluster connectivity using appropriate method for the CLI tool."""
    cli_tool = detect_cli_tool()
    
    # oc has 'whoami', kubectl doesn't - use 'get ns' for kubectl
    if cli_tool == "oc":
        rc, out, err = run_cli_subcommand(
            context, ["whoami"], retries=retries, cli_timeout_seconds=oc_timeout_seconds
        )
    else:  # kubectl
        # Use 'get ns' as connectivity check (works for all auth methods)
        # Note: --request-timeout is already added by cli_cmd_parts, so we don't need it here
        rc, out, err = run_cli_subcommand(
            context, ["get", "ns"], retries=retries, cli_timeout_seconds=oc_timeout_seconds
        )
    
    if rc == 0:
        return True, ""
    return False, err or out or "unknown error"


def check_all_clusters_connectivity(
    contexts: List[str], retries: int, oc_timeout_seconds: int
) -> Tuple[bool, List[Tuple[str, bool, str]]]:
    """
    Check connectivity to all clusters.
    
    Returns:
        tuple: (all_connected, connectivity_report)
        - all_connected: True if all clusters are accessible
        - connectivity_report: List of (cluster_name, connected, error_message) tuples
    """
    report = []
    all_connected = True
    
    print(color("\n" + "="*80, BLUE))
    print(color("Checking Cluster Connectivity", BLUE))
    print(color("="*80, BLUE))
    
    for ctx in contexts:
        cluster = short_cluster_name(ctx)
        connected, error_msg = check_cluster_connectivity(
            ctx, retries=retries, oc_timeout_seconds=oc_timeout_seconds
        )
        if connected:
            report.append((cluster, True, "Connected"))
            print(color(f"  ✓ {cluster}: Connected", GREEN))
        else:
            report.append((cluster, False, error_msg))
            print(color(f"  ✗ {cluster}: {error_msg}", RED))
            all_connected = False
    
    print(color("="*80, BLUE))
    
    return all_connected, report


def prompt_user_confirmation(connectivity_report: List[Tuple[str, bool, str]]) -> bool:
    """
    Prompt user for confirmation after showing connectivity report.
    
    Returns:
        bool: True if user confirms, False otherwise
    """
    print(color("\nCluster Connectivity Report:", BLUE))
    for cluster, connected, message in connectivity_report:
        if connected:
            print(color(f"  ✓ {cluster}: {message}", GREEN))
        else:
            print(color(f"  ✗ {cluster}: {message}", RED))
    
    all_connected = all(connected for _, connected, _ in connectivity_report)
    if not all_connected:
        print(color("\nWARNING: Some clusters are not accessible.", YELLOW))
        print(color("  Data collection may fail for these clusters.", YELLOW))
        print(color("  Continuing with accessible clusters only...", YELLOW))
    else:
        print(color("\n✓ All clusters are accessible", GREEN))
    
    print(color("="*80, BLUE))
    
    while True:
        sys.stdout.flush()
        sys.stderr.flush()
        response = input("\nProceed with data collection? [y/N]: ").strip().lower()
        if response in ('y', 'yes'):
            print()
            return True
        elif response in ('n', 'no', ''):
            return False
        else:
            print(color("Please enter 'y' or 'n'", YELLOW))


# ---------------------------
# oc namespace workers
# ---------------------------
def parse_time_range(time_range_str: str) -> int:
    """
    Parse time range string (e.g., '1d', '2h', '30m', '1M') into seconds.
    Returns seconds from now to look back.
    """
    if not time_range_str:
        return 86400  # Default 1 day
    time_range_str = time_range_str.strip().lower()
    match = re.match(r"^(\d+)([smhdM])$", time_range_str)
    if not match:
        raise ValueError(f"Invalid time range format: {time_range_str}")
    value = int(match.group(1))
    unit = match.group(2)
    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "M": 2592000,  # 30 days
    }
    return value * multipliers.get(unit, 86400)


def get_all_events_oc(
    context: str,
    namespace: str,
    retries: int,
    oc_timeout_seconds: int,
    time_range_seconds: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Get all events for a namespace (single API call for efficiency).

    Args:
        time_range_seconds: If provided, filter events to this time range
    """
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
    events = obj.get("items", [])

    # Filter by time range if provided
    if time_range_seconds:
        cutoff_time = datetime.now(timezone.utc).timestamp() - time_range_seconds
        filtered_events = []
        for ev in events:
            ts = (
                ev.get("eventTime")
                or ev.get("lastTimestamp")
                or ev.get("firstTimestamp")
            )
            if ts:
                try:
                    # Parse timestamp and compare
                    ts_str = ts.split(".")[0].rstrip("Z")
                    ev_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                    if ev_dt.timestamp() >= cutoff_time:
                        filtered_events.append(ev)
                except (ValueError, AttributeError):
                    # If we can't parse, include it to be safe
                    filtered_events.append(ev)
            else:
                # No timestamp, include it
                filtered_events.append(ev)
        return filtered_events

    return events


def find_events_by_reason_oc(
    context: str,
    namespace: str,
    reason_substring: str,
    retries: int,
    oc_timeout_seconds: int,
    time_range_seconds: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Find events matching a reason substring in a namespace."""
    events = get_all_events_oc(
        context, namespace, retries, oc_timeout_seconds, time_range_seconds
    )
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


def oomkilled_via_pods_oc(
    context: str, namespace: str, retries: int, oc_timeout_seconds: int
) -> List[Dict[str, str]]:
    """Find pods that were OOMKilled by querying pod status.
    
    Enhanced detection checks multiple states:
    - lastState.terminated.reason == "OOMKilled" (previous OOM kill)
    - state.terminated.reason == "OOMKilled" (current/just OOM killed)
    - Also checks initContainerStatuses for init container OOM kills
    """
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
    seen_pods: Set[str] = set()  # Avoid duplicates with timestamps
    
    for item in obj.get("items", []):
        pod_name = item.get("metadata", {}).get("name")
        if not pod_name:
            continue
            
        # Check both regular containers and init containers
        container_statuses = item.get("status", {}).get("containerStatuses", []) or []
        init_container_statuses = item.get("status", {}).get("initContainerStatuses", []) or []
        all_statuses = container_statuses + init_container_statuses
        
        for cs in all_statuses:
            # Check current state.terminated (just OOM killed)
            terminated = cs.get("state", {}).get("terminated", {})
            if terminated and terminated.get("reason") == "OOMKilled":
                finished_at = terminated.get("finishedAt", "")
                key = f"{pod_name}:current"
                if key not in seen_pods:
                    res.append(
                        {
                            "pod": pod_name,
                            "reason": "OOMKilled",
                            "timestamp": (
                                parse_timestamp_to_iso(finished_at) if finished_at else ""
                            ),
                        }
                    )
                    seen_pods.add(key)
                continue
            
            # Check lastState.terminated.reason for OOMKilled (previous OOM kill)
            last_state = cs.get("lastState", {})
            last_terminated = last_state.get("terminated", {})
            if last_terminated and last_terminated.get("reason") == "OOMKilled":
                finished_at = last_terminated.get("finishedAt", "")
                key = f"{pod_name}:last:{finished_at}"
                if key not in seen_pods:
                    res.append(
                        {
                            "pod": pod_name,
                            "reason": "OOMKilled",
                            "timestamp": (
                                parse_timestamp_to_iso(finished_at) if finished_at else ""
                            ),
                        }
                    )
                    seen_pods.add(key)
    
    return res


def crashloop_via_pods_oc(
    context: str, namespace: str, retries: int, oc_timeout_seconds: int
) -> List[Dict[str, str]]:
    """Find pods in CrashLoopBackOff state by querying pod status.
    
    Enhanced detection checks multiple states:
    - state.waiting.reason == "CrashLoopBackOff" (current waiting state)
    - state.terminated.reason == "CrashLoopBackOff" (just crashed)
    - lastState.terminated.reason == "CrashLoopBackOff" (previous crash)
    - High restart count (restartCount > 0) as indicator of crash loops
    - Also checks initContainerStatuses for init container failures
    """
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
    seen_pods: Set[str] = set()  # Avoid duplicates
    
    for item in obj.get("items", []):
        pod_name = item.get("metadata", {}).get("name")
        if not pod_name:
            continue
            
        # Check both regular containers and init containers
        container_statuses = item.get("status", {}).get("containerStatuses", []) or []
        init_container_statuses = item.get("status", {}).get("initContainerStatuses", []) or []
        all_statuses = container_statuses + init_container_statuses
        
        for cs in all_statuses:
            # Check current state.waiting
            waiting = cs.get("state", {}).get("waiting")
            if waiting and waiting.get("reason") == "CrashLoopBackOff":
                if pod_name not in seen_pods:
                    res.append(
                        {"pod": pod_name, "reason": "CrashLoopBackOff", "timestamp": ""}
                    )
                    seen_pods.add(pod_name)
                continue
            
            # Check current state.terminated (container just crashed)
            terminated = cs.get("state", {}).get("terminated")
            if terminated and terminated.get("reason") == "CrashLoopBackOff":
                if pod_name not in seen_pods:
                    res.append(
                        {"pod": pod_name, "reason": "CrashLoopBackOff", "timestamp": ""}
                    )
                    seen_pods.add(pod_name)
                continue
            
            # Check lastState.terminated (previous crash)
            last_state = cs.get("lastState", {})
            last_terminated = last_state.get("terminated", {})
            if last_terminated and last_terminated.get("reason") == "CrashLoopBackOff":
                if pod_name not in seen_pods:
                    res.append(
                        {"pod": pod_name, "reason": "CrashLoopBackOff", "timestamp": ""}
                    )
                    seen_pods.add(pod_name)
                continue
            
            # Check restart count as indicator of crash loops
            # Only flag if restart count is high (>= 3) AND there's evidence of crashes
            restart_count = cs.get("restartCount", 0)
            if restart_count >= 3:
                # High restart count suggests crash loop
                # Additional check: look for evidence of crashes (terminated states)
                has_terminated_state = (
                    cs.get("state", {}).get("terminated") is not None
                    or cs.get("lastState", {}).get("terminated") is not None
                )
                # If high restart count and has terminated states, likely crash loop
                if has_terminated_state and pod_name not in seen_pods:
                    res.append(
                        {"pod": pod_name, "reason": "CrashLoopBackOff", "timestamp": ""}
                    )
                    seen_pods.add(pod_name)
                    continue
        
        # Also check pod phase - Failed or Pending might indicate issues
        pod_phase = item.get("status", {}).get("phase", "")
        if pod_phase == "Failed":
            # Pod in Failed phase might be due to crash loops
            if pod_name not in seen_pods:
                # Double-check: only add if we have evidence of restarts or crashes
                has_restarts = any(
                    cs.get("restartCount", 0) > 0
                    for cs in all_statuses
                )
                if has_restarts:
                    res.append(
                        {"pod": pod_name, "reason": "CrashLoopBackOff", "timestamp": ""}
                    )
                    seen_pods.add(pod_name)
    
    return res




# ---------------------------
# Ephemeral namespace detection
# ---------------------------
def is_ephemeral_namespace(namespace_name: str, namespace_metadata: Optional[Dict[str, Any]] = None) -> bool:
    """
    Detect if a namespace is an ephemeral test or cluster namespace.

    Detection methods (in order of reliability):
    1. Label-based detection (most reliable):
       - konflux-ci.dev/namespace-type: eaas (EaaS ephemeral namespaces)
       - Other ephemeral namespace labels
    2. Name pattern matching:
       - Ephemeral cluster namespaces: clusters-<uuid> pattern
       - Ephemeral test namespaces: test-*, e2e-*, ephemeral-*, ci-*, pr-*, temp-*

    Args:
        namespace_name: Name of the namespace
        namespace_metadata: Optional namespace metadata dict (from Kubernetes API)
                          If provided, labels will be checked for more reliable detection

    Returns True if the namespace matches ephemeral patterns or labels.
    """
    if not namespace_name:
        return False

    # Method 1: Check labels (most reliable - works even if namespace name is modified)
    if namespace_metadata:
        labels = namespace_metadata.get("labels", {})
        if labels:
            # Primary check: EaaS ephemeral namespace label (most reliable indicator)
            # konflux-ci.dev/namespace-type: eaas
            if labels.get("konflux-ci.dev/namespace-type") == "eaas":
                return True
            
            # Check for other ephemeral namespace label indicators
            # Look for labels that suggest ephemeral/test namespaces
            ephemeral_label_indicators = {
                "konflux-ci.dev/namespace-type": ["eaas", "ephemeral", "test"],
                "namespace-type": ["eaas", "ephemeral", "test"],
                "ephemeral": ["true", "yes"],
            }
            
            for label_key, label_value in labels.items():
                label_key_lower = label_key.lower()
                label_value_lower = str(label_value).lower()
                
                # Check if label key matches known ephemeral indicators
                for indicator_key, indicator_values in ephemeral_label_indicators.items():
                    if indicator_key in label_key_lower:
                        if any(val in label_value_lower for val in indicator_values):
                            return True

    # Method 2: Name pattern matching (fallback if labels not available)
    # Ephemeral cluster namespaces: clusters-<uuid> pattern
    # UUID format: 8-4-4-4-12 hex digits (e.g., clusters-4e52ba17-c17b-4f35-b7e0-0215e63678a0)
    if re.match(r'^clusters-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', namespace_name, re.IGNORECASE):
        return True

    # Ephemeral test namespaces: common test/e2e/ephemeral patterns
    ephemeral_test_patterns = [
        r'^test-',
        r'^e2e-',
        r'^ephemeral-',
        r'^ci-',
        r'^pr-',
        r'^temp-',
        r'^tmp-',
        r'-test$',
        r'-e2e$',
        r'-ephemeral$',
    ]

    for pattern in ephemeral_test_patterns:
        if re.search(pattern, namespace_name, re.IGNORECASE):
            return True

    return False


# ---------------------------
# get namespaces and apply include/exclude regex lists
# ---------------------------
def get_namespaces_for_context(
    context: str,
    retries: int,
    oc_timeout_seconds: int,
    include_patterns: Optional[List[Pattern]] = None,
    exclude_patterns: Optional[List[Pattern]] = None,
    exclude_ephemeral: bool = True,
) -> List[str]:
    """
    Get namespaces for a context, optionally filtered by include/exclude patterns.

    Args:
        exclude_ephemeral: If True, automatically exclude ephemeral test and cluster namespaces
                          (default: True for EaaS clusters)
    """
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
    # Collect namespace names and metadata for ephemeral detection
    namespaces_with_metadata = []
    for item in obj.get("items", []):
        metadata = item.get("metadata", {})
        ns_name = metadata.get("name")
        if ns_name:
            namespaces_with_metadata.append((ns_name, metadata))
    
    filtered: List[str] = []
    for ns_name, ns_metadata in namespaces_with_metadata:
        # Exclude ephemeral namespaces if enabled (check both labels and name patterns)
        if exclude_ephemeral and is_ephemeral_namespace(ns_name, ns_metadata):
            if _VERBOSE:
                print(color(f"  [skip ephemeral] {ns_name}", YELLOW))
            continue

        include = True
        if include_patterns:
            include = any(p.search(ns_name) for p in include_patterns)
        if not include:
            if _VERBOSE:
                print(color(f"  [skip include filter] {ns_name}", YELLOW))
            continue
        if exclude_patterns and any(p.search(ns_name) for p in exclude_patterns):
            if _VERBOSE:
                print(color(f"  [skip exclude filter] {ns_name}", YELLOW))
            continue
        filtered.append(ns_name)
    return filtered


# ---------------------------
# Save pod artifacts (describe + logs) into per-cluster directory under /private/tmp/<cluster>/
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
    Save 'oc describe pod' and 'oc logs pod' (or --previous) into files under /private/tmp/<cluster>/
    Filenames include namespace, pod name and timestamp to avoid collisions.
    Returns absolute file paths (description_file, pod_log_file)
    """
    ts = now_ts_for_filename()
    cluster_dir = Path("/private/tmp") / cluster
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
    context: str,
    namespace: str,
    retries: int,
    oc_timeout_seconds: int,
    time_range_seconds: Optional[int] = None,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Process namespace to find OOMKilled and CrashLoopBackOff pods."""
    pod_map: Dict[str, Dict[str, Any]] = {}

    # OPTIMIZATION: Fetch events once instead of 3 separate API calls
    all_events = get_all_events_oc(
        context,
        namespace,
        retries=retries,
        oc_timeout_seconds=oc_timeout_seconds,
        time_range_seconds=time_range_seconds,
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

    # Also check pod status directly for OOMKilled and CrashLoopBackOff
    oom_pods = oomkilled_via_pods_oc(
        context, namespace, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
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
    # Add OOM pods found via pod status
    for e in oom_pods:
        p = e["pod"]
        pod_map.setdefault(
            p,
            {"pod": p, "oom_timestamps": [], "crash_timestamps": [], "sources": set()},
        )
        pod_map[p]["oom_timestamps"].append(e.get("timestamp", ""))
        pod_map[p]["sources"].add("oc_get_pods")
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
    time_range_seconds: Optional[int] = None,
    exclude_ephemeral: bool = True,
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

    # Access global patterns (set in parse_args)
    namespaces = get_namespaces_for_context(
        context,
        retries=retries,
        oc_timeout_seconds=oc_timeout_seconds,
        include_patterns=_INCLUDE_PATTERNS,
        exclude_patterns=_EXCLUDE_PATTERNS,
        exclude_ephemeral=exclude_ephemeral,
    )
    if not namespaces:
        return cluster, {}, None

    if _VERBOSE:
        print(color(f"  Will scan {len(namespaces)} namespaces:", BLUE))
        for ns in namespaces:
            print(color(f"    {ns}", BLUE))

    cluster_result: Dict[str, Any] = {}

    total_ns = len(namespaces)
    for i in range(0, total_ns, ns_batch_size):
        ns_batch = namespaces[i : i + ns_batch_size]
        print(
            color(
                f"  Namespace batch {i//ns_batch_size + 1}: {len(ns_batch)} namespaces",
                YELLOW,
            )
        )
        if _VERBOSE:
            print(color(f"    Scanning: {', '.join(ns_batch)}", BLUE))

        with ThreadPoolExecutor(max_workers=min(ns_workers, len(ns_batch))) as ex:
            futures = {
                ex.submit(
                    namespace_worker_oc,
                    context,
                    ns,
                    retries,
                    oc_timeout_seconds,
                    time_range_seconds,
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
                                f"    Namespace {ns}: {len(res)} pod(s) found",
                                YELLOW,
                            )
                        )
                except Exception as e:
                    print(color(f"    Error processing namespace {ns}: {e}", RED))

    # write per-cluster log
    try:
        outfile = Path("/private/tmp") / f"{cluster}.log"
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
    time_range_seconds: Optional[int] = None,
    exclude_ephemeral: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Run cluster processing with constant parallelism.

    Instead of processing in fixed batches, maintains constant parallelism:
    when one cluster finishes, immediately start the next one.
    """
    results: Dict[str, Any] = {}
    skipped: Dict[str, str] = {}
    total = len(contexts)
    context_index = 0
    active_futures: Dict[Any, str] = {}

    with ThreadPoolExecutor(max_workers=batch_size) as ex:
        # Start initial batch
        while context_index < total and len(active_futures) < batch_size:
            ctx = contexts[context_index]
            context_index += 1
            fut = ex.submit(
                query_context,
                ctx,
                retries,
                oc_timeout_seconds,
                ns_batch_size,
                ns_workers,
                time_range_seconds,
                exclude_ephemeral,
            )
            active_futures[fut] = ctx
            print(
                color(
                    f"Started processing cluster: {short_cluster_name(ctx)}",
                    BLUE,
                )
            )

        # Process as they complete, starting new ones to maintain parallelism
        while active_futures:
            for fut in as_completed(active_futures):
                ctx = active_futures.pop(fut)
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

                # Start next cluster if available
                if context_index < total:
                    next_ctx = contexts[context_index]
                    context_index += 1
                    next_fut = ex.submit(
                        query_context,
                        next_ctx,
                        retries,
                        oc_timeout_seconds,
                        ns_batch_size,
                        ns_workers,
                        time_range_seconds,
                        exclude_ephemeral,
                    )
                    active_futures[next_fut] = next_ctx
                    print(
                        color(
                            f"Started processing cluster: {short_cluster_name(next_ctx)}",
                            BLUE,
                        )
                    )

    return results, skipped


# ---------------------------
# output directory management
# ---------------------------
def ensure_output_directory() -> Path:
    """
    Ensure the 'output' subdirectory exists, creating it if necessary.
    
    Returns:
        Path to the output directory
    """
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    return output_dir


def move_existing_output_files() -> int:
    """
    Move all existing output files (oom_results.* and timestamped versions)
    from current directory to 'output' subdirectory.
    
    Returns:
        Number of files moved
    """
    output_dir = ensure_output_directory()
    moved_count = 0
    
    # Pattern to match output files
    output_patterns = [
        "oom_results.csv",
        "oom_results.json",
        "oom_results.html",
        "oom_results.table",
        "oom_results_*.csv",
        "oom_results_*.json",
        "oom_results_*.html",
        "oom_results_*.table",
    ]
    
    current_dir = Path(".")
    for pattern in output_patterns:
        # Handle wildcard patterns
        if "*" in pattern:
            files = glob.glob(str(current_dir / pattern))
        else:
            files = [str(current_dir / pattern)] if (current_dir / pattern).exists() else []
        
        for file_path_str in files:
            file_path = Path(file_path_str)
            if file_path.exists() and file_path.is_file():
                try:
                    dest_path = output_dir / file_path.name
                    # If file already exists in output dir, skip (don't overwrite)
                    if not dest_path.exists():
                        file_path.rename(dest_path)
                        moved_count += 1
                    else:
                        # If destination exists, try to rename with a timestamp
                        timestamp = timestamp_for_backup()
                        suffix = file_path.suffix
                        stem = file_path.stem
                        backup_name = f"{stem}_{timestamp}{suffix}"
                        dest_path = output_dir / backup_name
                        file_path.rename(dest_path)
                        moved_count += 1
                except Exception as e:
                    logging.warning(f"Failed to move {file_path} to output directory: {e}")
    
    if moved_count > 0:
        print(color(f"Moved {moved_count} existing output file(s) to 'output' directory", YELLOW))
    
    return moved_count


# ---------------------------
# file backup utilities
# ---------------------------
def backup_existing_file(file_path: Path) -> Optional[Path]:
    """Backup an existing file by renaming it with a timestamp.
    
    Args:
        file_path: Path to the file to backup
        
    Returns:
        Path to the backup file if backup was successful, None otherwise
    """
    if not file_path.exists():
        return None
    
    try:
        timestamp = timestamp_for_backup()
        # Get file extension
        suffix = file_path.suffix
        stem = file_path.stem
        backup_name = f"{stem}_{timestamp}{suffix}"
        backup_path = file_path.parent / backup_name
        
        # Rename the file
        file_path.rename(backup_path)
        return backup_path
    except Exception as e:
        logging.warning(f"Failed to backup {file_path}: {e}")
        return None


def backup_output_files(
    json_path: Path,
    csv_path: Path,
    table_path: Path,
    html_path: Path,
) -> None:
    """Backup existing output files before generating new ones."""
    backups = []
    
    for file_path in [json_path, csv_path, table_path, html_path]:
        backup_path = backup_existing_file(file_path)
        if backup_path:
            backups.append(backup_path)
    
    if backups:
        print(color(f"\nBacked up {len(backups)} existing file(s):", YELLOW))
        for backup_path in backups:
            print(color(f"  → {backup_path.name}", YELLOW))


# ---------------------------
# exports & pretty print
# ---------------------------
def collect_rows(
    results: Dict[str, Any], time_range_str: str = "1d"
) -> List[Dict[str, str]]:
    """
    Collect all rows from results dictionary.

    Returns a list of dictionaries representing rows, sorted by type
    (OOMKilled first, then CrashLoopBackOff).
    """
    rows = []
    # Skip _metadata if present
    for cluster, ns_map in results.items():
        if cluster == "_metadata":
            continue
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
                    rows.append(
                        {
                            "cluster": cluster,
                            "namespace": ns,
                            "pod": pod_name,
                            "type": "OOMKilled",
                            "timestamps": ";".join(info.get("oom_timestamps")),
                            "sources": sources,
                            "description_file": desc,
                            "pod_log_file": plog,
                            "time_range": time_range_str,
                        }
                    )
                # Crash rows
                if info.get("crash_timestamps"):
                    rows.append(
                        {
                            "cluster": cluster,
                            "namespace": ns,
                            "pod": pod_name,
                            "type": "CrashLoopBackOff",
                            "timestamps": ";".join(
                                info.get("crash_timestamps")
                            ),
                            "sources": sources,
                            "description_file": desc,
                            "pod_log_file": plog,
                            "time_range": time_range_str,
                        }
                    )

    # Sort: OOMKilled first, then CrashLoopBackOff
    def sort_key(row: Dict[str, str]) -> Tuple[int, str, str, str]:
        type_val = row.get("type", "")
        if type_val == "OOMKilled":
            return (
                0,
                row.get("cluster", ""),
                row.get("namespace", ""),
                row.get("pod", ""),
            )
        elif type_val == "CrashLoopBackOff":
            return (
                1,
                row.get("cluster", ""),
                row.get("namespace", ""),
                row.get("pod", ""),
            )
        else:
            return (
                2,
                row.get("cluster", ""),
                row.get("namespace", ""),
                row.get("pod", ""),
            )

    rows.sort(key=sort_key)
    return rows


def export_table(rows: List[Dict[str, str]], table_path: Path) -> None:
    """Export rows to a table-formatted file."""
    if not rows:
        return

    columns = [
        "cluster",
        "namespace",
        "pod",
        "type",
        "timestamps",
        "sources",
        "description_file",
        "pod_log_file",
        "time_range",
    ]

    # Calculate column widths
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(row.get(col, "")))

    # Generate table
    lines = []

    # Build header row first to calculate exact width
    header_parts = [f" {col:<{widths[col]}} " for col in columns]
    header_row = "|" + "|".join(header_parts) + "|"
    
    # Calculate total width: length of the header row
    total_width = len(header_row)

    # Header separator (continuous line of dashes matching table width)
    header_sep = "-" * total_width
    lines.append(header_sep)

    # Header row
    lines.append(header_row)

    # Header separator again
    lines.append(header_sep)

    # Data rows
    for row in rows:
        data_parts = [f" {row.get(col, ''):<{widths[col]}} " for col in columns]
        data_row = "|" + "|".join(data_parts) + "|"
        lines.append(data_row)

    # Footer separator
    lines.append(header_sep)

    # Write to file
    try:
        table_path.write_text("\n".join(lines))
        print(color(f"Table written → {table_path}", GREEN))
    except (IOError, OSError) as e:
        logging.error(f"Failed to write table file {table_path}: {e}")
        print(color(f"ERROR: Failed to write table file: {e}", RED))


def export_results(
    results: Dict[str, Any],
    json_path: Path,
    csv_path: Path,
    table_path: Path,
    html_path: Optional[Path] = None,
    time_range_str: str = "1d",
) -> None:
    """Export results to JSON, CSV, TABLE, and HTML files."""
    # Collect and sort rows
    rows = collect_rows(results, time_range_str)

    # Export JSON
    results_with_metadata = results.copy()
    results_with_metadata["_metadata"] = {"time_range": time_range_str}
    try:
        json_path.write_text(json.dumps(results_with_metadata, indent=2))
        print(color(f"JSON written → {json_path}", GREEN))
    except (IOError, OSError) as e:
        logging.error(f"Failed to write JSON file {json_path}: {e}")
        print(color(f"ERROR: Failed to write JSON file: {e}", RED))

    # Export CSV
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
                    "time_range",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row["cluster"],
                        row["namespace"],
                        row["pod"],
                        row["type"],
                        row["timestamps"],
                        row["sources"],
                        row["description_file"],
                        row["pod_log_file"],
                        row["time_range"],
                    ]
                )
        print(color(f"CSV written → {csv_path}", GREEN))
    except (IOError, OSError) as e:
        logging.error(f"Failed to write CSV file {csv_path}: {e}")
        print(color(f"ERROR: Failed to write CSV file: {e}", RED))

    # Export TABLE
    export_table(rows, table_path)

    # Export HTML
    if html_path and generate_html_report:
        try:
            generate_html_report(rows, time_range_str, html_path)
            print(color(f"HTML written → {html_path}", GREEN))
        except Exception as e:
            logging.error(f"Failed to write HTML file {html_path}: {e}")
            print(color(f"ERROR: Failed to write HTML file: {e}", RED))
    elif html_path and not generate_html_report:
        logging.warning("HTML export module not available, skipping HTML generation")
        print(color("WARNING: HTML export module not available, skipping HTML generation", YELLOW))


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
# global patterns and flags (populated in parse_args)
# ---------------------------
_INCLUDE_PATTERNS: Optional[List[Pattern]] = None
_EXCLUDE_PATTERNS: Optional[List[Pattern]] = None
_VERBOSE: bool = False
_LIST_NAMESPACES: bool = False


# ---------------------------
# argument parsing
# ---------------------------
def print_usage_and_exit() -> None:
    print(
        """
Usage:
  oc_get_ooms.py [OPTIONS]

Context Selection (choose one):
  --current                Run only on current-context
  --contexts ctxA,ctxB     Comma-separated context substrings (matched against available contexts)
                           If neither specified, runs on all available contexts

Parallelism & Performance:
  --batch N                Cluster-level parallelism (default: 2)
                           Maintains constant parallelism: when one cluster finishes,
                           immediately starts the next one
  --ns-batch-size M        Number of namespaces in each namespace batch (default: 10)
  --ns-workers W           Thread pool size for oc checks per namespace batch (default: 5)

Namespace Filtering:
  --include-ns regex,...   Comma-separated regex patterns to include (namespace must match any)
                           Examples: --include-ns "tenant|prod"
  --exclude-ns regex,...   Comma-separated regex patterns to exclude (if match any -> excluded)
                           Examples: --exclude-ns "test|debug"
  --include-ephemeral      Include ephemeral test and cluster namespaces (default: excluded)
                           Ephemeral namespaces include:
                           - Ephemeral cluster namespaces: clusters-<uuid> pattern
                           - Ephemeral test namespaces: test-*, e2e-*, ephemeral-*, ci-*, etc.
                           On EaaS clusters, ephemeral namespaces are excluded by default
                           to avoid false positives from temporary test environments.

Time Range Filtering:
  --time-range RANGE       Time range to look back for events (default: 1d)
                           Format: <number><unit> where unit is:
                           s=seconds, m=minutes, h=hours, d=days, M=months (30 days)
                           Examples: 30s, 1h, 6h, 1d, 7d, 1M

Resilience & Timeouts:
  --retries R              Number of retries for oc calls (default: 3)
  --timeout S              OC request timeout in seconds used as --request-timeout (default: 45)

Output:
  All output formats are generated automatically:
  - oom_results.json       Structured JSON with metadata
  - oom_results.csv        Spreadsheet-friendly CSV format
  - oom_results.table      Human-readable table format
  - oom_results.html       Standalone HTML report (open in browser)

Debug & Troubleshooting:
  -v, --verbose            Show which namespaces are scanned or skipped (ephemeral/include/exclude)
  --list-namespaces        Print namespaces that would be scanned (per context) and exit.
                           Use to verify a namespace (e.g. preflight-dev-tenant) is included.

Other:
  -h, --help               Show this help message

Examples:
  # Run on current context only
  ./oc_get_ooms.py --current

  # Run on specific contexts using substrings
  ./oc_get_ooms.py --contexts kflux-prd-rh02,stone-prd-rh01

  # High-performance mode for large clusters
  ./oc_get_ooms.py --batch 4 --ns-batch-size 250 --ns-workers 250 --timeout 200

  # Filter by time range (last 6 hours)
  ./oc_get_ooms.py --time-range 6h

  # Include only tenant namespaces, exclude test namespaces
  ./oc_get_ooms.py --include-ns tenant --exclude-ns test

  # Combine multiple options
  ./oc_get_ooms.py --contexts prod-cluster --time-range 1d --include-ns "tenant|prod" --batch 4

  # All contexts, last 7 days, with custom parallelism
  ./oc_get_ooms.py --time-range 7d --batch 8 --ns-batch-size 100 --ns-workers 50

  # Verify which namespaces will be scanned (e.g. check if preflight-dev-tenant is included)
  ./oc_get_ooms.py --contexts stone-stg-rh01 --list-namespaces | grep preflight

  # Verbose run to see skipped vs scanned namespaces
  ./oc_get_ooms.py --contexts stone-stg-rh01 --verbose --time-range 1d
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


def parse_args(
    argv: List[str],
) -> Tuple[List[str], int, int, int, int, Optional[int], str, bool, bool, bool]:
    args = list(argv)
    if "--help" in args or "-h" in args:
        print_usage_and_exit()

    contexts: List[str] = []
    batch_size = DEFAULT_BATCH_SIZE
    ns_batch_size = DEFAULT_NS_BATCH_SIZE
    ns_workers = DEFAULT_NS_WORKERS
    retries = DEFAULT_RETRIES
    oc_timeout_seconds = DEFAULT_OC_TIMEOUT
    include_csv = None
    exclude_csv = None
    time_range_str = "1d"  # Default 1 day
    exclude_ephemeral = True  # Default: exclude ephemeral namespaces
    verbose = False
    list_namespaces = False

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
        context_substrings = [c.strip() for c in args[i + 1].split(",") if c.strip()]
        # Get all available contexts and match substrings
        available_contexts = get_all_contexts(
            retries=retries, oc_timeout_seconds=oc_timeout_seconds
        )
        if not available_contexts:
            print(
                color(
                    "ERROR: Could not retrieve available contexts. "
                    "Please check your oc/kubectl configuration.",
                    RED,
                )
            )
            sys.exit(1)
        contexts = match_contexts_by_substring(context_substrings, available_contexts)
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

    if "--include-ns" in args:
        i = args.index("--include-ns")
        include_csv = args[i + 1] if i + 1 < len(args) else None

    if "--exclude-ns" in args:
        i = args.index("--exclude-ns")
        exclude_csv = args[i + 1] if i + 1 < len(args) else None

    if "--include-ephemeral" in args:
        exclude_ephemeral = False  # User wants to include ephemeral namespaces

    if "--verbose" in args or "-v" in args:
        verbose = True

    if "--list-namespaces" in args:
        list_namespaces = True

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

    if "--time-range" in args:
        i = args.index("--time-range")
        if i + 1 >= len(args):
            print(color("ERROR: missing argument for --time-range", RED))
            print_usage_and_exit()
        time_range_str = args[i + 1]
        try:
            # Validate the format
            parse_time_range(time_range_str)
        except ValueError as e:
            print(color(f"ERROR: invalid --time-range value: {e}", RED))
            print_usage_and_exit()

    global _INCLUDE_PATTERNS, _EXCLUDE_PATTERNS, _VERBOSE, _LIST_NAMESPACES
    _INCLUDE_PATTERNS = compile_patterns(include_csv)
    _EXCLUDE_PATTERNS = compile_patterns(exclude_csv)
    _VERBOSE = verbose
    _LIST_NAMESPACES = list_namespaces

    # Parse time range to seconds
    try:
        time_range_seconds = parse_time_range(time_range_str)
    except ValueError:
        time_range_seconds = 86400  # Default to 1 day if parsing fails

    return (
        contexts,
        batch_size,
        ns_batch_size,
        ns_workers,
        retries,
        oc_timeout_seconds,
        time_range_seconds,
        time_range_str,
        exclude_ephemeral,
        verbose,
        list_namespaces,
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
        retries,
        oc_timeout_seconds,
        time_range_seconds,
        time_range_str,
        exclude_ephemeral,
        verbose,
        list_namespaces,
    ) = parse_args(sys.argv[1:])

    if not contexts:
        print(color("No contexts discovered. Exiting.", RED))
        sys.exit(1)

    # --list-namespaces: print namespaces that would be scanned per context and exit
    if list_namespaces:
        for ctx in contexts:
            namespaces = get_namespaces_for_context(
                ctx,
                retries=retries,
                oc_timeout_seconds=oc_timeout_seconds,
                include_patterns=_INCLUDE_PATTERNS,
                exclude_patterns=_EXCLUDE_PATTERNS,
                exclude_ephemeral=exclude_ephemeral,
            )
            cluster = short_cluster_name(ctx)
            print(color(f"Context: {ctx} (cluster: {cluster}) — {len(namespaces)} namespaces", BLUE))
            for ns in sorted(namespaces):
                print(ns)
        sys.exit(0)

    print(color(f"Using contexts: {contexts}", BLUE))
    print(
        color(
            f"Cluster-parallelism: {batch_size}  NS-batch-size: {ns_batch_size}  "
            f"NS-workers: {ns_workers}",
            BLUE,
        )
    )
    print(
        color(
            f"Retries: {retries}  OC timeout(s): {oc_timeout_seconds}s  "
            f"Time-range: {time_range_str}",
            BLUE,
        )
    )
    if exclude_ephemeral:
        print(
            color(
                "Ephemeral namespaces: EXCLUDED (ephemeral test/cluster namespaces will be skipped)",
                BLUE,
            )
        )
    else:
        print(
            color(
                "Ephemeral namespaces: INCLUDED (all namespaces will be scanned)",
                YELLOW,
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

    # Check cluster connectivity and prompt user for confirmation
    all_connected, connectivity_report = check_all_clusters_connectivity(
        contexts, retries=retries, oc_timeout_seconds=oc_timeout_seconds
    )
    
    if not prompt_user_confirmation(connectivity_report):
        print(color("Aborted by user.", YELLOW))
        sys.exit(0)

    # Move existing output files to output directory (one-time migration)
    move_existing_output_files()
    
    # Ensure output directory exists
    output_dir = ensure_output_directory()
    
    results, skipped = run_batches(
        contexts,
        batch_size,
        retries,
        oc_timeout_seconds,
        ns_batch_size,
        ns_workers,
        time_range_seconds,
        exclude_ephemeral,
    )

    # All output files go to 'output' subdirectory
    json_path = output_dir / "oom_results.json"
    csv_path = output_dir / "oom_results.csv"
    table_path = output_dir / "oom_results.table"
    html_path = output_dir / "oom_results.html"
    
    # Backup existing files before generating new ones
    backup_output_files(json_path, csv_path, table_path, html_path)
    
    export_results(results, json_path, csv_path, table_path, html_path, time_range_str)

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
            "\nPer-cluster logs written to /private/tmp/<cluster>/ (if any findings were found)",
            GREEN,
        )
    )
    print(
        color(
            f"Output files written to '{output_dir}/' directory",
            GREEN,
        )
    )


if __name__ == "__main__":
    main()
