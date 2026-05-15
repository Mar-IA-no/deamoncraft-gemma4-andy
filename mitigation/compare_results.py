#!/usr/bin/env python3
"""compare_results.py — produce pre/post comparison markdown from baseline and mitigation JSONs.

Inputs: list of baseline JSONs + list of mitigation JSONs.
Joins by (experiment_id, variant_id) and emits a markdown table with:
  - baseline pass + outcome breakdown
  - mitigation pass + outcome breakdown
  - Δ pass + Δ per-outcome
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def load_variants(paths: list[Path]) -> dict[tuple[str, str], dict]:
    """Returns dict keyed by (spec_id, variant_id) → variant summary."""
    out = {}
    for path in paths:
        data = json.loads(path.read_text())
        spec_id = data.get("spec", {}).get("id", path.name)
        for v in data.get("results", []):
            v_id = v.get("id")
            samples = v.get("samples", [])
            n = len(samples)
            pass_count = sum(1 for s in samples if s.get("passed"))
            outcomes = Counter(s.get("outcome", "unknown") for s in samples)
            tools = Counter(t for s in samples for t in s.get("tool_names", []))
            out[(spec_id, v_id)] = {
                "n": n,
                "pass": pass_count,
                "outcomes": dict(outcomes),
                "tools": dict(tools.most_common(5)),
                "source": path.name,
                "ts": data.get("ts"),
                "metrics": v.get("metrics", {}),
            }
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", nargs="+", type=Path, required=True)
    p.add_argument("--mitigation", nargs="+", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("/tmp/mitigation_results.md"))
    p.add_argument("--manifest-output", type=Path, default=Path("/tmp/result_manifest.md"))
    args = p.parse_args()

    base = load_variants(args.baseline)
    miti = load_variants(args.mitigation)
    keys = sorted(set(base.keys()) | set(miti.keys()))

    lines = []
    lines.append("# Mitigation results — pre / post comparison")
    lines.append("")
    lines.append("Comparison of baseline (patched runner, execution-aware scoring) vs post-mitigation (HermesPolicy wrapper) on the same Tier 1 primitives + variants. Both sides use identical scoring; outcomes are tripartite: `policy_handled_upstream` / `embodied_succeeded` / `embodied_failed`.")
    lines.append("")
    lines.append("## Per-variant outcome breakdown")
    lines.append("")
    lines.append("| Experiment | Variant | n | Baseline pass | Baseline outcomes | Mitigation pass | Mitigation outcomes | Δ pass |")
    lines.append("|---|---|---|---|---|---|---|---|")

    agg_X = 0  # mitigation embodied_succeeded
    agg_Y = 0  # mitigation policy_handled_upstream
    agg_Z = 0  # mitigation embodied_failed
    agg_total = 0
    for key in keys:
        spec_id, v_id = key
        b = base.get(key)
        m = miti.get(key)
        b_pass = f"{b['pass']}/{b['n']}" if b else "—"
        m_pass = f"{m['pass']}/{m['n']}" if m else "—"
        b_outc = ", ".join(f"{k}:{v}" for k, v in (b['outcomes'].items() if b else {}))
        m_outc = ", ".join(f"{k}:{v}" for k, v in (m['outcomes'].items() if m else {}))
        delta = (m['pass'] - b['pass']) if (b and m) else None
        delta_str = f"{delta:+d}" if delta is not None else "—"
        lines.append(
            f"| {spec_id} | {v_id} | {(b or m)['n']} | {b_pass} | {b_outc} | {m_pass} | {m_outc} | {delta_str} |"
        )
        if m:
            agg_total += m['n']
            agg_X += m['outcomes'].get("embodied_succeeded", 0)
            agg_Y += m['outcomes'].get("policy_handled_upstream", 0)
            agg_Z += m['outcomes'].get("embodied_failed", 0)

    lines.append("")
    lines.append("## Agregado X/Y/Z (post-mitigation)")
    lines.append("")
    lines.append("| Outcome | Count | % |")
    lines.append("|---|---|---|")
    if agg_total > 0:
        lines.append(f"| X — embodied_succeeded (Gemma ejecutó OK) | {agg_X} | {100*agg_X/agg_total:.0f}% |")
        lines.append(f"| Y — policy_handled_upstream (Hermes evitó la call) | {agg_Y} | {100*agg_Y/agg_total:.0f}% |")
        lines.append(f"| Z — embodied_failed (gap real, no resuelto) | {agg_Z} | {100*agg_Z/agg_total:.0f}% |")
        lines.append(f"| **Total** | **{agg_total}** | 100% |")
    lines.append("")
    lines.append("**Headline framing**: el sistema completo mejora porque Hermes aprende a delegar con criterio. NO es 'Gemma mejoró'.")
    lines.append("")
    args.output.write_text("\n".join(lines) + "\n")

    # Manifest
    mlines = []
    mlines.append("# Result manifest — primitives_lab runs 2026-05-15 / 2026-05-16")
    mlines.append("")
    mlines.append("Trazabilidad reproducible para los runs usados en el pitch. Los JSONs raw viven en `onairam-agent:~/agents/hermes-daemoncraft/daemoncraft/agents/embodied-service/primitives_lab/results/`. SHA256 calculado al momento de generar este manifest.")
    mlines.append("")
    mlines.append("| Kind | Experiment | File | TS | SHA256 |")
    mlines.append("|---|---|---|---|---|")
    for kind, paths in [("baseline", args.baseline), ("mitigation", args.mitigation)]:
        for path in paths:
            if not path.exists():
                continue
            content = path.read_bytes()
            sha = hashlib.sha256(content).hexdigest()[:16]
            data = json.loads(content)
            spec_id = data.get("spec", {}).get("id", path.name)
            ts = data.get("ts", "?")
            mlines.append(f"| {kind} | {spec_id} | `{path.name}` | {ts} | `{sha}…` |")
    mlines.append("")
    args.manifest_output.write_text("\n".join(mlines) + "\n")

    print(f"[compare] wrote: {args.output}")
    print(f"[manifest] wrote: {args.manifest_output}")
    # Also echo the summary table to stdout
    print()
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main()
