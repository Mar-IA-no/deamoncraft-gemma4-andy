# Gemma-Andy v2.2.3 — LoRA adapter

LoRA (Low-Rank Adaptation) fine-tune of `google/gemma-4-E4B-it` for the role of
**body orchestrator** in a Mineflayer-based Minecraft companion: receives a
JSON request describing intent + world state + allowed tools + constraints,
and returns a JSON plan with `body_plan`, `checks`, `tool_calls`,
`failure_policy`, and `operational_risk`.

This directory contains only the LoRA adapter weights and the tokenizer needed
to load it. The base model (~16 GB) must be downloaded separately from
Hugging Face.

---

## License and notices

- The **base model** `google/gemma-4-E4B-it` is governed by the
  **Gemma Terms of Use** (https://ai.google.dev/gemma/terms). This adapter is
  a derivative of Gemma 4. Anyone using or distributing it must accept and
  comply with those Terms, including the Gemma Prohibited Uses Policy.
- The **adapter weights**, training data (synthetic), and surrounding code in
  this repository are released under **Apache License 2.0** (see `LICENSE`
  at the repo root).
- This adapter is **based on Gemma**, as required by the Gemma Terms attribution
  clause.

---

## Files

| File | Size | Purpose |
|---|---|---|
| `adapter_model.safetensors` | ~67 MB | LoRA delta weights |
| `adapter_config.json` | 1 KB | PEFT LoRA config (target_modules, r, alpha, etc.) |
| `chat_template.jinja` | 17 KB | Gemma 4 chat template (must be used as-is) |
| `tokenizer.json` | 31 MB | Tokenizer vocabulary |
| `tokenizer_config.json` | 3 KB | Tokenizer settings (pad token, chat template ref) |

---

## Loading

### With PEFT (vanilla Hugging Face)

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE = "google/gemma-4-E4B-it"   # gated; accept Gemma Terms first
ADAPTER = "./adapter"            # this directory

model = AutoModelForCausalLM.from_pretrained(
    BASE,
    torch_dtype=torch.bfloat16,
    attn_implementation="eager",  # Gemma 4 gotcha (FA2 has issues)
    device_map="cuda",
)
model = PeftModel.from_pretrained(model, ADAPTER)
model.eval()

tokenizer = AutoTokenizer.from_pretrained(ADAPTER)
```

See `examples/eval_with_adapter.py` for a complete runnable example with
prompt construction and output parsing.

### With Ollama (production serving)

The adapter must first be merged into the base and converted to GGUF. See
[`ollama/build.sh`](../ollama/build.sh) for the full pipeline (~30 min wall
time on a workstation with the base model already downloaded).

---

## Training summary

| | |
|---|---|
| Base model | `google/gemma-4-E4B-it` (~16 GB safetensors, BF16) |
| Adapter type | LoRA (PEFT 0.19.1) |
| Rank `r` | 16 |
| Alpha | 32 |
| Dropout | 0.05 |
| Target modules | `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` |
| Excluded modules | `audio_tower`, `vision_tower`, `embed_vision`, `embed_audio` (Gemma 4 multimodal — text-only fine-tune) |
| Quantization (training) | NF4 4-bit base + BF16 LoRA (QLoRA) |
| Quantization (output) | none — adapter is full BF16 |
| Training data | ~33k synthetic SFT records (no PII, no copyrighted data); held-out eval ~7k |
| Schema | v2 (68 tools, see `schema/tool_schema_v2.json`) |
| Recipe | vanilla TRL 1.3.0 + transformers 5.7.0 + bitsandbytes 0.49.2 |
| Loss | `completion_only_loss=True` (system + user tokens masked) |
| Hardware | NVIDIA A30 24 GB |
| Wall time | ~10h 21min, 4125 steps total |
| Final train_loss | 0.09 |

---

## Critical: SYSTEM byte-exact rule

The adapter was trained with a single, fixed system prompt across all 5000+
SFT records. **Anything that serves this adapter must use that exact system
prompt verbatim** — including any framework wrapper, Modelfile, or HTTP
service. Mismatch produces silent behavioral drift: instructions added after
training are ignored, conditions removed are still respected by the model.

The literal training system prompt:

```
You are Gemma-Andy v2.1, the embodied-service body orchestrator for a Minecraft companion. You do not chat with players and you do not write code. You receive one JSON body-state request and return body orchestration only. Return valid JSON with body_plan, checks, tool_calls, failure_policy, and operational_risk. You may prepend a short <think>...</think> block only for medium/high/critical risk, real multi-step cases, previous_error recovery, or adverse world state.
```

For Ollama: the `SYSTEM` directive in [`ollama/Modelfile`](../ollama/Modelfile)
already contains this verbatim. Verify post-`ollama create` with:

```bash
ollama show <your-tag> --system
```

---

## Intended use

- **Body orchestration** for a Mineflayer-based Minecraft bot, called by a
  separate "narrative" / conversational agent (Hermes / similar). Translates
  high-level intent into safe tool calls.
- Output is **JSON only**, never prose. The model was trained to refuse out-of-scope
  requests (chitchat, code generation, etc.) by emitting
  `raise_guardian_event(category="out_of_scope")`.
- 14 hard-blocked tools (TNT placement, attacking players by default, opening
  other players' chests, etc.) — see `schema/tool_schema_v2.json` field
  `blocked_tools`.

---

## Out-of-scope use

- **Conversational chatbot**: this model does not chat. Use a different model
  for natural-language interaction with users.
- **Low-level VLA control** (keyboard/mouse): this model does not emit keypress
  sequences. Use a vision-language-action model for that.
- **General instruction-following**: heavily specialized for the Mineflayer
  body-orchestrator role. For general-purpose use cases, use the base
  Gemma 4 directly.

---

## Limitations

- Trained on synthetic data only — has not been validated against extensive
  real-world deployment yet. Use behind a Guardian validator (whitelist +
  schema check + policy gate) before execution.
- Adversarial robustness: held-out eval shows 73-87% compliance across
  attack buckets (prompt override, social engineering, claim ambiguity, etc.).
  This is good but not perfect; do not rely on the model alone for safety —
  always validate tool calls against an explicit policy before execution.
- Cross-version comparison with the v1 adapter (15-tool schema) is not
  apples-to-apples; v1 had higher raw "compliance" simply because it didn't
  know the additional tools that v2 might propose.

---

## See also

- [`docs/INTEGRATION_GUIDE.md`](../docs/INTEGRATION_GUIDE.md) — full I/O
  contract with examples per case (positive, clarification, refusal, recovery,
  out-of-scope).
- [`docs/OLLAMA_USAGE.md`](../docs/OLLAMA_USAGE.md) — quickstart with curl
  examples.
- [`docs/INTEGRATION_OPTIONS.md`](../docs/INTEGRATION_OPTIONS.md) — embodied
  service architecture decision.
- [`docs/TOOLS_NOT_IMPLEMENTED.md`](../docs/TOOLS_NOT_IMPLEMENTED.md) — how
  to filter `allowed_tools` against your executor's actual capabilities.
