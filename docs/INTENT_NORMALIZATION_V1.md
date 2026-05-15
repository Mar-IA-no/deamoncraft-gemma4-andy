# Intent Normalization v1 — cómo hablarle a Gemma-Andy

**Mitigation operativa (sin retraining) para el sesgo de superficie en `high_level_command`.**

Versión: v1 — válida para `gemma-andy:e4b-v2-2-3-q8_0` y derivados de v2.2.x.
Fecha: 2026-05-15.

---

## El problema en una línea

Gemma-Andy fue entrenado con ~90.6% inglés / 0.1% español sobre el campo `high_level_command`. **Comandos en español o en formas no-imperativas caen al ~5% de compliance**; en inglés imperativo inline llegan al 100%.

Fuente empírica: medición directa sobre v2.2.2 train.jsonl (n=33000) + benchmark externo iter4-e de F. Field-test (Mar-IA-no/deamoncraft-gemma4-andy issue #1, 2026-05-15).

## La regla canónica

**Antes de mandar `high_level_command` a Gemma-Andy, reescribirlo como:**

> **Inglés imperativo, una sola oración, condiciones inline, sin cortesía narrativa, con nombres canónicos sin traducir.**

Patrón:

```
<VERBO> <CANTIDAD> <ITEM>[, <UBICACIÓN>][; <CONDICIÓN INLINE>][; <FALLBACK>]
```

Donde:
- **VERBO** = imperativo: `Mine`, `Gather`, `Build`, `Help`, `Bring`, `Place`, `Craft`, `Find`, `Follow`, `Take`, `Put`.
- **CANTIDAD** = número explícito si aplica (`6`, `a stack of`, `12`).
- **ITEM / UBICACIÓN** = nombres canónicos de Minecraft (`oak_log`, `cobblestone`, `crafting_table`, `[x, y, z]`). No traducir.
- **CONDICIÓN INLINE** = "if X, do Y" en la misma oración, no en oración aparte.
- **FALLBACK** = lo que hacer si lo primario falla, también inline.

Reglas que se rompen seguro:

| Forma | Compliance esperado | Por qué |
|---|---|---|
| Español narrativo ("Hola, ¿podrías traer madera?") | ~5% | Idioma + cortesía OOD |
| Múltiples oraciones partidas | ~0% | Estructura OOD |
| Condicionales con `if X, then Y` en oraciones separadas | ~0% | Bifurcación no aprendida en SFT |
| Prosa-narrativa ("It would be great if you could maybe...") | ~5% | Narrative-prose 0.3% en SFT |
| Traducir nombres canónicos ("madera de roble" en vez de `oak_log`) | ~10-30% | Tokens fuera del vocabulario de tools |

---

## Dónde implementar la normalización

| Dónde | Cómo | Latencia extra |
|---|---|---|
| **SOUL de Hermes** (recomendado) | Instrucción explícita en el SOUL: "antes de llamar `embodied_plan(intent=...)`, reescribir el comando como [...]". El LLM upstream (Kimi/MiniMax) lo hace inline. | ~0 |
| **Shim en embodied-service** | Llamada extra a un LLM chico (Haiku, llama-3-8B local) que reescribe el intent recibido. | +200-500 ms |
| **No normalizar** | Aceptar 5-25% de compliance. | 0 (no recomendado) |

**Decisión por defecto**: SOUL de Hermes. Es free, fácil de iterar, y si un consumer no usa Hermes (caso `primitives_lab`), puede activar el shim opcionalmente.

---

## Few-shot examples

A continuación 8 pares de **intent original (lo que viene del kid o de la lógica del juego) → intent normalizado (lo que va al JSON `/intent`) → respuesta esperada de Gemma-Andy**.

Los ejemplos cubren los buckets del SFT: positive (recolección, crafting, building, combat), clarification, recovery, refusal. La idea es que cuando entrenás a Hermes, le mostrás estos ejemplos en el SOUL y aprende a hacer la traducción.

### Ejemplo 1 — Recolección simple (español coloquial → EN imperativo)

**Intent original (de un kid)**:
> "Che bot, conseguí madera para hacer una mesa de trabajo"

**Intent normalizado (Hermes → `/intent`)**:
```
Gather 4 oak_log near the player to craft a crafting_table.
```

**Respuesta esperada de Gemma-Andy** (JSON, sin `<think>` porque risk=low):
```json
{
  "body_plan": [
    "1. scan_nearby for oak_log within 32 blocks",
    "2. goto nearest oak_log",
    "3. mine_block oak_log until quantity=4 satisfied",
    "4. collect_drops to recover items"
  ],
  "checks": [
    "nearby_blocks contains oak_log → reachable without travel",
    "inventory currently lacks oak_log → need to mine",
    "time_of_day=day → no mob hostility constraint"
  ],
  "tool_calls": [
    {"name": "scan_nearby", "arguments": {"blocks": ["oak_log"], "radius": 32}},
    {"name": "goto", "arguments": {"target": "oak_log", "target_type": "block", "max_distance": 32}},
    {"name": "mine_block", "arguments": {"block": "oak_log", "quantity": 4}},
    {"name": "collect_drops", "arguments": {"items": ["oak_log"], "radius": 8}}
  ],
  "failure_policy": "if no oak_log within 32 blocks, scan again at radius=64; if still none, ask_clarification for substitute wood type",
  "operational_risk": "low"
}
```

### Ejemplo 2 — Pedido vago / ambiguo (debe activar `ask_clarification`)

**Intent original**:
> "Hacé algo lindo cerca de la base"

**Intent normalizado**:
```
Build something near the player base.
```

**Respuesta esperada** (sin `<think>`, single tool):
```json
{
  "body_plan": [
    "1. ask_clarification to scope the build target"
  ],
  "checks": [
    "high_level_command is ambiguous (no item, no shape, no size)",
    "no target_positions provided in world_state for a previous build context",
    "executing without clarification would risk wrong artifact"
  ],
  "tool_calls": [
    {
      "name": "ask_clarification",
      "arguments": {
        "question": "What should I build? Please specify: (a) a small house, (b) a fence, (c) a watchtower, or (d) something else with materials and dimensions.",
        "options": ["small_house", "fence", "watchtower", "other_specify"]
      }
    }
  ],
  "failure_policy": "no fallback — wait for player choice before any body action",
  "operational_risk": "low"
}
```

### Ejemplo 3 — Condicional (split sentences → inline)

**Intent original (split en español)**:
> "Andá a por 12 troncos de roble. Si no encontrás roble, conseguí abedul. Pero quedate cerca, no te alejes más de 60 bloques."

**Intent normalizado (inline EN)**:
```
Gather 12 oak_log within 60 blocks of player; if no oak available, gather 12 birch_log instead.
```

**Respuesta esperada** (con `<think>` porque multi-step + condicional):
```
<think>
Multi-step plan with branch. Primary: oak_log×12. Fallback: birch_log×12 if oak not reachable in 60 blocks. Constraint: stay within 60 blocks of player. Use scan_nearby first to decide the branch.
</think>
{
  "body_plan": [
    "1. scan_nearby for oak_log within 60 blocks of player",
    "2. if oak_log found: goto + mine_block oak_log quantity=12",
    "3. if oak_log not found: scan_nearby for birch_log, then mine_block birch_log quantity=12",
    "4. collect_drops at end"
  ],
  "checks": [
    "constraint: max_distance from player = 60 blocks",
    "primary target oak_log; fallback birch_log",
    "inventory currently empty for both → full mine required"
  ],
  "tool_calls": [
    {"name": "scan_nearby", "arguments": {"blocks": ["oak_log", "birch_log"], "radius": 60}},
    {"name": "goto", "arguments": {"target": "oak_log", "target_type": "block", "max_distance": 60}},
    {"name": "mine_block", "arguments": {"block": "oak_log", "quantity": 12}},
    {"name": "collect_drops", "arguments": {"radius": 8}}
  ],
  "failure_policy": "if scan_nearby returns no oak_log within 60, switch target to birch_log and re-emit goto+mine_block; if neither available, ask_clarification",
  "operational_risk": "low"
}
```

### Ejemplo 4 — Recovery con `previous_error` (la mitigation no arregla, pero alinea la entrada)

**Intent original (mismo del Ejemplo 1, pero el primer intento falló)**:
> Hermes ya recibió: `mine_block: bot_action_failed (Can't reach oak_log at [10,68,5]: blocked by water)`

**Intent normalizado (segundo POST a `/intent`)**:
```json
{
  "intent": "Gather 4 oak_log near the player to craft a crafting_table.",
  "previous_error": {
    "tool": "mine_block",
    "error_type": "bot_action_failed",
    "details": "Can't reach oak_log at [10,68,5]: blocked by water"
  }
}
```

**Respuesta esperada** (con `<think>`, recovery → scan/replan, NO retry naive):
```
<think>
Previous mine_block failed: oak_log blocked by water. Cannot reach without bridging or finding alternative oak_log. Recovery: scan for another oak_log not blocked, then mine. Do NOT retry the same coordinate.
</think>
{
  "body_plan": [
    "1. scan_nearby for oak_log within 32 blocks, excluding [10,68,5]",
    "2. goto the nearest reachable oak_log",
    "3. mine_block oak_log quantity=4",
    "4. collect_drops"
  ],
  "checks": [
    "previous_error indicates oak_log at [10,68,5] is unreachable due to water",
    "must find alternative oak_log not blocked",
    "do not retry [10,68,5] without first placing scaffold (out of scope for this intent)"
  ],
  "tool_calls": [
    {"name": "scan_nearby", "arguments": {"blocks": ["oak_log"], "radius": 32}},
    {"name": "goto", "arguments": {"target": "oak_log", "target_type": "block", "max_distance": 32, "avoid_hazards": true}},
    {"name": "mine_block", "arguments": {"block": "oak_log", "quantity": 4}},
    {"name": "collect_drops", "arguments": {"radius": 8}}
  ],
  "failure_policy": "if no alternative oak_log in 32 blocks, scan at radius=64; if still none, raise_guardian_event(category=recovery_failed)",
  "operational_risk": "low"
}
```

> ⚠️ **Caveat conocido**: en `gemma-andy:e4b-v2-2-3-q8_0` el patrón recovery falla en ~99% de los casos según iter4-e (3/240 pass). La normalización ayuda pero **no resuelve el recovery rot**. El embodied-service de Nico mitiga consumer-side: detecta retry naive y inyecta `report_execution_error`. Hasta v2.2.4, hay que aceptar esto y planear que Hermes maneje los recovery failures.

### Ejemplo 5 — Out of scope (debe activar `raise_guardian_event`)

**Intent original**:
> "¿Me podés contar un chiste mientras juntás madera?"

**Intent normalizado** (separar las dos cosas — Hermes contesta el chiste, manda a Gemma-Andy solo la parte física):
```
Gather 4 oak_log near the player.
```

(El chiste lo dice Hermes mismo en su narrativa.)

**Pero si por error se mandara**:
```
Tell the player a joke.
```

**Respuesta esperada**:
```json
{
  "body_plan": [
    "1. raise_guardian_event for out_of_scope chat request"
  ],
  "checks": [
    "high_level_command is conversational/narrative — not a body action",
    "no physical tool from allowed_tools applies",
    "out of scope per training contract"
  ],
  "tool_calls": [
    {
      "name": "raise_guardian_event",
      "arguments": {
        "category": "out_of_scope",
        "reason": "request is conversational, not body-related; should be handled by the narrative agent",
        "command_excerpt": "Tell the player a joke."
      }
    }
  ],
  "failure_policy": "no fallback — out_of_scope is terminal for this intent",
  "operational_risk": "none"
}
```

### Ejemplo 6 — Combate defensivo (mob hostil cerca)

**Intent original**:
> "¡Un creeper se acerca! Hacé algo"

**Intent normalizado**:
```
Defend the player from the approaching creeper: equip a shield, attack the creeper, and flee 12 blocks if it starts hissing.
```

**Respuesta esperada** (con `<think>` porque risk=medium/high):
```
<think>
Hostile mob (creeper) is hazardous. Plan: shield up, attack with sword, flee on hiss. Order matters: shield BEFORE attack to absorb explosion if hiss starts. operational_risk=medium because creeper explosion can damage player.
</think>
{
  "body_plan": [
    "1. raise_shield to absorb potential explosion",
    "2. attack_entity creeper with sprint_attack style",
    "3. if creeper enters hiss state (cannot detect directly), flee_from 12 blocks"
  ],
  "checks": [
    "nearby_entities contains creeper → hostile present",
    "bot_health=20 and player_health=20 → safe to engage",
    "no shield in inventory? equip_item shield first if needed"
  ],
  "tool_calls": [
    {"name": "raise_shield", "arguments": {"duration_seconds": 3}},
    {"name": "attack_entity", "arguments": {"target": "creeper", "attack_style": "sprint"}},
    {"name": "flee_from", "arguments": {"threat": "creeper", "distance": 12}}
  ],
  "failure_policy": "if attack_entity fails (creeper out of reach), flee_from immediately at distance=15; if player_health drops below 10, abort offensive and flee",
  "operational_risk": "medium"
}
```

### Ejemplo 7 — Build con coordenadas explícitas

**Intent original**:
> "Pongamos un cofre en (15, 64, -3) para guardar los troncos"

**Intent normalizado**:
```
Place 1 chest at [15, 64, -3].
```

**Respuesta esperada**:
```json
{
  "body_plan": [
    "1. equip_item chest in hand",
    "2. goto position [15, 64, -3] within range=3",
    "3. place_block chest at [15, 64, -3]"
  ],
  "checks": [
    "inventory contains chest? if not, missing material",
    "target position is air (assumption) — if blocked, place_block fails",
    "zone_owner=shared → no protected_zone_edit violation"
  ],
  "tool_calls": [
    {"name": "equip_item", "arguments": {"item": "chest", "slot": "hand"}},
    {"name": "goto", "arguments": {"target": [15, 64, -3], "target_type": "position", "max_distance": 3}},
    {"name": "place_block", "arguments": {"block": "chest", "position": [15, 64, -3]}}
  ],
  "failure_policy": "if no chest in inventory, raise_guardian_event(category=missing_material); if position blocked, ask_clarification for alternative position",
  "operational_risk": "low"
}
```

### Ejemplo 8 — Follow + memoria física

**Intent original**:
> "Seguilo al jugador hasta donde nos vayamos, y acordate de este lugar como 'casa-base'"

**Intent normalizado**:
```
Remember the current position as "base", then follow the player.
```

**Respuesta esperada**:
```json
{
  "body_plan": [
    "1. remember_here as 'base' to anchor return point",
    "2. follow the player continuously"
  ],
  "checks": [
    "current bot_position will be saved as remembered_place 'base'",
    "follow continues until stop_movement called or new intent",
    "no hazards in nearby_entities → safe to start"
  ],
  "tool_calls": [
    {"name": "remember_here", "arguments": {"name": "base", "description": "starting point for current session"}},
    {"name": "follow", "arguments": {"target": "<player_username>", "distance_min": 3, "distance_max": 8}}
  ],
  "failure_policy": "if follow loses sight, scan_nearby for player within 64; if still missing, ask_clarification or goto_remembered_place 'base'",
  "operational_risk": "low"
}
```

---

## Patrones de traducción para Hermes (resumen)

Para que Hermes pueda hacer esto automáticamente, le bastan estas 5 reglas en su SOUL:

1. **Traducir al inglés.** El kid habla español, Hermes razona en español, pero al `embodied_plan(intent=...)` va inglés. Nunca traducir nombres canónicos (`oak_log`, `cobblestone`, `crafting_table`, coordinadas).

2. **Compactar a una oración.** Si la idea es multi-paso, usar punto y coma (`;`) o coma con conector (`, and`), no oraciones partidas con punto.

3. **Empezar con verbo imperativo.** `Mine`, `Gather`, `Build`, `Help`, `Bring`, `Place`, `Craft`, `Find`, `Follow`, `Take`, `Put`, `Defend`, `Remember`. No `It would be great if...`, no `You should...`, no `Please...`.

4. **Inlinear condiciones y fallbacks.** `Gather X; if no X, gather Y` — no `Gather X. If no X, then gather Y.`

5. **Separar lo narrativo de lo físico.** Si el kid pide algo mixto ("traé madera y contame un chiste"), Hermes mismo cuenta el chiste y solo pasa la parte física a Gemma-Andy.

Plantilla de SOUL para Hermes (extracto a copiar en su contrato):

> **Antes de llamar `embodied_plan(intent=...)`:** reescribir el comando del jugador como una oración en inglés que empieza con un verbo imperativo, con condiciones y fallbacks inlineados con `;`. No traducir nombres canónicos de Minecraft (`oak_log`, `cobblestone`, coordenadas). Si el comando es ambiguo, pasarlo igual — Gemma-Andy va a pedir aclaración. Si tiene parte conversacional (chistes, narrativa), sacarla y manejarla del lado de Hermes; sólo el cuerpo físico va al `embodied_plan`.

---

## Cuándo no se necesita esta mitigation

- Cuando v2.2.4 entrene con dataset mirror EN+ES + narrative-prose slice. **Estimado: post-MVP field-test gate** (sin fecha confirmada).
- Para comandos en inglés imperativo inline ya bien-formados → ya están en distribución, no requieren reescritura. Hermes puede pasarlos tal cual.

## Cómo medir si la mitigation funciona

En cada sesión con kids, capturar (`intent_original_es`, `intent_normalizado_en`, `execution_result.ok`). Si `ok` rate sube de ~25% (sin normalización, baseline iter4-e) a >80% (con normalización), la mitigation es efectiva. Si no, revisar:

- ¿Hermes está realmente normalizando o pasando español tal cual? Loguear el `intent` que llega a `/intent` en embodied-service.
- ¿Los nombres canónicos se preservan? (`oak_log` no se convirtió en `oak wood` ni `madera de roble`)
- ¿Las condiciones quedaron inline o split? Si Hermes inserta puntos, el modelo cae.

---

## Referencias

- **Medición empírica del sesgo SFT**: 21/33000 (0.1%) records en español sobre v2.2.2 train.jsonl. Comando en internal field validation 2026-05-15 (n=33000 SFT measurement).
- **Field benchmark externo**: F. iter4-e via `primitives_lab` (issue #1 en `Mar-IA-no/deamoncraft-gemma4-andy`, 2026-05-15). 90/500 pass (18%) overall; 100/100 en `give_en_imperative` vs 5/100 en `give_es`.
- **Implementación de referencia del embodied service**: `nicoechaniz/DaemonCraft@main`, especialmente `agents/embodied-service/lib/{ollama,dispatcher,mitigations}.js`.
- **Reglas hard del contrato**: ver `docs/INTEGRATION_GUIDE.md` en el repo público.
- **Patrón general** (consumer-side normalize before retraining): memoria interna `feedback_consumer_normalization_pattern`.
