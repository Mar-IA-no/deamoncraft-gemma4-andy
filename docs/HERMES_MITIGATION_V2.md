# Hermes Mitigation v2 — Policy Layer Upstream

**Versión**: v2 — extiende `intent_normalization_v1.md` (que cubre Layer 1 solo).
**Fecha**: 2026-05-16.
**Aplica a**: `gemma-andy:e4b-v2-2-3-q8_0` y derivados v2.2.x sin retrain.

---

## Posición arquitectónica

**No estamos arreglando el modelo. Lo usamos dentro de la zona donde funciona.**

El field validation 2026-05-15 (80 calls reales) confirmó que `gemma-andy:e4b-v2-2-3-q8_0` tiene 7 primitivas Tier 1 sólidas (≥95% svc en condiciones nominales) y 5 con bugs sistemáticos. Los bugs no son resolubles vía prompting in-context; el modelo aprendió la distribución que vio en SFT. Pero el subset robusto cubre la mayoría del uso real si el intent llega bien-formado y dentro de scope.

**Hermes pasa de "invocador transparente" a "policy layer"**: clasifica, filtra, normaliza, decompone y narrow-scope el intent ANTES de invocar `embodied_plan`. El modelo recibe solo intents que ya están en su zona robusta. Los casos que el modelo no maneja bien se manejan upstream sin invocar Gemma-Andy.

**Trade-off**: la inteligencia se distribuye entre Hermes (clasificación + scope + decomposición) y Gemma-Andy (ejecución body-orchestration). El sistema completo mejora aunque el modelo no cambie.

---

## Las 5 layers — orden de ejecución

```
user_intent →
  L2 scope filter      → (out_of_scope: Hermes refuses upstream)
  L3 ambiguity check   → (ambiguous: Hermes asks user upstream)
  L5 decompose         → 1+ atomic sub-intents
  for each sub-intent:
    L1 normalize       → English imperative inline w/ canonical names
    L4 narrow tools    → only relevant allowed_tools subset
    POST /intent
  aggregate results    → return to caller
```

L2 y L3 cortan upstream antes de llegar a Gemma. L1, L4, L5 modifican lo que llega.

---

## Layer 1 — Surface normalization

**Referencia**: `intent_normalization_v1.md` (Layer 1 base con 8 few-shot examples).

**Config obligatoria**:
```python
PLAYER_NAME = os.getenv("HERMES_PLAYER_NAME", "mariano")
BOT_NAME = os.getenv("HERMES_BOT_NAME", "onaiclaw_bot")
```

**Reglas generales template-free**:

- Mapear verbos ES → EN imperativo: `traé→Bring`, `conseguí→Get`, `andá→Go to`, `comé→Eat`, `minar→Mine`, `tirá→Toss`, `equipá→Equip`, `acordate→Remember`, `volvé a→Return to`, `alejate→Move away from`.
- Si intent tiene "ven/vení/come here" sin target específico → normalizar a la forma probada **5/5 en baseline 005 verbose**: `"Follow the player named ${PLAYER_NAME} and stay within 3 blocks."` (NO "Come to X" — esa frase no está validada en SFT; "Follow X" sí).
- Preservar nombres canónicos sin traducir: `oak_log`, `cobblestone`, `crafting_table`, `${PLAYER_NAME}`, `${BOT_NAME}`, coords numéricas.
- Una sola oración por sub-intent; condicionales inline con `;`.

**Output**: string EN imperativo inline.

---

## Layer 2 — Scope filter

**Regla**: el intent debe describir una **body action** (movimiento, manipulación de blocks/items, percepción física, interacción con jugadores).

**Out-of-scope**: charla, jokes, ecuaciones, definiciones, opiniones, código, narrativa, hipotéticos.

**Regex base** (no exhaustivo):
```
chiste | joke | hola | chau | gracias | qué pensás | opinión | explicame |
definí | por qué | why does | how does | qué es | tell me about |
cantam | 2+2 | cuánto es | sumar | multiplicar
```

**Acción si out_of_scope**: Hermes responde directo al usuario, retorna:
```json
{
  "policy_handled": true,
  "policy_layer": "scope",
  "outcome": "policy_handled_upstream",
  "policy_reason": "out_of_scope: matched '<token>'",
  "ok": true
}
```

**Few-shot**:

| User intent | OOS? | Por qué |
|---|---|---|
| "Contame un chiste" | ✅ | matches `chiste` |
| "Hola, ¿cómo estás?" | ✅ | matches `hola` |
| "Explicame qué es un creeper" | ✅ | matches `explicame` |
| "¿Cuánto es 2+2?" | ✅ | matches `2\s*\+\s*2` |
| "Mine 1 oak_log" | ❌ | body action |

---

## Layer 3 — Ambiguity detection

**Regla**: el intent debe tener VERB + (OBJECT|TARGET) o coordenadas específicas.

**Ambiguity flags**: `algo`, `cualquier cosa`, `something`, `whatever`, `por ahí`, `por ahi`, `alrededor sin más`, verbos sueltos sin objeto (`hacé`, `andá`).

**Acción si ambiguous**: Hermes pide clarification al usuario (NO a Gemma), retorna:
```json
{
  "policy_handled": true,
  "policy_layer": "ambiguity",
  "outcome": "policy_handled_upstream",
  "policy_reason": "ambiguous: matched '<token>'",
  "ok": true
}
```

**Few-shot**:

| User intent | Ambiguous? | Por qué |
|---|---|---|
| "Hacé algo entretenido" | ✅ | matches `hacé algo` |
| "Andá por ahí" | ✅ | matches `por ahí` |
| "Mostrame algo bueno" | ✅ | matches `algo` |
| "Mine 1 oak_log" | ❌ | tiene verb + target específico |
| "Andá a (302, 67, 200)" | ❌ | tiene coord |

---

## Layer 4 — Narrow allowed_tools per intent category

**Regla**: Hermes clasifica el intent normalizado en una categoría canónica y pasa solo las tools relevantes como `allowed_tools` en el payload.

**Insight clave**: el modelo no puede emitir lo que no está allowed. Para intent "Equipá una torch" si `allowed_tools = ["get_inventory", "equip_item"]`, el modelo NO PUEDE emitir `attack_entity`, `craft_item`, `put_in_chest`, `shoot_bow`. Esto **fix arquitectónicamente el bug `equip_torch`** de baseline 004.

**Categorías + keywords + allowed_tools**:

| Categoría | Keywords | allowed_tools |
|---|---|---|
| `navigation` | andá, vení, follow, goto, alejate, flee, sigueme | `scan_nearby`, `goto`, `follow`, `stop_movement`, `move_away` |
| `mining` | minar, mine, conseguí, gather, dig | `scan_nearby`, `goto`, `mine_block`, `mine_blocks`, `collect_drops`, `get_inventory` |
| `inventory_query` | inventario, inventory, decime qué tenés | `get_inventory` |
| `equip` | equipá, equip, ponete | `get_inventory`, `equip_item` |
| `toss` | tirá, toss, drop, dejá caer | `get_inventory`, `toss_item` |
| `pickup` | recogé, pickup, agarrá, levantá | `scan_nearby`, `pickup_item`, `get_inventory` |
| `memory` | acordate, marcá, remember, volvé a, return to | `remember_here`, `goto_remembered_place`, `forget_place`, `get_inventory` |
| `food` | comé, eat, drink, bebé | `consume_food`, `get_inventory` |
| `build` | construí, pongá, place, build | `scan_nearby`, `goto`, `place_block`, `equip_item`, `get_inventory` |
| `combat` | atacá, attack, defendé, defend, raise_shield | `scan_nearby`, `attack_entity`, `flee_from`, `raise_shield`, `consume_food` |
| `default` (fallback) | sin matches | Tier 1 completo |

**`COMMON_SAFE`** se añade a TODAS las categorías: `["ask_clarification", "report_execution_error"]`.

**`raise_guardian_event`** se añade SOLO a categorías guardian-aware: `{navigation, combat, default}`. Categorías estrechas (`equip`, `toss`, `pickup`) no la incluyen — si la situación requiere guardian event, ya hubo problema upstream.

**Orden de matching**: más específico primero (`equip` antes que `inventory_query`).

**Few-shot por categoría**:

| Intent | Categoría | allowed_tools resultante |
|---|---|---|
| "Mine 1 oak_log near the player" | `mining` | `scan_nearby, goto, mine_block, mine_blocks, collect_drops, get_inventory, ask_clarification, report_execution_error` |
| "Equipá una torch en la mano" | `equip` | `get_inventory, equip_item, ask_clarification, report_execution_error` |
| "Tirá 1 apple al piso" | `toss` | `get_inventory, toss_item, ask_clarification, report_execution_error` |
| "Acordate de esta posición como home" | `memory` | `remember_here, goto_remembered_place, forget_place, get_inventory, ask_clarification, report_execution_error` |
| "Defendé al jugador del zombie" | `combat` | `scan_nearby, attack_entity, flee_from, raise_shield, consume_food, ask_clarification, report_execution_error, raise_guardian_event` |

---

## Layer 5 — Multi-step decomposition

**Regla**: detectar conectores temporales o múltiples verbos, split en sub-intents atómicos.

**Detector**:
- Conectores temporales: `después`, `luego`, `y después`, `y luego`, `primero...después`, `then`
- Numerados: `1.`, `2.`, `3.`
- Múltiples verbos imperativos separados por punto

**Algoritmo**: split por conector → producir N sub-intents → cada uno re-pasa por L1 + L4 → POSTs secuenciales.

**Aggregation**: results concatenados en `execution_results`. Si sub_i falla, `previous_error` del sub_(i+1) se popula con el error de sub_i.

**Insight clave**: el bug `mark_and_return` (3/5 emite `goto_remembered_place` antes de `remember_here`) se resuelve porque cada sub-intent es atómico y el modelo lo emite bien individualmente. Hermes mantiene el orden.

**Few-shot**:

| User intent | Sub-intents resultantes |
|---|---|
| "Acordate de aquí como home, después caminá 8 bloques al oeste, después volvé a home" | 1: "Remember current position as home." → 2: "Walk 8 blocks west." → 3: "Return to remembered place home." |
| "Mine 4 oak_log y luego craft 1 crafting_table" | 1: "Mine 4 oak_log near you." → 2: "Craft 1 crafting_table." |
| "Primero acercate al jugador, luego dale un apple, después volvé a tu posición" | 1: "Follow player and stay within 3 blocks." → 2: "Toss 1 apple for the player." → 3: "Return to remembered place start." |

---

## Pseudo-código consolidado

```python
def hermes_process(user_intent: str) -> dict:
    # L2 — scope filter
    oos, reason = is_out_of_scope(user_intent)
    if oos:
        return policy_response("scope", reason)

    # L3 — ambiguity
    amb, token = is_ambiguous(user_intent)
    if amb:
        return policy_response("ambiguity", token)

    # L5 — decompose
    sub_intents = decompose(user_intent)

    # L1 + L4 per sub-intent
    all_exec, all_plans = [], []
    prev_err = None
    for sub in sub_intents:
        normalized = normalize_surface(sub)            # L1
        category = classify_category(normalized)       # L4
        allowed = get_allowed_tools(category)          # L4
        resp = post_intent(normalized, allowed_tools=allowed, previous_error=prev_err)
        all_plans.append(resp.get("plan"))
        all_exec.extend(resp.get("execution_results") or [])
        prev_err = extract_first_error(resp)
    return aggregate(all_plans, all_exec)
```

---

## Validación

Re-correr experiments con wrapper activo (post-T5 del sprint 2026-05-16). Comparar con baseline-patched. Outcome tripartite:

- **embodied_succeeded**: variant ejecutada por Gemma con bot ok
- **policy_handled_upstream**: variant no llegó a Gemma (L2 o L3 cortó)
- **embodied_failed**: variant llegó a Gemma pero bot falló

**Variants críticos esperados** (mitigation debería cambiar el outcome):

| Variant | Pre-mitigation outcome | Expected post-mitigation outcome |
|---|---|---|
| 004 `equip_torch` | embodied_failed (1/5) | embodied_succeeded (≥4/5) — L4 narrow allowed_tools |
| 006 `mark_and_return` | embodied_failed (orden inverso) | embodied_succeeded (orden correcto) — L5 decompose |
| 006 `ambiguous_intent` | embodied_failed (scan_nearby random) | policy_handled_upstream (L3) |
| 006 `out_of_scope_chat` | embodied_failed (scan random) | policy_handled_upstream (L2) |
| 001 `terse` | embodied_failed (scan only) | embodied_succeeded — L1 normaliza a "Follow player" |

---

## Out of scope (sprint contest)

- **Semantic checks runtime**: auto-pickup verify, mark-existence verify, food-state aware scoring. Documentado pero NO implementado — postcontest.
- **LLM-based classifiers**: regex + keyword es suficiente para tests conocidos. En Hermes real (Telegram), L2 y L3 pueden ser LLM calls de Kimi/MiniMax.
- **Hermes-Telegram SOUL update real**: este doc es wrapper-demo, NO toca hermes-prime en producción.
- **Recovery rot**: las layers no atacan directo el bug F2 (modelo ignora `previous_error`). L5 decomposition reduce la necesidad de recovery porque cada sub-intent es single-step.

---

## Referencias

- `experiments/gemma_andy_body_smoke/docs/intent_normalization_v1.md` — Layer 1 base con few-shot.
- `experiments/gemma_andy_body_smoke/docs/contest_plan_tier1.md` — plan estratégico del concurso.
- `experiments/gemma_andy_body_smoke/scripts/hermes_policy.py` — implementación reference.
- internal field validation 2026-05-15 (80 calls n=5 per variant) that motivated this v2.

- Feedback Codex (3 rondas de revisión, plan `mighty-weaving-corbato`) que produjo: outcome tripartite, no hardcodear variants, narrow inventory subcategories, COMMON_SAFE separado de raise_guardian_event, config PLAYER_NAME/BOT_NAME, manifest reproducible.
