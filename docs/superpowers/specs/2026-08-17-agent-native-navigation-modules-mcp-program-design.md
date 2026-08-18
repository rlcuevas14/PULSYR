# Spec padre — Arquitectura de navegación, módulos y paridad MCP agent-native

**Fecha**: 2026-08-17
**Estado**: aprobado; implementación secuencial en curso
**Tipo**: spec padre / programa de implementación
**Producto**: Pulsyr — FastAPI + Jinja + HTMX + PostgreSQL + JSON-RPC MCP

## 1. Objetivo

Reorganizar Pulsyr para que **Backlog sea el protagonista del trabajo diario**, mientras Gestión pasa a ser una capacidad opcional para proyectos que realmente la necesitan, sin degradar la arquitectura backend ni fragmentar la integración con agentes de IA.

Este programa coordina tres cambios que deben diseñarse como un solo sistema:

1. una nueva arquitectura de información responsive;
2. capacidades configurables por proyecto, con presets de inicio pero sin encasillar permanentemente al proyecto;
3. descubrimiento y paridad funcional MCP por módulo sobre el endpoint único del proyecto.

El resultado debe permitir que una persona y un agente —Codex, Claude u otro cliente MCP— observen la misma superficie efectiva del proyecto y ejecuten las mismas operaciones de dominio mediante los mismos servicios.

## 2. Estado actual verificado

Al 2026-08-17:

- La navegación superior declara como pares `Management`, `Backlog`, `Priority`, `Threads`, `Incidents` y `Archive`.
- Gestión contiene tres subtabs: `documentos`, `plan` y `pendientes`; `/management` redirige actualmente a Documentos.
- Backlog, Prioridad y Archivo son tres vistas del mismo dominio `Item`, no tres dominios independientes.
- `Project` no guarda tipo, preset ni módulos habilitados.
- MCP usa un único `POST /mcp`, autenticado por bearer token asociado a proyecto.
- El registro MCP expone 26 tools de forma estática, aunque pertenezcan a dominios opcionales.
- UI, REST y MCP ya comparten varios servicios de dominio; esa propiedad debe preservarse y ampliarse.
- El head de migraciones observado es `v0021`; el número definitivo de la nueva revisión se asignará al implementar para evitar colisiones.

## 3. Decisiones adoptadas

Estas decisiones rigen la implementación de esta familia de specs:

1. **Backlog es el núcleo universal**. Prioridad y Archivo pasan a ser vistas contextuales del dominio Backlog.
2. **Gestión es un módulo opcional** y se presenta como menú desplegable en escritorio; su orden es Pendientes, Plan, Documentos.
3. **Hilos e Incidentes también son módulos opcionales**. El núcleo Backlog nunca se puede deshabilitar.
4. **Los presets se aplican al crear un proyecto, pero no constituyen un tipo rígido**. Después de la creación, el owner puede activar o desactivar módulos.
5. **Se mantiene un solo endpoint MCP por proyecto**. Cada módulo se representa mediante familias de tools, prompts y resources; no se crean endpoints ni tokens por módulo.
6. **`tools/list` es dinámico**, pero ocultar una tool no es autorización: `tools/call` vuelve a validar el módulo en servidor.
7. **UI, REST y MCP llaman al mismo servicio de dominio** para toda mutación. Los routers no duplican reglas de negocio.
8. **Deshabilitar un módulo no elimina sus datos**. Solo retira su navegación y bloquea operaciones mientras esté deshabilitado.
9. **Los 26 nombres MCP actuales se conservan**. La evolución de paridad será aditiva y compatible.
10. **No se incorpora una barra lateral persistente al trabajo diario**. Backlog y Gantt conservan el ancho disponible; la navegación vertical queda reservada para Settings/Admin.

## 4. Arquitectura de información objetivo

```text
Pulsyr
├── Backlog                         siempre habilitado
│   ├── Trabajo                    /backlog
│   ├── Prioridad                  /priority
│   └── Archivo                    /archive
├── Hilos                           opcional: threads
├── Gestión ▾                       opcional: management
│   ├── Pendientes                 /management/pendientes
│   ├── Plan                       /management/plan
│   └── Documentos                 /management/documentos
└── Incidentes                      opcional: incidents; badge de nuevos
```

En escritorio, la primera línea de navegación muestra `Backlog`, `Hilos`, `Gestión ▾` e `Incidentes`; Prioridad y Archivo aparecen como navegación contextual de Backlog. En móvil se utiliza navegación inferior con `Backlog`, `Prioridad`, acción primaria, `Hilos` y `Más`; Gestión, Archivo, Incidentes y Settings viven en la hoja `Más`, filtrados por capacidades.

## 5. Modelo conceptual

Se separan explícitamente cuatro conceptos:

| Concepto | Ejemplos | Responsabilidad |
|---|---|---|
| Dominio | Backlog, Hilos, Gestión, Incidentes | Reglas, entidades, servicios y auditoría |
| Módulo configurable | `threads`, `management`, `incidents` | Disponibilidad efectiva por proyecto |
| Vista | Trabajo, Prioridad, Archivo, Pendientes, Plan, Documentos | Proyección o flujo de UI dentro de un dominio |
| Transporte | UI HTML/HTMX, REST, MCP | Adaptación de entrada/salida; nunca dueño de reglas |

`core` identifica el dominio siempre disponible formado por Backlog y sus vistas. No se persiste como un módulo que pueda apagarse.

## 6. Specs hijos

### Hijo 1 — Navegación responsive y arquitectura de información

Documento: [2026-08-17-responsive-navigation-information-architecture-design.md](2026-08-17-responsive-navigation-information-architecture-design.md)

Define navegación de escritorio, tablet y móvil; jerarquía Backlog/Prioridad/Archivo; menú de Gestión; estados activos; accesibilidad; render server-side e integración con módulos efectivos.

### Hijo 2 — Capacidades configurables por proyecto

Documento: [2026-08-17-project-module-capabilities-design.md](2026-08-17-project-module-capabilities-design.md)

Define persistencia, presets, servicio autoritativo, auditoría, guards, backfill de proyectos existentes, settings, concurrencia y estrategia de despliegue.

### Hijo 3 — Descubrimiento y paridad MCP por módulo

Documento: [2026-08-17-mcp-module-parity-dynamic-discovery-design.md](2026-08-17-mcp-module-parity-dynamic-discovery-design.md)

Define catálogo MCP con metadata de módulo, filtrado dinámico, defensa en `tools/call`, recurso/tool de capacidades, compatibilidad y cierre de brechas entre UI y agentes.

## 7. Invariantes transversales

### 7.1 Aislamiento y autorización

- Toda lectura y escritura continúa limitada a `project_id` y, donde corresponda, `account_id`.
- La UI resuelve el proyecto mediante `projects/access.py`; MCP usa exclusivamente `token.project_id`.
- Un ID válido de otro proyecto se responde como no encontrado; nunca se filtra su existencia.
- `read` y `write` del token siguen siendo independientes de los módulos. Una tool requiere ambas condiciones: módulo efectivo y scope adecuado.
- Solo el owner puede cambiar módulos o aplicar presets a un proyecto existente.

### 7.2 Servicios de dominio y transacciones

- Routers UI, REST y MCP no actualizan entidades directamente cuando existe una operación de dominio equivalente.
- Toda mutación nueva entra por un servicio, valida invariantes y emite el evento de auditoría correspondiente.
- Una operación y su evento se confirman en la misma transacción.
- Las operaciones contra Sentry mantienen degradación controlada y distinguen el estado local del remoto.

### 7.3 Integridad y datos

- Desactivar un módulo es reversible y no ejecuta `DELETE`, cascadas ni archivado implícito.
- Las migraciones son aditivas y tienen backfill determinista.
- Los valores de módulo y preset son enums cerrados en aplicación y constraints cerrados en base de datos.
- Una ausencia accidental de configuración no puede producir una mezcla silenciosa de capacidades.

### 7.4 Compatibilidad

- Se conservan rutas canónicas actuales y se usan redirecciones para URLs legadas.
- Los nombres y esquemas requeridos de las 26 tools MCP actuales no se cambian en esta iniciativa.
- Las respuestas nuevas se versionan mediante `schema_version` cuando incorporan estructura propia.
- Clientes MCP que no consumen resources pueden descubrir lo mismo mediante `pulsyr_capabilities`.

### 7.5 UX, i18n y accesibilidad

- No se duplican listas de navegación entre desktop y mobile: ambas se derivan del mismo descriptor server-side.
- Todo copy visible se resuelve mediante las traducciones existentes.
- Menús y hojas son operables con teclado, presentan foco visible, roles/atributos ARIA correctos y cierre predecible.
- Los badges no dependen solo del color y no anuncian cambios irrelevantes repetidamente.

### 7.6 Observabilidad y secretos

- Los cambios de módulo generan auditoría con actor, módulo, valor anterior, valor nuevo y origen.
- Los errores MCP usan códigos estables sin incluir stack traces, SQL, tokens ni secretos de integración.
- Ninguna respuesta de capacidades devuelve bearer tokens, secretos GitHub/Sentry ni credenciales.

## 8. Dependencias y secuencia de implementación

La implementación se divide en entregas integrables:

### Entrega A — Jerarquía visual sin condicionalidad

- Implementar el hijo 1 con todos los módulos actualmente visibles.
- Mover Prioridad y Archivo bajo el contexto de Backlog.
- Convertir Gestión en menú y cambiar su destino por defecto a Pendientes.
- Mantener disponibles todas las rutas actuales.

Esta entrega no requiere migración y puede validarse de manera aislada.

### Entrega B — Modelo de capacidades

- Implementar migración, servicio, auditoría, presets, settings y guards del hijo 2.
- Hacer backfill conservador: los proyectos existentes mantienen habilitados todos los módulos opcionales.
- Conectar el descriptor de navegación del hijo 1 al servicio de capacidades.

### Entrega C — MCP dinámico y paridad prioritaria

- Incorporar metadata de módulo al registro MCP.
- Filtrar discovery y revalidar calls.
- Añadir `pulsyr_capabilities` y las tools P0 de paridad.
- Añadir las tools P1 en una iteración posterior si se desea reducir el tamaño del cambio.

No se activa filtrado condicional en producción antes de que el backfill y los tests de aislamiento estén completos.

## 9. Criterios de aceptación del programa

| Área | Criterio |
|---|---|
| Jerarquía | Backlog es el primer destino y Prioridad/Archivo se entienden como vistas suyas |
| Gestión | Se muestra como agrupación opcional con Pendientes primero |
| Responsive | Desktop y mobile ofrecen las mismas capacidades efectivas sin duplicar configuración |
| Configuración | Un owner puede aplicar un preset y luego ajustar módulos individualmente |
| Datos | Deshabilitar/reactivar conserva Hilos, Incidentes y Gestión sin cambios |
| UI directa | Una ruta de módulo deshabilitado no permite operar el dominio |
| MCP discovery | `tools/list`, prompts y resources reflejan los módulos efectivos del proyecto |
| MCP enforcement | Una llamada directa a una tool deshabilitada falla con `module_disabled` |
| Scopes | Un token `read` nunca ejecuta una mutación aunque el módulo esté habilitado |
| Compatibilidad | Las 26 tools actuales siguen funcionando para proyectos con todos los módulos habilitados |
| Auditoría | Cada cambio de módulo y cada nueva mutación de dominio deja evento trazable |
| Calidad | Tests unitarios, integración, aislamiento, plantillas y MCP pasan en CI |

## 10. Estrategia de validación global

1. **Tests de dominio**: capacidades, presets, transiciones, idempotencia y eventos.
2. **Tests de aislamiento**: IDs de otros proyectos, tokens cruzados y navegación de miembros.
3. **Tests de contrato MCP**: snapshots/sets de tools por combinación de módulos, scopes y compatibilidad.
4. **Tests de rutas**: desktop/mobile, active state, redirects y guard de acceso directo.
5. **Tests de plantilla**: una sola fuente de navegación y copy i18n.
6. **QA visual**: 360, 390, 768, 1024 y 1440 px; menús largos; badge 0/1/99+; ES/EN.
7. **Regresión**: flujos actuales de Backlog, Hilos, Incidentes, Gestión y Archivo con todos los módulos habilitados.

## 11. Rollback

- El cambio de navegación puede revertirse sin tocar datos.
- La configuración de módulos se entrega primero con todos los proyectos legados habilitados; un feature flag temporal puede mantener el catálogo completo mientras se valida el backfill.
- Si discovery dinámico causa incompatibilidad, se puede volver a listar el catálogo completo sin retirar la validación server-side ni las filas de configuración.
- Las tablas nuevas no se eliminan como parte de un rollback de aplicación; se mantienen hasta confirmar que ninguna release activa depende de ellas.

## 12. No-objetivos

- Crear un endpoint MCP o token por módulo.
- Convertir Pulsyr en una suite PMO universal.
- Cambiar el motor SSR/HTMX por una SPA.
- Crear una barra lateral permanente para el trabajo cotidiano.
- Diseñar facturación o entitlements comerciales por módulo en esta fase.
- Eliminar rutas, tools o datos existentes.
- Rediseñar visualmente cada pantalla interna fuera de su nueva jerarquía de navegación.
- Desplegar a producción como parte de la aprobación de estos documentos.

## 13. Decisiones de producto cerradas para implementación

1. Los presets usan los labels localizados Personal/independiente, Producto/software, Cliente/consultoría e Híbrido; los identificadores técnicos quedan en inglés.
2. El preset por defecto de un proyecto nuevo es `solo`; onboarding permite elegir otro de forma explícita.
3. `Incidentes` puede habilitarse antes de conectar Sentry y muestra una llamada a configurar la integración.

Ninguna de estas decisiones cambia la arquitectura de dominio, el endpoint MCP único ni los invariantes de aislamiento.
