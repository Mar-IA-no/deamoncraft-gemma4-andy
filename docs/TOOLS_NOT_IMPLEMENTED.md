# Tools que Gemma-Andy conoce pero el executor (`bot/server.js`) aún no implementa

Resumen práctico de cómo manejar la asimetría entre el dominio del modelo y el dominio del executor.

> Tag de referencia: `gemma-andy:e4b-v2-2-3-q8_0` (schema v2, 68 tools). El modelo conoce todo el dominio cuerpo de Mineflayer; el executor (típicamente un servidor Mineflayer wrapper) implementa un subset que se va expandiendo.

---

## El problema en una línea

**El modelo conoce 68 tools** del dominio cuerpo de Mineflayer (schema v2), pero `bot/server.js` implementa **43 hoy**. Las otras **25** son tools que entrenamos igual (porque retrain es caro y el modelo aprende mejor con cobertura completa) y que el executor incorpora cuando se pueda / cuando aparezca demanda real.

La pregunta es: **¿cómo evitar que el modelo emita un tool que el executor no puede ejecutar?**

---

## El principio: filtrar del lado del consumer, no del modelo

El modelo no sabe qué soporta el executor — no debería saberlo, porque eso cambia y el modelo no se reentrena cada vez. Quien sabe es el **consumer** (el embodied service o, en fallback, el tool en Hermes).

```
              ┌─────────────────────────────────────┐
              │  Registry de tools soportadas       │
              │  (vive en el consumer)              │
              │  - lista de tool names              │
              │  - se actualiza cuando agregás un   │
              │    endpoint nuevo a bot/server.js   │
              └──────────────┬──────────────────────┘
                             │
   intent ──▶ consumer ──filtra──▶ Gemma-Andy ──▶ tool_calls
                                                    │
                                                    └─▶ todos ejecutables
                                                        (porque allowed_tools
                                                        ya estaba filtrado)
```

El consumer:

1. Antes de cada llamada al modelo, **filtra `allowed_tools`** dejando solo las tools que el executor implementa hoy.
2. El modelo nunca ve en `allowed_tools` algo que el executor no puede ejecutar, así que **nunca lo emite** (fue entrenado a respetar `allowed_tools`).
3. Cuando agregás un endpoint nuevo a `bot/server.js`, **añadís el tool al registry**. Listo. El modelo no se toca.

---

## Cómo se implementa

### 1. La fuente de verdad: `tool_schema_v2.json`

Cada tool en el schema lleva un flag `executor_supported`:

```json
{
  "name": "goto",
  "category": "movement",
  "executor_supported": true,
  ...
}
{
  "name": "enchant_item",
  "category": "crafting",
  "executor_supported": false,
  "notes": "ENDPOINT NUEVO requerido en bot/server.js"
}
```

Vive en `schema/tool_schema_v2.json`. El consumer lo lee al arrancar (o lo cachea con TTL corto).

### 2. La función de filtro

```python
SCHEMA = json.load(open("tool_schema_v2.json"))

SUPPORTED = {
    t["name"] for t in SCHEMA["allowed_tools"]
    if t["executor_supported"]
}

def filter_supported(allowed_tools: list[str] | None) -> list[str]:
    """Intersect requested allowed_tools with what the executor supports today.

    If allowed_tools is None, returns the full supported set.
    """
    if allowed_tools is None:
        return sorted(SUPPORTED)
    return sorted(set(allowed_tools) & SUPPORTED)
```

### 3. Punto donde se invoca el filtro

En el embodied service v1, justo antes de componer el payload para Ollama:

```python
def handle_intent(req):
    # ... read world_state ...
    payload = {
        "high_level_command": req.intent,
        "world_state": world_state,
        "allowed_tools": filter_supported(req.allowed_tools),  # ← ACÁ
        "guardian_constraints": ...,
        "previous_error": req.previous_error,
    }
    # ... call Ollama ...
```

Eso es todo. El modelo ahora solo ve tools ejecutables.

---

## Cuando agregás un endpoint nuevo a `bot/server.js`

Workflow:

1. Implementás el endpoint nuevo. Ej: `POST /enchant` que toma `{slot, level}`.
2. Mapping en el dispatcher del consumer: agregás la entrada `enchant_item → /enchant`.
3. Editás `tool_schema_v2.json`: cambiás `executor_supported: false` → `true` para `enchant_item`.
4. Reiniciás el embodied service (o esperás a que se recargue el schema si tenés hot-reload).

El modelo no se toca. Nada se reentrena. La nueva capacidad aparece en el siguiente intent de Hermes.

---

## ¿Y si el modelo igual emite un tool no soportado?

No debería pasar — fue entrenado para respetar `allowed_tools`. Pero defensivo:

```python
def dispatch(call):
    if call.name not in SUPPORTED:
        return {
            "ok": False,
            "tool": call.name,
            "error_type": "tool_not_implemented",
            "details": f"Model emitted '{call.name}' but executor does not support it yet.",
        }
    return dispatch_to_bot_server(call.name, call.arguments)
```

Si esto se dispara, hay tres causas posibles:

1. **Bug en el filtro**: chequear `filter_supported` no esté ignorando algo.
2. **Modelo divergente**: el modelo emitió algo que no estaba en `allowed_tools`. Es señal de degradación; loggear el caso completo y abrir issue.
3. **Schema desactualizado**: el modelo conoce un tool que el schema marca como soportado pero el dispatcher no tiene el mapping. Agregar el mapping.

Cuando se devuelve `ok: false` con `error_type: "tool_not_implemented"`, el siguiente intent de Hermes traerá ese `previous_error` y el modelo replanifica. **No reintentar localmente** — Hermes decide si insistir, pedir clarification, o cambiar el plan.

---

## ¿Qué tools están sin implementar hoy?

Lista del schema v2 con `executor_supported: false` (25 tools). Cuando vos implementes alguna, flippeás el flag.

**Percepción** (3): `look_at`, `check_world_state`, `look_around`.

**Movimiento** (5): `mount`, `dismount`, `jump`, `sprint`, `swim_to`.

**Mining** (1): `dig_direction` (down/up/forward).

**Building** (4): `demolish_volume` (3D batch), `place_liquid`, `pickup_liquid`, `light_area`.

**Crafting** (2): `enchant_item`, `repair_item`.

**Inventory** (1): `unequip`.

**Consumibles** (2): `drink_potion`, `throw_projectile`.

**Combat** (1): `shoot_crossbow`.

**Farming** (4): `plant_crop`, `harvest_crop`, `breed_animals`, `collect_animal_product`.

**Villagers** (2): `view_villager_trades`, `trade_with_villager`.

Total: 25 (verificado contra `schema/tool_schema_v2.json`). Ninguna es bloqueante para los flujos comunes (gather wood, build, mine, craft, combat). Las que conviene priorizar son las que aparezcan más seguido en logs como `tool_not_implemented` cuando se empiece a operar — **el orden de prioridad lo decide el uso real, no la adivinanza a priori**.

---

## Lo que NO te pedimos

- **No tenés que implementar las 25 antes de poder usar v2.** Empezás con las que están y agregás progresivamente. El uso real va a indicar cuáles importan más.
- **No tenés que reentrenar el modelo cuando agregás una.** El modelo ya las conoce. Solo se prende el flag.
- **No tenés que mantener una whitelist hardcodeada en código.** El schema es la fuente de verdad. Si el código tiene una lista propia y diverge del schema, ese es el bug.

---

## Resumen en tres líneas

1. El modelo conoce 68 tools; vos implementás ~40 hoy y el resto cuando puedas.
2. El consumer filtra `allowed_tools` antes de cada call al modelo, usando el flag `executor_supported` del schema. El modelo nunca ve un tool no ejecutable.
3. Cuando implementás un endpoint nuevo, flippeás el flag en el schema y agregás el mapping en el dispatcher. Cero retrain.
