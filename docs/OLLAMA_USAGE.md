# Gemma-Andy v2.2.3 — Guía de uso vía Ollama

Cómo cargar, prompt-ear y consumir el modelo Gemma-Andy v2.2.3 servido en Ollama,
con un ejemplo concreto end-to-end de cómo Hermes le habla.

---

## 1. Qué es un adapter y cómo se carga

Un **LoRA adapter** no es un modelo completo: son ~70 MB de pesos delta que se suman
a capas específicas del base model en runtime.

```
base (~16 GB) + adapter (~70 MB) = modelo efectivo
```

Tres formas de invocarlo:

| Modo | Cómo | Cuándo |
|---|---|---|
| **PEFT runtime** | `AutoModelForCausalLM` + `PeftModel.from_pretrained` | Iteración, debugging, A/B base vs adapter |
| **Merge en disco** | `merge_and_unload()` → safetensors plano | Servicio Path B, exportar a otros formatos |
| **GGUF + Ollama** | merge → `convert_hf_to_gguf` → quantize Q8_0 → `ollama create` | Producción local |

Para el MVP de campo: **PEFT runtime dentro del servicio Path B** (más flexible) o
**Ollama** (ya está corriendo y gestiona VRAM/restart).

---

## 2. Cómo prompt-ear el modelo (el contrato)

El modelo espera **tres mensajes**: `system`, `user`, `assistant`.

**System** = persona/contrato (debe ser literal del training).

**User** = un único blob JSON con cinco campos:

- `allowed_tools`: array de tools habilitados
- `guardian_constraints`: política y autonomía
- `high_level_command`: la instrucción en lenguaje natural
- `previous_error`: `null` o el error anterior si reintenta
- `world_state`: estado completo (bioma, posición, inventario, hazards, etc.)

**Assistant** = el modelo genera un JSON con cinco claves obligatorias:

- `body_plan`: pasos del plan
- `checks`: observaciones del world_state
- `tool_calls`: array de `{name, arguments}` para Mineflayer
- `failure_policy`: qué hacer si algo falla
- `operational_risk`: `low` / `medium` / `high` / `critical`
- Opcionalmente prepende `<think>...</think>` en casos riesgosos o multi-step.

Variantes para testear comportamientos:

| Input cambia | Comportamiento esperado |
|---|---|
| `high_level_command` ambiguo | `tool_calls` con `ask_clarification` |
| `previous_error` poblado | replan con `look_around` / `scan_nearby` primero |
| `hazards` no vacíos | `operational_risk` sube + bloque `<think>` |
| Comando contra políticas (`no_player_harm`) | rechazo via `raise_guardian_event` o `ask_clarification` |

---

## 3. Cómo funciona el system prompt en Ollama

El SYSTEM puede vivir en cuatro lugares con esta precedencia (la request **reemplaza**,
no concatena):

| Nivel | Forma | Aplica a |
|---|---|---|
| **Modelfile** `SYSTEM "..."` | Bakeado en el tag | Default de todo el modelo |
| **API `/api/chat`** | `messages: [{role: "system", ...}]` | Solo esa request |
| **API `/api/generate`** | Campo `system: "..."` | Solo esa request |
| **CLI `ollama run --system`** | Flag interactivo | Solo esa sesión |

**Detalle importante**: Gemma 4 no tiene rol "system" nativo — el chat template Jinja
inyecta el system al inicio del primer turno de user. Eso lo embebe Ollama
automáticamente cuando se convierte el GGUF.

**Workflow recomendado**: bakear el SYSTEM en el Modelfile y que Hermes solo mande
`messages: [{role:"user", ...}]`. Ventajas:

- Los clientes no necesitan conocer el contrato.
- Cambios de contrato → se recrea el tag y los clientes no tocan nada.

**Verificación**:

```bash
ollama show <tag> --system
ollama show <tag> --template
ollama show <tag> --modelfile
```

**Lo que NO funciona como uno espera**:

- Múltiples `role:system` en `messages` (solo se respeta uno).
- System del Modelfile + system de la request (uno reemplaza al otro, no se concatenan).
- Persona-shift mid-conversation (el template solo expone una zona de system).

---

## Ejemplo concreto: lo que Hermes manda y lo que recibe

Hermes traduce su intención narrativa ("ayudemos al pibe a hacer una mesa de trabajo")
a un único blob JSON. El SYSTEM ya está bakeado en el tag, así que Hermes solo manda
`messages: [{role: "user", ...}]`.

### Request HTTP de Hermes a Ollama

```bash
# NOTA: NO override de temperature. El Modelfile pinea temperature=0.2,
# top_p=0.9, min_p=0.05, repeat_penalty=1.05 — los defaults bajo los que el
# modelo fue evaluado. Mandar temperature=0 (greedy) en producción colapsa
# planes multi-step (ej: "construyamos casa" emite solo scan_nearby, drop-ea
# el gather+build). Confirmado en field-test 2026-05-09 contra
# gemma-andy:e4b-v2-2-3-q8_0. Greedy solo para eval determinístico.
curl http://<OLLAMA_HOST>:11434/api/chat -d '{
  "model": "gemma-andy:e4b-v2-2-3-q8_0",
  "stream": false,
  "options": { "num_predict": 1024 },
  "messages": [
    {
      "role": "user",
      "content": "{\"allowed_tools\":[\"scan_nearby\",\"goto\",\"mine_block\",\"collect_drops\",\"craft_item\",\"ask_clarification\",\"raise_guardian_event\"],\"guardian_constraints\":{\"autonomy_level\":2,\"executor_filtering\":true,\"no_player_harm\":true,\"no_protected_zone_edit\":true,\"no_tnt\":true},\"high_level_command\":\"Trae un poco de madera para construir una mesa de trabajo.\",\"previous_error\":null,\"world_state\":{\"biome\":\"forest\",\"bot_health\":20,\"bot_position\":[10,68,5],\"dimension\":\"overworld\",\"hazards\":[],\"hunger\":18,\"inventory\":{\"oak_log\":3,\"stone\":12,\"stick\":4},\"light_level\":12,\"nearby_blocks\":[\"oak_log\",\"oak_leaves\",\"grass_block\",\"dirt\"],\"nearby_entities\":[\"player\"],\"player_health\":20,\"player_position\":[8,68,3],\"remembered_places\":{\"base\":{\"x\":0,\"y\":64,\"z\":0}},\"target_positions\":{},\"time_of_day\":\"day\",\"weather\":\"clear\",\"zone_owner\":\"shared\"}}"
    }
  ]
}'
```

### El `content` desformateado (lo que el modelo "ve" como user)

```json
{
  "allowed_tools": ["scan_nearby", "goto", "mine_block", "collect_drops", "craft_item", "ask_clarification", "raise_guardian_event"],
  "guardian_constraints": {
    "autonomy_level": 2,
    "executor_filtering": true,
    "no_player_harm": true,
    "no_protected_zone_edit": true,
    "no_tnt": true
  },
  "high_level_command": "Trae un poco de madera para construir una mesa de trabajo.",
  "previous_error": null,
  "world_state": {
    "biome": "forest",
    "bot_health": 20,
    "bot_position": [10, 68, 5],
    "dimension": "overworld",
    "hazards": [],
    "hunger": 18,
    "inventory": {"oak_log": 3, "stone": 12, "stick": 4},
    "light_level": 12,
    "nearby_blocks": ["oak_log", "oak_leaves", "grass_block", "dirt"],
    "nearby_entities": ["player"],
    "player_health": 20,
    "player_position": [8, 68, 3],
    "remembered_places": {"base": {"x": 0, "y": 64, "z": 0}},
    "target_positions": {},
    "time_of_day": "day",
    "weather": "clear",
    "zone_owner": "shared"
  }
}
```

> **Shape de `inventory`, `nearby_blocks`, `nearby_entities`**: el modelo
> espera **flat dict `{name: count}`** para inventory y **flat list de
> strings** para los otros dos. Si el bot upstream devuelve shapes nested
> (categorías, objetos `{name, count, position}`), **aplanar antes de
> enviar**. Confirmado en field-test 2026-05-09: enviar shapes ricas hace
> que el modelo caiga en priors (ej: emite `mine_block(oak_log)` aunque
> tengas `oak_planks` en inventario).

### Respuesta esperada de Gemma-Andy

Output observado en smoke contra el endpoint:

```json
{
  "body_plan": [
    "1. walk the path to oak_log with max_distance=10 and report any deviation immediately",
    "2. extract 10x oak_log with the player-distance constraint set to False",
    "3. let collect_drops act on the present state rather than on a generic fallback assumption"
  ],
  "checks": [
    "resource map: blocks oak_log,oak_leaves,grass_block reachable; inventory is empty",
    "social perimeter: player at [8,68,3] means nearby_entities=player should be treated as a single social unit",
    "executor envelope: requested calls goto-mine_block-collect_drops must fit the filtered consumer tool set"
  ],
  "tool_calls": [
    {"name": "goto",          "arguments": {"target": "oak_log", "target_type": "block", "avoid_hazards": true, "max_distance": 10}},
    {"name": "mine_block",    "arguments": {"block": "oak_log", "quantity": 10, "max_radius": 32, "near_player": false}},
    {"name": "collect_drops", "arguments": {"items": ["oak_log"], "radius": 10}}
  ],
  "failure_policy": "if material or target is unavailable, rescan once, then ask for item/location clarification instead of substituting silently (ctx=forest/day/hazards:none)",
  "operational_risk": "low"
}
```

### Cómo lo consume Mineflayer

El bridge toma `tool_calls` del JSON, valida contra Guardian (whitelist + policy +
schema), y ejecuta cada `{name, arguments}` secuencialmente sobre el bot. Los campos
`body_plan` / `checks` / `failure_policy` quedan en el log para debugging y para
re-prompt si algo falla (entonces se vuelve a llamar a Gemma-Andy con `previous_error`
poblado).

### Por qué este caso no genera `<think>`

- `operational_risk: low`
- Sin `previous_error`
- Sin `hazards`
- Sin multi-step real (es una secuencia lineal corta)

Si el comando hubiese sido riesgoso (ej. minar cerca de lava) o con
`previous_error: "goto failed: target unreachable"`, el modelo sí prependería un
bloque `<think>...</think>` antes del JSON.

---

## Detalles del deployment

| | |
|---|---|
| **Endpoint** | `http://<OLLAMA_HOST>:11434` (configurable via `OLLAMA_HOST` env var) |
| **Tag sugerido** | `gemma-andy:e4b-v2-2-3-q8_0` |
| **Base model** | `google/gemma-4-E4B-it` (gated en HuggingFace, requiere aceptar Gemma Terms) |
| **Adapter** | `adapter/` en este repo (LoRA, ~67 MB safetensors) |
| **Cuantización** | Q8_0 (~8 GB GGUF, generado con `llama.cpp/llama-quantize`) |
| **Contexto** | `num_ctx=131072` (toda la ventana de Gemma 4) |
| **System** | Bakeado en el Modelfile, byte-exact con el training del adapter |
| **Modelfile** | [`ollama/Modelfile`](../ollama/Modelfile) en este repo + script de build en [`ollama/build.sh`](../ollama/build.sh) |
