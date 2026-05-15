#!/usr/bin/env python3
"""run_with_mitigation.py — primitives_lab runner using HermesPolicy.

Mirror of runner.py but invokes HermesPolicy.execute() instead of direct
call_embodied_plan. Records outcome tripartite (policy_handled_upstream /
embodied_succeeded / embodied_failed) per sample.

Output JSON shape compatible with runner.py results, with extra fields:
  - sample.outcome
  - sample.policy_layer (when policy_handled)
  - sample.mitigation_meta (sub_intents, category_chain, allowed_tools_chain)

Usage:
  python run_with_mitigation.py experiments/004_tier1_visual_distinct.yaml --samples 5

Designed to live alongside runner.py in the primitives_lab folder.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import yaml

# Import score_sample + fixture utilities from runner.py (in same dir)
LAB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_DIR))
import runner  # noqa: E402
from hermes_policy import HermesPolicy  # noqa: E402

SERVICE_API = os.getenv("EMBODIED_SERVICE_URL", "http://localhost:7790")
FIXTURES_DIR = LAB_DIR / "fixtures"
RESULTS_DIR = LAB_DIR / "results"


def run_variant_mitigated(
    policy: HermesPolicy,
    variant: dict,
    expectations: dict,
    samples: int,
    deadline: int = 30,
) -> dict:
    samples_data = []
    intent = variant["primitives"]["intent"]
    for i in range(samples):
        t0 = time.time()
        resp = policy.execute(intent, deadline_seconds=deadline)
        wall = time.time() - t0
        passed, failures = runner.score_sample(resp, expectations)
        plan = resp.get("plan") or {}

        # Outcome tripartite (already set by HermesPolicy.execute, but recompute for safety)
        if resp.get("policy_handled"):
            outcome = "policy_handled_upstream"
        else:
            er = resp.get("execution_results") or []
            outcome = (
                "embodied_succeeded"
                if (resp.get("ok") and er and all(x.get("ok") for x in er))
                else "embodied_failed"
            )

        samples_data.append({
            "iter": i + 1,
            "wall_elapsed_s": round(wall, 2),
            "service_elapsed_s": resp.get("elapsed_seconds"),
            "ok": resp.get("ok"),
            "outcome": outcome,
            "policy_layer": resp.get("policy_layer"),
            "passed": passed,
            "failures": failures,
            "tool_names": [t.get("name") for t in plan.get("tool_calls", [])] if plan else [],
            "tool_count": len(plan.get("tool_calls", [])) if plan else 0,
            "operational_risk": plan.get("operational_risk") if plan else None,
            "mitigations": [m.get("regression") for m in (resp.get("mitigations") or [])],
            "execution_results": resp.get("execution_results") or [],
            "mitigation_meta": resp.get("mitigation"),
        })
    return {
        "id": variant["id"],
        "primitives": variant["primitives"],
        "samples": samples_data,
        "metrics": _aggregate_metrics(samples_data),
    }


def _aggregate_metrics(samples_data: list[dict]) -> dict:
    n = len(samples_data)
    if n == 0:
        return {}
    passes = [s for s in samples_data if s["passed"]]
    elapsed = [s["service_elapsed_s"] for s in samples_data if isinstance(s.get("service_elapsed_s"), (int, float))]
    tool_counts = [s["tool_count"] for s in samples_data]

    outcome_counts = {"policy_handled_upstream": 0, "embodied_succeeded": 0, "embodied_failed": 0}
    for s in samples_data:
        outcome_counts[s.get("outcome", "embodied_failed")] = outcome_counts.get(s.get("outcome", "embodied_failed"), 0) + 1

    all_tools = [t for s in samples_data for t in s["tool_names"]]
    freq: dict[str, int] = {}
    for t in all_tools:
        freq[t] = freq.get(t, 0) + 1

    return {
        "n": n,
        "success_rate": round(len(passes) / n, 3),
        "latency_p50": round(statistics.median(elapsed), 2) if elapsed else None,
        "latency_p95": round(sorted(elapsed)[int(0.95 * (len(elapsed) - 1))], 2) if len(elapsed) >= 2 else (elapsed[0] if elapsed else None),
        "mean_tool_count": round(statistics.mean(tool_counts), 2) if tool_counts else None,
        "outcome_counts": outcome_counts,
        "tool_freq": dict(sorted(freq.items(), key=lambda kv: -kv[1])),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("experiment", type=Path, help="path to experiment YAML")
    p.add_argument("--variant", help="only run this variant id (default: all)")
    p.add_argument("--samples", type=int, help="override samples_per_variant")
    p.add_argument("--deadline", type=int, default=45)
    p.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    p.add_argument("--player-name", default=os.getenv("HERMES_PLAYER_NAME", "player"))
    p.add_argument("--bot-name", default=os.getenv("HERMES_BOT_NAME", "minecraft_bot"))
    args = p.parse_args()

    spec = runner.load_experiment(args.experiment)
    policy = HermesPolicy(SERVICE_API, player_name=args.player_name, bot_name=args.bot_name)

    print(f"experiment: {spec['id']}")
    print(f"hypothesis: {spec.get('hypothesis', '(none)')}")
    print(f"fixture: {spec.get('fixture')}")
    print(f"hermes-policy player={args.player_name}, bot={args.bot_name}")

    samples = args.samples or spec["samples_per_variant"]
    variants = spec["variants"]
    if args.variant:
        variants = [v for v in variants if v["id"] == args.variant]
        if not variants:
            print(f"[error] variant '{args.variant}' not found in spec", file=sys.stderr)
            sys.exit(2)

    results: list[dict] = []
    for variant in variants:
        print(f"\n--- variant: {variant['id']} ---")
        print(f"  intent: {variant['primitives'].get('intent', '(?)')!r}")
        # Merge per-variant expectations_override over global spec.expectations
        per_variant_expect = {**spec["expectations"], **(variant.get("expectations_override") or {})}
        result = run_variant_mitigated(policy, variant, per_variant_expect, samples, deadline=args.deadline)
        results.append(result)
        m = result["metrics"]
        print(f"  success_rate={m['success_rate']:.0%}  p50={m['latency_p50']}s  mean_tools={m['mean_tool_count']}")
        print(f"  outcomes: {m['outcome_counts']}")
        print(f"  tool_freq: {m['tool_freq']}")
        for s in result["samples"]:
            mark = "✓" if s["passed"] else "✗"
            elapsed_s = s.get('service_elapsed_s')
            elapsed_str = f"{elapsed_s:.1f}s" if isinstance(elapsed_s, (int, float)) else str(elapsed_s)
            mit = s.get("mitigation_meta") or {}
            cat = mit.get("category_chain", [])
            extras = []
            extras.append(f"outcome={s['outcome']}")
            if s.get("policy_layer"):
                extras.append(f"layer={s['policy_layer']}")
            if cat:
                extras.append(f"cat={cat}")
            if s["failures"]:
                extras.append(f"fail={s['failures'][0][:80]}")
            print(f"    {mark} #{s['iter']} {elapsed_str} tools={s['tool_names']} {' '.join(extras)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Include microseconds + variant slug to avoid filename collisions when running
    # multiple variants back-to-back (Codex correction 2026-05-15).
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    variant_slug = ""
    if args.variant:
        variant_slug = "_" + re.sub(r"[^a-zA-Z0-9_-]", "-", args.variant)[:40]
    out_path = args.output_dir / f"{spec['id']}{variant_slug}_{ts}_MITIGATED.json"
    out_path.write_text(json.dumps({
        "spec": spec, "results": results, "ts": ts,
        "policy_config": {"player_name": args.player_name, "bot_name": args.bot_name},
    }, indent=2))
    print(f"\n→ saved: {out_path}")


if __name__ == "__main__":
    main()
