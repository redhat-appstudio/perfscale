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
import subprocess
import sys
import tempfile
import urllib.parse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

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


def run_data_collection(task_name, steps, days=7, show_table=True):
    """Run the wrapper script to collect data."""
    script_path = Path(__file__).parent / 'wrapper_for_promql_for_all_clusters.sh'
    if not script_path.exists():
        print(f"Error: wrapper script not found: {script_path}", file=sys.stderr)
        sys.exit(1)
    
    # Create a temporary wrapper that sets TASK_NAME and STEPS
    temp_wrapper = script_path.parent / '.temp_wrapper.sh'
    try:
        with open(script_path, 'r') as f:
            wrapper_content = f.read()
        
        # Replace hardcoded TASK_NAME and STEPS
        # Steps in CSV are "step-{name}", so ensure we use that format
        wrapper_content = re.sub(
            r'TASK_NAME="[^"]*"',
            f'TASK_NAME="{task_name}"',
            wrapper_content
        )
        # Convert step names to "step-{name}" format for the wrapper
        steps_str = ' '.join(f'step-{s}' if not s.startswith('step-') else s for s in steps)
        wrapper_content = re.sub(
            r'STEPS="[^"]*"',
            f'STEPS="{steps_str}"',
            wrapper_content
        )
        
        with open(temp_wrapper, 'w') as f:
            f.write(wrapper_content)
        os.chmod(temp_wrapper, 0o755)
        
        # Run the wrapper
        print(f"Collecting data for task '{task_name}' with steps: {', '.join(steps)}", file=sys.stderr)
        print(f"Running: {temp_wrapper} {days} --csv", file=sys.stderr)
        print(file=sys.stderr)
        
        # Run with table format if requested
        if show_table:
            result = subprocess.run(
                [str(temp_wrapper), str(days), '--table'],
                capture_output=True,
                text=True,
                cwd=script_path.parent
            )
            # Also capture raw CSV for parsing
            result_csv = subprocess.run(
                [str(temp_wrapper), str(days), '--raw'],
                capture_output=True,
                text=True,
                cwd=script_path.parent
            )
            # Display table output
            print(result.stdout)
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
    finally:
        if temp_wrapper.exists():
            temp_wrapper.unlink()


def parse_csv_data(csv_text):
    """Parse CSV data from the wrapper script output."""
    data = []
    lines = [line for line in csv_text.strip().split('\n') if line.strip()]
    if not lines:
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
    mem_max_values = [float(r['mem_max_mb']) for r in step_rows if r.get('mem_max_mb') and r['mem_max_mb'] not in ('0', '', 'N/A')]
    mem_p95_values = [float(r['mem_p95_mb']) for r in step_rows if r.get('mem_p95_mb') and r['mem_p95_mb'] not in ('0', '', 'N/A')]
    mem_p90_values = [float(r['mem_p90_mb']) for r in step_rows if r.get('mem_p90_mb') and r['mem_p90_mb'] not in ('0', '', 'N/A')]
    mem_median_values = [float(r['mem_median_mb']) for r in step_rows if r.get('mem_median_mb') and r['mem_median_mb'] not in ('0', '', 'N/A')]
    
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
    
    print("=" * 120)
    print("RESOURCE LIMITS COMPARISON: CURRENT vs PROPOSED")
    print("=" * 120)
    print()
    
    # Table header
    print(f"{'Step':<25} {'Current Requests':<30} {'Proposed Requests':<30} {'Current Limits':<30} {'Proposed Limits':<30}")
    print("-" * 120)
    
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
        
        print(f"{step_name_yaml:<25} {curr_req:<30} {prop_req:<30} {curr_lim:<30} {prop_lim:<30}")
    
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


def get_cache_file_path(file_path_or_url):
    """Generate cache file path based on file path or URL."""
    script_dir = Path(__file__).parent
    cache_dir = script_dir / '.analyze_cache'
    cache_dir.mkdir(exist_ok=True)
    
    # Create a hash of the file path/URL for cache filename
    cache_key = hashlib.md5(file_path_or_url.encode()).hexdigest()
    return cache_dir / f"{cache_key}.json"


def save_recommendations_cache(file_path_or_url, recommendations, margin_pct, base, days):
    """Save recommendations to cache file."""
    cache_file = get_cache_file_path(file_path_or_url)
    
    cache_data = {
        'file_path_or_url': file_path_or_url,
        'timestamp': datetime.now().isoformat(),
        'margin_pct': margin_pct,
        'base': base,
        'days': days,
        'recommendations': recommendations
    }
    
    with open(cache_file, 'w') as f:
        json.dump(cache_data, f, indent=2)
    
    print(f"Cached recommendations to: {cache_file}", file=sys.stderr)


def load_recommendations_cache(file_path_or_url):
    """Load recommendations from cache file."""
    cache_file = get_cache_file_path(file_path_or_url)
    
    if not cache_file.exists():
        return None
    
    try:
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)
        
        # Verify it matches the requested file
        if cache_data.get('file_path_or_url') == file_path_or_url:
            return cache_data
        else:
            print(f"Warning: Cache file exists but for different file/URL. Ignoring cache.", file=sys.stderr)
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


def update_yaml_file(yaml_path, recommendations, original_yaml, file_path_or_url=None):
    """Update YAML file with recommended resource limits."""
    updated = False
    updated_yaml = yaml.safe_load(yaml.dump(original_yaml))  # Deep copy
    
    for rec in recommendations:
        if rec is None:
            continue
        
        step_name_csv = rec['step_name']  # e.g., "step-build"
        mem_k8s = rec['mem_recommended_k8s']
        cpu_k8s = rec['cpu_recommended_k8s']
        
        # Convert "step-build" to "build" for YAML matching
        step_name_yaml = step_name_csv.replace('step-', '') if step_name_csv.startswith('step-') else step_name_csv
        
        # Find the step in the YAML
        steps = updated_yaml.get('spec', {}).get('steps', [])
        for step in steps:
            if step.get('name') == step_name_yaml:
                # Update or create computeResources
                if 'computeResources' not in step:
                    step['computeResources'] = {}
                
                if 'limits' not in step['computeResources']:
                    step['computeResources']['limits'] = {}
                if 'requests' not in step['computeResources']:
                    step['computeResources']['requests'] = {}
                
                step['computeResources']['limits']['memory'] = mem_k8s
                step['computeResources']['limits']['cpu'] = cpu_k8s
                step['computeResources']['requests']['memory'] = mem_k8s
                step['computeResources']['requests']['cpu'] = cpu_k8s
                
                updated = True
                print(f"Updated step '{step_name_yaml}': memory={mem_k8s}, cpu={cpu_k8s}", file=sys.stderr)
                break
    
    if updated:
        # If remote URL, generate patch file
        if file_path_or_url and (file_path_or_url.startswith('http://') or file_path_or_url.startswith('https://')):
            generate_diff_patch(original_yaml, updated_yaml, file_path_or_url)
        elif yaml_path and not yaml_path.startswith('http'):
            # Local file - update directly
            with open(yaml_path, 'w') as f:
                yaml.dump(updated_yaml, f, default_flow_style=False, sort_keys=False, width=120)
            print(f"\nUpdated YAML file: {yaml_path}", file=sys.stderr)
    
    return updated


def main():
    parser = argparse.ArgumentParser(
        description='Analyze resource consumption and provide recommendations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From piped input:
  ./wrapper_for_promql_for_all_clusters.sh 7 --csv | ./analyze_resource_limits.py

  # From YAML file (auto-runs data collection):
  ./analyze_resource_limits.py --file /path/to/buildah.yaml
  ./analyze_resource_limits.py --file https://github.com/.../buildah.yaml

  # Update YAML file with recommendations:
  ./analyze_resource_limits.py --file /path/to/buildah.yaml --update
  
  # Update using cached recommendations (from previous run):
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
        help='Update the YAML file with recommended resource limits. If --file is not provided, uses cached recommendations from the last run.'
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
    
    args = parser.parse_args()
    
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
        
        # Get most recent cache file
        cache_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        latest_cache = cache_files[0]
        
        print(f"Loading recommendations from cache: {latest_cache}", file=sys.stderr)
        with open(latest_cache, 'r') as f:
            cache_data = json.load(f)
        
        file_path_or_url = cache_data['file_path_or_url']
        recommendations = cache_data['recommendations']
        margin_pct = cache_data.get('margin_pct', 10)
        base = cache_data.get('base', 'max')
        
        print(f"Cache info: file={file_path_or_url}, margin={margin_pct}%, base={base}", file=sys.stderr)
        print()
        
        # Load YAML to update
        yaml_content, yaml_path = fetch_yaml_content(file_path_or_url)
        _, _, _, current_resources = extract_task_info(yaml_content)
        
        # Show comparison table
        print_comparison_table(recommendations, current_resources)
        
        # Update YAML
        update_yaml_file(yaml_path, recommendations, yaml_content, file_path_or_url)
        return
    
    # Determine input source
    current_resources = None
    file_path_or_url = args.file
    
    if args.file:
        # Load YAML and extract task info
        yaml_content, yaml_path = fetch_yaml_content(args.file)
        task_name, steps, default_resources, current_resources = extract_task_info(yaml_content)
        
        if not task_name:
            print("Error: Could not extract task name from YAML", file=sys.stderr)
            sys.exit(1)
        
        if not steps:
            print("Error: Could not extract steps from YAML", file=sys.stderr)
            sys.exit(1)
        
        # Run data collection (show table format)
        csv_data = run_data_collection(task_name, steps, args.days, show_table=True)
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
    
    # Save to cache if using --file (even without --update)
    if file_path_or_url:
        save_recommendations_cache(file_path_or_url, recommendations, args.margin, args.base, args.days)
        if not args.update:
            print(f"\nRecommendations cached. Run with --update to apply changes.", file=sys.stderr)
    
    # Update YAML if requested
    if args.update and file_path_or_url:
        if yaml_path and not yaml_path.startswith('http'):
            # Local file - update directly
            updated = update_yaml_file(yaml_path, recommendations, yaml_content, file_path_or_url)
            if not updated:
                print("Warning: No steps were updated in YAML file", file=sys.stderr)
        elif file_path_or_url.startswith('http'):
            # Remote URL - generate patch file
            update_yaml_file(None, recommendations, yaml_content, file_path_or_url)


if __name__ == '__main__':
    main()

