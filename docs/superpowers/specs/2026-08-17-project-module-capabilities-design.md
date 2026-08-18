# Spec hijo 2 — Capacidades configurables por proyecto

**Fecha**: 2026-08-17
**Estado**: implementado; paridad MCP completada por el spec hijo 3
**Spec padre**: [Arquitectura de navegación, módulos y paridad MCP](2026-08-17-agent-native-navigation-modules-mcp-program-design.md)
**Consumidores**: navegación UI, guards de rutas, MCP discovery y MCP execution

## 1. Objetivo

Introducir un modelo backend autoritativo para que cada proyecto habilite únicamente las capacidades que necesita, sin convertir el “tipo de proyecto” en una identidad rígida ni mezclar configuración de producto con datos de dominio.

El núcleo Backlog siempre está disponible. Hilos, Incidentes y Gestión son módulos opcionales, reversibles y auditables.

## 2. Principios

1. **Preset no es tipo**: facilita onboarding, pero el owner puede ajustar el resultado.
2. **Configuración no es entitlement**: el proyecto decide qué usa; el plan comercial decide qué puede usar. En esta fase todos los planes actuales permiten todos los módulos.
3. **Desactivar no borra**: datos, adjuntos, eventos y enlaces permanecen intactos.
4. **Un servicio autoritativo**: UI, REST y MCP consultan la misma decisión efectiva.
5. **Fail explícito**: una configuración incompleta se detecta; no se interpreta silenciosamente de manera distinta entre consumidores.
6. **Compatibilidad conservadora**: todos los proyectos existentes conservan la superficie actual después de la migración.

## 3. Catálogo cerrado

```python
CORE_MODULE = "core"
OPTIONAL_MODULES = ("threads", "incidents", "management")
```

`core` representa Backlog, Prioridad y Archivo. No se persiste en `project_modules` y no se expone como toggle.

Los módulos opcionales agrupan:

| Módulo | UI | Dominio/datos |
|---|---|---|
| `threads` | Hilos | `Thread`, `ThreadArtifact`, vínculos `Item.thread_id` |
| `incidents` | Incidentes | `SentryIssue`, conexión y acciones Sentry |
| `management` | Pendientes, Plan, Documentos | modelos y eventos de `app/management` |

## 4. Presets de onboarding

| ID técnico | Label sugerido | Módulos iniciales |
|---|---|---|
| `solo` | Personal / independiente | `core` |
| `product` | Producto / software | `core`, `threads`, `incidents` |
| `client` | Cliente / consultoría | `core`, `management` |
| `hybrid` | Híbrido | todos |

Reglas:

- El preset solo determina las tres filas opcionales al crear el proyecto.
- No se guarda `project_type` ni se condicionan reglas futuras a un label comercial.
- El preset visible actual se calcula comparando el conjunto habilitado; si no coincide, se muestra `Personalizado`.
- Aplicar un preset a un proyecto existente equivale a tres cambios explícitos en una transacción y genera auditoría por cada valor modificado.
- La UI debe explicar las capacidades, no pedir que el usuario entienda la taxonomía técnica.

El default es `solo`; onboarding permite elegir otro preset de forma explícita.

## 5. Persistencia

### 5.1 Tabla `project_modules`

| Campo | Tipo | Regla |
|---|---|---|
| `id` | UUID PK | `uuid4` |
| `project_id` | UUID FK `projects(id)` | no null, `ON DELETE CASCADE` |
| `module` | varchar(30) | check `threads|incidents|management` |
| `enabled` | boolean | no null |
| `created_at` | timestamptz | server default `now()` |
| `updated_at` | timestamptz | server default `now()`, actualizado en cambio |

Constraints e índices:

- `UNIQUE(project_id, module)`.
- Índice por `project_id`; el unique ya cubre el lookup principal.
- Check cerrado de módulo en PostgreSQL además del enum de aplicación.

Invariante: cada proyecto activo tiene exactamente tres filas, una por módulo opcional.

### 5.2 Tabla `project_module_events`

Auditoría append-only:

| Campo | Tipo | Regla |
|---|---|---|
| `id` | UUID PK | `uuid4` |
| `project_id` | UUID FK `projects(id)` | no null, `ON DELETE CASCADE` |
| `module` | varchar(30) | mismo check cerrado |
| `actor` | varchar(255) | email o identidad técnica normalizada |
| `previous_enabled` | boolean | no null |
| `enabled` | boolean | no null |
| `source` | varchar(20) | `onboarding|preset|manual|migration` |
| `created_at` | timestamptz | server default `now()` |

No se genera evento para un `set` idempotente cuyo valor ya coincide, salvo durante backfill si se desea un recibo inicial. La decisión propuesta es no poblar millones de eventos de migración: el estado backfilled queda trazado por la propia revisión Alembic.

### 5.3 Migración

Nombre semántico: `project_modules`; revisión: la siguiente disponible después del head al momento de implementar (actualmente se observó `v0021`).

Pasos:

1. Crear ambas tablas y constraints.
2. Insertar tres filas por cada proyecto existente con `enabled = true`.
3. Verificar por SQL que cada proyecto tiene exactamente tres módulos distintos.
4. Crear protección de aplicación para que todo proyecto nuevo inserte las tres filas en la misma transacción.

El backfill “todo habilitado” es obligatorio: evita retirar UI o tools a proyectos existentes sin una decisión del owner.

## 6. Servicio autoritativo

Ubicación propuesta: `app/projects/modules.py`.

Interfaz:

```python
class ModuleConfigurationError(RuntimeError): ...
class ModuleDisabled(PermissionError): ...

async def enabled_modules(db, project_id) -> frozenset[str]: ...
async def module_states(db, project_id) -> dict[str, bool]: ...
async def is_module_enabled(db, project_id, module) -> bool: ...
async def require_module(db, project_id, module) -> None: ...
async def initialize_modules(db, project_id, preset, actor) -> dict[str, bool]: ...
async def set_module_enabled(db, project_id, module, enabled, actor, source="manual") -> bool: ...
async def apply_preset(db, project_id, preset, actor) -> dict[str, bool]: ...
def infer_preset(states) -> str: ...  # solo|product|client|hybrid|custom
```

Reglas del servicio:

- Valida `project_id` y catálogo antes de consultar o mutar.
- Devuelve siempre `core` dentro de `enabled_modules`.
- Rechaza filas faltantes/duplicadas con `ModuleConfigurationError`; no inventa defaults en runtime normal.
- `set_module_enabled` bloquea la fila con `SELECT ... FOR UPDATE`, compara, cambia y añade evento en la misma transacción.
- `apply_preset` bloquea las tres filas en orden estable para evitar deadlocks.
- El servicio hace `flush`, no `commit`; la frontera de transporte controla la transacción.
- No consulta tablas del dominio opcional para decidir disponibilidad.

### 6.1 Capacidad configurada versus efectiva

Contrato preparado para entitlements futuros:

```text
effective(module) = configured_enabled(module) AND entitled(module)
```

En esta fase `entitled(module)` retorna true para los tres módulos en todos los planes. No se añaden columnas de plan ni lógica comercial. La API interna conserva la distinción para evitar que una futura restricción comercial reinterprete datos de configuración.

## 7. Creación y configuración de proyecto

### 7.1 Onboarding

El formulario de creación añade `preset`, validado como enum cerrado. La creación de `Project` y sus tres `ProjectModule` ocurre en una sola transacción.

Si falla la creación de cualquier fila, el proyecto completo hace rollback; nunca queda parcialmente configurado.

### 7.2 Settings

Se agrega una sección “Módulos del proyecto” solo para owners:

- selector de preset con descripción del resultado;
- toggles separados para Hilos, Incidentes y Gestión;
- Backlog visible como “Siempre activo”, sin control editable;
- indicación de que desactivar oculta acceso pero conserva datos;
- CTA de configuración Sentry cuando Incidentes está activo pero no conectado.

Mutaciones propuestas:

```text
POST /projects/{slug}/settings/modules/preset
POST /projects/{slug}/settings/modules/{module}
```

Requisitos:

- sesión autenticada, owner del mismo proyecto y protección CSRF vigente;
- `module`, `preset` y booleanos validados antes del servicio;
- patrón POST/Redirect/GET y flash localizado;
- los miembros pueden ver, pero no editar, un resumen de módulos efectivos si Settings ya les es visible.

No se expone un endpoint público genérico para toggles en esta fase. Si se añade REST posteriormente, reutiliza exactamente el mismo servicio y autorización owner.

## 8. Guards de acceso

Se crea una dependencia reusable, por ejemplo:

```python
require_project_module("threads")
require_project_module("incidents")
require_project_module("management")
```

Orden obligatorio:

1. autenticar;
2. resolver proyecto y verificar membership/token;
3. evaluar capacidad efectiva;
4. cargar o mutar la entidad solicitada.

Esto conserva el comportamiento de aislamiento: un usuario externo no puede distinguir un proyecto válido de otro inexistente.

Adaptadores:

| Transporte | Módulo deshabilitado |
|---|---|
| UI GET/POST | `403` localizado; owner recibe enlace a Settings; ningún dato de entidad se carga antes |
| REST autenticado | `403` JSON con `code=module_disabled` |
| MCP discovery | la familia no se lista |
| MCP call directa | tool result `isError=true`, `code=module_disabled` |

Todas las rutas bajo `/threads`, `/incidents` y `/management` —incluidas acciones HTMX, descargas, backfill y endpoints JSON— deben tener guard. Proteger solo la página de lista no es suficiente.

## 9. Integración con navegación

El builder del hijo 1 llama una sola vez a `enabled_modules` por request y filtra el catálogo. Debe recibir los estados ya resueltos en el contexto, evitando consultas desde Jinja.

Las consultas auxiliares siguen la visibilidad:

- no contar incidentes si `incidents` está apagado;
- no precargar pendientes/documentos si `management` está apagado;
- no calcular Hilos activos si `threads` está apagado, salvo en una operación explícita de administración.

El resumen `/` también filtra tarjetas y consultas con el mismo conjunto efectivo.

## 10. Integración con MCP

El servidor MCP consume `enabled_modules` después de validar que el token tiene `project_id`.

- El catálogo de cada tool/resource/prompt declara `module`.
- `tools/list` filtra por módulos efectivos.
- `tools/call` ejecuta `require_module` antes del handler.
- `pulsyr_capabilities` pertenece a `core` y siempre está disponible.
- Deshabilitar un módulo afecta de inmediato nuevos requests; no requiere regenerar el token.

El token continúa siendo por proyecto. No se crean secretos adicionales por preset o módulo.

## 11. Referencias cruzadas, webhooks y jobs

Deshabilitar una capacidad no rompe datos core ni procesa trabajo nuevo de ese dominio por una vía lateral:

- Si `threads` está apagado, `Item.thread_id` se conserva. Backlog puede indicar que existe una referencia no disponible, pero no carga artefactos ni ofrece enlace operativo al Hilo.
- Si `incidents` está apagado, los items ya promovidos continúan como items core; no se borran ni pierden `source_refs`.
- El webhook Sentry autentica y acusa recibo de forma compatible, pero no ingiere ni encola triage para un proyecto con `incidents` apagado. Registra una métrica/log seguro de `module_disabled` para diagnóstico.
- Un job Sentry que ya estaba en cola revalida el módulo antes de mutar. Si fue deshabilitado, termina como omitido de forma explícita, sin retry infinito.
- GitHub/webhooks core no se condicionan a `incidents` o `threads`.
- Ningún job o webhook puede saltarse `require_module` por no pasar por un router UI/MCP.

## 12. Concurrencia, caché y rendimiento

- Son tres filas pequeñas por proyecto; se consultan en una sola query.
- No se guardan módulos en sesión, JWT ni bearer token: eso produciría decisiones obsoletas.
- Se permite memoización solo dentro del request.
- Los updates concurrentes se serializan por fila; el último commit válido determina el estado.
- `apply_preset` usa orden fijo `incidents`, `management`, `threads` o el orden lexicográfico único acordado.
- El catálogo cerrado evita joins o configuración dinámica por nombre arbitrario.

## 13. Estrategia de despliegue compatible

Por tratarse de configuración que afecta rutas y MCP, se usa expansión en dos releases si el mecanismo de despliegue no garantiza migrar antes de recibir tráfico:

### Release A — Expandir

- Crear tablas y ejecutar backfill.
- Crear escritura de filas para proyectos nuevos.
- Incorporar servicio y tests, sin ocultar ni bloquear módulos todavía.
- Añadir verificación operativa de que no existen proyectos incompletos.

### Release B — Activar

- Habilitar toggles, navegación condicional, guards y discovery MCP.
- Mantener todos los proyectos legados activos hasta que un owner cambie su configuración.

Si el pipeline garantiza migración previa al tráfico, ambas releases pueden viajar en un PR, pero deben conservar fases internas y gate de datos.

## 14. Tests

### Modelo y migración

- Constraints rechazan módulo desconocido y duplicado.
- Backfill crea exactamente tres filas habilitadas por proyecto.
- Proyectos nuevos reciben exactamente tres filas según preset.
- Downgrade, si se implementa, no borra dominios opcionales.

### Servicio

- `core` siempre efectivo.
- Cada preset produce el conjunto exacto.
- `infer_preset` reconoce cuatro presets y `custom`.
- `set` idempotente no duplica eventos.
- Cambio real emite un evento con actor y before/after.
- Configuración incompleta falla explícitamente.
- Dos cambios concurrentes no crean filas duplicadas ni auditoría incoherente.

### Autorización y aislamiento

- Owner puede cambiar solo su proyecto.
- Member no puede cambiar módulos.
- Usuario de otra cuenta recibe no encontrado antes de evaluar capacidad.
- Ruta directa, acción HTMX, descarga y JSON quedan bloqueados cuando corresponde.
- Deshabilitar no altera el conteo ni contenido almacenado; reactivar lo restaura.

### Integración

- Navegación, home, MCP list y MCP call observan el mismo estado en el mismo request posterior al cambio.
- Token `read` mantiene sus restricciones aunque el módulo esté activo.
- Proyecto legado con todo habilitado no pierde regresiones.
- Webhook y job Sentry omiten trabajo cuando `incidents` está apagado y no entran en retry.
- Referencias core hacia Hilos o Incidentes permanecen íntegras al apagar/reactivar.

## 15. Criterios de aceptación

- Existe un único estado autoritativo por proyecto/módulo.
- Backlog no puede deshabilitarse.
- Los cuatro presets son reproducibles y editables después.
- Todo proyecto contiene tres filas válidas tras migración/onboarding.
- Solo owners cambian configuración.
- Desactivar conserva datos y bloquea todas las entradas del módulo.
- UI y MCP reflejan el cambio sin regenerar token.
- Cada cambio efectivo es auditable.
- Proyectos existentes conservan todos los módulos habilitados inicialmente.

## 16. No-objetivos

- Facturación por módulo.
- Roles diferentes por módulo.
- Módulos instalables de terceros.
- Borrar o exportar datos al desactivar.
- Personalizar capacidades por usuario.
- Permitir que un agente MCP cambie la configuración del proyecto.
- Guardar un `project_type` permanente.
