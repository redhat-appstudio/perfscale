#!/usr/bin/env python3
"""
Analyze resource consumption data and provide recommendations for resource limits.

Usage:
    # From piped input:
    ./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py

    # From YAML file (auto-runs data collection):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml
    ./analyze_resource_limits.py --file https://github.com/.../buildah.yaml

    # Update YAML file with recommendations:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --update
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock, Event, Thread
import time

try:
    import requests
    import yaml
except ImportError as e:
    print(f"Error: Missing required library. Install with: pip install requests pyyaml", file=sys.stderr)
    sys.exit(1)


def convert_github_url_to_raw(url):
    """Convert GitHub blob URL to raw content URL."""
    # Convert blob URL to raw URL
    # https://github.com/user/repo/blob/branch/path -> https://raw.githubusercontent.com/user/repo/branch/path
    pattern = r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)'
    match = re.match(pattern, url)
    if match:
        user, repo, branch, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
    return url


def fetch_yaml_content(file_path_or_url):
    """Fetch YAML content from file path or URL."""
    if file_path_or_url.startswith('http://') or file_path_or_url.startswith('https://'):
        url = convert_github_url_to_raw(file_path_or_url)
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return yaml.safe_load(response.text), url
        except requests.RequestException as e:
            print(f"Error fetching URL {url}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        path = Path(file_path_or_url)
        if not path.exists():
            print(f"Error: File not found: {file_path_or_url}", file=sys.stderr)
            sys.exit(1)
        with open(path, 'r') as f:
            return yaml.safe_load(f), str(path.absolute())


def extract_task_info(yaml_content):
    """Extract task name, step names, and current resource limits from Tekton Task YAML."""
    task_name = yaml_content.get('metadata', {}).get('name', '')
    steps = []
    current_resources = {}
    
    # Get default resources from stepTemplate
    step_template = yaml_content.get('spec', {}).get('stepTemplate', {})
    default_resources = step_template.get('computeResources', {})
    default_mem_req = default_resources.get('requests', {}).get('memory', '')
    default_cpu_req = default_resources.get('requests', {}).get('cpu', '')
    default_mem_lim = default_resources.get('limits', {}).get('memory', '')
    default_cpu_lim = default_resources.get('limits', {}).get('cpu', '')
    
    # Extract step names and current resources from spec.steps
    for step in yaml_content.get('spec', {}).get('steps', []):
        step_name = step.get('name', '')
        if step_name:
            steps.append(step_name)
            
            # Get current resources for this step (use defaults if not specified)
            step_resources = step.get('computeResources', {})
            step_req = step_resources.get('requests', {})
            step_lim = step_resources.get('limits', {})
            
            # Get values, using None if not set (to distinguish from empty string)
            mem_req = step_req.get('memory') if 'memory' in step_req else (default_mem_req if default_mem_req else None)
            cpu_req = step_req.get('cpu') if 'cpu' in step_req else (default_cpu_req if default_cpu_req else None)
            mem_lim = step_lim.get('memory') if 'memory' in step_lim else (default_mem_lim if default_mem_lim else None)
            cpu_lim = step_lim.get('cpu') if 'cpu' in step_lim else (default_cpu_lim if default_cpu_lim else None)
            
            current_resources[step_name] = {
                'requests': {
                    'memory': mem_req,
                    'cpu': cpu_req,
                },
                'limits': {
                    'memory': mem_lim,
                    'cpu': cpu_lim,
                }
            }
    
    return task_name, steps, default_resources, current_resources


def read_wrapper_config(wrapper_path):
    """Read TASK_NAME and STEPS from wrapper script.
    
    Returns:
        tuple: (task_name, steps_list, is_defined)
        - task_name: Task name if defined, None otherwise
        - steps_list: List of step names (without 'step-' prefix) if defined, None otherwise
        - is_defined: True if both TASK_NAME and STEPS are defined (not commented, non-empty)
    """
    task_name = None
    steps_list = None
    is_defined = False
    
    try:
        with open(wrapper_path, 'r') as f:
            lines = f.readlines()
        
        for line in lines:
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith('#'):
                continue
            
            # Check for TASK_NAME
            if stripped.startswith('TASK_NAME='):
                # Extract value between quotes
                match = re.search(r'TASK_NAME="([^"]*)"', line)
                if match:
                    task_name = match.group(1).strip()
            
            # Check for STEPS
            elif stripped.startswith('STEPS='):
                # Extract value between quotes
                match = re.search(r'STEPS="([^"]*)"', line)
                if match:
                    steps_str = match.group(1).strip()
                    if steps_str:
                        # Split by space and remove 'step-' prefix
                        steps_list = [
                            s.replace('step-', '') if s.startswith('step-') else s
                            for s in steps_str.split()
                        ]
        
        # Both must be defined and non-empty
        is_defined = (task_name is not None and task_name != '' and 
                     steps_list is not None and len(steps_list) > 0)
        
    except Exception as e:
        print(f"Warning: Could not read wrapper script: {e}", file=sys.stderr)
    
    return task_name, steps_list, is_defined


def validate_wrapper_steps(wrapper_task, wrapper_steps, yaml_task, yaml_steps):
    """Validate wrapper-defined task and steps against YAML file.
    
    Args:
        wrapper_task: Task name from wrapper script
        wrapper_steps: List of step names from wrapper (without 'step-' prefix)
        yaml_task: Task name from YAML file
        yaml_steps: List of step names from YAML file
    
    Returns:
        tuple: (is_valid, error_messages)
        - is_valid: True if validation passes
        - error_messages: List of error messages (empty if valid)
    """
    errors = []
    
    # Check task name match (case-sensitive)
    if wrapper_task != yaml_task:
        errors.append(
            f"Task name mismatch: wrapper has '{wrapper_task}', "
            f"YAML file has '{yaml_task}'"
        )
    
    # Convert to sets for comparison (normalize step names)
    wrapper_steps_set = set(wrapper_steps)
    yaml_steps_set = set(yaml_steps)
    
    # Check if wrapper steps are subset or equal to YAML steps
    extra_steps = wrapper_steps_set - yaml_steps_set
    if extra_steps:
        errors.append(
            f"Wrapper defines steps not found in YAML file: {sorted(extra_steps)}"
        )
    
    missing_steps = yaml_steps_set - wrapper_steps_set
    if missing_steps:
        # This is a warning, not an error (wrapper can be a subset)
        pass
    
    is_valid = len(errors) == 0
    return is_valid, errors


def check_cluster_connectivity(wrapper_path):
    """Check connectivity to all clusters defined in wrapper script.
    
    Returns:
        tuple: (all_connected, connectivity_report)
        - all_connected: True if all clusters are accessible
        - connectivity_report: List of (cluster_display_name, status, error_message) tuples
                              Note: cluster_display_name is the short name for display purposes
    """
    report = []
    all_connected = True
    
    try:
        with open(wrapper_path, 'r') as f:
            lines = f.readlines()
        
        # Extract CONTEXTS line (only non-commented lines, similar to read_wrapper_config)
        contexts_str = None
        for line in lines:
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith('#'):
                continue
            # Check for CONTEXTS (not commented)
            if stripped.startswith('CONTEXTS='):
                # Extract value between quotes
                match = re.search(r'CONTEXTS="([^"]*)"', line)
                if match:
                    contexts_str = match.group(1).strip()
                    break
        
        if not contexts_str:
            # No CONTEXTS found in non-commented lines, try to get contexts from kubectl as fallback
            result = subprocess.run(
                ['kubectl', 'config', 'get-contexts', '-o', 'name'],
                capture_output=True,
                text=True,
                timeout=120  # 2 minutes timeout for connectivity check
            )
            if result.returncode == 0:
                contexts = [c.strip() for c in result.stdout.strip().split('\n') if c.strip()]
            else:
                return False, [("unknown", False, "Could not get cluster contexts")]
        else:
            # Handle cases where CONTEXTS uses command substitution
            if '$(' in contexts_str:
                # Execute command substitution to get contexts
                # Extract the command inside $()
                cmd_match = re.search(r'\$\(([^)]+)\)', contexts_str)
                if cmd_match:
                    cmd = cmd_match.group(1).strip()
                    # Handle the specific case: kubectl config get-contexts -o name 2>/dev/null | xargs
                    if 'kubectl config get-contexts' in cmd:
                        result = subprocess.run(
                            ['kubectl', 'config', 'get-contexts', '-o', 'name'],
                            capture_output=True,
                            text=True,
                            timeout=120  # 2 minutes timeout for connectivity check
                        )
                        if result.returncode == 0:
                            contexts = [c.strip() for c in result.stdout.strip().split('\n') if c.strip()]
                        else:
                            # Fallback to default if command fails (extract from echo)
                            fallback_match = re.search(r'echo\s+[\'"]([^\'"]+)[\'"]', contexts_str)
                            if fallback_match:
                                contexts = [fallback_match.group(1).strip()]
                            else:
                                return False, [("unknown", False, "Could not execute CONTEXTS command")]
                    else:
                        return False, [("unknown", False, f"Unsupported CONTEXTS command: {cmd}")]
                else:
                    return False, [("unknown", False, "Invalid CONTEXTS command substitution")]
            else:
                # Simple string value - split by space
                contexts = [c.strip() for c in contexts_str.split() if c.strip()]
        
        # Test connectivity to each cluster
        for ctx in contexts:
            if not ctx:
                continue
            try:
                result = subprocess.run(
                    ['kubectl', 'config', 'use-context', ctx],
                    capture_output=True,
                    text=True,
                    timeout=120  # 2 minutes timeout for connectivity check
                )
                if result.returncode == 0:
                    # Try a simple kubectl command to verify connectivity
                    test_result = subprocess.run(
                        ['kubectl', 'get', 'namespaces', '--request-timeout=2m'],
                        capture_output=True,
                        text=True,
                        timeout=120  # 2 minutes timeout for connectivity check
                    )
                    if test_result.returncode == 0:
                        # Use display name for report, but ctx (full context) is still used for operations
                        display_name = get_cluster_display_name(ctx)
                        report.append((display_name, True, "Connected"))
                    else:
                        display_name = get_cluster_display_name(ctx)
                        report.append((display_name, False, f"Cannot access cluster: {test_result.stderr[:100]}"))
                        all_connected = False
                else:
                    display_name = get_cluster_display_name(ctx)
                    report.append((display_name, False, f"Cannot switch context: {result.stderr[:100]}"))
                    all_connected = False
            except subprocess.TimeoutExpired:
                display_name = get_cluster_display_name(ctx)
                report.append((display_name, False, "Connection timeout"))
                all_connected = False
            except Exception as e:
                display_name = get_cluster_display_name(ctx)
                report.append((display_name, False, f"Error: {str(e)[:100]}"))
                all_connected = False
    
    except Exception as e:
        return False, [("unknown", False, f"Error reading wrapper script: {str(e)}")]
    
    return all_connected, report


def prompt_confirmation(task_name, steps, source="extracted from YAML"):
    """Prompt user for confirmation before proceeding.
    
    Args:
        task_name: Task name to confirm
        steps: List of step names to confirm
        source: Source of the values (for display)
    
    Returns:
        bool: True if user confirms, False otherwise
    """
    print("\n" + "="*80, file=sys.stderr)
    print(f"CONFIRMATION: Task and Steps Configuration", file=sys.stderr)
    print("="*80, file=sys.stderr)
    print(f"Source: {source}", file=sys.stderr)
    print(f"Task Name: {task_name}", file=sys.stderr)
    print(f"Steps ({len(steps)}):", file=sys.stderr)
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}", file=sys.stderr)
    print("="*80, file=sys.stderr)
    
    while True:
        # CRITICAL: Flush stderr before prompting to ensure any previous output is visible
        sys.stderr.flush()
        sys.stdout.flush()
        response = input("Proceed with these values? [y/N]: ").strip().lower()
        if response in ('y', 'yes'):
            # Print newline after confirmation to ensure clean separation
            print("", file=sys.stderr)
            sys.stderr.flush()
            return True
        elif response in ('n', 'no', ''):
            return False
        else:
            print("Please enter 'y' or 'n'", file=sys.stderr)


def extract_cluster_list(wrapper_path):
    """Extract list of clusters from wrapper script.
    
    Returns:
        list: List of cluster context names
    """
    try:
        with open(wrapper_path, 'r') as f:
            lines = f.readlines()
        
        # Extract CONTEXTS line (only non-commented lines, same logic as check_cluster_connectivity)
        contexts_str = None
        for line in lines:
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith('#'):
                continue
            # Check for CONTEXTS (not commented)
            if stripped.startswith('CONTEXTS='):
                # Extract value between quotes
                match = re.search(r'CONTEXTS="([^"]*)"', line)
                if match:
                    contexts_str = match.group(1).strip()
                    break
        
        if not contexts_str:
            # Try to get contexts from kubectl
            result = subprocess.run(
                ['kubectl', 'config', 'get-contexts', '-o', 'name'],
                capture_output=True,
                text=True,
                timeout=120  # 2 minutes timeout for connectivity check
            )
            if result.returncode == 0:
                contexts = [c.strip() for c in result.stdout.strip().split('\n') if c.strip()]
            else:
                return []
        else:
            # Handle cases where CONTEXTS uses command substitution
            if '$(' in contexts_str:
                # Execute command substitution
                cmd_match = re.search(r'\$\(([^)]+)\)', contexts_str)
                if cmd_match:
                    cmd = cmd_match.group(1).strip()
                    if 'kubectl config get-contexts' in cmd:
                        result = subprocess.run(
                            ['kubectl', 'config', 'get-contexts', '-o', 'name'],
                            capture_output=True,
                            text=True,
                            timeout=120  # 2 minutes timeout for connectivity check
                        )
                        if result.returncode == 0:
                            contexts = [c.strip() for c in result.stdout.strip().split('\n') if c.strip()]
                        else:
                            # Fallback to default if command fails
                            fallback_match = re.search(r'echo\s+[\'"]([^\'"]+)[\'"]', contexts_str)
                            if fallback_match:
                                contexts = [fallback_match.group(1).strip()]
                            else:
                                return []
                    else:
                        return []
                else:
                    return []
            else:
                # Simple string value - split by space
                contexts = [c.strip() for c in contexts_str.split() if c.strip()]
        
        # Remove duplicates while preserving order
        # CRITICAL: Use dict.fromkeys() for guaranteed deduplication
        # This is more efficient and ensures no duplicates slip through
        unique_contexts = list(dict.fromkeys(ctx.strip() for ctx in contexts if ctx and ctx.strip()))
        
        return unique_contexts
    except Exception as e:
        print(f"Warning: Could not extract cluster list: {e}", file=sys.stderr)
        return []


def get_cluster_display_name(cluster_ctx):
    """Extract short cluster display name from full context string.
    
    This function extracts a user-friendly short name for display purposes only.
    The full context string should still be used for all cluster operations.
    
    Example: 
        Input:  'default/api-stone-prd-rh01-pg1f-p1-openshiftapps-com:6443/smodak'
        Output: 'stone-prd-rh01'
    
    Uses same regex logic as wrapper_for_promql.sh: s#.*/api-([^-]+-[^-]+-[^-]+).*#\1#
    
    Args:
        cluster_ctx: Full cluster context string (e.g., 'default/api-stone-prd-rh01-...')
    
    Returns:
        str: Short cluster display name (e.g., 'stone-prd-rh01')
    """
    # Use same regex as wrapper_for_promql.sh: s#.*/api-([^-]+-[^-]+-[^-]+).*#\1#
    match = re.search(r'/api-([^-]+-[^-]+-[^-]+)', cluster_ctx)
    if match:
        return match.group(1)
    # Fallback: try to extract from context name
    if '/' in cluster_ctx:
        parts = cluster_ctx.split('/')
        if len(parts) > 1:
            # Try to extract from parts
            for part in parts:
                if 'api-' in part:
                    match = re.search(r'api-([^-]+-[^-]+-[^-]+)', part)
                    if match:
                        return match.group(1)
    # Last resort: return last part after /
    return cluster_ctx.split('/')[-1] if '/' in cluster_ctx else cluster_ctx


def _spinner_thread(stop_event, progress_data=None, progress_lock=None, total_clusters=0):
    """Display a spinning wheel with percentage progress while processing clusters.
    
    Args:
        stop_event: threading.Event to signal when to stop spinning
        progress_data: dict with 'completed' list (optional, for percentage calculation)
        progress_lock: threading.Lock for thread-safe access to progress_data (optional)
        total_clusters: total number of clusters being processed (for percentage calculation)
    """
    spinner_chars = ['|', '/', '-', '\\']
    idx = 0
    
    while not stop_event.is_set():
        # Calculate percentage if we have progress data
        percentage = 0
        if progress_data and total_clusters > 0:
            if progress_lock:
                with progress_lock:
                    completed_count = len(progress_data.get('completed', []))
            else:
                completed_count = len(progress_data.get('completed', []))
            percentage = int((completed_count / total_clusters) * 100)
        
        # Build message with percentage and spinner
        if percentage > 0:
            message = f"Getting information from all clusters: (Completed No. of clusters: {percentage}%) {spinner_chars[idx % len(spinner_chars)]}"
        else:
            message = f"Getting information from all clusters: {spinner_chars[idx % len(spinner_chars)]}"
        
        # Print spinner on the same line, overwriting previous output
        print(f"\r{message}", end='', file=sys.stderr)
        sys.stderr.flush()
        idx += 1
        time.sleep(0.2)  # Update spinner every 200ms
    
    # Clear the spinner line when done
    print("\r\033[K", end='', file=sys.stderr)  # Clear the entire line
    sys.stderr.flush()


def process_single_cluster(cluster_ctx, temp_wrapper_path, days, steps_str, task_name):
    """Process a single cluster and return CSV data.
    
    Args:
        cluster_ctx: Cluster context name
        temp_wrapper_path: Path to temporary wrapper script
        days: Number of days
        steps_str: Space-separated step names (with 'step-' prefix)
        task_name: Task name being processed
        progress_lock: Lock for thread-safe progress updates
        progress_lines: Dict to track progress lines {cluster_index: line_content}
        cluster_index: Index of this cluster (0-based) for progress line tracking
        total_clusters: Total number of clusters being processed
    
    Returns:
        tuple: (cluster_ctx, csv_data, error_message)
    """
    try:
        # Extract cluster display name (for user-facing messages only)
        cluster_name = get_cluster_display_name(cluster_ctx)
        
        # Create cluster-specific wrapper that only processes this cluster
        cluster_wrapper = temp_wrapper_path.parent / f'.temp_wrapper_{cluster_ctx.replace("/", "_").replace(":", "_")}.sh'
        with open(temp_wrapper_path, 'r') as f:
            wrapper_content = f.read()
        
        # Replace CONTEXTS to only include this cluster
        wrapper_content = re.sub(
            r'CONTEXTS="[^"]*"',
            f'CONTEXTS="{cluster_ctx}"',
            wrapper_content
        )
        
        # CRITICAL FIX for parallel execution:
        # When running in parallel, multiple processes calling 'kubectl config use-context'
        # will interfere with each other because they modify the shared ~/.kube/config file.
        # Solution: Use a lock file or ensure context is set atomically, OR better:
        # Set KUBECONFIG to a process-specific location or use --context flag.
        # Since oc/kubectl commands read from current context, we need to ensure
        # each parallel process has its own context isolation.
        # 
        # For now, we'll add a small delay and ensure context is set right before use
        # But better: modify to use kubectl --context flag in all commands
        # Actually, wrapper_for_promql.sh uses 'oc config current-context' which reads
        # from the shared config, so we need to ensure context is set correctly.
        #
        # Best approach: Set context right before the loop and add a lock mechanism
        # OR use process-specific kubeconfig files
        #
        # CRITICAL FIX: When running in parallel, multiple processes calling 'kubectl config use-context'
        # interfere because they modify the shared ~/.kube/config file.
        # Solution: Create a process-specific kubeconfig file for this cluster
        
        # Create a temporary kubeconfig file for this process
        original_kubeconfig = os.environ.get('KUBECONFIG', os.path.expanduser('~/.kube/config'))
        temp_kubeconfig = temp_wrapper_path.parent / f'.kubeconfig_{cluster_ctx.replace("/", "_").replace(":", "_")}'
        
        # Copy the original kubeconfig to temp location
        if os.path.exists(original_kubeconfig):
            shutil.copy2(original_kubeconfig, temp_kubeconfig)
        else:
            # Create empty kubeconfig if it doesn't exist
            temp_kubeconfig.parent.mkdir(parents=True, exist_ok=True)
            temp_kubeconfig.touch()
        
        # Modify wrapper to use the temp kubeconfig and set context once at start
        wrapper_content = re.sub(
            r'for ctx in \$\{CONTEXTS\}; do',
            f'export KUBECONFIG="{temp_kubeconfig}"\nfor ctx in ${{CONTEXTS}}; do',
            wrapper_content
        )
        
        # Set context once before the loop (since we only have one cluster)
        wrapper_content = re.sub(
            r'kubectl config use-context "\$\{ctx\}" >/dev/null 2>&1 \|\| continue',
            'kubectl config use-context "${ctx}" >/dev/null 2>&1 || continue',
            wrapper_content
        )
        
        with open(cluster_wrapper, 'w') as f:
            f.write(wrapper_content)
        os.chmod(cluster_wrapper, 0o755)
        
        # Run the wrapper for this cluster (it processes all steps internally)
        # Set KUBECONFIG environment variable to use the process-specific config
        env = os.environ.copy()
        env['KUBECONFIG'] = str(temp_kubeconfig)
        
        result = subprocess.run(
            [str(cluster_wrapper), str(days), '--raw'],
            capture_output=True,
            text=True,
            cwd=temp_wrapper_path.parent,
            env=env,
            timeout=10800  # 3 hours timeout per cluster
        )
        
        # Clean up temporary kubeconfig
        if temp_kubeconfig.exists():
            temp_kubeconfig.unlink()
        
        # Clean up cluster-specific wrapper
        if cluster_wrapper.exists():
            cluster_wrapper.unlink()
        
        if result.returncode != 0:
            error_msg = result.stderr[:200] if result.stderr else f"Exit code: {result.returncode}"
            return (cluster_ctx, None, error_msg)
        
        # Return CSV data (skip header if present, we'll add it once)
        csv_lines = result.stdout.strip().split('\n')
        # Filter out header line and empty lines
        data_lines = [line for line in csv_lines if line.strip() and not line.strip().startswith('"cluster"')]
        
        # Debug: check if we got any data
        if not data_lines:
            # Check if stdout has any content at all
            if result.stdout.strip():
                # Has content but no data lines - check if it's just the header
                header_line = '"cluster", "task", "step", "pod_max_mem", "namespace_max_mem", "component_max_mem", "application_max_mem", "mem_max_mb", "mem_p95_mb", "mem_p90_mb", "mem_median_mb", "pod_max_cpu", "namespace_max_cpu", "component_max_cpu", "application_max_cpu", "cpu_max", "cpu_p95", "cpu_p90", "cpu_median"'
                stdout_clean = result.stdout.strip()
                if stdout_clean == header_line or stdout_clean == header_line + '\n' or stdout_clean == header_line + '\r\n':
                    return (cluster_ctx, None, "No data rows (only CSV header - no pods found)")
                else:
                    return (cluster_ctx, None, f"No data rows in CSV output")
            else:
                return (cluster_ctx, None, "Empty CSV output from wrapper script")
        
        return (cluster_ctx, '\n'.join(data_lines), None)
    
    except subprocess.TimeoutExpired:
        return (cluster_ctx, None, "Timeout after 3 hours")
    except Exception as e:
        return (cluster_ctx, None, str(e)[:200])


def run_data_collection(task_name, steps, days=7, show_table=True, dry_run=False, use_wrapper_values=False, parallel_clusters=None):
    """Run the wrapper script to collect data.
    
    Args:
        task_name: Task name to use
        steps: List of step names (without 'step-' prefix)
        days: Number of days for data collection
        show_table: Whether to show table output
        dry_run: If True, don't actually run data collection
        use_wrapper_values: If True, use wrapper's TASK_NAME/STEPS (don't replace)
        parallel_clusters: If set to N, process clusters in parallel with N workers
    """
    script_path = Path(__file__).parent / 'wrapper_for_promql_for_all_clusters.sh'
    if not script_path.exists():
        print(f"Error: wrapper script not found: {script_path}", file=sys.stderr)
        sys.exit(1)
    
    if dry_run:
        print("DRY-RUN: Would collect data for:", file=sys.stderr)
        print(f"  Task: {task_name}", file=sys.stderr)
        print(f"  Steps: {', '.join(steps)}", file=sys.stderr)
        print(f"  Days: {days}", file=sys.stderr)
        return None
    
    # Create a temporary wrapper that sets TASK_NAME and STEPS
    temp_wrapper = script_path.parent / '.temp_wrapper.sh'
    try:
        with open(script_path, 'r') as f:
            wrapper_content = f.read()
        
        if not use_wrapper_values:
            # Replace hardcoded TASK_NAME and STEPS (including commented lines)
            # Steps in CSV are "step-{name}", so ensure we use that format
            # Match TASK_NAME="..." even if commented out (we'll uncomment if needed)
            task_name_pattern = re.compile(
                r'^(\s*)(#?\s*)TASK_NAME="[^"]*"',
                flags=re.MULTILINE
            )
            if task_name_pattern.search(wrapper_content):
                wrapper_content = task_name_pattern.sub(
                    r'\1TASK_NAME="' + task_name + '"',
                    wrapper_content
                )
            else:
                # TASK_NAME not found, add it after the LAST_DAYS line or at the top
                wrapper_content = re.sub(
                    r'^(LAST_DAYS="[^"]*")',
                    r'\1\nTASK_NAME="' + task_name + '"',
                    wrapper_content,
                    flags=re.MULTILINE
                )
            
            # Convert step names to "step-{name}" format for the wrapper
            steps_str = ' '.join(f'step-{s}' if not s.startswith('step-') else s for s in steps)
            steps_pattern = re.compile(
                r'^(\s*)(#?\s*)STEPS="[^"]*"',
                flags=re.MULTILINE
            )
            if steps_pattern.search(wrapper_content):
                wrapper_content = steps_pattern.sub(
                    r'\1STEPS="' + steps_str + '"',
                    wrapper_content
                )
            else:
                # STEPS not found, add it after TASK_NAME
                wrapper_content = re.sub(
                    r'^(TASK_NAME="[^"]*")',
                    r'\1\nSTEPS="' + steps_str + '"',
                    wrapper_content,
                    flags=re.MULTILINE
                )
        
        with open(temp_wrapper, 'w') as f:
            f.write(wrapper_content)
        os.chmod(temp_wrapper, 0o755)
        
        # Verify the temp wrapper has correct TASK_NAME and STEPS (only in debug mode)
        # Skip this check in normal mode to avoid garbled output during parallel execution
        # The warnings can interfere with progress display when running in parallel
        
        # Check if parallel processing is requested
        if parallel_clusters and parallel_clusters > 0:
            # Extract cluster list
            clusters_raw = extract_cluster_list(script_path)
            if not clusters_raw:
                print("Warning: Could not extract cluster list, falling back to serial processing", file=sys.stderr)
                parallel_clusters = None
            else:
                # CRITICAL: Deduplicate IMMEDIATELY after extraction, before any other operations
                # Use dict.fromkeys() which preserves order and removes duplicates in one pass
                # Also normalize whitespace and filter empty strings
                clusters_normalized = [c.strip() for c in clusters_raw if c and c.strip()]
                clusters = list(dict.fromkeys(clusters_normalized))  # Remove duplicates, preserve order
                num_clusters = len(clusters)  # Set num_clusters IMMEDIATELY after deduplication
                
                # CRITICAL: Final verification - ensure we have exactly num_clusters unique clusters
                # This is a safety check to catch any edge cases
                unique_set = set(clusters)
                if len(unique_set) != num_clusters:
                    # Force re-deduplication if there's a mismatch
                    clusters = list(dict.fromkeys(clusters))
                    num_clusters = len(clusters)
                
                # CRITICAL: At this point, clusters MUST have exactly num_clusters unique items
                # If not, something is seriously wrong - log and fix
                assert len(clusters) == num_clusters, f"Cluster count mismatch: {len(clusters)} != {num_clusters}"
                assert len(set(clusters)) == num_clusters, f"Duplicate clusters found: {len(set(clusters))} != {num_clusters}"
                
                # Process clusters in parallel with progress spinner
                steps_str = ' '.join(f'step-{s}' if not s.startswith('step-') else s for s in steps)
                all_results = []
                errors = []
                
                # Track progress for spinner
                progress_data = {'completed': []}
                progress_lock = Lock()
                total_clusters = len(clusters)
                
                # Start spinner thread with progress tracking
                spinner_stop = Event()
                spinner_thread_obj = Thread(target=_spinner_thread, args=(spinner_stop, progress_data, progress_lock, total_clusters), daemon=True)
                spinner_thread_obj.start()
                
                try:
                    with ThreadPoolExecutor(max_workers=parallel_clusters) as executor:
                        # Submit all cluster processing tasks
                        futures = {}
                        for cluster in clusters:
                            future = executor.submit(
                                process_single_cluster,
                                cluster,
                                temp_wrapper,
                                days,
                                steps_str,
                                task_name
                            )
                            futures[future] = cluster
                        
                        # Collect results as they complete
                        for future in as_completed(futures):
                            cluster_ctx, csv_data, error = future.result()
                            cluster_name = get_cluster_display_name(cluster_ctx)
                            
                            # Update progress: mark cluster as completed
                            with progress_lock:
                                if cluster_name not in progress_data['completed']:
                                    progress_data['completed'].append(cluster_name)
                            
                            if error:
                                errors.append((cluster_name, error))
                            elif csv_data and csv_data.strip():
                                all_results.append(csv_data)
                            else:
                                # No error but also no data
                                errors.append((cluster_name, "Wrapper completed but returned no CSV data"))
                finally:
                    # Stop spinner and wait for it to finish
                    spinner_stop.set()
                    spinner_thread_obj.join(timeout=1.0)  # Wait up to 1 second for spinner to stop
                
                # Print final progress message and separator before summary
                print("="*80, file=sys.stderr)
                print("Getting information from all clusters: (Completed No. of clusters: 100%)", file=sys.stderr)
                print("="*80, file=sys.stderr)
                
                # Print summary of results on clean lines (no interference from progress lines)
                print("Data Collection Summary", file=sys.stderr)
                print("="*80, file=sys.stderr)
                
                successful_clusters = len(all_results)
                failed_clusters = len(errors)
                total_clusters = num_clusters  # Use num_clusters (after deduplication) instead of len(clusters)
                
                print(f"Total clusters processed: {total_clusters}", file=sys.stderr)
                print(f"Successful: {successful_clusters}", file=sys.stderr)
                if failed_clusters > 0:
                    print(f"Failed: {failed_clusters}", file=sys.stderr)
                print("="*80, file=sys.stderr)
                
                # Report any errors with clean formatting
                if errors:
                    print(f"\nWarning: {len(errors)} cluster(s) had issues:", file=sys.stderr)
                    for cluster, error in errors:
                        # Truncate long error messages for cleaner output
                        error_display = error[:150] + "..." if len(error) > 150 else error
                        print(f"  • {cluster}: {error_display}", file=sys.stderr)
                    print("\nNote: Cluster failures typically occur when:", file=sys.stderr)
                    print("  - No pods found for the specified task/steps in the given time period", file=sys.stderr)
                    print("  - Task/step names don't match what's actually running in that cluster", file=sys.stderr)
                    print("  - Time period is too short (try increasing --days)", file=sys.stderr)
                    print(file=sys.stderr)
                
                # Debug: show what we collected (only if successful)
                if all_results:
                    total_lines = sum(len(r.split('\n')) for r in all_results)
                    print(f"Collected data from {len(all_results)} cluster(s), total CSV lines: {total_lines}", file=sys.stderr)
                    print(file=sys.stderr)
                
                if not all_results:
                    print("\nError: No data collected from any cluster", file=sys.stderr)
                    print("\nThis usually means:", file=sys.stderr)
                    print("  1. No pods found for the specified task/steps in the given time period", file=sys.stderr)
                    print("  2. Task/step names don't match what's actually running in clusters", file=sys.stderr)
                    print("  3. Time period too short (try increasing --days)", file=sys.stderr)
                    print(file=sys.stderr)
                    sys.exit(1)
                
                # Combine all CSV results (add header once)
                csv_header = '"cluster", "task", "step", "pod_max_mem", "namespace_max_mem", "component_max_mem", "application_max_mem", "mem_max_mb", "mem_p95_mb", "mem_p90_mb", "mem_median_mb", "pod_max_cpu", "namespace_max_cpu", "component_max_cpu", "application_max_cpu", "cpu_max", "cpu_p95", "cpu_p90", "cpu_median"'
                combined_csv = csv_header + '\n' + '\n'.join(all_results)
                
                # If show_table, format and display as CSV (with single space after comma)
                if show_table:
                    # Convert CSV to formatted output with single space after comma
                    # Use csv module to properly parse quoted fields
                    import io
                    csv_reader = csv.reader(io.StringIO(combined_csv))
                    for row in csv_reader:
                        if row:  # Skip empty rows
                            # Strip whitespace from each field and join with comma + single space
                            cleaned_row = [field.strip() for field in row]
                            formatted_line = ', '.join(cleaned_row)
                            print(formatted_line)
                    print(file=sys.stderr)
                
                return combined_csv
        
        # Serial processing (default or fallback) - now matches parallel mode behavior
        if not parallel_clusters:
            # Extract cluster list (same as parallel mode)
            clusters_raw = extract_cluster_list(script_path)
            if not clusters_raw:
                print("Warning: Could not extract cluster list, falling back to simple wrapper execution", file=sys.stderr)
                # Fallback to old behavior if cluster extraction fails
                if show_table:
                    result = subprocess.run(
                        [str(temp_wrapper), str(days), '--table'],
                        capture_output=True,
                        text=True,
                        cwd=script_path.parent
                    )
                    if result.stdout:
                        print(result.stdout)
                    if result.stderr:
                        print(result.stderr, file=sys.stderr)
                    if result.returncode != 0:
                        print(f"Warning: Wrapper script exited with code {result.returncode}", file=sys.stderr)
                    
                    result_csv = subprocess.run(
                        [str(temp_wrapper), str(days), '--raw'],
                        capture_output=True,
                        text=True,
                        cwd=script_path.parent
                    )
                    
                    if result_csv.returncode != 0:
                        print(f"Error running wrapper script (CSV collection):", file=sys.stderr)
                        print(f"Exit code: {result_csv.returncode}", file=sys.stderr)
                        if result_csv.stderr:
                            print("STDERR:", file=sys.stderr)
                            print(result_csv.stderr, file=sys.stderr)
                        sys.exit(1)
                    
                    if not result_csv.stdout or not result_csv.stdout.strip():
                        print("Error: Wrapper script produced no CSV output", file=sys.stderr)
                        sys.exit(1)
                    
                    print(file=sys.stderr)
                    return result_csv.stdout
                else:
                    result = subprocess.run(
                        [str(temp_wrapper), str(days), '--csv'],
                        capture_output=True,
                        text=True,
                        cwd=script_path.parent
                    )
                    
                    if result.returncode != 0:
                        print(f"Error running wrapper script:", file=sys.stderr)
                        print(result.stderr, file=sys.stderr)
                        sys.exit(1)
                    
                    return result.stdout
            else:
                # Normalize and deduplicate clusters (same as parallel mode)
                clusters_normalized = [c.strip() for c in clusters_raw if c and c.strip()]
                clusters = list(dict.fromkeys(clusters_normalized))
                num_clusters = len(clusters)
                
                # Process clusters serially with progress indicator
                steps_str = ' '.join(f'step-{s}' if not s.startswith('step-') else s for s in steps)
                all_results = []
                errors = []
                
                # Track progress for spinner
                progress_data = {'completed': []}
                progress_lock = Lock()
                total_clusters = len(clusters)
                
                # Start spinner thread with progress tracking
                spinner_stop = Event()
                spinner_thread_obj = Thread(target=_spinner_thread, args=(spinner_stop, progress_data, progress_lock, total_clusters), daemon=True)
                spinner_thread_obj.start()
                
                try:
                    # Process clusters one by one
                    for cluster in clusters:
                        cluster_ctx, csv_data, error = process_single_cluster(
                            cluster,
                            temp_wrapper,
                            days,
                            steps_str,
                            task_name
                        )
                        cluster_name = get_cluster_display_name(cluster_ctx)
                        
                        # Update progress: mark cluster as completed
                        with progress_lock:
                            if cluster_name not in progress_data['completed']:
                                progress_data['completed'].append(cluster_name)
                        
                        if error:
                            errors.append((cluster_name, error))
                        elif csv_data and csv_data.strip():
                            all_results.append(csv_data)
                        else:
                            # No error but also no data
                            errors.append((cluster_name, "Wrapper completed but returned no CSV data"))
                finally:
                    # Stop spinner and wait for it to finish
                    spinner_stop.set()
                    spinner_thread_obj.join(timeout=1.0)
                
                # Print final progress message and separator before summary
                print("="*80, file=sys.stderr)
                print("Getting information from all clusters: (Completed No. of clusters: 100%)", file=sys.stderr)
                print("="*80, file=sys.stderr)
                
                # Print summary of results on clean lines (matching parallel mode)
                print("Data Collection Summary", file=sys.stderr)
                print("="*80, file=sys.stderr)
                
                successful_clusters = len(all_results)
                failed_clusters = len(errors)
                total_clusters = num_clusters
                
                print(f"Total clusters processed: {total_clusters}", file=sys.stderr)
                print(f"Successful: {successful_clusters}", file=sys.stderr)
                if failed_clusters > 0:
                    print(f"Failed: {failed_clusters}", file=sys.stderr)
                print("="*80, file=sys.stderr)
                
                # Report any errors with clean formatting (matching parallel mode)
                if errors:
                    print(f"\nWarning: {len(errors)} cluster(s) had issues:", file=sys.stderr)
                    for cluster, error in errors:
                        # Truncate long error messages for cleaner output
                        error_display = error[:150] + "..." if len(error) > 150 else error
                        print(f"  • {cluster}: {error_display}", file=sys.stderr)
                    print("\nNote: Cluster failures typically occur when:", file=sys.stderr)
                    print("  - No pods found for the specified task/steps in the given time period", file=sys.stderr)
                    print("  - Task/step names don't match what's actually running in that cluster", file=sys.stderr)
                    print("  - Time period is too short (try increasing --days)", file=sys.stderr)
                    print(file=sys.stderr)
                
                # Debug: show what we collected (only if successful)
                if all_results:
                    total_lines = sum(len(r.split('\n')) for r in all_results)
                    print(f"Collected data from {len(all_results)} cluster(s), total CSV lines: {total_lines}", file=sys.stderr)
                    print(file=sys.stderr)
                
                if not all_results:
                    print("\nError: No data collected from any cluster", file=sys.stderr)
                    print("\nThis usually means:", file=sys.stderr)
                    print("  1. No pods found for the specified task/steps in the given time period", file=sys.stderr)
                    print("  2. Task/step names don't match what's actually running in clusters", file=sys.stderr)
                    print("  3. Time period too short (try increasing --days)", file=sys.stderr)
                    print(file=sys.stderr)
                    sys.exit(1)
                
                # Combine all CSV results (add header once)
                csv_header = '"cluster", "task", "step", "pod_max_mem", "namespace_max_mem", "component_max_mem", "application_max_mem", "mem_max_mb", "mem_p95_mb", "mem_p90_mb", "mem_median_mb", "pod_max_cpu", "namespace_max_cpu", "component_max_cpu", "application_max_cpu", "cpu_max", "cpu_p95", "cpu_p90", "cpu_median"'
                combined_csv = csv_header + '\n' + '\n'.join(all_results)
                
                # If show_table, format and display as CSV (with single space after comma)
                if show_table:
                    # Convert CSV to formatted output with single space after comma
                    # Use csv module to properly parse quoted fields
                    import io
                    csv_reader = csv.reader(io.StringIO(combined_csv))
                    for row in csv_reader:
                        if row:  # Skip empty rows
                            # Strip whitespace from each field and join with comma + single space
                            cleaned_row = [field.strip() for field in row]
                            formatted_line = ', '.join(cleaned_row)
                            print(formatted_line)
                    print(file=sys.stderr)
                
                return combined_csv
    finally:
        if temp_wrapper.exists():
            temp_wrapper.unlink()


def parse_csv_data(csv_text):
    """Parse CSV data from the wrapper script output."""
    data = []
    lines = [line for line in csv_text.strip().split('\n') if line.strip()]
    if not lines:
        return data
    
    # Check if we only have header line (no data rows)
    if len(lines) <= 1:
        return data
    
    reader = csv.DictReader(lines)
    for row in reader:
        # Clean up keys (remove spaces and quotes)
        cleaned_row = {k.strip().strip('"'): v.strip().strip('"') for k, v in row.items()}
        data.append(cleaned_row)
    return data


def round_memory_to_standard(mb):
    """Round memory to standard Kubernetes values.
    
    For values < 1Gi: round to increments of 256Mi (256Mi, 512Mi, 768Mi)
    For values >= 1Gi: round to whole Gi values (1Gi, 2Gi, 3Gi, etc.)
    
    Minimum value: 256Mi (to account for sparse monitoring data)
    Rounds to nearest, but ensures we don't go below the recommended value.
    """
    mb = float(mb)
    
    # Enforce minimum of 256Mi
    MIN_MEMORY_MB = 256
    
    if mb < MIN_MEMORY_MB:
        mb = MIN_MEMORY_MB
    
    if mb < 1024:
        # Round UP to next highest increment of 256Mi
        # Using +255 ensures we always round up (e.g., 300Mi -> 512Mi, not 256Mi)
        rounded = ((int(mb) + 255) // 256) * 256
        
        # Ensure minimum
        if rounded < MIN_MEMORY_MB:
            rounded = MIN_MEMORY_MB
        
        return rounded
    else:
        # Round to nearest whole Gi, but round up if fractional
        gi = mb / 1024.0
        rounded_gi = round(gi)
        # If rounding down would go below original, round up instead
        if rounded_gi * 1024 < mb:
            rounded_gi += 1
        return rounded_gi * 1024


def mb_to_kubernetes(mb):
    """Convert MB to Kubernetes memory format with standard rounding."""
    mb = float(mb)
    rounded_mb = round_memory_to_standard(mb)
    
    if rounded_mb < 1024:
        return f"{int(rounded_mb)}Mi"
    else:
        gi = rounded_mb / 1024.0
        # Should always be whole number after rounding
        return f"{int(gi)}Gi"


def round_cpu_to_standard(cores):
    """Round CPU to standard Kubernetes values.
    
    Rounds UP to next highest increment of 100m (100m, 200m, 300m, etc.)
    Minimum value: 100m (to account for sparse monitoring data)
    Always rounds up to ensure we don't go below the recommended value.
    """
    cores = float(cores)
    millicores = cores * 1000
    
    # Enforce minimum of 100m
    MIN_CPU_MILLICORES = 100
    
    if millicores < MIN_CPU_MILLICORES:
        millicores = MIN_CPU_MILLICORES
    
    # Round UP to next highest increment of 100m
    # Using +99 ensures we always round up (e.g., 243m -> 300m, not 200m)
    rounded_m = ((int(millicores) + 99) // 100) * 100
    
    # Ensure minimum
    if rounded_m < MIN_CPU_MILLICORES:
        rounded_m = MIN_CPU_MILLICORES
    
    return rounded_m / 1000.0


def cores_to_kubernetes(cores):
    """Convert cores to Kubernetes CPU format, always in millicores."""
    cores = float(cores)
    rounded_cores = round_cpu_to_standard(cores)
    
    # Always return as millicores
    millicores = int(rounded_cores * 1000)
    return f"{millicores}m"


def parse_cpu_value(cpu_str):
    """Parse CPU value from format like '3569m' or '4.5'."""
    if not cpu_str or cpu_str == '0m' or cpu_str == '0':
        return 0.0
    if cpu_str.endswith('m'):
        return float(cpu_str[:-1]) / 1000.0
    return float(cpu_str)


def analyze_step_data(step_name, step_rows, margin_pct=10, base='max'):
    """Analyze data for a specific step and return recommendations.
    
    Args:
        step_name: Name of the step
        step_rows: List of data rows for this step
        margin_pct: Safety margin percentage to add
        base: Base metric to use ('max', 'p95', 'p90', 'median')
    """
    if not step_rows:
        return None
    
    # Extract memory values
    mem_max_values = [
        float(r['mem_max_mb']) for r in step_rows
        if r.get('mem_max_mb') and r['mem_max_mb'] not in ('0', '', 'N/A')
    ]
    mem_p95_values = [
        float(r['mem_p95_mb']) for r in step_rows
        if r.get('mem_p95_mb') and r['mem_p95_mb'] not in ('0', '', 'N/A')
    ]
    mem_p90_values = [
        float(r['mem_p90_mb']) for r in step_rows
        if r.get('mem_p90_mb') and r['mem_p90_mb'] not in ('0', '', 'N/A')
    ]
    mem_median_values = [
        float(r['mem_median_mb']) for r in step_rows
        if r.get('mem_median_mb') and r['mem_median_mb'] not in ('0', '', 'N/A')
    ]
    
    # Extract CPU values
    cpu_max_values = [parse_cpu_value(r.get('cpu_max', '0m')) for r in step_rows if r.get('cpu_max')]
    cpu_p95_values = [parse_cpu_value(r.get('cpu_p95', '0m')) for r in step_rows if r.get('cpu_p95')]
    cpu_p90_values = [parse_cpu_value(r.get('cpu_p90', '0m')) for r in step_rows if r.get('cpu_p90')]
    cpu_median_values = [parse_cpu_value(r.get('cpu_median', '0m')) for r in step_rows if r.get('cpu_median')]
    
    if not mem_max_values:
        return None
    
    # Calculate max across all clusters
    mem_max_max = max(mem_max_values)
    mem_p95_max = max(mem_p95_values) if mem_p95_values else 0
    mem_p90_max = max(mem_p90_values) if mem_p90_values else 0
    mem_median_max = max(mem_median_values) if mem_median_values else 0
    
    cpu_max_max = max(cpu_max_values) if cpu_max_values else 0
    cpu_p95_max = max(cpu_p95_values) if cpu_p95_values else 0
    cpu_p90_max = max(cpu_p90_values) if cpu_p90_values else 0
    cpu_median_max = max(cpu_median_values) if cpu_median_values else 0
    
    # Select base value based on user choice
    if base == 'max':
        mem_base = mem_max_max
        cpu_base = cpu_max_max
        base_label = 'Max'
    elif base == 'p95':
        mem_base = mem_p95_max if mem_p95_max > 0 else mem_max_max
        cpu_base = cpu_p95_max if cpu_p95_max > 0 else cpu_max_max
        base_label = 'P95'
    elif base == 'p90':
        mem_base = mem_p90_max if mem_p90_max > 0 else mem_max_max
        cpu_base = cpu_p90_max if cpu_p90_max > 0 else cpu_max_max
        base_label = 'P90'
    elif base == 'median':
        mem_base = mem_median_max if mem_median_max > 0 else mem_max_max
        cpu_base = cpu_median_max if cpu_median_max > 0 else cpu_max_max
        base_label = 'Median'
    else:
        # Default to max if invalid option
        mem_base = mem_max_max
        cpu_base = cpu_max_max
        base_label = 'Max'
    
    # Calculate recommendations: base + margin, but don't exceed max observed
    mem_recommended = min(mem_max_max, int(mem_base * (1 + margin_pct / 100))) if mem_base > 0 else mem_max_max
    if cpu_base > 0:
        cpu_recommended = min(cpu_max_max * 1.1, cpu_base * (1 + margin_pct / 100))
    else:
        cpu_recommended = cpu_max_max if cpu_max_max > 0 else 0
    
    # Count coverage
    mem_coverage = len([x for x in mem_max_values if x <= mem_recommended])
    cpu_coverage = len([x for x in cpu_max_values if x <= cpu_recommended]) if cpu_max_values else 0
    
    return {
        'step_name': step_name,
        'mem_recommended_mb': mem_recommended,
        'mem_recommended_k8s': mb_to_kubernetes(mem_recommended),
        'cpu_recommended_cores': cpu_recommended,
        'cpu_recommended_k8s': cores_to_kubernetes(cpu_recommended),
        'mem_max_max': mem_max_max,
        'mem_p95_max': mem_p95_max,
        'mem_p90_max': mem_p90_max,
        'mem_median_max': mem_median_max,
        'mem_base': mem_base,
        'cpu_max_max': cpu_max_max,
        'cpu_p95_max': cpu_p95_max,
        'cpu_p90_max': cpu_p90_max,
        'cpu_median_max': cpu_median_max,
        'cpu_base': cpu_base,
        'base_label': base_label,
        'mem_coverage': mem_coverage,
        'mem_total': len(mem_max_values),
        'cpu_coverage': cpu_coverage,
        'cpu_total': len(cpu_max_values) if cpu_max_values else 0,
    }


def print_comparison_table(recommendations, current_resources=None):
    """Print comparison table of current vs proposed resource limits."""
    if not recommendations:
        return
    
    print("=" * 100)
    print("RESOURCE LIMITS COMPARISON: CURRENT vs PROPOSED")
    print("=" * 100)
    print()
    
    # Table header with reduced spacing
    print(
        f"{'Step':<15} {'Current Requests':<20} {'Proposed Requests':<20} "
        f"{'Current Limits':<20} {'Proposed Limits':<20}"
    )
    print("-" * 100)
    
    for rec in recommendations:
        if rec is None:
            continue
        
        step_name = rec['step_name']
        # Convert step-build to build for matching
        step_name_yaml = step_name.replace('step-', '') if step_name.startswith('step-') else step_name
        proposed_mem = rec['mem_recommended_k8s']
        proposed_cpu = rec['cpu_recommended_k8s']
        
        # Get current values
        if current_resources and step_name_yaml in current_resources:
            curr = current_resources[step_name_yaml]
            curr_mem_req = curr['requests'].get('memory') or 'null'
            curr_cpu_req = curr['requests'].get('cpu') or 'null'
            curr_mem_lim = curr['limits'].get('memory') or 'null'
            curr_cpu_lim = curr['limits'].get('cpu') or 'null'
        else:
            curr_mem_req = 'N/A'
            curr_cpu_req = 'N/A'
            curr_mem_lim = 'N/A'
            curr_cpu_lim = 'N/A'
        
        # Format values (memory / cpu)
        curr_req = f"{curr_mem_req} / {curr_cpu_req}"
        curr_lim = f"{curr_mem_lim} / {curr_cpu_lim}"
        prop_req = f"{proposed_mem} / {proposed_cpu}"
        prop_lim = f"{proposed_mem} / {proposed_cpu}"
        
        print(f"{step_name_yaml:<15} {curr_req:<20} {prop_req:<20} {curr_lim:<20} {prop_lim:<20}")
    
    print()


def print_analysis(recommendations, margin_pct, base='max', current_resources=None):
    """Print analysis results."""
    base_label = recommendations[0]['base_label'] if recommendations and recommendations[0] else base.upper()
    print("=" * 80)
    print(f"RESOURCE LIMIT RECOMMENDATIONS ({base_label} + {margin_pct}% Safety Margin)")
    print("=" * 80)
    print()
    
    for rec in recommendations:
        if rec is None:
            continue
        
        print(f"Step: {rec['step_name']}")
        print("-" * 80)
        print(f"  Memory: {rec['mem_recommended_k8s']}")
        if rec['base_label'] == 'Max':
            print(f"    - Base ({rec['base_label']}): {mb_to_kubernetes(rec['mem_base'])}")
        else:
            print(f"    - Base ({rec['base_label']}): {mb_to_kubernetes(rec['mem_base'])}")
            print(f"    - Max observed: {mb_to_kubernetes(rec['mem_max_max'])}")
        print(f"    - Coverage: {rec['mem_coverage']}/{rec['mem_total']} clusters")
        print()
        
        if rec['cpu_total'] > 0:
            print(f"  CPU: {rec['cpu_recommended_k8s']}")
            if rec['base_label'] == 'Max':
                print(f"    - Base ({rec['base_label']}): {cores_to_kubernetes(rec['cpu_base'])}")
            else:
                print(f"    - Base ({rec['base_label']}): {cores_to_kubernetes(rec['cpu_base'])}")
                print(f"    - Max observed: {cores_to_kubernetes(rec['cpu_max_max'])}")
            print(f"    - Coverage: {rec['cpu_coverage']}/{rec['cpu_total']} clusters")
        else:
            print(f"  CPU: No data available")
        print()
    
    # Print comparison table if current resources are available
    if current_resources:
        print()
        print_comparison_table(recommendations, current_resources)


def get_cache_file_path(task_name):
    """Generate cache file path based on task name."""
    script_dir = Path(__file__).parent
    cache_dir = script_dir / '.analyze_cache'
    cache_dir.mkdir(exist_ok=True)
    
    # Use task name as cache filename (sanitize for filesystem)
    # Replace any characters that might be problematic in filenames
    safe_task_name = re.sub(r'[^a-zA-Z0-9_-]', '_', task_name)
    return cache_dir / f"{safe_task_name}.json"


def save_recommendations_cache(task_name, file_path_or_url, recommendations, margin_pct, base, days):
    """Save recommendations to cache file based on task name."""
    cache_file = get_cache_file_path(task_name)
    
    cache_data = {
        'task_name': task_name,
        'file_path_or_url': file_path_or_url,
        'timestamp': datetime.now().isoformat(),
        'margin_pct': margin_pct,
        'base': base,
        'days': days,
        'recommendations': recommendations
    }
    
    with open(cache_file, 'w') as f:
        json.dump(cache_data, f, indent=2)
    
    print(f"Cached recommendations for task '{task_name}' to: {cache_file}", file=sys.stderr)


def load_recommendations_cache(task_name):
    """Load recommendations from cache file based on task name."""
    cache_file = get_cache_file_path(task_name)
    
    if not cache_file.exists():
        return None
    
    try:
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)
        
        # Verify it matches the requested task
        cached_task_name = cache_data.get('task_name')
        if cached_task_name == task_name:
            return cache_data
        else:
            print(f"Warning: Cache file exists but for different task '{cached_task_name}'. Ignoring cache.", file=sys.stderr)
            return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: Failed to load cache file: {e}", file=sys.stderr)
        return None


def generate_diff_patch(original_yaml, updated_yaml, file_path_or_url):
    """Generate a diff/patch file for remote YAML files."""
    script_dir = Path(__file__).parent
    
    # Create temporary files
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as orig_file:
        yaml.dump(original_yaml, orig_file, default_flow_style=False, sort_keys=False, width=120)
        orig_path = orig_file.name
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as upd_file:
        yaml.dump(updated_yaml, upd_file, default_flow_style=False, sort_keys=False, width=120)
        upd_path = upd_file.name
    
    try:
        # Generate diff using diff command
        result = subprocess.run(
            ['diff', '-u', orig_path, upd_path],
            capture_output=True,
            text=True
        )
        
        # Extract filename from URL for patch file naming
        url_parts = file_path_or_url.split('/')
        filename = url_parts[-1] if url_parts else 'buildah.yaml'
        # Remove .yaml extension and add timestamp
        base_name = filename.replace('.yaml', '').replace('.yml', '')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        patch_file = script_dir / f"{base_name}_{timestamp}.patch"
        
        # Extract relative path from URL for patch file context
        # e.g., "task/buildah/0.7/buildah.yaml" from the full URL
        relative_path = None
        if '/task/' in file_path_or_url:
            idx = file_path_or_url.find('/task/')
            relative_path = file_path_or_url[idx+1:] if idx >= 0 else filename
        
        # Write patch file with header
        with open(patch_file, 'w') as f:
            f.write(f"# Resource limits patch for: {file_path_or_url}\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n")
            f.write(f"# File: {relative_path or filename}\n")
            f.write("#\n")
            f.write("# To apply this patch:\n")
            f.write("#   1. Download the original file from the URL above\n")
            f.write("#   2. Apply: patch <original_file> < {}\n".format(patch_file.name))
            f.write("#   3. Or manually apply the changes shown below\n")
            f.write("#\n")
            # Replace temp file paths in diff with relative path
            diff_output = result.stdout
            if relative_path:
                # Replace the temp file paths with the relative path
                lines = diff_output.split('\n')
                if len(lines) >= 2:
                    f.write(f"--- a/{relative_path}\n")
                    f.write(f"+++ b/{relative_path}\n")
                    # Skip the first two lines (temp file paths) and write the rest
                    f.write('\n'.join(lines[2:]))
                else:
                    f.write(diff_output)
            else:
                f.write(diff_output)
        
        print(f"\nGenerated patch file: {patch_file}", file=sys.stderr)
        print(f"Apply with: patch -p1 < {patch_file.name}", file=sys.stderr)
        
        return patch_file
    finally:
        # Clean up temp files
        os.unlink(orig_path)
        os.unlink(upd_path)


def update_yaml_file(yaml_path, recommendations, original_yaml, file_path_or_url=None, debug=False):
    """Update YAML file with recommended resource limits, preserving original formatting."""
    updated = False
    
    # Build a map of step names to their resource recommendations
    step_updates = {}
    for rec in recommendations:
        if rec is None:
            continue
        
        step_name_csv = rec['step_name']  # e.g., "step-build"
        mem_k8s = rec['mem_recommended_k8s']
        cpu_k8s = rec['cpu_recommended_k8s']
        
        # Convert "step-build" to "build" for YAML matching
        step_name_yaml = step_name_csv.replace('step-', '') if step_name_csv.startswith('step-') else step_name_csv
        step_updates[step_name_yaml] = {'memory': mem_k8s, 'cpu': cpu_k8s}
    
    if not step_updates:
        return False
    
    if debug:
        print(f"DEBUG: Looking for steps: {list(step_updates.keys())}", file=sys.stderr)
    
    # If remote URL, use the old method (generate patch)
    if file_path_or_url and (file_path_or_url.startswith('http://') or file_path_or_url.startswith('https://')):
        updated_yaml = yaml.safe_load(yaml.dump(original_yaml))  # Deep copy
        
        for step_name_yaml, resources in step_updates.items():
            steps = updated_yaml.get('spec', {}).get('steps', [])
            for step in steps:
                if step.get('name') == step_name_yaml:
                    if 'computeResources' not in step:
                        step['computeResources'] = {}
                    if 'limits' not in step['computeResources']:
                        step['computeResources']['limits'] = {}
                    if 'requests' not in step['computeResources']:
                        step['computeResources']['requests'] = {}
                    
                    step['computeResources']['limits']['memory'] = resources['memory']
                    step['computeResources']['limits']['cpu'] = resources['cpu']
                    step['computeResources']['requests']['memory'] = resources['memory']
                    step['computeResources']['requests']['cpu'] = resources['cpu']
                    updated = True
                    print(f"Updated step '{step_name_yaml}': memory={resources['memory']}, cpu={resources['cpu']}", file=sys.stderr)
                    break
        
        if updated:
            generate_diff_patch(original_yaml, updated_yaml, file_path_or_url)
        return updated
    
    # Note: debug parameter is not used for remote URLs (patch generation)
    
    # For local files, update by modifying text directly to preserve formatting
    if yaml_path and not yaml_path.startswith('http'):
        # Read the file as text
        with open(yaml_path, 'r') as f:
            lines = f.readlines()
        
        # First pass: collect all step positions (process in reverse to avoid index shifting)
        step_positions = []
        i = 0
        in_steps_section = False
        while i < len(lines):
            line = lines[i]
            
            # Track if we're in the steps section
            if re.match(r'^\s+steps:\s*$', line):
                in_steps_section = True
                if debug:
                    print(f"DEBUG: Entered steps section at line {i+1}", file=sys.stderr)
            elif in_steps_section:
                line_indent = len(line) - len(line.lstrip())
                if line_indent <= 2 and line.strip() and not line.strip().startswith('-') and not line.strip().startswith('#'):
                    if 'workspaces:' in line or 'results:' in line or 'volumes:' in line:
                        in_steps_section = False
                        if debug:
                            print(f"DEBUG: Left steps section at line {i+1}: {line.strip()}", file=sys.stderr)
            
            # Look for step names
            if in_steps_section:
                line_indent = len(line) - len(line.lstrip())
                is_step = False
                step_name_match = None
                
                # Pattern 1: "    - name: stepname" (4 spaces: 2 for steps:, 2 for list item)
                if re.match(r'^\s+-\s+name:\s+', line) and line_indent == 4:
                    is_step = True
                    step_name_match = re.search(r'name:\s+(.+)$', line)
                # Pattern 2: "    name: stepname" (not starting with "-")
                elif re.match(r'^\s+name:\s+', line) and line_indent == 4 and not line.strip().startswith('-'):
                    for lookback in range(1, min(11, i + 1)):
                        prev_line = lines[i - lookback]
                        prev_stripped = prev_line.lstrip()
                        prev_indent = len(prev_line) - len(prev_stripped)
                        if not prev_stripped:
                            continue
                        if prev_indent == 4 and prev_stripped.endswith(':'):
                            is_step = False
                            break
                        if prev_indent == 2 and prev_stripped.startswith('-'):
                            if 'image:' in prev_line or prev_stripped.startswith('- image:') or prev_stripped.startswith('- name:'):
                                is_step = True
                                break
                        if prev_indent <= 2 and (prev_stripped.startswith('steps:') or 
                                                 (prev_stripped and not prev_stripped.startswith('-') and ':' in prev_stripped)):
                            break
                    if is_step:
                        step_name_match = re.search(r'name:\s+(.+)$', line)
                
                if step_name_match and is_step:
                    step_name = step_name_match.group(1).strip()
                    if step_name in step_updates:
                        step_positions.append((i, step_name))
            
            i += 1
        
        # Second pass: process steps in reverse order (bottom to top) to avoid index shifting
        step_positions.sort(reverse=True)  # Process from bottom to top
        
        # Process each step
        for step_line_idx, step_name in step_positions:
            # Find the current line number for this step (may have shifted due to previous insertions)
            # Search more broadly - start from a reasonable position and search forward/backward
            current_step_line = None
            
            # Calculate approximate shift: each step processed before this one (in reverse order) may have added ~7 lines
            steps_before = sum(1 for idx, name in step_positions if idx > step_line_idx)
            estimated_shift = steps_before * 7  # Approximate lines added per step
            search_center = step_line_idx + estimated_shift
            
            # Search in a wider range
            search_start = max(0, search_center - 50)
            search_end = min(len(lines), search_center + 100)
            
            # First try near the estimated position
            for search_idx in range(search_start, search_end):
                if search_idx < len(lines):
                    search_line = lines[search_idx]
                    line_indent = len(search_line) - len(search_line.lstrip())
                    # Check Pattern 1: "    - name: stepname" (4 spaces: 2 for steps:, 2 for list item)
                    if re.match(r'^\s+-\s+name:\s+', search_line) and line_indent == 4:
                        name_match = re.search(r'name:\s+(.+)$', search_line)
                        if name_match and name_match.group(1).strip() == step_name:
                            current_step_line = search_idx
                            break
                    # Check Pattern 2: "    name: stepname" (not starting with "-")
                    elif re.match(r'^\s+name:\s+', search_line) and line_indent == 4 and not search_line.strip().startswith('-'):
                        # Verify it's a step name by checking previous lines
                        is_step_name = False
                        for lookback in range(1, min(6, search_idx + 1)):
                            prev_line = lines[search_idx - lookback]
                            prev_stripped = prev_line.lstrip()
                            prev_indent = len(prev_line) - len(prev_stripped)
                            if prev_indent == 2 and prev_stripped.startswith('-') and 'image:' in prev_line:
                                is_step_name = True
                                break
                            if prev_indent <= 2:
                                break
                        if is_step_name:
                            name_match = re.search(r'name:\s+(.+)$', search_line)
                            if name_match and name_match.group(1).strip() == step_name:
                                current_step_line = search_idx
                                break
            
            # If not found, do a broader search through the entire steps section
            if current_step_line is None:
                # Find steps: line first
                steps_section_start = None
                for idx, line in enumerate(lines):
                    if re.match(r'^\s+steps:\s*$', line):
                        steps_section_start = idx
                        break
                
                if steps_section_start is not None:
                    # Search from steps: to end of file (or next top-level section)
                    for search_idx in range(steps_section_start + 1, len(lines)):
                        search_line = lines[search_idx]
                        line_indent = len(search_line) - len(search_line.lstrip())
                        # Stop if we've left steps section
                        if line_indent <= 2 and search_line.strip() and not search_line.strip().startswith('-') and not search_line.strip().startswith('#'):
                            if 'workspaces:' in search_line or 'results:' in search_line or 'volumes:' in search_line:
                                break
                        
                        # Check for step name (4 spaces: 2 for steps:, 2 for list item)
                        if re.match(r'^\s+-\s+name:\s+', search_line) and line_indent == 4:
                            name_match = re.search(r'name:\s+(.+)$', search_line)
                            if name_match and name_match.group(1).strip() == step_name:
                                current_step_line = search_idx
                                break
                        elif re.match(r'^\s+name:\s+', search_line) and line_indent == 4 and not search_line.strip().startswith('-'):
                            name_match = re.search(r'name:\s+(.+)$', search_line)
                            if name_match and name_match.group(1).strip() == step_name:
                                # Verify it's a step
                                for lookback in range(1, min(6, search_idx + 1)):
                                    prev_line = lines[search_idx - lookback]
                                    if len(prev_line) - len(prev_line.lstrip()) == 2 and prev_line.lstrip().startswith('-') and 'image:' in prev_line:
                                        current_step_line = search_idx
                                        break
                                if current_step_line is not None:
                                    break
            
            if current_step_line is None:
                if debug:
                    print(f"WARNING: Could not find step '{step_name}' (original line {step_line_idx+1}), skipping", file=sys.stderr)
                continue
            
            line = lines[current_step_line]
            if debug:
                print(f"DEBUG: Processing step '{step_name}' at line {current_step_line+1}", file=sys.stderr)
            
            # Check if this step needs updating
            if step_name in step_updates:
                resources = step_updates[step_name]
                if debug:
                    print(f"DEBUG: Processing step '{step_name}', looking for computeResources...", file=sys.stderr)
                
                # Look ahead for computeResources section within this step
                j = current_step_line + 1
                step_line = lines[current_step_line]
                step_indent = len(step_line) - len(step_line.lstrip())
                
                # Find the actual indent for step content fields (like image:, computeResources:)
                # Step content should be indented 4 spaces (same as image:, env:, etc.)
                step_content_indent = step_indent + 2  # Default: 2 spaces more than step name
                # Look for image: or other step fields to determine correct indent
                for k in range(current_step_line, min(current_step_line + 10, len(lines))):
                    check_line = lines[k]
                    check_indent = len(check_line) - len(check_line.lstrip())
                    if 'image:' in check_line or 'env:' in check_line or 'script:' in check_line:
                        step_content_indent = check_indent
                        break
                
                in_compute_resources = False
                in_limits = False
                in_requests = False
                limits_indent = None
                requests_indent = None
                memory_updated_limits = False
                cpu_updated_limits = False
                memory_updated_requests = False
                cpu_updated_requests = False
                compute_resources_found = False
                compute_resources_insert_pos = None
                compute_resources_indent = None  # Will be set when we find computeResources
                
                while j < len(lines):
                    next_line = lines[j]
                    next_stripped = next_line.lstrip()
                    next_indent = len(next_line) - len(next_stripped)
                    
                    # Stop if we've moved to the next step (same or less indent with "- name:" or "name:")
                    if next_indent <= step_indent and (next_stripped.startswith('- name:') or next_stripped.startswith('name:')):
                        break
                    
                    # Stop if we've moved to a different top-level section
                    if next_indent < step_indent:
                        break
                    
                    # Find computeResources section (at same indent level as step content fields like image:)
                    # Allow some flexibility in indent to handle formatting variations
                    if 'computeResources:' in next_line and (next_indent == step_content_indent or (step_content_indent <= 4 and next_indent >= 2 and next_indent <= 6)):
                        in_compute_resources = True
                        compute_resources_found = True
                        compute_resources_indent = next_indent  # Store the actual indent of computeResources
                        if debug:
                            print(f"DEBUG: Found computeResources at line {j+1}, indent={next_indent} (step_content_indent={step_content_indent})", file=sys.stderr)
                        j += 1
                        continue
                    
                    # Track where to insert computeResources if not found (after name: and image:, before other fields)
                    if not compute_resources_found and compute_resources_insert_pos is None:
                        # Stop if we've left the step (hit next step marker or back to step list level)
                        if next_indent <= step_indent:
                            # We've left the step, insert before leaving (at the end of current step)
                            # Find the last field of current step by going back
                            for k in range(j - 1, max(current_step_line, j - 20), -1):
                                if k < len(lines):
                                    check_line = lines[k]
                                    check_indent = len(check_line) - len(check_line.lstrip())
                                    if check_indent == step_content_indent and check_line.strip() and not check_line.strip().startswith('#'):
                                        compute_resources_insert_pos = k + 1
                                        break
                            if compute_resources_insert_pos is None:
                                # Fallback: insert right before leaving the step
                                compute_resources_insert_pos = j
                            break
                        # Insert after name: or image: (whichever comes last)
                        elif 'image:' in next_line or 'name:' in next_line:
                            compute_resources_insert_pos = j + 1
                        elif next_stripped and not next_stripped.startswith('#') and next_indent == step_content_indent:
                            # We've hit another field at step content level, insert before it
                            compute_resources_insert_pos = j
                            break
                    
                    if not in_compute_resources:
                        j += 1
                        continue
                    
                    # Stop if we've left computeResources section (back to step level or next step)
                    # computeResources is at step_indent, so if we go back to step_indent or less, we've left it
                    if next_indent <= step_indent:
                        # But make sure we're not still in computeResources (could be empty line or comment)
                        if next_stripped and not next_stripped.startswith('#') and 'computeResources' not in next_line:
                            break
                            
                    # Find limits section (should be indented 2 spaces more than computeResources)
                    # Use compute_resources_indent if available, otherwise fall back to step_indent + 2
                    expected_limits_indent = compute_resources_indent + 2 if compute_resources_indent is not None else step_indent + 2
                    if 'limits:' in next_line and next_indent == expected_limits_indent:
                        in_limits = True
                        in_requests = False
                        limits_indent = next_indent
                        if debug:
                            print(f"DEBUG: Found limits at line {j+1}, indent={next_indent}", file=sys.stderr)
                        j += 1
                        continue
                    
                    # Find requests section (should be indented 2 spaces more than computeResources)
                    # Use compute_resources_indent if available, otherwise fall back to step_indent + 2
                    expected_requests_indent = compute_resources_indent + 2 if compute_resources_indent is not None else step_indent + 2
                    if 'requests:' in next_line and next_indent == expected_requests_indent:
                        # Before leaving limits section, add missing fields
                        if in_limits:
                            if not cpu_updated_limits:
                                indent = ' ' * (limits_indent + 2)
                                lines.insert(j, f"{indent}cpu: {resources['cpu']}\n")
                                cpu_updated_limits = True
                                updated = True
                                if debug:
                                    print(f"DEBUG: Added cpu to limits at line {j+1}: {resources['cpu']}", file=sys.stderr)
                                j += 1
                        
                        in_requests = True
                        in_limits = False
                        requests_indent = next_indent
                        if debug:
                            print(f"DEBUG: Found requests at line {j+1}, indent={next_indent}", file=sys.stderr)
                        j += 1
                        continue
                    
                    # Update memory and cpu values
                    if in_limits:
                        # Update memory in limits (should be indented 2 spaces more than limits:)
                        if re.match(r'^\s+memory:\s+', next_line) and next_indent == limits_indent + 2:
                            indent = next_line[:len(next_line) - len(next_line.lstrip())]
                            lines[j] = f"{indent}memory: {resources['memory']}\n"
                            memory_updated_limits = True
                            updated = True
                            if debug:
                                print(f"DEBUG: Updated memory in limits at line {j+1}: {resources['memory']}", file=sys.stderr)
                        
                        # Update cpu in limits (should be indented 2 spaces more than limits:)
                        elif re.match(r'^\s+cpu:\s+', next_line) and next_indent == limits_indent + 2:
                            indent = next_line[:len(next_line) - len(next_line.lstrip())]
                            lines[j] = f"{indent}cpu: {resources['cpu']}\n"
                            cpu_updated_limits = True
                            updated = True
                            if debug:
                                print(f"DEBUG: Updated cpu in limits at line {j+1}: {resources['cpu']}", file=sys.stderr)
                        
                        # Check if we need to add missing fields (after limits: but before requests:)
                        elif limits_indent is not None and next_indent > limits_indent:
                            # Still in limits section, check if we need to add fields
                            if not memory_updated_limits and ('requests:' in next_line or next_indent <= limits_indent):
                                # Insert memory before leaving limits section
                                indent = ' ' * (limits_indent + 2)
                                lines.insert(j, f"{indent}memory: {resources['memory']}\n")
                                memory_updated_limits = True
                                updated = True
                                j += 1
                            elif not cpu_updated_limits and ('requests:' in next_line or next_indent <= limits_indent):
                                # Insert cpu before leaving limits section
                                indent = ' ' * (limits_indent + 2)
                                lines.insert(j, f"{indent}cpu: {resources['cpu']}\n")
                                cpu_updated_limits = True
                                updated = True
                                j += 1
                    
                    elif in_requests:
                        # Update memory in requests (should be indented 2 spaces more than requests:)
                        if re.match(r'^\s+memory:\s+', next_line) and next_indent == requests_indent + 2:
                            indent = next_line[:len(next_line) - len(next_line.lstrip())]
                            lines[j] = f"{indent}memory: {resources['memory']}\n"
                            memory_updated_requests = True
                            updated = True
                            if debug:
                                print(f"DEBUG: Updated memory in requests at line {j+1}: {resources['memory']}", file=sys.stderr)
                        
                        # Update cpu in requests (should be indented 2 spaces more than requests:)
                        elif re.match(r'^\s+cpu:\s+', next_line) and next_indent == requests_indent + 2:
                            indent = next_line[:len(next_line) - len(next_line.lstrip())]
                            lines[j] = f"{indent}cpu: {resources['cpu']}\n"
                            cpu_updated_requests = True
                            updated = True
                            if debug:
                                print(f"DEBUG: Updated cpu in requests at line {j+1}: {resources['cpu']}", file=sys.stderr)
                    
                    # Stop if we've left computeResources section (back to step level)
                    if in_compute_resources and next_indent <= step_indent:
                        # But make sure we're not still in computeResources (could be empty line or comment)
                        if next_stripped and not next_stripped.startswith('#') and 'computeResources' not in next_line:
                            # Add missing fields before leaving
                            if in_limits:
                                if not cpu_updated_limits:
                                    indent = ' ' * (limits_indent + 2) if limits_indent else ' ' * (step_content_indent + 2)
                                    lines.insert(j, f"{indent}cpu: {resources['cpu']}\n")
                                    cpu_updated_limits = True
                                    updated = True
                                    if debug:
                                        print(f"DEBUG: Added cpu to limits before leaving computeResources: {resources['cpu']}", file=sys.stderr)
                                    j += 1
                            if in_requests:
                                if not memory_updated_requests:
                                    indent = ' ' * (requests_indent + 2) if requests_indent else ' ' * (step_content_indent + 2)
                                    lines.insert(j, f"{indent}memory: {resources['memory']}\n")
                                    memory_updated_requests = True
                                    updated = True
                                    if debug:
                                        print(f"DEBUG: Added memory to requests before leaving computeResources: {resources['memory']}", file=sys.stderr)
                                    j += 1
                                if not cpu_updated_requests:
                                    indent = ' ' * (requests_indent + 2) if requests_indent else ' ' * (step_content_indent + 2)
                                    lines.insert(j, f"{indent}cpu: {resources['cpu']}\n")
                                    cpu_updated_requests = True
                                    updated = True
                                    if debug:
                                        print(f"DEBUG: Added cpu to requests before leaving computeResources: {resources['cpu']}", file=sys.stderr)
                                    j += 1
                            break
                    
                    j += 1
                
                # If computeResources wasn't found, create it
                if not compute_resources_found and compute_resources_insert_pos is not None:
                    indent = ' ' * step_content_indent
                    compute_resources_block = [
                        f"{indent}computeResources:\n",
                        f"{indent}  limits:\n",
                        f"{indent}    memory: {resources['memory']}\n",
                        f"{indent}    cpu: {resources['cpu']}\n",
                        f"{indent}  requests:\n",
                        f"{indent}    memory: {resources['memory']}\n",
                        f"{indent}    cpu: {resources['cpu']}\n"
                    ]
                    # Insert the block
                    for idx, new_line in enumerate(compute_resources_block):
                        lines.insert(compute_resources_insert_pos + idx, new_line)
                    updated = True
                    if debug:
                        print(f"DEBUG: Created computeResources for step '{step_name}'", file=sys.stderr)
                
                if updated:
                    print(f"Updated step '{step_name}': memory={resources['memory']}, cpu={resources['cpu']}", file=sys.stderr)
        
        # Write back the modified file
        if updated:
            with open(yaml_path, 'w') as f:
                f.writelines(lines)
            print(f"\nUpdated YAML file: {yaml_path}", file=sys.stderr)
        
        return updated
    
    return False


def main():
    parser = argparse.ArgumentParser(
        description='Analyze resource consumption and provide recommendations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Basic Usage:
    # From piped input:
    ./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py

    # From YAML file (auto-runs data collection):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml
    ./analyze_resource_limits.py --file https://github.com/.../buildah.yaml

  Base Metric Options (default: max):
    # Using Max (most conservative):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --base max

    # Using P95 (recommended for production):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --base p95

    # Using P90 (more aggressive):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --base p90

    # Using Median (most aggressive):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --base median

  Margin and Time Range:
    # Custom safety margin (default: 10%):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --margin 5
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --margin 20

    # Custom data collection period (default: 7 days):
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --days 1
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --days 10
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --days 30

    # Combine options:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --base p95 --margin 15 --days 10

  Parallel Processing:
    # Process multiple clusters concurrently:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 3
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 5

    # Combine with other options:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --pll-clusters 3 --days 10 --base p95 --margin 5

  Update Workflows:
    # Update YAML file with recommendations:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --update

    # Update using cached recommendations (from previous run):
    ./analyze_resource_limits.py --update

    # Update local file using cache (no re-analysis):
    ./analyze_resource_limits.py --update --file /path/to/local/buildah.yaml

    # Force re-analysis and update local file:
    ./analyze_resource_limits.py --update --file /path/to/local/buildah.yaml --analyze-again

    # Update with specific parameters:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --update --margin 10 --base max --days 7

  Debug and Validation:
    # Enable debug output:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --debug
    ./analyze_resource_limits.py --update --file /path/to/buildah.yaml --debug

    # Dry-run: Validate task/steps and check cluster connectivity:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --dry-run
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --dry-run --debug

  Complete Examples:
    # Full-featured analysis:
    ./analyze_resource_limits.py --file /path/to/buildah.yaml --base p95 --margin 15 --days 10 --pll-clusters 4 --debug

    # Two-step workflow (recommended):
    ./analyze_resource_limits.py --file https://github.com/.../buildah.yaml --base p95 --margin 10 --days 7
    ./analyze_resource_limits.py --update
        """
    )
    parser.add_argument(
        '--file',
        help='YAML file path or GitHub URL to analyze (auto-runs data collection)'
    )
    parser.add_argument(
        '--update',
        action='store_true',
        help='Update the YAML file with recommended resource limits. If --file is not provided, uses cached recommendations from the last run. If --file <local_file> is provided, loads from cache (or runs analysis if no cache exists).'
    )
    parser.add_argument(
        '--analyze-again',
        '--aa',
        dest='analyze_again',
        action='store_true',
        help='Force re-analysis even when cache exists (only used with --update --file)'
    )
    parser.add_argument(
        '--margin',
        type=int,
        default=10,
        help='Safety margin percentage (default: 10)'
    )
    parser.add_argument(
        '--base',
        type=str,
        choices=['max', 'p95', 'p90', 'median'],
        default='max',
        help='Base metric for margin calculation: max, p95, p90, or median (default: max)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days for data collection (default: 7)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug output showing detailed processing information'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate task/steps and check cluster connectivity without running data collection'
    )
    parser.add_argument(
        '--pll-clusters',
        type=int,
        metavar='N',
        help='Enable parallel processing across clusters with N workers (only during analysis, ignored during --update)'
    )
    
    args = parser.parse_args()
    
    # Handle --update with --file <local_file> (load from cache, update specified local file)
    if args.update and args.file:
        # Check if it's a local file (not a URL)
        if not (args.file.startswith('http://') or args.file.startswith('https://')):
            # Load the local file YAML to get task_name
            yaml_content, yaml_path = fetch_yaml_content(args.file)
            task_name, file_steps, _, current_resources = extract_task_info(yaml_content)
            
            if not task_name:
                print("Error: Could not extract task name from YAML file", file=sys.stderr)
                sys.exit(1)
            
            # Load cache for this task_name
            cache_data = load_recommendations_cache(task_name)
            
            # If cache exists and user didn't request re-analysis, use cache
            if cache_data and not args.analyze_again:
                original_file_path_or_url = cache_data.get('file_path_or_url', 'unknown')
                recommendations = cache_data['recommendations']
                margin_pct = cache_data.get('margin_pct', 10)
                base = cache_data.get('base', 'max')
                
                print(f"Loading recommendations from cache for task '{task_name}'", file=sys.stderr)
                print(f"Cache info: original_file={original_file_path_or_url}, margin={margin_pct}%, base={base}", file=sys.stderr)
                print(f"Updating local file: {args.file}", file=sys.stderr)
                print()
                
                # Validate that cached recommendations match the file being updated
                cache_step_names = set()
                for rec in recommendations:
                    if rec is None:
                        continue
                    step_name_csv = rec['step_name']
                    step_name_yaml = step_name_csv.replace('step-', '') if step_name_csv.startswith('step-') else step_name_csv
                    cache_step_names.add(step_name_yaml)
                
                file_step_names = set(file_steps)
                
                # Check for mismatches
                cache_only_steps = cache_step_names - file_step_names
                file_only_steps = file_step_names - cache_step_names
                
                if cache_only_steps:
                    print(f"Warning: Cache contains steps not found in file: {sorted(cache_only_steps)}", file=sys.stderr)
                    print(f"  These steps will be skipped during update.", file=sys.stderr)
                
                if file_only_steps:
                    print(f"Warning: File contains steps not found in cache: {sorted(file_only_steps)}", file=sys.stderr)
                    print(f"  These steps will not be updated. Consider running analysis on this file.", file=sys.stderr)
                
                if not cache_step_names & file_step_names:
                    print("Error: No matching steps found between cache and file.", file=sys.stderr)
                    print(f"  Cache steps: {sorted(cache_step_names)}", file=sys.stderr)
                    print(f"  File steps: {sorted(file_step_names)}", file=sys.stderr)
                    print(f"  Original cache file: {original_file_path_or_url}", file=sys.stderr)
                    print(f"  Current file: {args.file}", file=sys.stderr)
                    sys.exit(1)
                
                # If --dry-run, just show what would be updated and exit
                if args.dry_run:
                    print("\n" + "="*80, file=sys.stderr)
                    print("DRY-RUN: Would update file with cached recommendations", file=sys.stderr)
                    print("="*80, file=sys.stderr)
                    print_comparison_table(recommendations, current_resources)
                    print("\nDRY-RUN completed. No file was updated.", file=sys.stderr)
                    return
                
                # Show comparison table
                print_comparison_table(recommendations, current_resources)
                
                # Update the local file directly (not generate patch)
                updated = update_yaml_file(yaml_path, recommendations, yaml_content, None, debug=args.debug)
                if not updated:
                    print("Warning: No steps were updated in YAML file", file=sys.stderr)
                return
            else:
                # No cache exists or user requested re-analysis
                # Fall through to normal processing to run analysis (with validation)
                if not cache_data:
                    print(f"No cache found for task '{task_name}'. Running analysis...", file=sys.stderr)
                else:
                    print(f"Re-running analysis for task '{task_name}' as requested (--analyze-again)...", file=sys.stderr)
                # Continue to normal processing below (will go through validation flow)
        else:
            # --update --file <URL> - treat as normal update (will generate patch)
            # Fall through to normal processing below
            pass
    
    # Handle --update without --file (load from cache)
    if args.update and not args.file:
        # Find the most recent cache file
        script_dir = Path(__file__).parent
        cache_dir = script_dir / '.analyze_cache'
        
        if not cache_dir.exists():
            print("Error: No cache found. Please run with --file first to generate recommendations.", file=sys.stderr)
            sys.exit(1)
        
        cache_files = list(cache_dir.glob('*.json'))
        if not cache_files:
            print("Error: No cache found. Please run with --file first to generate recommendations.", file=sys.stderr)
            sys.exit(1)
        
        # Load all cache files to show available tasks
        available_tasks = []
        for cache_file in cache_files:
            try:
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                    task_name = cache_data.get('task_name', 'unknown')
                    file_path = cache_data.get('file_path_or_url', 'unknown')
                    timestamp = cache_data.get('timestamp', 'unknown')
                    available_tasks.append((task_name, file_path, timestamp, cache_file))
            except Exception as e:
                print(f"Warning: Could not read cache file {cache_file}: {e}", file=sys.stderr)
        
        if not available_tasks:
            print("Error: No valid cache files found.", file=sys.stderr)
            sys.exit(1)
        
        # Sort by timestamp (most recent first)
        available_tasks.sort(key=lambda x: x[2], reverse=True)
        
        # Show available tasks
        print("Available cached tasks:", file=sys.stderr)
        for i, (task_name, file_path, timestamp, _) in enumerate(available_tasks[:5], 1):
            print(f"  {i}. {task_name} (from {file_path}) - {timestamp}", file=sys.stderr)
        if len(available_tasks) > 5:
            print(f"  ... and {len(available_tasks) - 5} more", file=sys.stderr)
        print()
        
        # Use most recent cache
        task_name, file_path_or_url, timestamp, latest_cache = available_tasks[0]
        
        print(f"Using most recent cache for task '{task_name}': {latest_cache}", file=sys.stderr)
        with open(latest_cache, 'r') as f:
            cache_data = json.load(f)
        
        recommendations = cache_data['recommendations']
        margin_pct = cache_data.get('margin_pct', 10)
        base = cache_data.get('base', 'max')
        
        print(f"Cache info: task={task_name}, file={file_path_or_url}, margin={margin_pct}%, base={base}", file=sys.stderr)
        print()
        
        # Load YAML to update
        yaml_content, yaml_path = fetch_yaml_content(file_path_or_url)
        yaml_task_name, file_steps, _, current_resources = extract_task_info(yaml_content)
        
        # Verify task name matches
        if yaml_task_name != task_name:
            print(f"Warning: Cache is for task '{task_name}' but file contains task '{yaml_task_name}'", file=sys.stderr)
            print(f"  Proceeding with update, but steps may not match.", file=sys.stderr)
        
        # Validate that cached recommendations match the file being updated
        cache_step_names = set()
        for rec in recommendations:
            if rec is None:
                continue
            step_name_csv = rec['step_name']
            step_name_yaml = step_name_csv.replace('step-', '') if step_name_csv.startswith('step-') else step_name_csv
            cache_step_names.add(step_name_yaml)
        
        file_step_names = set(file_steps)
        
        # Check for mismatches (file may have changed since cache was created)
        cache_only_steps = cache_step_names - file_step_names
        file_only_steps = file_step_names - cache_step_names
        
        if cache_only_steps:
            print(f"Warning: Cache contains steps not found in file: {sorted(cache_only_steps)}", file=sys.stderr)
            print(f"  These steps will be skipped during update.", file=sys.stderr)
        
        if file_only_steps:
            print(f"Warning: File contains steps not found in cache: {sorted(file_only_steps)}", file=sys.stderr)
            print(f"  These steps will not be updated. Consider running analysis again.", file=sys.stderr)
        
        if not cache_step_names & file_step_names:
            print("Error: No matching steps found between cache and file.", file=sys.stderr)
            print(f"  Cache steps: {sorted(cache_step_names)}", file=sys.stderr)
            print(f"  File steps: {sorted(file_step_names)}", file=sys.stderr)
            print(f"  File: {file_path_or_url}", file=sys.stderr)
            sys.exit(1)
        
        # Show comparison table
        print_comparison_table(recommendations, current_resources)
        
        # Update YAML
        update_yaml_file(yaml_path, recommendations, yaml_content, file_path_or_url, debug=args.debug)
        return
    
    # Determine input source
    current_resources = None
    file_path_or_url = args.file
    
    if args.file:
        # Load YAML and extract task info
        yaml_content, yaml_path = fetch_yaml_content(args.file)
        yaml_task_name, yaml_steps, default_resources, current_resources = extract_task_info(yaml_content)
        
        if not yaml_task_name:
            print("Error: Could not extract task name from YAML", file=sys.stderr)
            sys.exit(1)
        
        if not yaml_steps:
            print("Error: Could not extract steps from YAML", file=sys.stderr)
            sys.exit(1)
        
        # Read wrapper script configuration
        script_dir = Path(__file__).parent
        wrapper_path = script_dir / 'wrapper_for_promql_for_all_clusters.sh'
        wrapper_task, wrapper_steps, wrapper_defined = read_wrapper_config(wrapper_path)
        
        # Determine which task/steps to use
        use_wrapper_values = False
        final_task_name = yaml_task_name
        final_steps = yaml_steps
        source_desc = "extracted from YAML file"
        
        if wrapper_defined:
            # Validate wrapper-defined values against YAML
            print("\n" + "="*80, file=sys.stderr)
            print("VALIDATION: Wrapper-defined Task and Steps", file=sys.stderr)
            print("="*80, file=sys.stderr)
            print(f"Wrapper Task: {wrapper_task}", file=sys.stderr)
            print(f"Wrapper Steps: {', '.join(wrapper_steps)}", file=sys.stderr)
            print(f"YAML Task: {yaml_task_name}", file=sys.stderr)
            print(f"YAML Steps: {', '.join(yaml_steps)}", file=sys.stderr)
            print("="*80, file=sys.stderr)
            
            is_valid, errors = validate_wrapper_steps(wrapper_task, wrapper_steps, yaml_task_name, yaml_steps)
            
            if not is_valid:
                print("\nERROR: Validation failed:", file=sys.stderr)
                for error in errors:
                    print(f"  - {error}", file=sys.stderr)
                print("\nPlease fix the wrapper script or use a different YAML file.", file=sys.stderr)
                sys.exit(1)
            
            # Show which steps will be used vs available
            wrapper_steps_set = set(wrapper_steps)
            yaml_steps_set = set(yaml_steps)
            missing_steps = yaml_steps_set - wrapper_steps_set
            
            if missing_steps:
                print(f"\nINFO: YAML file has additional steps not in wrapper: {sorted(missing_steps)}", file=sys.stderr)
                print("  These steps will not be analyzed.", file=sys.stderr)
            
            print("\n✓ Validation passed: Wrapper steps are valid subset of YAML steps", file=sys.stderr)
            
            # Use wrapper's values
            use_wrapper_values = True
            final_task_name = wrapper_task
            final_steps = wrapper_steps
            source_desc = "defined in wrapper script (validated against YAML)"
        else:
            # Extract from YAML and prefix steps with 'step-'
            final_steps = [f'step-{s}' if not s.startswith('step-') else s for s in yaml_steps]
            source_desc = "extracted from YAML file"
        
        # Always check cluster connectivity (before proceeding with analysis)
        print("\n" + "="*80, file=sys.stderr)
        if args.dry_run:
            print("DRY-RUN: Checking Cluster Connectivity", file=sys.stderr)
        else:
            print("Checking Cluster Connectivity", file=sys.stderr)
        print("="*80, file=sys.stderr)
        all_connected, connectivity_report = check_cluster_connectivity(wrapper_path)
        
        print("\nCluster Connectivity Report:", file=sys.stderr)
        for cluster, connected, message in connectivity_report:
            status = "✓" if connected else "✗"
            print(f"  {status} {cluster}: {message}", file=sys.stderr)
        
        if not all_connected:
            print("\nWARNING: Some clusters are not accessible.", file=sys.stderr)
            if args.dry_run:
                print("  Data collection may fail for these clusters.", file=sys.stderr)
            else:
                print("  Data collection may fail for these clusters.", file=sys.stderr)
                print("  Continuing with accessible clusters only...", file=sys.stderr)
        else:
            print("\n✓ All clusters are accessible", file=sys.stderr)
        
        # Always prompt for confirmation
        # For display, ensure steps have 'step-' prefix (for consistency with wrapper script format)
        steps_for_display = [
            f'step-{s}' if not s.startswith('step-') else s 
            for s in final_steps
        ]
        if not prompt_confirmation(final_task_name, steps_for_display, source_desc):
            print("Aborted by user.", file=sys.stderr)
            sys.exit(0)
        
        # If dry-run, exit here
        if args.dry_run:
            print("\n" + "="*80, file=sys.stderr)
            print("DRY-RUN completed successfully. No data collection performed.", file=sys.stderr)
            print("="*80, file=sys.stderr)
            sys.exit(0)
        
        # Convert final_steps to list without 'step-' prefix for run_data_collection
        steps_for_collection = [
            s.replace('step-', '') if s.startswith('step-') else s 
            for s in final_steps
        ]
        
        # Run data collection (show table format)
        # Only use parallel processing during analysis (not during --update)
        parallel_workers = args.pll_clusters if args.pll_clusters and not args.update else None
        csv_data = run_data_collection(final_task_name, steps_for_collection, args.days, show_table=True, dry_run=False, use_wrapper_values=use_wrapper_values, parallel_clusters=parallel_workers)
        # Track task_name for caching
        task_name = final_task_name
        steps = steps_for_collection
    else:
        # Read from stdin
        if sys.stdin.isatty():
            print("Error: No input provided. Use --file or pipe CSV data.", file=sys.stderr)
            sys.exit(1)
        csv_data = sys.stdin.read()
        yaml_content = None
        yaml_path = None
        task_name = None
        steps = None
        file_path_or_url = None
    
    # Parse CSV data
    data = parse_csv_data(csv_data)
    if not data:
        print("Error: No data found in CSV input", file=sys.stderr)
        print("\nPossible reasons:", file=sys.stderr)
        print("  1. No pods found for the specified task/steps in the given time period", file=sys.stderr)
        print("  2. Task or step names don't match what's actually running in clusters", file=sys.stderr)
        print("  3. Cluster connectivity issues (try --dry-run to check)", file=sys.stderr)
        print("  4. Time period too short (try increasing --days)", file=sys.stderr)
        if args.file:
            print(f"\nTask: {task_name}", file=sys.stderr)
            print(f"Steps: {', '.join(steps)}", file=sys.stderr)
            print(f"Days: {args.days}", file=sys.stderr)
        # Show first few lines of CSV to help debug
        if csv_data:
            csv_lines = csv_data.strip().split('\n')
            print(f"\nCSV output (first {min(5, len(csv_lines))} lines):", file=sys.stderr)
            for i, line in enumerate(csv_lines[:5], 1):
                print(f"  {i}: {line[:100]}", file=sys.stderr)
        sys.exit(1)
    
    # Group by step
    by_step = defaultdict(list)
    for row in data:
        step = row.get('step', '').strip()
        if step:
            by_step[step].append(row)
    
    # Analyze each step
    recommendations = []
    for step_name in sorted(by_step.keys()):
        rec = analyze_step_data(step_name, by_step[step_name], args.margin, args.base)
        if rec:
            recommendations.append(rec)
    
    # Print analysis
    print_analysis(recommendations, args.margin, args.base, current_resources)
    
    # Save to cache if using --file and we have task_name (even without --update)
    if file_path_or_url and task_name:
        save_recommendations_cache(task_name, file_path_or_url, recommendations, args.margin, args.base, args.days)
        if not args.update:
            print(f"\nRecommendations cached for task '{task_name}'. Run with --update to apply changes.", file=sys.stderr)
    
    # Update YAML if requested
    if args.update and file_path_or_url:
        if yaml_path and not yaml_path.startswith('http'):
            # Local file - update directly
            updated = update_yaml_file(yaml_path, recommendations, yaml_content, file_path_or_url, debug=args.debug)
            if not updated:
                print("Warning: No steps were updated in YAML file", file=sys.stderr)
        elif file_path_or_url.startswith('http'):
            # Remote URL - generate patch file
            update_yaml_file(None, recommendations, yaml_content, file_path_or_url, debug=args.debug)


if __name__ == '__main__':
    main()

