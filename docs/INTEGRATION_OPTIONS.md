# Integrando Gemma-Andy en DaemonCraft — decisión arquitectónica

Documento que describe **dónde encaja Gemma-Andy en una arquitectura Hermes ↔ Mineflayer**, y por qué se eligió **Path B (embodied service separado)** como camino canónico sobre Path A (tool dentro del agente narrativo).

> Tag de referencia: `gemma-andy:e4b-v2-2-3-q8_0` (E4B, schema v2 con 68 tools).
>
> Para empezar rápido, ver [`OLLAMA_USAGE.md`](./OLLAMA_USAGE.md) (carga del adapter + ejemplo end-to-end con request y response renderizados).
>
> Para los detalles del contrato del modelo (input/output schema, reglas, ejemplos), ver [`INTEGRATION_GUIDE.md`](./INTEGRATION_GUIDE.md). Este doc es la decisión arquitectónica.
>
> Para el manejo de tools que el modelo conoce pero el executor aún no expone, ver [`TOOLS_NOT_IMPLEMENTED.md`](./TOOLS_NOT_IMPLEMENTED.md).

---

## TL;DR — la decisión

**Vamos por embodied service (Path B), aunque el primer release arranque sin memoria ni capacidades adicionales sobre el patrón tool.**

Razón: es la decisión **ontológica** correcta para DaemonCraft. Un cuerpo con identidad propia, separable de Hermes, es el camino conceptual que queremos explorar — y aunque la primera versión sea funcionalmente equivalente a un tool stateless, arquitectónicamente queda en la dirección correcta. Los upgrades futuros (memoria, planes en curso, interruptibilidad, auto-iniciativa) caben sin refactor.

Path A (`mc_embodied_plan` como tool en Hermes) sigue documentado abajo como referencia y como fallback si Path B muestra problemas operativos no anticipados, pero **no es el camino canónico**.

---

## Estado actual del stack

- `bot/server.js` — expone API REST de Mineflayer.
- `agent_loop.py` — heartbeat injector (lee `/status`, `/nearby`, `/inventory`, `/plan` cada 30 s e inyecta en el gateway de Hermes).
- `minecraft_tools.py` — 13 tools high-level que el AIAgent de Hermes invoca (`mc_perceive`, `mc_move`, `mc_mine`, `mc_build`, etc.). Wrapper sobre `bot/server.js`.
- `gemma-andy:e4b-v2-2-3-q8_0` — servible en `http://<OLLAMA_HOST>:11434` vía Ollama. E4B + schema v2 (68 tools) + `<think>` selectivo. SYSTEM byte-exact con el training (regla crítica — ver [`INTEGRATION_GUIDE.md`](./INTEGRATION_GUIDE.md#1-no-mandar-system-prompt-propio)).

Hermes ya tiene world_state (vía heartbeat), ya tiene toolkit Mineflayer, ya funciona. El problema actual es que el LLM cloud de Hermes (Kimi/MiniMax) divaga y es caro cuando tiene que razonar planes embodied multi-step. Gemma-Andy local resuelve eso.

---

## Las dos opciones (descripción técnica)

### Opción B — Embodied service separado (canónica, vamos por acá)

Proceso nuevo (Node.js o Python) que:

```
- Expone POST /intent  ← Hermes manda intents acá
- Mantiene su propia Mineflayer session (o RPC con bot/server.js)
- Internamente: lee world_state propio + llama a Gemma-Andy + ejecuta + reporta
- Versión v1 (la primera): sin memoria entre intents, sin auto-iniciativa.
  Funcionalmente equivalente a A pero con la arquitectura ontológica correcta.
- Versión v2+: memoria de planes en curso, body.is_busy(),
  body.what_did_you_just_do(), body.estimate_time_to(target),
  interruptibilidad, auto-deferimiento ante peligro.
```

- **~300-500 líneas v1** (más cuando se agreguen capacidades) + deploy + monitoreo.
- **Proceso nuevo** a mantener.
- **Sin estado en v1**, con estado en v2+.

### Opción A — `mc_embodied_plan` como tool dentro de Hermes (no canónica, fallback)

Agregar a `agents/hermescraft/minecraft_tools.py` un tool más:

```
mc_embodied_plan(intent, autonomy_level=2, allowed_tools=None, previous_error=None)
  ↓
  1. Lee world_state vía endpoints existentes (/status, /nearby, /inventory)
  2. Compone JSON canónico v2
  3. Llama a Ollama (gemma-andy:e4b-v2-2-3-q8_0)
  4. Parsea response (con fallback de extracción)
  5. Despacha cada tool_call al mc_* correspondiente (mc_move, mc_mine, etc.)
  6. Devuelve {plan, execution_results} al AIAgent
```

- **~80 líneas de Python** en el toolkit existente.
- **Cero proceso nuevo**.
- **Stateless**: cada invocación es independiente.

Si Path B encuentra un blocker de implementación serio en sesión, Path A es el plan B operativo.

---

## Tabla comparativa (preservada para referencia)

| Aspecto | A — tool en Hermes | B — embodied service |
|---|---|---|
| Procesos nuevos | 0 | 1 |
| Líneas de código nuevas | ~80 | ~300-500 (v1) |
| Hermes le pega a Mineflayer | Indirecto (vía mc_* → bot/server.js) | Indirecto (vía intent → service → bot/server.js o Mineflayer propio) |
| Body con state propio | No (stateless wrapper) | **Eventualmente sí** (v2+ con memoria, planes en curso). v1 no tiene. |
| Hermes pregunta al body | No tiene sentido — no hay "body" como entidad | **Sí** (en v2+: `body.is_busy()`, `body.progress()`, etc.) |
| Body interrumpible | No directamente | **En v2+** (cancel + replanifica) |
| Body con auto-iniciativa | No | **En v2+** |
| Hermes lee Mineflayer directo (heartbeat) | Sí (sin cambios) | Sí (sin cambios) |
| Costo cloud (LLM Hermes) | Bajo — un solo tool call por intent | Bajo — un solo intent call por intent |
| Latencia caso simple | Baja | Algo mayor (un hop más) — aceptable para esta clase de uso |
| Complejidad de diseño v1 | Mínima | Real (intent shape, eventos de progreso, sincronización heartbeat) |
| Fine-tune v2 sirve | Sí (idéntico) | Sí (idéntico) |
| **Camino ontológico correcto** | No (wrapper apátrida) | **Sí** (cuerpo con identidad) |

---

## Por qué B aunque la v1 sea funcionalmente equivalente a A

Es una pregunta legítima: si la v1 del servicio no tiene memoria ni capacidades extra, ¿no es lo mismo que A pagando overhead?

Respuesta corta: **no, porque la arquitectura te encierra o te abre**.

Respuesta larga:

1. **Path A te encierra**. Si arrancás por A y después necesitás memoria del cuerpo (planes en curso, "seguí buscando madera"), terminás metiéndola en el contexto de Hermes — exactamente lo caro y divagante que estamos tratando de evitar. La salida del encierro es construir B, lo cual cuesta lo mismo que construirlo de entrada.

2. **Path B te deja crecer**. La v1 es funcionalmente plana, pero la unidad arquitectónica "cuerpo con HTTP propio" ya existe. Cuando aparece la primera necesidad de memoria, agregás un ringbuffer dentro del proceso. Cuando aparece interruptibilidad, agregás un cancel handler. Ningún cambio requiere mover código entre procesos.

3. **Es la decisión ontológica que el proyecto quiere explorar**. DaemonCraft no es solo un agente Minecraft — es un sistema de agentes con identidades distintas. Que el cuerpo sea un proceso con su propio loop, su propio HTTP, su propia (eventual) memoria, refuerza esa lectura. Hermes habla con un cuerpo, no con una función.

4. **El costo extra v1 es bajo**. ~300 líneas más que A. Un proceso más para deployar (systemd unit + nginx route si hace falta). Esto es manejable. La "trampa" de over-engineering aplicaba cuando la decisión era hipotética; ahora que está tomada, lo que aplica es disciplina de "v1 minimal, no meter capacidades sin necesidad operativa".

5. **Latencia un hop más es aceptable**. El uso de Gemma-Andy es para colapsar varias rondas del AIAgent en una. Un hop extra de localhost a localhost (Hermes → embodied service → Ollama) cuesta ~5 ms. Sigue siendo orden de magnitud más rápido que las múltiples rondas que reemplaza.

---

## Disciplina v1 (qué NO meter)

Para no caer en over-engineering, **la v1 del embodied service NO debe tener**:

- Memoria persistente entre intents (eso es v2).
- Queue de prioridades de intents (lo serializa: un intent a la vez, FIFO).
- Auto-iniciativa / detección autónoma de peligro (eso es v2).
- Cancelación limpia de planes en curso (en v1: cancel = matar y reportar; v2 hace cleanup).
- Estimación de progreso (`body.estimate_time_to`) — v2.
- Su propia Mineflayer session aparte de `bot/server.js` (v1: RPC al server existente; v2 puede tener session propia si hace falta).

**Lo que sí va en v1**:

- HTTP `POST /intent` con shape canónico (intent + autonomy + allowed_tools? + previous_error?).
- Lectura de world_state (vía `bot/server.js` por ahora).
- Llamada a Ollama (`gemma-andy:e4b-v2-q8_0`).
- Despacho de `tool_calls` a `bot/server.js`.
- Retorno sincrónico con `{plan, execution_results, ok}`.
- Logs estructurados.

Equivalente funcional a A. Pero proceso aparte.

---

## Qué necesitás para implementar B v1

### 1. Decisiones de implementación

- **Lenguaje**: Node.js (mismo ecosystem que `bot/server.js`, fácil compartir Mineflayer code) o Python (mismo ecosystem que Hermes/agent-bridge). Voto suave por **Node.js** dado que `bot/server.js` ya está en Node — menos contexto switching para vos. No es decisión cerrada.
- **Mineflayer session**: en v1, **no propia**. Hacer RPC al `bot/server.js` existente. En v2+ se puede evaluar session propia si hay razón operativa.
- **Puerto**: convención sugerida `7790`. Configurable.
- **Protocolo**: HTTP/REST por ahora. Podemos migrar a WebSocket/SSE en v2 si necesitamos eventos de progreso bidireccionales.

### 2. Shape del intent (request)

```jsonc
POST /intent
{
  "intent": "Help the player gather 12 oak logs before night.",
  "autonomy_level": 2,
  "allowed_tools": null,           // si null, el service usa su default safe set
  "guardian_constraints": {        // opcional, override del default
    "no_tnt": true,
    "no_protected_zone_edit": true,
    "protected_zone_owner": null
  },
  "previous_error": null,
  "deadline_seconds": 30,          // opcional, máx tiempo total
  "context_id": "uuid-..."         // para logging / correlación
}
```

### 3. Shape de la response

```jsonc
HTTP 200
{
  "ok": true,
  "context_id": "uuid-...",
  "plan": {
    "body_plan": [...],
    "checks": [...],
    "tool_calls": [...],
    "failure_policy": "...",
    "operational_risk": "low"
  },
  "execution_results": [
    {"tool": "scan_nearby", "ok": true, "data": {...}},
    {"tool": "mine_block", "ok": true, "data": {...}}
  ],
  "elapsed_seconds": 3.2
}
```

Si fallás antes de poder ejecutar, devolvé `ok: false` + `error`. Si fallás mid-execución, devolvé `ok: false` + `execution_results` parcial + `error` con `tool` y `error_type` para que Hermes pueda armar `previous_error` para el siguiente intent.

### 4. Loop interno del service v1

```python
def handle_intent(req):
    # 1. Read world state
    status    = api.get("/status").data
    nearby    = api.get("/nearby").data
    inventory = api.get("/inventory").data

    # 2. Compose canonical JSON (schema v2)
    payload = {
        "high_level_command": req.intent,
        "world_state": normalize_world_state(status, nearby, inventory),
        "allowed_tools": filter_supported(req.allowed_tools or default_allowed()),
        "guardian_constraints": req.guardian_constraints or default_constraints(),
        "previous_error": req.previous_error,
    }

    # 3. Call Gemma-Andy via Ollama (production candidate v2.2.3)
    plan = call_ollama(
        endpoint="http://<OLLAMA_HOST>:11434",
        model="gemma-andy:e4b-v2-2-3-q8_0",
        prompt=json.dumps(payload, sort_keys=True, ensure_ascii=True),
    )

    # 4. Parse <think>...</think> if present, then JSON
    plan_dict = parse_response(plan)  # see INTEGRATION_GUIDE.md

    # 5. Execute tool_calls in order, dispatching to bot/server.js
    results = []
    for call in plan_dict["tool_calls"]:
        result = dispatch_to_bot_server(call.name, call.arguments)
        results.append(result)
        if not result.ok:
            break  # fail fast; Hermes will resend with previous_error

    return {
        "ok": all(r.ok for r in results),
        "plan": plan_dict,
        "execution_results": results,
        ...
    }
```

`filter_supported` es la función clave para el manejo de tools no implementadas — ver [`TOOLS_NOT_IMPLEMENTED.md`](./TOOLS_NOT_IMPLEMENTED.md).

### 5. Cómo Hermes lo invoca

Desde el AIAgent de Hermes, el LLM cloud (Kimi/MiniMax) tiene un nuevo tool registrado en su toolkit:

```python
@register_tool
def embodied_plan(intent: str, autonomy_level: int = 2,
                  allowed_tools: list[str] | None = None) -> dict:
    """Delegate body work to Gemma-Andy via the embodied service."""
    response = requests.post(
        "http://localhost:7790/intent",
        json={"intent": intent, "autonomy_level": autonomy_level,
              "allowed_tools": allowed_tools},
        timeout=60,
    )
    return response.json()
```

Es un único tool de la perspectiva del LLM cloud. Lo que pasa adentro (HTTP a service → service compone payload → Ollama → service ejecuta) es transparente.

---

## Próximos pasos para implementar

El modelo está deployable vía Ollama (ver [`OLLAMA_USAGE.md`](./OLLAMA_USAGE.md)). El roadmap de integración:

1. **Decidir lenguaje** del embodied service v1 (Node.js sugerido por compartir ecosystem con `bot/server.js`).
2. **Implementar service v1** según las secciones de arriba, contra el tag `gemma-andy:e4b-v2-2-3-q8_0`.
3. **Pruebas end-to-end** (Hermes ↔ embodied service ↔ Gemma-Andy ↔ executor ↔ Mineflayer ↔ usuarios reales) con instrumentación / logging estructurado.
4. **Recoger señal de uso** (logs de qué tools fallaron, qué intenciones quedaron sin resolver, qué casos adversariales aparecieron en uso real).
5. **Iterar dataset / retrain / nuevos tools** solo informado por señal de uso real. Curados especulativos sin evidencia concreta tienden a no rendir.

Iteración a v2+ del service (memoria persistente, interruptibilidad, auto-iniciativa) sigue siendo agenda eventual, pero subordinada a que la v1 corra y produzca señal.

---

## Si después de operar B v1 vemos que es overkill

Difícil que pase, pero si la v1 corre por meses sin agregar nada y B v2 nunca se justifica, **sí podemos colapsar a A** (mover el código del service a un tool dentro de Hermes). El costo del churn es bajo — ~80 líneas reescritas, deployment más simple, pero perdemos la unidad arquitectónica. **Esa decisión la tomamos con evidencia, no a priori.**

Mientras tanto: B.
