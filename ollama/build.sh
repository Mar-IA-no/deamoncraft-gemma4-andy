#!/usr/bin/env bash
# build.sh — Build a Gemma-Andy v2.2.3 Ollama tag from this repo
#
# Pipeline:
#   1. Download Gemma 4 E4B-it base from Hugging Face (gated; you must accept
#      the Gemma Terms of Use at https://ai.google.dev/gemma/terms first).
#   2. Merge the LoRA adapter from ./adapter/ into the base (PEFT
#      merge_and_unload). Output is a full safetensors checkpoint.
#   3. Convert HF → GGUF F16 via llama.cpp's convert_hf_to_gguf.py.
#   4. Quantize F16 → Q8_0 via llama.cpp's llama-quantize.
#   5. Generate Ollama Modelfile pointing at the Q8_0 GGUF.
#   6. ollama create the tag.
#
# Requirements:
#   - Python 3.11+ with: torch, transformers, peft, safetensors, huggingface_hub
#   - llama.cpp built locally (https://github.com/ggml-org/llama.cpp)
#   - ollama installed and running locally
#   - HF_TOKEN env var with access to gated google/gemma-4-E4B-it
#
# Usage:
#   export HF_TOKEN=hf_...
#   export LLAMA_CPP_DIR=/path/to/llama.cpp
#   ./ollama/build.sh
#
# Output:
#   - ./build/merged/                       (transient, ~16 GB — can be deleted)
#   - ./build/gemma-andy-v2-2-3-e4b-f16.gguf (transient, ~16 GB)
#   - ./build/gemma-andy-v2-2-3-e4b-q8_0.gguf (~8 GB, kept)
#   - ./build/Modelfile                      (final Modelfile used)
#   - Ollama tag: gemma-andy:e4b-v2-2-3-q8_0

set -euo pipefail

# --------------------------- config ---------------------------------------
BASE_HF_REPO="${BASE_HF_REPO:-google/gemma-4-E4B-it}"
ADAPTER_DIR="${ADAPTER_DIR:-./adapter}"
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-/opt/llama.cpp}"
BUILD_DIR="${BUILD_DIR:-./build}"
TAG="${TAG:-gemma-andy:e4b-v2-2-3-q8_0}"
OLLAMA_HOST_VAL="${OLLAMA_HOST:-127.0.0.1:11434}"

mkdir -p "${BUILD_DIR}"

# --------------------------- step 1: download base ------------------------
echo "[1/6] Downloading base model ${BASE_HF_REPO} (requires accepted Gemma Terms)..."
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('${BASE_HF_REPO}', local_dir='${BUILD_DIR}/base', token='${HF_TOKEN}')
"

# --------------------------- step 2: merge LoRA ---------------------------
echo "[2/6] Merging LoRA adapter into base..."
python3 - <<PYEOF
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
base = AutoModelForCausalLM.from_pretrained(
    "${BUILD_DIR}/base",
    torch_dtype=torch.bfloat16,
    attn_implementation="eager",
    device_map="cpu",
)
model = PeftModel.from_pretrained(base, "${ADAPTER_DIR}")
merged = model.merge_and_unload()
merged.save_pretrained("${BUILD_DIR}/merged", safe_serialization=True)
tok = AutoTokenizer.from_pretrained("${ADAPTER_DIR}")
tok.save_pretrained("${BUILD_DIR}/merged")
print("merged saved")
PYEOF

# --------------------------- step 3: HF -> GGUF F16 -----------------------
echo "[3/6] Converting HF -> GGUF F16..."
python3 "${LLAMA_CPP_DIR}/convert_hf_to_gguf.py" \
    "${BUILD_DIR}/merged" \
    --outfile "${BUILD_DIR}/gemma-andy-v2-2-3-e4b-f16.gguf" \
    --outtype bf16

# --------------------------- step 4: quantize F16 -> Q8_0 -----------------
echo "[4/6] Quantizing F16 -> Q8_0..."
"${LLAMA_CPP_DIR}/build/bin/llama-quantize" \
    "${BUILD_DIR}/gemma-andy-v2-2-3-e4b-f16.gguf" \
    "${BUILD_DIR}/gemma-andy-v2-2-3-e4b-q8_0.gguf" \
    Q8_0

# --------------------------- step 5: generate Modelfile -------------------
echo "[5/6] Generating Modelfile..."
ABS_GGUF="$(realpath "${BUILD_DIR}/gemma-andy-v2-2-3-e4b-q8_0.gguf")"
cat > "${BUILD_DIR}/Modelfile" <<MODELFILEEOF
FROM ${ABS_GGUF}

PARAMETER temperature 0.2
PARAMETER top_p 0.9
PARAMETER min_p 0.05
PARAMETER repeat_penalty 1.05
PARAMETER num_ctx 131072

# SYSTEM byte-exact with SFT training. DO NOT EDIT.
SYSTEM """You are Gemma-Andy v2.1, the embodied-service body orchestrator for a Minecraft companion. You do not chat with players and you do not write code. You receive one JSON body-state request and return body orchestration only. Return valid JSON with body_plan, checks, tool_calls, failure_policy, and operational_risk. You may prepend a short <think>...</think> block only for medium/high/critical risk, real multi-step cases, previous_error recovery, or adverse world state."""
MODELFILEEOF

# --------------------------- step 6: register in Ollama -------------------
echo "[6/6] Registering Ollama tag ${TAG} on host ${OLLAMA_HOST_VAL}..."
OLLAMA_HOST="${OLLAMA_HOST_VAL}" ollama create "${TAG}" -f "${BUILD_DIR}/Modelfile"

echo ""
echo "Done. Verify with:"
echo "  OLLAMA_HOST=${OLLAMA_HOST_VAL} ollama show ${TAG} --system"
echo ""
echo "The SYSTEM shown should match exactly the SYSTEM in this script."
