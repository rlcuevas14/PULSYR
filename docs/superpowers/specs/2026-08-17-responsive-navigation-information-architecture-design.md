# Spec hijo 1 — Navegación responsive y arquitectura de información

**Fecha**: 2026-08-17
**Estado**: implementado
**Spec padre**: [Arquitectura de navegación, módulos y paridad MCP](2026-08-17-agent-native-navigation-modules-mcp-program-design.md)
**Dependencia**: el render condicional final consume el servicio definido en el spec hijo 2

## 1. Objetivo

Reordenar la navegación de Pulsyr para que comunique correctamente la estructura del producto:

- Backlog es la superficie principal y universal.
- Prioridad y Archivo son vistas del Backlog.
- Hilos es un espacio de elaboración de trabajo complejo.
- Gestión agrupa Pendientes, Plan y Documentos solo cuando el proyecto lo requiere.
- Incidentes es una bandeja operativa condicionada por el proyecto.

La solución debe conservar ancho útil, funcionar con Jinja/HTMX, ser accesible y evitar dos taxonomías distintas entre desktop y mobile.

## 2. Problema actual

La cinta superior presenta seis destinos como equivalentes aunque no lo son:

- Backlog, Prioridad y Archivo consultan el mismo dominio `Item` en diferentes estados/proyecciones.
- Gestión es una agrupación PMO con tres vistas internas.
- Hilos e Incidentes son capacidades especializadas.

Además, Gestión ocupa el primer lugar y abre Documentos, lo que sobrerrepresenta un flujo propio de proyectos cliente-proveedor frente al trabajo cotidiano de un solo-preneur.

## 3. Decisiones de navegación

### 3.1 Desktop, ancho mayor o igual a 1024 px

La barra superior se divide en tres zonas:

```text
[Pulsyr]  [Backlog] [Hilos] [Gestión ▾] [Incidentes 3]     [Nuevo] [Proyecto ▾] [Usuario ▾]
```

- `Backlog` es el primer destino de producto.
- `Hilos` aparece solo si `threads` está efectivo.
- `Gestión` es un botón de menú y aparece solo si `management` está efectivo.
- `Incidentes` aparece solo si `incidents` está efectivo; su badge representa incidentes `new`.
- `Nuevo` conserva la acción primaria contextual ya disponible; no se convierte en un módulo.
- Selector de proyecto, identidad, idioma y settings permanecen en utilidades.

Al entrar a una vista Backlog se presenta una segunda línea contextual:

```text
Trabajo    Prioridad    Archivo
```

Al entrar a Gestión, el propio menú y el encabezado de sección comunican las tres vistas. No se agrega otra barra global permanente.

### 3.2 Menú Gestión

Orden obligatorio:

1. Pendientes — `GET /management/pendientes`
2. Plan — `GET /management/plan`
3. Documentos — `GET /management/documentos`

`GET /management` responde `303` hacia `/management/pendientes`. La URL antigua de cada subvista continúa válida.

El menú muestra icono, nombre y una descripción corta únicamente si el ancho lo permite. No incluye contadores en v1 para evitar tres consultas adicionales en cada página.

### 3.3 Mobile, ancho menor a 768 px

Se usa barra inferior fija con cinco posiciones:

```text
Backlog    Prioridad       +       Hilos       Más
```

- `Backlog` y `Prioridad` siempre están presentes.
- `+` abre la creación contextual existente; no navega a una sección vacía.
- Si Hilos está deshabilitado, su posición se reemplaza por `Archivo`; no se deja un hueco.
- `Más` abre una hoja inferior con las capacidades restantes.

Contenido y orden de `Más`:

1. Archivo, salvo que haya ocupado la cuarta posición por estar Hilos deshabilitado.
2. Incidentes, si está habilitado, con badge de nuevos.
3. Gestión, si está habilitado, como grupo con Pendientes, Plan y Documentos.
4. Settings/Administración según rol.

La hoja inferior no replica lógica de permisos: recibe el mismo descriptor ya filtrado que la navegación desktop.

### 3.4 Tablet, 768–1023 px

- Se conserva la barra superior compacta.
- Las etiquetas de Hilos e Incidentes pueden mantenerse si caben; Gestión sigue siendo menú.
- Las utilidades secundarias pasan al menú de usuario/proyecto.
- No se introduce una barra lateral como estado intermedio.

### 3.5 Página raíz y entradas

- `/` continúa siendo el pulso/resumen del proyecto; no se elimina ni se convierte en un segundo sistema de navegación.
- Su primera tarjeta y CTA principal apuntan a Backlog.
- El logo puede seguir enlazando a `/`.
- El primer destino de la navegación y de los flujos de vuelta al trabajo es `/backlog`.

## 4. Fuente única de navegación

Se introduce un descriptor server-side, sin crear un framework de menús genérico:

```python
@dataclass(frozen=True)
class NavigationEntry:
    key: str
    label_key: str
    href: str | None
    icon: str
    active_prefixes: tuple[str, ...]
    module: str                     # core|threads|management|incidents
    children: tuple[NavigationEntry, ...] = ()
    badge_key: str | None = None
```

Responsabilidades:

- Un módulo pequeño de UI construye el catálogo estable.
- El servicio de capacidades filtra el catálogo por proyecto.
- Un helper resuelve `active` a partir de `request.url.path`, no por coincidencia de texto.
- Desktop, navegación contextual, mobile y la hoja `Más` consumen ese resultado.
- Los templates solo renderizan; no contienen reglas de negocio ni consultas.

Ubicación propuesta:

```text
app/ui/navigation.py
app/templates/partials/_primary_nav.html
app/templates/partials/_backlog_tabs.html
app/templates/partials/_management_menu.html
app/templates/partials/_mobile_nav.html
app/templates/partials/_mobile_more_sheet.html
```

`base.html` orquesta los parciales. No mantiene una segunda constante `NAV` con contenido divergente.

## 5. Reglas de estado activo

| Entrada | Rutas activas |
|---|---|
| Backlog | `/backlog`, `/priority`, `/archive`, detalle/acciones de item |
| Trabajo | `/backlog` y filtros derivados |
| Prioridad | `/priority` |
| Archivo | `/archive` y semana seleccionada |
| Hilos | `/threads` y detalle de hilo |
| Gestión | cualquier `/management...` |
| Pendientes | `/management/pendientes` |
| Plan | `/management/plan` |
| Documentos | `/management/documentos` y detalle/versión/descarga |
| Incidentes | `/incidents` y acciones derivadas |

En `/priority` o `/archive`, tanto Backlog global como la subvista correspondiente se marcan activas. La navegación global usa `aria-current="page"` solo en su entrada activa; los tabs usan su propio `aria-current`.

## 6. Contrato con capacidades

La navegación recibe:

```python
NavigationContext(
    primary=...,
    backlog_tabs=...,
    mobile_primary=...,
    mobile_more=...,
    active_key=...,
    active_child_key=...,
)
```

Las entradas se incluyen según el conjunto efectivo devuelto por `projects.modules.enabled_modules(...)`:

- `core`: siempre.
- `threads`: Hilos.
- `management`: Gestión y sus tres hijos.
- `incidents`: Incidentes y su badge.

Los contadores se calculan solo para entradas visibles. Un proyecto sin `incidents` no consulta `sentry_issues` para construir la navegación.

## 7. Comportamiento y accesibilidad

### 7.1 Gestión desktop

- Disparador como enlace real a `/management`, con `aria-haspopup="menu"` y `aria-expanded` sincronizado.
- Abre con click, Enter, Space, ArrowDown.
- Navegación interna con flechas; Escape cierra y devuelve foco al disparador.
- Click fuera cierra.
- El foco nunca queda atrapado tras navegación HTMX.
- Sin JavaScript, el enlace navega a `/management`, que redirige a Pendientes.

### 7.2 Hoja móvil

- Se implementa como `dialog` o patrón equivalente correctamente etiquetado.
- Atrapa foco solo mientras está abierta.
- Escape y botón Cerrar funcionan.
- Bloquea scroll del fondo sin perder la posición al cerrar.
- Respeta safe-area inferior mediante `env(safe-area-inset-bottom)`.

### 7.3 Navegación contextual

- Los tabs usan semántica de navegación, no `role=tab`, porque cambian de URL/documento.
- El foco visible usa tokens existentes y alcanza contraste WCAG AA.
- Los targets táctiles miden al menos 44 × 44 CSS px.
- El badge presenta texto accesible localizado, por ejemplo “3 incidentes nuevos”.

## 8. Responsive y layout

- La barra inferior móvil reserva espacio en el contenido para no cubrir acciones ni la última fila.
- Backlog board y Gantt no pierden ancho por una sidebar permanente.
- Los menús usan capas/tokens de elevación existentes y no introducen valores de color fuera del design system.
- En 200 % de zoom desktop no se pierde acceso a ningún módulo: las entradas que no caben pasan a overflow/`Más`.
- En landscape móvil la barra puede permanecer inferior; la hoja limita altura y permite scroll interno.

## 9. i18n

Claves propuestas, con versiones ES/EN:

```text
nav.backlog
nav.backlog_work
nav.priority
nav.archive
nav.threads
nav.management
nav.management_pending
nav.management_plan
nav.management_documents
nav.incidents
nav.more
nav.create
nav.close
nav.new_incidents
```

No se reutilizan labels en inglés hardcodeados de la constante actual. Los identificadores internos permanecen en inglés.

## 10. Rutas y compatibilidad

No se eliminan endpoints. Cambios explícitos:

| Ruta | Comportamiento objetivo |
|---|---|
| `/backlog` | vista Trabajo y primer módulo |
| `/priority` | vista contextual Prioridad |
| `/archive` | vista contextual Archivo |
| `/management` | `303` a `/management/pendientes` |
| `/management/pending` | si existe como alias legado, `308` a `/management/pendientes` |
| `/gestion` y otras rutas legadas existentes | conservan su redirect actual hacia ruta canónica |

Los formularios POST mantienen sus destinos actuales salvo que una ruta UI ya sea duplicada; cualquier consolidación se trata en otro spec.

## 11. Plan de implementación

### Iteración 1 — Descriptor y desktop

- Extraer el catálogo actual desde `base.html`.
- Crear parciales y active-state centralizado.
- Introducir Backlog contextual.
- Convertir Gestión en menú y ajustar redirect.
- Renderizar todos los módulos, todavía sin condicionalidad.

### Iteración 2 — Mobile y accesibilidad

- Agregar barra inferior y hoja `Más`.
- Implementar teclado, foco, Escape, click fuera y progressive enhancement.
- Validar zoom, safe areas y ausencia de contenido cubierto.

### Iteración 3 — Capacidades

- Conectar el descriptor al servicio del hijo 2.
- Omitir módulos inactivos y sus consultas de badge.
- Añadir estado vacío/explicativo en Settings para owners.

## 12. Tests y QA

### Unitarios

- El descriptor produce el orden definido.
- Active-state cubre rutas de detalle y no confunde prefijos.
- Cada combinación de módulos produce desktop y mobile coherentes.
- Sin Hilos, Archivo ocupa la cuarta posición mobile.

### Integración/template

- `/management` redirige a Pendientes.
- Las rutas actuales siguen respondiendo.
- Un módulo deshabilitado no aparece en ninguna navegación.
- Un módulo habilitado aparece una sola vez en el destino esperado.
- El badge solo se consulta/renderiza si Incidentes está habilitado.
- ES y EN no muestran claves crudas.

### QA visual

Matriz mínima: 360×800, 390×844, 768×1024, 1024×768 y 1440×900; temas soportados; 100 % y 200 % zoom; badge 0, 1, 99 y 100+; nombres de proyecto largo.

### Accesibilidad

- Recorrido completo solo con teclado.
- Orden de foco consistente.
- Axe o equivalente sin violaciones críticas en base, menú y hoja.
- Lectura comprensible con lector de pantalla para active state y badges.

## 13. Criterios de aceptación

- Backlog es la primera entrada en desktop y mobile.
- Prioridad y Archivo ya no compiten como módulos globales en desktop.
- Gestión es un menú único con Pendientes, Plan y Documentos, en ese orden.
- `/management` abre Pendientes.
- No existe sidebar persistente en Backlog ni Gantt.
- Desktop y mobile se derivan de una sola fuente de navegación.
- Las entradas respetan módulos efectivos y roles.
- Todas las operaciones son accesibles sin depender de hover.
- Ninguna ruta ni acción existente se elimina.

## 14. No-objetivos

- Rediseñar las tarjetas, tablas o board internos.
- Cambiar el modelo de datos.
- Construir un command palette.
- Incorporar personalización de orden de navegación por usuario.
- Agregar contadores para todos los módulos.
- Sustituir HTMX/Jinja.
