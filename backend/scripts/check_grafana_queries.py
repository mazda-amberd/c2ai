#!/usr/bin/env python3
"""
Script to test Prometheus queries against Grafana API.

Usage:
    export GRAFANA_API_TOKEN="your-token-here"
    python scripts/check_grafana_queries.py

Or:
    python scripts/check_grafana_queries.py --token "your-token-here"
"""

import argparse
import asyncio
import json
import os
import sys

# Make the backend package importable when run as scripts/check_grafana_queries.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c2ai.clients.grafana import (
    GrafanaClient,
    _build_grafana_query_body as build_grafana_query_body,
)
from c2ai.config import get_settings
from c2ai.constants.prometheus import TIER_CONFIG
from c2ai.schemas.grafana import MetricType


async def test_single_query(client: GrafanaClient, metric_type: MetricType, name: str):
    """Test a single query and print results."""
    print(f"\n{'='*60}")
    print(f"Testing: {name} ({metric_type.value})")
    print(f"{'='*60}")
    
    # Print the query being sent
    body = build_grafana_query_body(metric_type)
    print("\nQuery expressions:")
    for q in body["queries"]:
        print(f"  {q['refId']}: {q['expr']}")
    
    try:
        response = await client.fetch_grafana_data(metric_type)
        
        for ref_id, result in response.results.items():
            print(f"\n--- {ref_id} (status: {result.status}) ---")
            if result.frames:
                print(f"  Frames: {len(result.frames)}")
                for i, frame in enumerate(result.frames[:3]):  # Show first 3 frames
                    labels = {}
                    if len(frame.schema_.fields) > 1 and frame.schema_.fields[1].labels:
                        labels = {
                            "groupname": frame.schema_.fields[1].labels.groupname,
                            "nodename": frame.schema_.fields[1].labels.nodename,
                        }
                    value = frame.data.values[1][0] if frame.data.values and len(frame.data.values) > 1 else None
                    print(f"    [{i}] value={value}, labels={labels}")
                if len(result.frames) > 3:
                    print(f"    ... and {len(result.frames) - 3} more frames")
            else:
                print("  No frames returned")
                
    except Exception as e:
        print(f"  ERROR: {e}")


async def test_all_queries(token: str):
    """Test all query types."""
    client = GrafanaClient(api_token=token)
    
    print("\n" + "="*60)
    print("TIER CONFIGURATION")
    print("="*60)
    for tier, config in TIER_CONFIG.items():
        if config:
            print(f"  {tier}: gpus={config.get('gpus', 'N/A')} (app count from metrics)")
        else:
            print(f"  {tier}: null (no instances)")
    
    # Test each query type
    queries = [
        (MetricType.CPU, "CPU Used (rate per instance)"),
        (MetricType.MEMORY, "Memory Used (bytes per instance)"),
        (MetricType.CPU_TOTAL, "CPU total usage (rate)"),
        (MetricType.MEMORY_TOTAL, "Memory total usage (sum container working set)"),
        (MetricType.GPU, "GPU Utilization (placeholder)"),
    ]
    
    for metric_type, name in queries:
        await test_single_query(client, metric_type, name)


async def probe_metrics_twice(token: str, wait_seconds: float) -> None:
    """
    Fetch get_all_metrics twice and print tier_gpu_totals deltas.

    Use this when Athena/Grafana disagree: if both samples are identical but Grafana
    moves, check GRAFANA_API_URL / GRAFANA_PROMETHEUS_DATASOURCE_UID / ATHENA_TIER*_GPU_CLUSTER.
    """
    client = GrafanaClient(api_token=token)
    print("\n" + "=" * 60)
    print(f"METRICS PROBE (two samples, {wait_seconds}s apart)")
    print("=" * 60)

    async def sample(label: str) -> tuple[dict, dict]:
        tiers, gpu_totals = await client.get_all_metrics()
        print(f"\n--- {label} ---")
        print("tier_gpu_totals:", json.dumps(gpu_totals, indent=2, default=str))
        for tier_name in ("Tier 1", "Tier 2", "Tier 3"):
            insts = tiers.get(tier_name) or []
            if not insts:
                continue
            top = max(insts, key=lambda i: i.gpu)
            print(
                f"  {tier_name} max per-app gpu: {top.name} ns={top.nodename} gpu={top.gpu:.4f}"
            )
        return tiers, gpu_totals

    try:
        _, first = await sample("first")
        print(f"\n(waiting {wait_seconds}s...)")
        await asyncio.sleep(wait_seconds)
        _, second = await sample("second")

        print("\n--- delta (second - first) ---")
        for key in sorted(set(first) | set(second)):
            a, b = first.get(key), second.get(key)
            if a is None and b is None:
                continue
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                print(f"  {key}: {b - a:.6f}")
            else:
                print(f"  {key}: {a!r} -> {b!r}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


async def test_full_metrics(token: str):
    """Test the full get_all_metrics call with percentage calculations."""
    print("\n" + "="*60)
    print("FULL METRICS (with percentage calculations)")
    print("="*60)
    
    client = GrafanaClient(api_token=token)
    
    try:
        tiers, _gpu_totals = await client.get_all_metrics()
        
        for tier_name, instances in tiers.items():
            print(f"\n--- {tier_name} ---")
            if instances is None:
                print("  null (no instances)")
            elif not instances:
                print("  empty (no data)")
            else:
                for inst in instances[:5]:  # Show first 5 instances
                    print(f"  {inst.name}:")
                    print(f"    CPU: {inst.cpu:.2f}% | Memory: {inst.memory:.2f}% | GPU: {inst.gpu:.2f}%")
                    print(f"    Status: {inst.status.value} | Node: {inst.nodename}")
                if len(instances) > 5:
                    print(f"  ... and {len(instances) - 5} more instances")
                    
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="Test Grafana queries")
    parser.add_argument("--token", "-t", help="Grafana API token")
    parser.add_argument("--full", "-f", action="store_true", help="Run full metrics test with calculations")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Call get_all_metrics twice and print tier_gpu_totals / per-app GPU deltas",
    )
    parser.add_argument(
        "--probe-wait",
        type=float,
        default=15.0,
        metavar="SEC",
        help="Seconds to wait between probe samples (default: 15)",
    )
    args = parser.parse_args()
    
    settings = get_settings()
    token = args.token or settings.grafana_api_token
    
    if not token:
        print("ERROR: No API token provided!")
        print("Set GRAFANA_API_TOKEN environment variable or use --token flag")
        sys.exit(1)
    
    grafana_url = settings.grafana_api_url or "https://grafana-k8s.amberd.ai/api/ds/query"
    print(f"Using Grafana URL: {grafana_url}")
    print(f"Token: {token[:10]}...{token[-4:]}" if len(token) > 14 else "Token: (too short to mask)")
    
    if args.probe:
        asyncio.run(probe_metrics_twice(token, args.probe_wait))
    elif args.full:
        asyncio.run(test_full_metrics(token))
    else:
        asyncio.run(test_all_queries(token))
        asyncio.run(test_full_metrics(token))


if __name__ == "__main__":
    main()
