#!/usr/bin/env python3
"""eval_with_adapter.py — minimal example of running Gemma-Andy v2.2.3 directly
via Hugging Face transformers + PEFT, without going through Ollama.

Useful for:
  - Validating the adapter end-to-end on a fresh checkout (no Ollama needed).
  - A/B comparing the adapter vs the base model on the same prompt
    (`--no-adapter` flag).
  - Sanity-checking the contract before integrating into a service.

For production serving, use Ollama (see docs/OLLAMA_USAGE.md). This script is
a reference / smoke test.

Requirements:
    pip install torch transformers peft accelerate bitsandbytes safetensors

The base model `google/gemma-4-E4B-it` is gated on Hugging Face. You must:
  1. Accept the Gemma Terms of Use at https://ai.google.dev/gemma/terms
  2. Login with `huggingface-cli login` (or set HF_TOKEN env var)

Usage:
    python examples/eval_with_adapter.py
    python examples/eval_with_adapter.py --no-adapter  # base model only
    python examples/eval_with_adapter.py --device cpu  # if no GPU
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_DIR = REPO_ROOT / "adapter"

# The system prompt is byte-exact with the SFT training. DO NOT modify.
TRAINING_SYSTEM = (
    "You are Gemma-Andy v2.1, the embodied-service body orchestrator for a "
    "Minecraft companion. You do not chat with players and you do not write "
    "code. You receive one JSON body-state request and return body "
    "orchestration only. Return valid JSON with body_plan, checks, tool_calls, "
    "failure_policy, and operational_risk. You may prepend a short "
    "<think>...</think> block only for medium/high/critical risk, real "
    "multi-step cases, previous_error recovery, or adverse world state."
)

# Example user payload — 17-field world_state matches the SFT training
# distribution exactly. Sending fewer fields, or different shapes (nested
# inventory, rich block objects), makes the model fall back to priors. See
# docs/INTEGRATION_GUIDE.md §2 "world_state" for the field-test gotchas.
#
# Inventory is populated to exercise the realistic case (empty inventory was
# the previous default but rarely matches a real bot mid-session).
EXAMPLE_USER = json.dumps({
    "allowed_tools": [
        "scan_nearby", "goto", "mine_block", "collect_drops",
        "craft_item", "ask_clarification", "raise_guardian_event",
    ],
    "guardian_constraints": {
        "autonomy_level": 2,
        "executor_filtering": True,
        "no_player_harm": True,
        "no_protected_zone_edit": True,
        "no_tnt": True,
    },
    "high_level_command": "Trae un poco de madera para construir una mesa de trabajo.",
    "previous_error": None,
    "world_state": {
        "biome": "forest",
        "bot_health": 20,
        "bot_position": [10, 68, 5],       # INT coords — floats produce fabricated goto targets
        "dimension": "overworld",
        "hazards": [],
        "hunger": 18,
        "inventory": {"oak_log": 3, "stone": 12, "stick": 4},  # flat {name: count}
        "light_level": 12,
        "nearby_blocks": ["oak_log", "oak_leaves", "grass_block", "dirt"],  # flat list of strings
        "nearby_entities": ["player"],     # filter noise like 'item', 'arrow', 'experience_orb'
        "player_health": 20,
        "player_position": [8, 68, 3],
        "remembered_places": {"base": {"x": 0, "y": 64, "z": 0}},
        "target_positions": {},
        "time_of_day": "day",
        "weather": "clear",
        "zone_owner": "shared",
    },
}, sort_keys=True, ensure_ascii=True)


def extract_json(s: str):
    """Tolerant JSON extractor — handles ~1% of outputs with residual text
    around the JSON. Strategy ladder, returns the first successful parse:

      1. Whole input.
      2. Balanced-brace scan from each `{` (respects strings and escapes),
         finds the first well-formed top-level object.
      3. Naive first-`{` to last-`}` slice (legacy fallback).

    Returns the parsed object on success, or None if all strategies fail.
    Cribbed from the reference implementation at
    nicoechaniz/DaemonCraft:agents/embodied-service/lib/parser.js (which
    has been hardened against real model outputs in field-test).
    """
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Strategy 2: balanced-brace scan.
    for start in range(len(s)):
        if s[start] != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(s)):
            c = s[i]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    # Strategy 3: naive first/last brace.
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base-model",
        default="google/gemma-4-E4B-it",
        help="HuggingFace repo id for the base model (gated).",
    )
    parser.add_argument(
        "--adapter",
        default=str(ADAPTER_DIR),
        help="Path to the LoRA adapter directory (default: ./adapter/).",
    )
    parser.add_argument(
        "--no-adapter",
        action="store_true",
        help="Skip attaching the adapter; run the base model only (for A/B).",
    )
    parser.add_argument(
        "--device",
        default="cuda",
        choices=["cuda", "cpu"],
        help="Device to load the model on.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=512,
    )
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"[init] loading base model: {args.base_model}", file=sys.stderr)
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        attn_implementation="eager",  # Gemma 4 gotcha — see docs
        device_map=args.device,
    )

    if not args.no_adapter:
        from peft import PeftModel
        print(f"[init] attaching adapter from: {args.adapter}", file=sys.stderr)
        model = PeftModel.from_pretrained(model, args.adapter)

    model.eval()

    print(f"[init] loading tokenizer from: {args.adapter}", file=sys.stderr)
    # Use tokenizer from adapter dir — it has the chat_template.jinja and the
    # correct pad token configuration for Gemma 4.
    tokenizer = AutoTokenizer.from_pretrained(args.adapter)

    messages = [
        {"role": "system", "content": TRAINING_SYSTEM},
        {"role": "user", "content": EXAMPLE_USER},
    ]
    encoded = tokenizer.apply_chat_template(
        messages,
        return_tensors="pt",
        return_dict=True,
        add_generation_prompt=True,
    ).to(args.device)

    # do_sample=False (greedy) is used here for DETERMINISTIC EVAL — same
    # input always produces same output, useful for smoke tests and A/B
    # comparison.
    #
    # DO NOT use greedy in production. Field-test 2026-05-09 against the
    # Q8_0-served model showed that temperature=0 collapses multi-step
    # plans (e.g. "construyamos casa" emits only `scan_nearby`, dropping
    # the gather+build sequence). Production should use the Ollama
    # Modelfile defaults (temperature=0.2, top_p=0.9, min_p=0.05,
    # repeat_penalty=1.05) which are the regime the SFT eval matrix was
    # measured under. See docs/OLLAMA_USAGE.md.
    print("[gen] sampling (greedy — eval mode)...", file=sys.stderr)
    with torch.no_grad():
        out = model.generate(
            **encoded,
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
            temperature=1.0,
        )

    new_tokens = out[0][encoded["input_ids"].shape[1]:]
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)

    print()
    print("=" * 60)
    print("RAW OUTPUT")
    print("=" * 60)
    print(text)
    print()

    # Try to parse as JSON (strip optional <think> block)
    parse_text = text.strip()
    if parse_text.startswith("<think>"):
        end = parse_text.find("</think>")
        if end != -1:
            think = parse_text[len("<think>"):end].strip()
            print("[think]", think[:200], "..." if len(think) > 200 else "")
            parse_text = parse_text[end + len("</think>"):].strip()

    parsed = extract_json(parse_text)
    if parsed is None:
        print("[error] could not parse JSON from output", file=sys.stderr)
        return 1

    print("=" * 60)
    print("PARSED")
    print("=" * 60)
    print("keys present:", sorted(parsed.keys()))
    print("tools chosen:", [t.get("name") for t in parsed.get("tool_calls", [])])
    print("operational_risk:", parsed.get("operational_risk"))

    required = {"body_plan", "checks", "tool_calls", "failure_policy", "operational_risk"}
    missing = required - set(parsed.keys())
    if missing:
        print(f"[warn] missing required keys: {sorted(missing)}", file=sys.stderr)
        return 1

    print()
    print("[ok] contract satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
