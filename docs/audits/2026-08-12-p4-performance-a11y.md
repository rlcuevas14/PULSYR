# Evidencia P4: rendimiento, accesibilidad y UX

- **Fecha:** 2026-08-12
- **Alcance:** sitio público Astro y patrones compartidos de la aplicación privada
- **Entorno de laboratorio:** Chromium 151 mediante Playwright 1.62.1
- **Deploy:** no ejecutado

## Presupuestos medidos

El script `site/scripts/check-performance-budget.mjs` falla el build cuando se supera
cualquiera de estos límites, medidos sin compresión para que el gate sea más estricto
que el presupuesto transferido por red:

| Recurso | Límite | Resultado local |
|---|---:|---:|
| HTML por página pública | 40 KiB | máximo 14.255 B |
| CSS público total | 35 KiB | 13.527 B |
| JavaScript público | 0 B | 0 B |
| CSS privado propio | 30 KiB | 20.313 B |
| JavaScript privado propio | 12 KiB | 9.328 B |
| Vendor privado individual | 60 KiB | HTMX 51.250 B; Sortable 45.092 B |

La tarjeta social PNG pesa 655.746 B y tiene un límite explícito de 700 KiB. La
excepción al objetivo general de 250 KiB es intencional: sólo se referencia como
metadata Open Graph, no se renderiza ni participa en LCP/CLS. Cualquier imagen de
contenido futura debe declarar `alt`, dimensiones intrínsecas y una estrategia de
carga; el smoke test comprueba esos atributos cuando existe un `<img>`.

Los objetivos LCP ≤ 2,5 s, INP ≤ 200 ms y CLS ≤ 0,1 son objetivos de campo p75. No
se presentan valores de laboratorio como sustituto de datos reales; la recolección y
el gate Lighthouse se incorporan en P5/P6 antes de evaluar tendencias.

## Accesibilidad y teclado

- Skip links con destino enfocable en sitio público, login, setup y aplicación.
- Foco visible de 3 px y objetivos táctiles compartidos de al menos 44×44 px.
- Modales con nombre accesible, bloqueo de scroll, focus trap, Escape y restauración
  del foco al disparador.
- Confirmaciones destructivas con `<dialog>` accesible; se eliminó el uso de
  `confirm()` inline.
- Formularios con errores por campo (`aria-invalid`, `aria-describedby`, `role=alert`),
  resumen enfocable, estado `aria-busy`, indicador visible y protección de doble envío.
- `autocomplete` e `inputmode` aplicados a auth, setup, miembros, proyectos e
  integración Sentry.
- `prefers-reduced-motion` conserva el contenido y reduce animaciones/transiciones.

La auditoría axe de las nueve rutas públicas no reporta violaciones serious/critical.
Los contrastes detectados inicialmente en tarjetas rosadas/teal y CTA fueron
corregidos antes de registrar esta evidencia.

## Matriz responsive y estabilidad

Playwright recorre las nueve rutas públicas en 320, 375, 768, 1024 y 1440 CSS px:
45 combinaciones sin scroll horizontal del documento. Además verifica errores de
consola, excepciones de página y respuestas HTTP 4xx/5xx esenciales. El overflow del
quick start a 320/375 px fue corregido en el contenedor del artículo, manteniendo el
scroll únicamente dentro de bloques de código.

Resultado local final: **57/57 pruebas Playwright aprobadas**, `astro check` sin
errores/advertencias, presupuesto aprobado y `npm audit` con 0 vulnerabilidades.

La app privada conserva tema claro/oscuro y ES/EN/FR. Su matriz autenticada completa
requiere fixtures de base de datos y se termina como gate separado en P6; los patrones
compartidos críticos de modal y formulario sí tienen pruebas de navegador aisladas en
esta fase.
