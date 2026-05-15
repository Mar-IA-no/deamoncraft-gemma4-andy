# DaemonCraft / HermesCraft — Gemma-Andy

LoRA adapter and integration documentation for **Gemma-Andy**, a fine-tuned
[Gemma 4](https://ai.google.dev/gemma) E4B-it specialized as a
**body orchestrator** for a Mineflayer-based Minecraft companion agent.

> **What it does**: receives a JSON request with intent, world state, allowed
> tools, and constraints; returns a JSON plan with `body_plan`, `checks`,
> `tool_calls`, `failure_policy`, `operational_risk`. JSON only — never prose.
>
> **What it doesn't do**: chat with players, write code, plan narrative
> missions. Those belong to a separate "narrative" agent (Hermes /
> Pamplinas in the DaemonCraft architecture, or any conversational LLM in
> a similar setup).

---

## Quickstart

### 1. Get the base model

Gemma 4 E4B-it is gated on Hugging Face. Accept the
[Gemma Terms of Use](https://ai.google.dev/gemma/terms) and login:

```bash
huggingface-cli login
```

### 2. Load adapter + base via PEFT (Python)

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained(
    "google/gemma-4-E4B-it",
    torch_dtype=torch.bfloat16,
    attn_implementation="eager",
    device_map="cuda",
)
model = PeftModel.from_pretrained(base, "./adapter")
tokenizer = AutoTokenizer.from_pretrained("./adapter")
```

Full runnable example: [`examples/eval_with_adapter.py`](./examples/eval_with_adapter.py).

### 3. Or serve via Ollama

Build a quantized Q8_0 GGUF tag (~30 min on a workstation, requires
[`llama.cpp`](https://github.com/ggml-org/llama.cpp) built locally):

```bash
export HF_TOKEN=hf_...
export LLAMA_CPP_DIR=/opt/llama.cpp
./ollama/build.sh
```

Then call from any HTTP client. Example with `curl`:

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "gemma-andy:e4b-v2-2-3-q8_0",
  "stream": false,
  "messages": [
    {"role": "user", "content": "<your JSON request, see docs/OLLAMA_USAGE.md>"}
  ]
}'
```

Full Ollama usage with sample request and response in
[`docs/OLLAMA_USAGE.md`](./docs/OLLAMA_USAGE.md).

---

## Repository layout

```
.
├── README.md                    ← you are here
├── LICENSE                      ← Apache 2.0 (code, docs, data)
│
├── docs/                          ← integration documentation
│   ├── OLLAMA_USAGE.md            ← quickstart with end-to-end example
│   ├── INTEGRATION_GUIDE.md       ← full I/O contract, rules, examples by case
│   ├── INTEGRATION_OPTIONS.md     ← architecture (embodied service vs tool)
│   ├── TOOLS_NOT_IMPLEMENTED.md   ← filter allowed_tools by executor support
│   ├── INTENT_NORMALIZATION_V1.md ← Layer 1 contract: how to phrase intents
│   ├── HERMES_MITIGATION_V2.md    ← 5-layer policy upstream pattern
│   ├── MITIGATION_RESULTS.md      ← measured X/Y/Z reliability per primitive
│   └── RESULT_MANIFEST.md         ← reproducibility manifest (sha256 per result)
│
├── schema/                        ← source-of-truth tool definitions
│   ├── tool_schema_v2.json        ← 68 tools, current target
│   ├── tool_schema_v1.json        ← 15 tools, historical
│   └── guardian_policy.json       ← hard rules + autonomy levels
│
├── adapter/                       ← LoRA adapter (~100 MB)
│   ├── README.md                  ← model card
│   ├── adapter_model.safetensors
│   ├── adapter_config.json
│   ├── chat_template.jinja
│   ├── tokenizer.json
│   └── tokenizer_config.json
│
├── ollama/                        ← deployment via Ollama
│   ├── Modelfile                  ← template (edit FROM path before use)
│   └── build.sh                   ← end-to-end pipeline: merge → GGUF → tag
│
├── mitigation/                    ← Hermes-side policy reference implementation
│   ├── hermes_policy.py           ← 5-layer policy class (scope/ambiguity/normalize/narrow_tools/decompose)
│   ├── run_with_mitigation.py     ← primitives_lab runner with policy wrapper
│   ├── compare_results.py         ← pre/post comparison + outcome tripartite
│   ├── regrade_json.py            ← retroactive execution-aware reclassification
│   └── runner_patch.diff          ← patch to upstream primitives_lab runner.py
│
└── examples/
    └── eval_with_adapter.py       ← runnable example via PEFT (no Ollama)
```

---

## Mitigation reference implementation (new — 2026-05-15)

If you're integrating Gemma-Andy into a system that has an upstream LLM agent
(Hermes, GPT, Claude, etc.), the [`mitigation/`](./mitigation/) folder
provides a reference implementation of a **5-layer policy** that:

1. **Filters out** non-body intents (chat, jokes, abstract questions) before
   they reach Gemma-Andy.
2. **Asks for clarification** when intents are ambiguous, instead of letting
   the model guess.
3. **Decomposes** multi-step intents into atomic sub-intents that the model
   handles reliably.
4. **Normalizes** Spanish/narrative intents to English imperative inline
   form, the canonical SFT shape.
5. **Narrows the `allowed_tools`** per intent category, preventing the model
   from emitting incoherent tool chains.

Measured impact on 9 critical Tier 1 variants (45 calls, n=5 per variant):
**+13 pass-rate points vs unmitigated baseline, zero regressions**. See
[`docs/MITIGATION_RESULTS.md`](./docs/MITIGATION_RESULTS.md) and
[`docs/HERMES_MITIGATION_V2.md`](./docs/HERMES_MITIGATION_V2.md) for the full
methodology, layer-by-layer specification, and per-variant breakdown.

---

## What is Gemma-Andy in the wider architecture?

```
   ┌─────────────────────┐    intent +
   │ Hermes / narrative  │    world state
   │ agent (any LLM)     │ ─────────────────┐
   └─────────────────────┘                  ▼
                              ┌────────────────────────────┐
                              │ Gemma-Andy                 │
                              │ (this repo's adapter)      │
                              │ → JSON tool_calls          │
                              └────────────┬───────────────┘
                                           │
                                           ▼
                              ┌────────────────────────────┐
                              │ Guardian (validator)       │
                              │ schema + whitelist + policy│
                              └────────────┬───────────────┘
                                           │ allowed calls
                                           ▼
                              ┌────────────────────────────┐
                              │ Mineflayer / bot/server.js │
                              │ (executor in Minecraft)    │
                              └────────────────────────────┘
```

Gemma-Andy replaces a generic cloud LLM in the body-orchestration slot:
faster, cheaper, more reliable for structured tool calling, and runs locally
on consumer-grade GPU (a single RTX 3090 / 4090 with ~10 GB VRAM is enough).
The narrative agent (Hermes) keeps doing what it's good at: language and
mediation.

For the full architectural rationale, see
[`docs/INTEGRATION_OPTIONS.md`](./docs/INTEGRATION_OPTIONS.md).

---

## Hardware and runtime

| | |
|---|---|
| Base model size | ~16 GB safetensors (BF16) |
| Adapter size | ~67 MB safetensors (BF16, LoRA r=16) |
| Q8_0 GGUF (Ollama) | ~8 GB |
| GPU VRAM (Q8_0 served) | ~10-14 GB (with `num_ctx=131072`) |
| GPU VRAM (BF16 + adapter direct) | ~18-20 GB |
| Inference latency | ~100-500 ms per JSON response on RTX 3090 |

---

## License

- **Code, docs, data, and adapter weights**: Apache License 2.0 (see `LICENSE`).
- **Base model `google/gemma-4-E4B-it`**: governed by the
  [Gemma Terms of Use](https://ai.google.dev/gemma/terms) and the
  [Gemma Prohibited Uses Policy](https://ai.google.dev/gemma/prohibited_use_policy).
  This adapter is a derivative work of Gemma 4 — using or distributing it
  requires accepting and complying with those terms.

This adapter is **based on Gemma**, as required by the Gemma Terms attribution
clause.

---

## Contributing / questions

This is the public release of an internal project. For specific integration
questions, please open a GitHub issue. Pull requests for documentation
improvements, additional examples, and bug fixes are welcome.

The full SFT pipeline, training datasets, and operational logs live in a
separate (private) workspace and are not included here. This release contains
only what is needed to **use** the adapter.
