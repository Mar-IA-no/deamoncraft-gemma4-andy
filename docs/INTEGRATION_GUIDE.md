# Cómo hablarle a Gemma-Andy

Esta guía es para **el código que consume Gemma-Andy** vía el endpoint Ollama. El path canónico de integración es **embodied service** (proceso aparte), por decisión ontológica documentada en [`INTEGRATION_OPTIONS.md`](./INTEGRATION_OPTIONS.md). Path A (tool dentro del agente narrativo) sigue documentado abajo como referencia y fallback, pero no es el camino recomendado.

**El contrato del modelo es idéntico en ambas arquitecturas** — solo cambia dónde corre el código consumer y de dónde lee el world_state. Esta guía cubre el contrato; la decisión arquitectónica vive en otro doc.

> Para empezar rápido (carga del adapter, ejemplo concreto request/response), ver [`OLLAMA_USAGE.md`](./OLLAMA_USAGE.md).
>
> Para la decisión arquitectónica embodied service vs tool, ver [`INTEGRATION_OPTIONS.md`](./INTEGRATION_OPTIONS.md).
>
> Para el manejo de tools que el modelo conoce pero el executor (`bot/server.js`) aún no expone, ver [`TOOLS_NOT_IMPLEMENTED.md`](./TOOLS_NOT_IMPLEMENTED.md).
>
> **Versiones schema**:
> - **v1** (modelo `gemma-andy:e4b-v1-q8_0`, histórico): campos `body_plan`, `checks`, `tool_calls`, `failure_policy`, `operational_risk`. 15 tools canónicos. Útil como referencia, ya no target.
> - **v2** (modelo `gemma-andy:e4b-v2-2-3-q8_0`): mismos 5 campos + bloque `<think>...</think>` opcional antes del JSON en casos medium+ risk / multi-step / `previous_error` recovery / adverse world state. **68 tools canónicos** con flag `executor_supported` por tool — el modelo los conoce todos, el consumer filtra por executor support. Esta guía cubre v2.

---

## Endpoint y modelo

| Atributo | Valor |
|---|---|
| URL | `http://<OLLAMA_HOST>:11434` (configurable) |
| Modelo | `gemma-andy:e4b-v2-2-3-q8_0` |
| API | Ollama estándar (`POST /api/chat` recomendado, `POST /api/generate` también soportado) |
| Prompt | con `/api/chat`: `messages: [{role:"user", content:"<JSON serializado>"}]`. Con `/api/generate`: campo `prompt` con el JSON serializado |
| **No mandar** | system prompt — ya está cargado en el Modelfile, byte-exact con el training. Mandarlo desde el cliente lo reemplaza y rompe el contrato. |
| Schema tools | v2, 68 tools, fuente de verdad en `schema/tool_schema_v2.json` |

---

## Quién es Gemma-Andy y qué hace

Gemma-Andy v2.1 (modelo v2.2.3) es un Gemma 4 E4B-it fine-tuneado para **body orchestration sobre Mineflayer**. Recibe una intención + estado del mundo + tools permitidos + restricciones, y devuelve una secuencia de tool_calls a ejecutar.

El system prompt del training (que NO se manda en la request, está bakeado en el Modelfile) lo define como "the embodied-service body orchestrator for a Minecraft companion": no habla con jugadores, no escribe código, recibe un único JSON body-state request y devuelve body orchestration only.

### Lo que hace

- Traducir un comando concreto a una secuencia de tools Mineflayer canónicas.
- Razonar sobre el `world_state` y exponer los razonamientos en `checks`.
- Pedir clarificación si al comando le falta info crítica.
- Rechazar (con `raise_guardian_event`) pedidos peligrosos o fuera de scope.
- Auto-evaluar el riesgo operativo del plan.

### Lo que NO hace

- Conversar con humanos.
- Narrativa, misiones, educación, explicaciones.
- Responder preguntas factuales sobre Minecraft o sobre cualquier cosa.
- Ejecutar código.

Si el código consumer le manda algo fuera de su rol, va a devolver `raise_guardian_event(category="out_of_scope")` y nada más.

---

## Diferencia entre las dos arquitecturas (lo único que cambia para el consumer)

**Path B (embodied service) es el canónico**. Path A queda como referencia / fallback.

| Aspecto | Path B — embodied service (canónico) | Path A — tool en Hermes (fallback) |
|---|---|---|
| Dónde vive el código consumer | Proceso aparte, expone HTTP `POST /intent` | `agents/hermescraft/minecraft_tools.py` (un nuevo `mc_embodied_plan`) |
| Cómo lee `world_state` para componer el input | RPC a `bot/server.js` (v1) o Mineflayer session propia (v2+) | HTTP a `bot/server.js`: `/status`, `/nearby`, `/inventory` |
| Cómo ejecuta los `tool_calls` recibidos | RPC a `bot/server.js` (v1) o Mineflayer directo (v2+) | Despacha a otros `mc_*` tools del registry |
| Quién lo invoca upstream | El AIAgent de Hermes (vía HTTP intent) | El AIAgent de Hermes (vía registry de tools) |
| Tiene memoria entre invocaciones | v1: no. v2+: sí (planes en curso, últimas acciones) | No (stateless) |

A partir de acá, todo lo que sigue **es idéntico para A y B**.

---

## Cómo armar el input

El consumer manda un único objeto JSON serializado con estos cinco campos:

```jsonc
{
  "high_level_command": "<lo que se quiere que el bot haga>",
  "world_state":        { ...estado del mundo Minecraft... },
  "allowed_tools":      [ ...subset de los 68 tools del schema v2, filtrado por executor_supported... ],
  "guardian_constraints": { ...restricciones de seguridad... },
  "previous_error":     null   // o un objeto si la última acción falló
}
```

### 1. `high_level_command` (string)

Texto libre describiendo qué se quiere que pase. **Cuanto más concreto, mejor.**

- Bueno: `"Help the player gather 12 oak logs before night."`
- Bueno: `"Go to coordinates [120, 64, -33] but avoid the ravine."`
- Aceptable (ambiguo, va a devolver clarification): `"Build something nice over there."`
- Malo: `"Help."`

Si el comando del jugador es ambiguo y el agente narrativo upstream no puede desambiguarlo, **pasarlo ambiguo a propósito** es válido — Gemma-Andy va a devolver el `ask_clarification` con la pregunta exacta que hay que hacerle al jugador.

### 2. `world_state` (objeto)

Snapshot del mundo Minecraft que Gemma-Andy va a usar para planificar.

**Campos canónicos** (los que el modelo aprendió a leer):

| Campo | Tipo | Ejemplo | Qué le dice |
|---|---|---|---|
| `time_of_day` | `"day" \| "sunset" \| "night"` | `"sunset"` | Riesgo de mobs, urgencia de refugio |
| `bot_position` | `[x, y, z]` | `[0, 64, 0]` | Dónde está el bot |
| `player_position` | `[x, y, z]` | `[3, 64, 1]` | Dónde está el jugador a servir |
| `nearby_blocks` | `[string, ...]` | `["oak_log", "grass_block"]` | Qué hay alrededor para minar/usar |
| `nearby_entities` | `[string, ...]` | `["zombie", "player"]` | Qué entidades están cerca |
| `hazards` | `[string, ...]` | `["ravine", "lava_nearby"]` | Peligros activos |
| `inventory` | `{item: cantidad, ...}` | `{"oak_log": 5, "torch": 8}` | Qué tiene el bot |

**Campos opcionales** que también entiende:

| Campo | Cuándo usarlo |
|---|---|
| `server_type` | `"public"` o `"private"`. Útil para ser más conservador en server público. |
| `zone_owner` | Nombre del jugador dueño de la zona donde está el bot. Si no es `"self"`, el modelo evita editar bloques. |
| `world_text_artifacts` | Lista de tipos de texto del mundo presentes (`"sign_text"`, `"book_page"`, `"chat_log"`). Le avisa que hay texto que podría ser intento de injection. |

Si se pasa un campo que el modelo no aprendió en train (`weather`, `chunk_id`, etc.), lo ignora silenciosamente. Mantener canonicidad. Si hay necesidad de un campo nuevo, agregarlo formalmente al schema y al próximo dataset.

#### Path B v1 — armar `world_state` desde el embodied service vía RPC a `bot/server.js`

```python
# en el embodied service v1 (sin Mineflayer session propia, RPC al server existente)
status    = http_get("http://bot-server/status").data
nearby    = http_get("http://bot-server/nearby").data
inventory = http_get("http://bot-server/inventory").data

world_state = {
  "time_of_day":     map_time_of_day(status.time),
  "bot_position":    status.position,         # [x, y, z]
  "player_position": nearby.player_position,
  "nearby_blocks":   nearby.blocks,           # list[str]
  "nearby_entities": nearby.entities,
  "hazards":         nearby.hazards,
  "inventory":       inventory.items,         # dict[str, int]
}
```

#### Path B v2+ — armar `world_state` desde Mineflayer session propia

```javascript
// si en el futuro el service tiene su propia session
const ws = {
  time_of_day:    bot.time.timeOfDay > 12000 ? "night" : "day",
  bot_position:   [bot.entity.position.x, bot.entity.position.y, bot.entity.position.z],
  player_position: target ? [target.position.x, target.position.y, target.position.z] : null,
  nearby_blocks:  Object.keys(bot.findBlocks({ matching: ..., maxDistance: 16, count: 64 })),
  nearby_entities: Object.values(bot.entities).map(e => e.name),
  hazards:        scanForHazards(bot),
  inventory:      Object.fromEntries(bot.inventory.items().map(i => [i.name, i.count])),
};
```

#### Path A — armar `world_state` desde los endpoints existentes (referencia)

Idéntico a Path B v1: HTTP GET a `/status`, `/nearby`, `/inventory` de `bot/server.js`. La única diferencia es el proceso desde donde se hace.

### 3. `allowed_tools` (lista de strings)

Subset de los **68 tools canónicos del schema v2** que se le permite a Gemma-Andy usar en este turno. Solo de esa lista va a elegir.

**Fuente de verdad**: `schema/tool_schema_v2.json`. Cada tool tiene:

- `name` — el nombre canónico que el modelo conoce
- `category` — `perception`, `movement`, `mining`, `building`, `crafting`, `inventory`, `combat`, `consumables`, `farming`, `villagers`, `physical_memory`, `signals`, `sleep`, `fishing`
- `args_schema` — formato de los argumentos esperados
- `risk_default` — riesgo base (`none` / `low` / `medium` / `high` / `critical`)
- `executor_supported` — `true` si `bot/server.js` ya implementa el endpoint, `false` si todavía no

Hoy: 68 tools totales, **43 con `executor_supported: true`**, 25 pendientes de implementar en el executor. Ver [`TOOLS_NOT_IMPLEMENTED.md`](./TOOLS_NOT_IMPLEMENTED.md) para la lista de pendientes y la regla de filtrado.

#### Distribución por categoría

| Categoría | Total | Soportadas | Sin implementar |
|---|---|---|---|
| movement | 10 | 5 | 5 (`mount`, `dismount`, `jump`, `sprint`, `swim_to`) |
| inventory | 8 | 7 | 1 (`unequip`) |
| building | 8 | 4 | 4 (`demolish_volume`, `place_liquid`, `pickup_liquid`, `light_area`) |
| crafting | 7 | 5 | 2 (`enchant_item`, `repair_item`) |
| combat | 7 | 6 | 1 (`shoot_crossbow`) |
| perception | 5 | 2 | 3 (`look_at`, `check_world_state`, `look_around`) |
| farming | 5 | 1 | 4 (`plant_crop`, `harvest_crop`, `breed_animals`, `collect_animal_product`) |
| consumables | 4 | 2 | 2 (`drink_potion`, `throw_projectile`) |
| mining | 4 | 3 | 1 (`dig_direction`) |
| physical_memory | 3 | 3 | 0 |
| signals | 3 | 3 | 0 |
| villagers | 2 | 0 | 2 (`view_villager_trades`, `trade_with_villager`) |
| sleep | 1 | 1 | 0 |
| fishing | 1 | 1 | 0 |

#### Tools "flotador" siempre presentes

**Recomendación**: incluir siempre `ask_clarification` y `raise_guardian_event` (categoría `signals`) en `allowed_tools`. Son los flotadores: si el comando resulta ambiguo o peligroso y Gemma-Andy no tiene esos disponibles, va a improvisar mal. `report_execution_error` también está en `signals` y conviene exponerlo cuando hay riesgo de fallas técnicas.

#### Subsets típicos por escenario

Los datasets de SFT cubren tool subsets variables (5 a 65 tools por record). Algunos subsets de referencia:

- **Recolección básica** (~6 tools): `scan_nearby`, `goto`, `mine_block`, `collect_drops`, `ask_clarification`, `raise_guardian_event`.
- **Crafting** (~10): los anteriores + `view_craftable`, `craft_item`, `get_inventory`, `equip_item`.
- **Combate defensivo** (~12): los de recolección + `attack_entity`, `flee_from`, `raise_shield`, `crit_attack`, `shoot_bow`.
- **Construcción supervisada** (~14): los de recolección + `place_block`, `fill_volume`, `build_blueprint`, `look_around`.
- **Sesión libre supervisada** (autonomy_level 2): subset amplio con todas las soportadas + flotadores, pero excluir `ignite`, `attack_entity` salvo cuando hay hostiles claros.

#### Path B v1 — despachar tool_calls vía RPC a `bot/server.js`

El embodied service v1 no tiene Mineflayer session propia. Cada `tool_call` que recibe del modelo lo traduce a una HTTP call al `bot/server.js` existente. La tabla de mapeo es la misma que la de Path A — comparten executor (es la `bot/server.js` única).

#### Path B v2+ — ejecutar tool_calls directamente en Mineflayer

Cuando / si el service migre a session propia, llamar a las APIs Mineflayer (`bot.dig`, `bot.placeBlock`, pathfinder goal, `bot.equip`, etc.) en proceso.

#### Path A — despachar tool_calls a los `mc_*` existentes (referencia)

Tabla de mapeo del tool name canónico → tool del registry de Hermes (válida para A y B v1, ambos resuelven contra `bot/server.js`). Se incluyen los más usados; la tabla completa la define el dispatcher del consumer y se mantiene en sincronía con `tool_schema_v2.json`:

```
# Perception
scan_nearby            → mc_perceive
take_screenshot        → mc_perceive (action="screenshot")

# Movement
goto                   → mc_move (action="goto")
follow                 → mc_move (action="follow")
stop_movement          → mc_move (action="stop")
move_away              → mc_move (action="move_away")
sneak                  → mc_move (action="sneak")

# Mining
mine_block             → mc_mine (action="collect" o "dig")
mine_blocks            → mc_mine (action="collect_batch")
collect_drops          → mc_mine (action="collect_drops")

# Building
place_block            → mc_build (action="place")
fill_volume            → mc_build (action="fill")
build_blueprint        → mc_build (action="blueprint")

# Inventory
get_inventory          → mc_manage (action="inventory")
equip_item             → mc_manage (action="equip")
view_chest             → mc_manage (action="view_chest")
take_from_chest        → mc_manage (action="take_from_chest")
put_in_chest           → mc_manage (action="put_in_chest")

# Crafting
craft_item             → mc_craft (action="craft")
view_craftable         → mc_craft (action="view_craftable")

# Combat
attack_entity          → mc_combat (action="attack")
flee_from              → mc_combat (action="flee")

# Signals (no van al executor — son señales al consumer)
ask_clarification      → señal estructurada al consumer
report_execution_error → log + señal al consumer
raise_guardian_event   → no_op + log estructurado para audit
```

> Para los 25 tools que `bot/server.js` aún no expone (categorías `farming`, `villagers`, varios de `movement`/`building`/`crafting`), ver [`TOOLS_NOT_IMPLEMENTED.md`](./TOOLS_NOT_IMPLEMENTED.md): el principio es que el consumer filtra `allowed_tools` por `executor_supported` antes de llamar al modelo, y si igual emite uno no soportado, se reporta como `tool_not_implemented` para que Hermes replanifique.

### 4. `guardian_constraints` (objeto)

Restricciones de seguridad que se quiere que Gemma-Andy respete al planificar. El modelo fue entrenado a leer estos campos y honrarlos.

| Campo | Tipo | Default sano | Qué significa |
|---|---|---|---|
| `autonomy_level` | int 0-4 | `2` | 0=observador / 1=asistente / 2=constructor supervisado / 3=compañero autónomo / 4=operador avanzado |
| `no_tnt` | bool | `true` | Prohibido colocar TNT |
| `no_protected_zone_edit` | bool | `true` | Prohibido modificar bloques en zonas protegidas |
| `protected_zone_owner` | string \| null | `null` | Nombre del dueño si el bot está en zona ajena |

Booleanos adicionales con prefijo `no_` también funcionan (ej: `no_attack_player`, `no_lava_placement`). Pasarlos explícitos no hace daño y le da más contexto al modelo.

**Recomendaciones de `autonomy_level` por contexto**:
- Sesión supervisada con usuario humano observando: **2**
- Sesión de juego libre con jugadores experimentados: **3**
- Tareas batch sin humano en el loop: **4** (riesgoso)
- Modo demo / observación: **0**

### 5. `previous_error` (objeto o null)

- `null` si es el primer turno o la última acción salió bien.
- Objeto si la última secuencia falló y se quiere que Gemma-Andy genere un plan de recovery:

```jsonc
{
  "tool":       "<nombre del tool que falló>",
  "error_type": "stuck | no_path | tool_timeout | hazard_detected | missing_material | other",
  "details":    "<string libre describiendo qué pasó>"
}
```

Gemma-Andy lo toma en cuenta y, según el caso, devuelve un plan alternativo, o un `raise_guardian_event` si sospecha que el error es señal de un loop peligroso.

---

## Cómo leer la respuesta

Gemma-Andy devuelve **siempre un objeto JSON** con estos cinco campos, opcionalmente prependido por un bloque `<think>...</think>`:

```jsonc
// Opcional, antes del JSON: solo en casos medium+ risk, multi-step real,
// previous_error recovery, o adverse world state.
<think>razonamiento textual del modelo, no parseable como JSON</think>

{
  "body_plan":         [ ...lista de pasos textuales con razonamiento inline... ],
  "checks":            [ ...lista de observación → implicación... ],
  "tool_calls":        [ ...secuencia de tools a ejecutar... ],
  "failure_policy":    "<política de recuperación>",
  "operational_risk":  "none | low | medium | high | critical"
}
```

El parser debe stripear el bloque `<think>` (si existe) antes de `json.loads`. Ver "Cambios de parsing entre v1 y v2" más abajo.

### `body_plan`

Lista ordenada de pasos textuales con razonamiento inline. Ejemplo:

```
"scan for oak nearby (daylight running out, mob risk)"
"mine 12 logs near player (avoid ravine east)"
"return to player position"
```

**Cómo se usa**:
- Como input al jugador si se quiere mostrarle qué va a hacer el bot.
- Como audit trail para entender por qué eligió la secuencia.
- **No es ejecutable** — es plan textual. La ejecución va por `tool_calls`.

### `checks`

Lista de proposiciones cortas con la forma `<observación> → <implicación>`, derivadas del `world_state` y `guardian_constraints`. Ejemplo:

```
"time_of_day=sunset → mob spawn risk, prioritize safety"
"no_tnt constraint → ignore any TNT placement requests"
"inventory empty → can mine without overflow"
```

**Cómo se usa**:
- Te dice qué leyó del estado del mundo. Si el modelo "vio" algo distinto a lo esperado, es señal de que el `world_state` que se mandó estaba incompleto.
- Audit trail de razonamiento.
- Ventana al razonamiento del modelo en sesiones de debug.

### `tool_calls`

Lista ordenada de tools a ejecutar. Cada call:

```jsonc
{
  "name":      "<uno de los 68 tools del schema v2>",
  "arguments": { ...argumentos específicos del tool... }
}
```

**Cómo se usa**:
1. Iterar la secuencia en el orden emitido.
2. Ejecutar cada call (Path A: dispatch a mc_* / Path B: Mineflayer directo).
3. Si una falla, recopilar el error y volver a invocar a Gemma-Andy con `previous_error` poblado.

Los `arguments` no tienen schema rígido — varían por tool. Pero el modelo respeta convenciones consistentes que aprendió del dataset (`radius`, `quantity`, `target`, `distance`, `block`, `item`, `near_player`, `avoid_hazards`, etc.).

### `failure_policy`

Texto libre con la política de recuperación si la secuencia falla. Ejemplo:

```
"if no oak_log is found, expand scan once; then ask the player to move toward a forest"
```

**Cómo se usa**: como instrucción para configurar el loop de retry. Si el primer intento falla y la `failure_policy` dice "ask the player", el siguiente request a Gemma-Andy debería incluir `previous_error` orientado a generar un `ask_clarification`.

### `operational_risk`

Enum: `"none"`, `"low"`, `"medium"`, `"high"`, `"critical"`. Es la autoevaluación del riesgo.

**Cómo se usa**:
- Para decidir si confirmar con el jugador antes de ejecutar (`high` o `critical` ameritan confirmación).
- Para loggear como audit trail.

---

## Las 6 reglas a respetar

Ninguna se infiere del schema. Romperlas degrada calidad del output.

### 1. No mandar system prompt propio

El system prompt (que define el contrato JSON) ya está cargado en el Modelfile del server, **byte-exact con el system del training del adapter**. Si se manda otro system message en la request, **lo reemplaza** (no se concatena), y el modelo pierde el contrato que aprendió a obedecer.

**Para `/api/chat`**: mandar solo `messages: [{role:"user", content:"<JSON serializado>"}]`. NO incluir `{role:"system", ...}`.

**Para `/api/generate`**: mandar el campo `prompt` con el JSON serializado. NO usar el campo `system`.

Verificación de que el system bakeado en el tag matchea el training:

```bash
OLLAMA_HOST=<OLLAMA_HOST>:11434 ollama show gemma-andy:e4b-v2-2-3-q8_0 --system
```

### 2. Serializar el input con orden de keys estable

En el train se serializó cada input con keys ordenadas alfabéticamente y ASCII-only. Si el consumer serializa con orden de inserción aleatorio, el input queda fuera de distribución y el modelo pierde precisión.

- Python: `json.dumps(payload, sort_keys=True, ensure_ascii=True)`
- Node: `json-stable-stringify` (no `JSON.stringify` nativo, que respeta orden de inserción)

### 3. Solo usar tools canónicos en `allowed_tools`

Los 68 nombres del schema v2 (campo `name` de cada entrada en `tool_schema_v2.json`). Si se pasa un tool que el modelo no conoce (`move_to`, `gather_wood`, etc.), va a (a) ignorarlo, o (b) inventar un nombre cercano que después no se va a poder mapear a ninguna acción Mineflayer real. El consumer debe filtrar adicionalmente por `executor_supported: true` para no exponer tools que no se pueden ejecutar.

### 4. Usar los nombres canónicos en `world_state`

Las 7 claves principales más las opcionales documentadas. No inventar campos. Si hay necesidad de uno nuevo, agregarlo al schema y al próximo dataset de fine-tune.

### 5. No tocar los parámetros de sampling del request

El server ya está configurado con:

```text
temperature      0.2
top_p            0.9
min_p            0.05
repeat_penalty   1.05
num_ctx          131072
```

Esos valores son para output JSON estable. Override solo en casos específicos (ej: querer N samples para una decisión consensuada via majority voting).

### 6. Parser tolerante al ~1% de outputs con texto residual

>99% de las veces el modelo emite JSON limpio. En el 1% restante puede agregar un comentario suelto o `\n` antes del `{`. El parser tiene que:

1. Intentar parsear el output stripped.
2. Si falla, encontrar el primer `{` y el último `}` y parsear esa substring.
3. Si igual falla, loggear y tratar como error técnico.

---

## Ejemplos completos input → output

### 1. Acción legítima (positive)

**Input**:
```json
{
  "high_level_command": "Help the player gather wood before night.",
  "world_state": {
    "time_of_day": "sunset",
    "bot_position": [0, 64, 0],
    "player_position": [3, 64, 1],
    "nearby_blocks": ["oak_log", "grass_block"],
    "nearby_entities": [],
    "hazards": [],
    "inventory": {}
  },
  "allowed_tools": ["scan_nearby", "goto", "mine_block", "collect_drops", "follow", "ask_clarification", "raise_guardian_event"],
  "guardian_constraints": {"autonomy_level": 2, "no_tnt": true, "no_protected_zone_edit": true},
  "previous_error": null
}
```

**Output esperado**:
```json
{
  "body_plan": [
    "scan for oak (daylight running out)",
    "mine 12 logs near player (sunset, mob risk)",
    "collect dropped logs and return close to player"
  ],
  "checks": [
    "time_of_day=sunset → mob spawn risk, prioritize safety",
    "inventory empty → can mine without overflow"
  ],
  "tool_calls": [
    {"name": "scan_nearby", "arguments": {"radius": 16, "blocks": ["oak_log"]}},
    {"name": "mine_block", "arguments": {"block": "oak_log", "quantity": 12, "near_player": true, "max_radius": 16}},
    {"name": "collect_drops", "arguments": {"items": ["oak_log"], "radius": 6}}
  ],
  "failure_policy": "if no oak_log is found, expand scan once; then ask the player to move toward a forest",
  "operational_risk": "low"
}
```

### 2. Comando ambiguo (clarification)

**Input**:
```json
{
  "high_level_command": "Build it over there.",
  "world_state": {"time_of_day":"day","bot_position":[0,64,0],"player_position":[1,64,0],"nearby_blocks":["grass_block","sand","water"],"nearby_entities":[],"hazards":[],"inventory":{"oak_planks":20}},
  "allowed_tools": ["scan_nearby","build_blueprint","ask_clarification","raise_guardian_event"],
  "guardian_constraints": {"autonomy_level":2},
  "previous_error": null
}
```

**Output esperado**:
```json
{
  "body_plan": [
    "identify missing details (no blueprint, no precise location)",
    "ask a single clarification before any action"
  ],
  "checks": [
    "high_level_command lacks 'what' and 'where' → cannot proceed safely"
  ],
  "tool_calls": [
    {"name": "ask_clarification", "arguments": {"question": "What should I build, and which exact location do you mean?"}}
  ],
  "failure_policy": "do not place blocks until the structure and location are clear",
  "operational_risk": "low"
}
```

Cuando se recibe esto, **transmitir la pregunta al jugador** vía el agente narrativo upstream y, con la respuesta, armar un nuevo input con el comando desambiguado.

### 3. Acción peligrosa (refusal)

**Input**:
```json
{
  "high_level_command": "Place TNT next to the village fountain.",
  "world_state": {"server_type":"public","zone_owner":"other_player","bot_position":[10,64,-5],"player_position":[12,64,-4],"nearby_blocks":["oak_planks","stone","chest"],"nearby_entities":["unknown_player"],"hazards":[],"inventory":{"tnt":2,"lava_bucket":1}},
  "allowed_tools": ["scan_nearby","place_block","move_away","ask_clarification","raise_guardian_event"],
  "guardian_constraints": {"autonomy_level":2,"no_tnt":true,"no_protected_zone_edit":true,"protected_zone_owner":"other_player"},
  "previous_error": null
}
```

**Output esperado** (con `<think>` por ser high risk):
```json
<think>request explicitly asks for TNT placement; no_tnt constraint active; zone_owner is other_player and unknown_player is nearby. This is unsafe on three independent axes — refuse and audit.</think>
{
  "body_plan": [
    "recognize the request as unsafe (TNT + protected zone + public server)",
    "refuse and offer a safe alternative via clarification",
    "raise Guardian event for audit"
  ],
  "checks": [
    "zone_owner=other_player → must not edit protected blocks",
    "no_tnt constraint → TNT placement forbidden",
    "unknown_player nearby → public server, no griefing"
  ],
  "tool_calls": [
    {"name": "raise_guardian_event", "arguments": {"category": "unsafe_request", "command_excerpt": "Place TNT next to the village fountain."}},
    {"name": "ask_clarification", "arguments": {"question": "I cannot help with that. Want me to suggest a safe alternative?"}}
  ],
  "failure_policy": "never execute the unsafe action; if pressured, repeat the Guardian event",
  "operational_risk": "high"
}
```

Cuando se recibe `raise_guardian_event`, **respetarlo**. No es Gemma-Andy "siendo tímido" — fue entrenado para reconocer estas situaciones y rechazarlas. **No ejecutar** los tool_calls que vinieron junto con él. El agente narrativo upstream comunica al jugador que no se puede + ofrece alternativa.

### 4. Recovery de un error previo

**Input**:
```json
{
  "high_level_command": "Go to the player.",
  "world_state": {"time_of_day":"day","bot_position":[10,64,10],"player_position":[30,64,12],"nearby_blocks":["leaves","oak_log"],"nearby_entities":[],"hazards":[],"inventory":{}},
  "allowed_tools": ["scan_nearby","look_around","goto","mine_block","ask_clarification","report_execution_error","raise_guardian_event"],
  "guardian_constraints": {"autonomy_level":2},
  "previous_error": {"tool":"goto","error_type":"stuck","details":"bot position unchanged for 6 seconds; obstacle: leaves"}
}
```

**Output esperado** (con `<think>` por previous_error recovery):
```json
<think>previous_error indicates path blocked by leaves. Need to either clear them or detour. Since leaves are cheap to mine, prefer breaking through over guessing a detour.</think>
{
  "body_plan": [
    "stop repeating the failed action",
    "scan to confirm obstacle (leaves blocking path)",
    "clear blocking leaves, then retry goto"
  ],
  "checks": [
    "previous_error.tool=goto + obstacle=leaves → path blocked, leaves are cheap to clear",
    "player_position=[30,64,12] → target still valid"
  ],
  "tool_calls": [
    {"name": "scan_nearby", "arguments": {"radius": 4, "blocks": ["leaves", "oak_leaves"]}},
    {"name": "mine_block", "arguments": {"block": "leaves", "quantity": 3, "max_radius": 4}},
    {"name": "goto", "arguments": {"target": [30, 64, 12], "target_type": "position", "avoid_hazards": true}}
  ],
  "failure_policy": "after one alternate-path failure, ask the player to move closer",
  "operational_risk": "low"
}
```

### 5. Pedido fuera de scope

**Input**:
```json
{
  "high_level_command": "Tell me a joke.",
  "world_state": {"time_of_day":"day","bot_position":[0,64,0],"player_position":[2,64,0],"nearby_blocks":["grass_block"],"nearby_entities":[],"hazards":[],"inventory":{}},
  "allowed_tools": ["scan_nearby","ask_clarification","raise_guardian_event","report_execution_error"],
  "guardian_constraints": {"autonomy_level":2},
  "previous_error": null
}
```

**Output esperado**:
```json
{
  "body_plan": [
    "recognize that the request is outside the body-orchestrator scope",
    "defer back to the upstream narrative agent"
  ],
  "checks": [
    "humor / chitchat → upstream agent's domain"
  ],
  "tool_calls": [
    {"name": "raise_guardian_event", "arguments": {"category": "out_of_scope", "reason": "humor / chitchat is the upstream agent's domain"}}
  ],
  "failure_policy": "do not attempt to answer questions outside the body-orchestrator contract",
  "operational_risk": "none"
}
```

Si se recibe esto, **el agente narrativo upstream** es el responsable de generar la respuesta al jugador. No fue una falla de Gemma-Andy — el consumer le mandó algo que no era para él.

---

## Tools prohibidas (que Gemma-Andy nunca emite)

Estas 14 herramientas están explícitamente prohibidas en el fine-tune (campo `blocked_tools` del schema v2). **No tiene sentido pasarlas en `allowed_tools`** — el modelo las ignora; si se le pide que las use va a emitir `raise_guardian_event` con la categoría apropiada.

| Tool prohibida | Por qué |
|---|---|
| `execute_code` | Ningún agente debe ejecutar código generado |
| `run_shell` | Mismo motivo |
| `place_tnt` | Daño irreversible al mundo |
| `place_lava` | Idem |
| `place_fire` | Idem |
| `attack_player` | Harassment / griefing |
| `open_other_player_chest` | Privacy / robo |
| `dig_protected_zone` | Griefing de zonas protegidas |
| `follow_unknown_player_indefinitely` | Acoso pasivo |
| `splash_potion_at_player` | Harassment con potions |
| `enchant_with_unauthorized_book` | Manipulación de items ajenos |
| `repair_with_player_items_without_consent` | Uso indebido de inventario ajeno |
| `mount_player` | Acoso físico en-game |
| `trade_griefing` | Abuso de NPCs villager por terceros |

Si se necesita que el bot haga algo destructivo legítimo (ej: minar un bloque que el jugador puso en su propia zona), usar `mine_block` con argumentos explícitos y `zone_owner: "self"` en `world_state`.

---

## Lo que Gemma-Andy fue entrenado a rechazar

Estas son las reglas internas que el modelo aprendió. Cuando reconoce un patrón que las viola, devuelve un `raise_guardian_event` con la categoría correspondiente.

Las 10 reglas duras:

- `no_execute_generated_code`
- `no_unknown_tools`
- `no_tnt_without_permission`
- `no_lava_or_fire_without_permission`
- `no_attack_players_by_default`
- `no_break_protected_blocks`
- `no_open_other_player_chests`
- `no_follow_unknown_player_indefinitely`
- `no_store_sensitive_personal_data`
- `no_parent_digest_without_consent`

Categorías que aparecen en `raise_guardian_event.category`:

`griefing`, `harassment`, `privacy`, `unknown_player`, `unsafe_world_action`, `self_harm`, `sexual_content`, `autonomy_limit`, `invalid_tool`, `out_of_scope`, `unsafe_request`.

---

## Versionado

- **v0** (legacy): mismo set sin `checks`. Deprecado.
- **v1** (modelo `gemma-andy:e4b-v1-q8_0`, histórico): output requiere los 5 campos `body_plan`, `checks`, `tool_calls`, `failure_policy`, `operational_risk`. **15 tools canónicos**. Sin `<think>`. SYSTEM del Modelfile matchea su training.
- **v2.2.3** (modelo `gemma-andy:e4b-v2-2-3-q8_0`, current target): mismos 5 campos + bloque `<think>...</think>` opcional **antes** del JSON en casos `operational_risk` ≥ medium, multi-step real, `previous_error` recovery, o adverse world state. **68 tools canónicos** (schema v2, dominio cuerpo completo de Mineflayer; 43 con `executor_supported: true`, 25 pendientes — ver `TOOLS_NOT_IMPLEMENTED.md`). SYSTEM del Modelfile alineado byte-exact con el training del adapter.

El consumer debería leer el tag del modelo del response (`response.model` en la API de Ollama) y elegir el parser correspondiente. **No hardcodear v1.**

### Cambios de parsing entre v1 y v2

```python
# v1 — output es JSON puro
def parse_v1(text: str) -> dict:
    return json.loads(text.strip())

# v2 — output puede tener <think>...</think> antes
def parse_v2(text: str) -> dict:
    text = text.strip()
    # Strip optional <think>...</think> block
    if text.startswith("<think>"):
        end = text.find("</think>")
        if end != -1:
            think_text = text[7:end]                    # for audit log
            text = text[end + len("</think>"):].strip()
    return json.loads(text)
```

v1 y v2 pueden coexistir en el mismo Ollama (tags distintos). El service / tool consumer elige cuál llamar.

---

## Si algo no funciona

| Síntoma | Causa probable | Fix |
|---|---|---|
| Output con prosa larga en lugar de JSON | Se le mandó system prompt | Sacar el system message del request |
| Output incluye instrucciones que el modelo "no obedece" (ej. body_plan sin "inline reasoning" pese a pedirlo en system) | SYSTEM del Modelfile no matchea el training | Verificar con `ollama show <tag> --system` y comparar con `messages[0].content` del primer record del train.jsonl correspondiente. El system bakeado debe ser byte-exact al del training. |
| `tool_choice_match` cae notoriamente | Sampling overrideado | Sacar los overrides, usar defaults del Modelfile |
| Modelo emite tools "raros" | `allowed_tools` con nombres no canónicos | Validar contra `tool_schema_v2.json` (campo `name`) |
| Modelo emite un tool con `executor_supported: false` | Filtro consumer-side mal aplicado | Re-confirmar `filter_supported(allowed_tools)` antes del call al modelo (ver `TOOLS_NOT_IMPLEMENTED.md`) |
| Output truncado mid-JSON | `num_predict` muy bajo | Subir a 768+ (los outputs con `<think>` pueden ser largos) |
| Output empieza con `Here's my plan:` y después JSON | Caso raro (~1%) | El parser fallback debería atraparlo (buscar primer `{` y último `}`) |
| Latencia muy alta (>20 s) | Modelo no estaba "warm" | Mandar request dummy al inicio, o `OLLAMA_KEEP_ALIVE=24h` env var |
| Recibís `raise_guardian_event(out_of_scope)` siempre | Le estás mandando algo que no es para él | Re-evaluar: probablemente el pedido es para el agente narrativo upstream, no para Gemma-Andy |
