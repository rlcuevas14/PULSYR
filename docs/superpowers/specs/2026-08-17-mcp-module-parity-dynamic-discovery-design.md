# Spec hijo 3 — Paridad MCP por módulo y discovery dinámico

**Fecha**: 2026-08-17
**Estado**: aprobado; pendiente de implementación secuencial
**Spec padre**: [Arquitectura de navegación, módulos y paridad MCP](2026-08-17-agent-native-navigation-modules-mcp-program-design.md)
**Dependencia**: servicio autoritativo del [spec de capacidades por proyecto](2026-08-17-project-module-capabilities-design.md)

## 1. Objetivo

Hacer que Pulsyr sea completamente operable por agentes sin fragmentar su transporte:

- un único endpoint MCP y un token por proyecto;
- familias de tools/resources/prompts asociadas a módulos;
- discovery que refleja las capacidades efectivas del proyecto;
- enforcement en cada ejecución;
- paridad entre las operaciones relevantes de UI y las disponibles al agente;
- compatibilidad con las 26 tools ya publicadas.

“Cada módulo tiene endpoint MCP” se interpreta aquí como **cada módulo tiene una superficie MCP completa y descubrible**, no como una URL distinta. Separar URLs o tokens aumentaría configuración, rotación de secretos y riesgo de scope cruzado sin aportar aislamiento adicional.

## 2. Estado actual

- Transporte: `POST /mcp`, JSON-RPC request/response, sin SSE.
- Auth: bearer token con `project_id` y scope `read|write`.
- Registry: `TOOLS` estático en `app/mcp/server.py`.
- Discovery: `tools/list` devuelve todas las tools registradas.
- Ejecución: `tools/call` valida existencia, scope write y `token.project_id`.
- Catálogo: 26 tools repartidas entre core/backlog, Hilos, Incidentes y Gestión.
- Prompts: `briefing` y `decision`.
- Resources: templates de área y grafo.

El transporte y el failsafe de proyecto son una buena base. La brecha no es crear más endpoints, sino declarar ownership de módulo, filtrar discovery y completar operaciones que ya existen en UI/servicios.

## 3. Decisiones de contrato

1. `POST /mcp` permanece como URL única.
2. Los bearer tokens existentes siguen siendo válidos; no se regeneran al cambiar módulos.
3. Cada tool, prompt y resource declara un `module` de catálogo cerrado.
4. `tools/list` filtra por módulos efectivos.
5. `tools/call` revalida el módulo inmediatamente antes del handler.
6. `pulsyr_capabilities` y `pulsyr://capabilities` pertenecen a `core` y siempre están disponibles.
7. Los nombres y argumentos requeridos de las 26 tools actuales no cambian.
8. Toda tool nueva reutiliza servicios compartidos; si hoy la UI muta ORM directamente, primero se extrae el servicio.
9. Los errores de tool mantienen respuesta JSON-RPC exitosa con `isError=true`; no se convierten en HTTP 500.
10. Las tools destructivas o con red externa se anotan y documentan explícitamente.

## 4. Catálogo modular

### 4.1 Metadata de tool

El descriptor actual se amplía conceptualmente:

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable
    module: Literal["core", "threads", "incidents", "management"]
    write: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False
```

`tools/list` serializa `annotations` MCP a partir de esa metadata:

```json
{
  "readOnlyHint": false,
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": false
}
```

Las annotations ayudan al cliente, pero no sustituyen autorización, validación ni confirmaciones que el propio cliente aplique.

### 4.2 Asignación de las 26 tools existentes

| Módulo | Tools existentes |
|---|---|
| `core` | `pulsyr_context`, `pulsyr_search`, `pulsyr_list`, `pulsyr_areas`, `pulsyr_move_area`, `pulsyr_create`, `pulsyr_advance`, `pulsyr_complete`, `pulsyr_link` |
| `threads` | `pulsyr_thread_create`, `pulsyr_thread_advance`, `pulsyr_thread_list`, `pulsyr_thread`, `pulsyr_thread_link` |
| `incidents` | `pulsyr_incidents`, `pulsyr_incident`, `pulsyr_incident_resolve` |
| `management` | `pulsyr_doc_list`, `pulsyr_doc_get`, `pulsyr_doc_put`, `pulsyr_pending_list`, `pulsyr_pending_upsert`, `pulsyr_pending_complete`, `pulsyr_gantt_get`, `pulsyr_gantt_task_upsert`, `pulsyr_gantt_task_remove` |

`pulsyr_context` permanece core, pero su respuesta se vuelve module-aware: no consulta ni incluye Hilos o Incidentes cuando esos módulos no están efectivos.

## 5. Discovery y enforcement

### 5.1 Flujo

```text
Bearer token
    │
    ├─ validar token y token.project_id
    │
    ├─ cargar enabled_modules(project_id)
    │
    ├─ tools/list ─────► filtrar catálogo por module
    │
    └─ tools/call
          ├─ resolver tool
          ├─ require_module(project_id, tool.module)
          ├─ validar scope read/write
          ├─ validar input
          └─ handler compartido + commit/rollback
```

La comprobación de módulo ocurre también si el cliente conserva en caché una tool que acaba de ser deshabilitada.

### 5.2 Orden de errores

Para no filtrar información:

1. token inválido: HTTP de autenticación actual;
2. token sin proyecto: error actual de asignación;
3. tool desconocida: `unknown_tool`;
4. módulo deshabilitado: `module_disabled`;
5. scope insuficiente: `write_scope_required`;
6. input/entidad/transición: error de dominio estable.

No se resuelve ningún item, hilo, incidente o documento antes de comprobar el módulo.

## 6. Capabilities

### 6.1 Tool `pulsyr_capabilities`

Tool core, read-only, sin argumentos:

```json
{
  "schema_version": 1,
  "project": {"id": "...", "name": "..."},
  "transport": {"endpoint": "/mcp", "token_scope": "write"},
  "modules": {
    "core": {"configured": true, "entitled": true, "effective": true},
    "threads": {"configured": false, "entitled": true, "effective": false},
    "incidents": {"configured": true, "entitled": true, "effective": true},
    "management": {"configured": false, "entitled": true, "effective": false}
  }
}
```

No devuelve token, secretos, connection strings ni credenciales Sentry/GitHub. Puede incluir los nombres de módulos deshabilitados para que el agente explique la limitación sin asumir que la tool no existe en Pulsyr.

### 6.2 Resource `pulsyr://capabilities`

Devuelve el mismo payload como `application/json`. Existe para clientes que prefieren resources; la tool garantiza compatibilidad con clientes que solo manejan tools.

### 6.3 `initialize`

Las instrucciones se generan con los módulos efectivos:

- comenzar con `pulsyr_context`;
- usar `pulsyr_complete` al cerrar trabajo;
- indicar qué familias opcionales están activas;
- recomendar `pulsyr_capabilities` si el cliente necesita explicar ausencias.

No se incrusta el catálogo entero en `initialize`.

## 7. Paridad funcional prioritaria

La paridad se define por **capacidad de dominio**, no por replicar cada botón o helper de UI. Por ejemplo, `elaborate-stage` llama a otro LLM para ayudar a un humano; un cliente MCP ya es el agente y puede leer el Hilo, crear el artefacto y avanzar sin invocar un segundo modelo.

### 7.1 P0 — Cerrar operaciones de trabajo cotidianas

#### Core / Backlog / Prioridad / Archivo

| Tool nueva | Modo | Contrato mínimo | Servicio/garantía |
|---|---|---|---|
| `pulsyr_item_get` | read | `item_id` o `query`; detalle, eventos, comentarios, enrichments y grafo opcional | misma carga aislada que REST `GET /items/{id}` |
| `pulsyr_item_update` | write | referencia + campos presentes: `title`, `summary`, `priority`, `impact_ai`, `effort_ai`, `stale_risk`, `agent_ready` | servicio nuevo; prioridad usa `set_priority`; evento `item_updated` |
| `pulsyr_discard` | write/destructive | referencia + `reason` obligatorio | `items.service.close_item(..., "discarded")` |
| `pulsyr_reopen` | write | referencia terminal | `items.service.reopen_item` |
| `pulsyr_comment_add` | write | referencia + `body_md` + `kind` | servicio compartido append-only; no editar/borrar comentarios |
| `pulsyr_unlink` | write/destructive/idempotent | source, target, relation | `items.relationships.delete_relationship` |
| `pulsyr_priority_view` | read | área/tipo opcional | proyección de matriz, orden y grupo “sin estimar”; no llama LLM |
| `pulsyr_archive_list` | read | `week=YYYY-Www`, `status=done|discarded`, paginación | query compartida por `closed_at`, incluye reason/commit del evento de cierre |

Reglas de `pulsyr_item_update`:

- Cambiar `status` no está permitido; se usan `pulsyr_advance`, `pulsyr_complete`, `pulsyr_discard` o `pulsyr_reopen`.
- La presencia de una propiedad se distingue de `null`; `priority`, `impact_ai` y `effort_ai` pueden limpiarse explícitamente si el dominio lo permite.
- `impact_ai` se valida 1–5 y `effort_ai` contra el enum canónico.
- El evento registra campos y before/after; no incluye contenido binario ni secretos.

`pulsyr_archive_list` evita obligar al agente a inferir Archivo desde un `pulsyr_list` genérico que no representa semanas ni razón de cierre.

#### Hilos

| Tool nueva | Modo | Contrato mínimo | Servicio/garantía |
|---|---|---|---|
| `pulsyr_thread_set_stage` | write | `thread_id`, `stage` | `threads.service.set_stage`; conserva validación de enum y aislamiento |
| `pulsyr_thread_artifact_add` | write | `thread_id`, `kind`, `content`, `stage` opcional | `threads.service.add_artifact` |

`pulsyr_thread_advance` sigue siendo el camino normal. `set_stage` existe para paridad con la UI y debe documentarse como operación explícita que puede retroceder; no puede saltar el guard de “linked items abiertos” al intentar llegar a `done`. Antes de exponerla, el servicio común debe aplicar esa misma invariantes a `set_stage`, porque hoy solo `advance_stage` la verifica.

#### Incidentes

| Tool nueva | Modo | Contrato mínimo | Servicio/garantía |
|---|---|---|---|
| `pulsyr_incident_promote` | write/idempotent | `id`, `priority=p0|p1|p2|p3` | `webhooks.service.promote_issue` |
| `pulsyr_incident_ignore` | write/idempotent | `id`, `reason` opcional | extraer servicio; transición válida a `ignored` |
| `pulsyr_incident_unignore` | write/idempotent | `id` | extraer servicio; solo `ignored → new` |

Las rutas UI de ignore/unignore dejan de actualizar ORM directamente y llaman a los mismos servicios que MCP. La auditoría del incidente debe guardar actor, transición y nota sin copiar payloads sensibles.

#### Gestión

| Tool nueva | Modo | Contrato mínimo | Servicio/garantía |
|---|---|---|---|
| `pulsyr_doc_rollback` | write | `deliverable_id`, `version_no` | `management.service.rollback_deliverable`; crea versión nueva, no reescribe historia |
| `pulsyr_pending_delete` | write/destructive/idempotent | `pending_id` | `management.service.delete_pending` + `ManagementEvent` |

Plan/Gantt ya tiene lectura, upsert y eliminación por MCP. Documentos ya tiene list/get/put; Pendientes ya tiene list/upsert/complete.

### 7.2 P1 — Operaciones administrativas o de red

| Tool nueva | Modo | Motivo de segunda iteración |
|---|---|---|
| `pulsyr_incident_backfill` | write/open-world/idempotent | consulta Sentry, requiere conexión válida, límites, timeouts y mensaje parcial |
| `pulsyr_compartment_upsert` | write/idempotent | completa nombre/descripción sin forzar upload documental |
| `pulsyr_doc_update` | write | actualiza metadata sin crear una versión de bytes artificial |

Contrato de `pulsyr_incident_backfill`:

- usa conexión server-side; jamás recibe ni devuelve token Sentry;
- `query` default `is:unresolved`, `limit` con máximo duro;
- timeout y errores remotos controlados;
- deduplicación existente por issue;
- respuesta separa `fetched`, `created`, `updated`, `ignored` y fallos;
- anotación `openWorldHint=true`.

### 7.3 Fuera de paridad MCP

- Descargar binarios grandes por MCP: continúa en UI/HTTP autenticado; `pulsyr_doc_get` mantiene el límite de inline.
- Pedir al servidor que otro LLM elabore un Hilo: el agente cliente hace esa elaboración.
- Cambiar módulos del proyecto: permanece owner-only en Settings, no se añade una tool administrativa.
- Operaciones de cuenta, billing, miembros y secretos de integraciones.

## 8. Resources y prompts por módulo

Catálogo objetivo inicial:

| Nombre/URI | Módulo | Disponibilidad |
|---|---|---|
| `briefing` | core | siempre; contenido adaptado a módulos |
| `decision` | core | siempre |
| `pulsyr://capabilities` | core | siempre |
| `pulsyr://area/{area_name}` | core | siempre |
| `pulsyr://graph/{item_id}` | core | siempre |
| `pulsyr://threads/{thread_id}` | threads | solo si efectivo |
| `pulsyr://incidents/open` | incidents | solo si efectivo |
| `pulsyr://management/status` | management | solo si efectivo |

`prompts/list`, `prompts/get`, `resources/list`, `resources/templates/list` y `resources/read` aplican la misma política que tools: filtrar discovery y revalidar lectura directa.

No es obligatorio crear todos los resources nuevos en P0; sí lo es que cualquier resource existente o nuevo tenga metadata y enforcement coherente.

## 9. Errores estables

Para nuevos errores transversales, el texto MCP contiene JSON versionado:

```json
{
  "schema_version": 1,
  "error": {
    "code": "module_disabled",
    "message": "The incidents module is disabled for this project.",
    "details": {"module": "incidents"}
  }
}
```

Códigos mínimos:

| Código | Uso |
|---|---|
| `unknown_tool` | nombre no registrado |
| `module_disabled` | catálogo conocido, módulo no efectivo |
| `write_scope_required` | token read en mutación |
| `not_found` | entidad inexistente o de otro proyecto |
| `invalid_argument` | schema o enum inválido |
| `invalid_transition` | ciclo de vida no permitido |
| `conflict` | estado concurrente o dependencia abierta |
| `integration_unavailable` | Sentry/servicio externo no configurado o no disponible |
| `internal_error` | safety net sin detalle sensible |

La transición de errores de tools existentes puede hacerse de forma aditiva: conservar un `message` humano y añadir estructura solo donde el servidor ya produce el error nuevo. No se cambia el envelope JSON-RPC.

## 10. Seguridad y consistencia

- La tool resuelve entidades con `token.project_id`; UUID no implica autorización.
- Los resolvers por texto nunca buscan fuera del proyecto.
- Scope `write` se valida en servidor para toda mutación.
- Módulo efectivo se valida antes de leer argumentos que referencian entidades.
- Toda mutación confirma servicio + evento en una transacción; una excepción hace rollback.
- Handlers no hacen `commit` interno.
- Logs pueden incluir tool y tipo de error, pero no contenido documental, tokens, payload Sentry completo ni argumentos sensibles.
- `pulsyr_doc_put` conserva límites de tamaño y protección de plan; dynamic discovery no los relaja.
- Backfill y resolución remota conservan timeout, degradación local/remota y límites duros.

## 11. Compatibilidad

- Con todos los módulos habilitados, `tools/list` contiene las 26 tools actuales con el mismo nombre y schema requerido.
- Las tools nuevas son aditivas.
- Un cliente con catálogo cacheado recibe `module_disabled` si una familia se apaga.
- Rehabilitar un módulo hace reaparecer sus tools sin emitir token nuevo.
- `pulsyr_context` mantiene sus claves actuales donde sea posible; las secciones opcionales ausentes se representan de forma documentada, no con consultas cruzadas.
- El endpoint, método HTTP, protocolo JSON-RPC y forma de autenticación no cambian.

## 12. Implementación

### Iteración 1 — Registry modular

- Añadir metadata de módulo/annotations a `Tool`.
- Etiquetar las 26 tools.
- Integrar `enabled_modules` en initialize/list/call.
- Hacer prompts/resources module-aware.
- Añadir `pulsyr_capabilities` y `pulsyr://capabilities`.

### Iteración 2 — Paridad P0

- Extraer servicios faltantes para comentario, actualización de item e ignore/unignore.
- Añadir tools core, Hilos, Incidentes y Gestión de la tabla P0.
- Conservar schemas cerrados, límites y auditoría.
- Documentar el catálogo actualizado en `docs/MCP.md`.

### Iteración 3 — P1 y endurecimiento

- Añadir backfill, compartments y metadata documental.
- Incorporar métricas por familia/error sin datos sensibles.
- Ejecutar matriz completa de combinaciones y clientes compatibles.

## 13. Tests de contrato

### Discovery

Para las ocho combinaciones de `threads/incidents/management`:

- core siempre listado;
- solo aparecen familias habilitadas;
- tools actuales aparecen en el módulo correcto;
- resources/prompts siguen la misma decisión;
- capability payload coincide con el catálogo visible.

### Enforcement

- Una tool conocida pero deshabilitada devuelve `module_disabled` y no llama handler.
- Un token read no ejecuta writes.
- Un ID de otro proyecto devuelve `not_found` y no filtra título/estado.
- Cambiar el módulo entre `tools/list` y `tools/call` bloquea la call.
- Un fallo del handler hace rollback de dato y evento.

### Paridad

- UI y MCP sobre la misma operación producen el mismo estado y evento.
- Discard/reopen respetan `items.lifecycle`.
- `thread_set_stage(done)` no elude linked items abiertos.
- Promote es idempotente y devuelve el item ya vinculado.
- Ignore/unignore validan transición.
- Rollback documental crea versión append-only.
- Pending delete no cruza proyecto y emite `ManagementEvent`.
- Archivo agrupa por semana ISO y pagina sin duplicar semanas/items.

### Compatibilidad

- Snapshot de las 26 definiciones actuales con proyecto `hybrid`.
- Token legado del mismo proyecto continúa autenticando.
- Clientes que no leen resources pueden usar `pulsyr_capabilities`.
- Error safety-net no contiene nombres de excepción internos, SQL ni secretos.

## 14. Criterios de aceptación

- Hay un solo `/mcp` y un token por proyecto.
- Cada catálogo MCP conoce su módulo.
- Discovery refleja capacidades efectivas.
- La ejecución directa revalida módulo y scope.
- Las 26 tools actuales mantienen compatibilidad.
- Las operaciones P0 de UI tienen equivalente MCP o una exclusión explícita justificada.
- Todos los handlers reutilizan servicios de dominio y auditoría.
- Los errores son estables, seguros y accionables.
- `docs/MCP.md` explica capacidades, familias, scopes y tools nuevas.

## 15. No-objetivos

- Crear `/mcp/backlog`, `/mcp/management` u otras URLs por módulo.
- Emitir tokens separados por módulo.
- Permitir configuración del proyecto vía MCP.
- Exponer secretos o administración de cuenta.
- Invocar un LLM server-side cuando el cliente ya puede realizar la elaboración.
- Romper o renombrar tools existentes para lograr una taxonomía más estética.
