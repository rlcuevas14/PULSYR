# ADR 0001: separar el sitio público de la aplicación privada

- **Estado:** aceptada
- **Fecha:** 2026-08-12
- **Decisores:** mantenedor de Pulsyr + implementación Codex
- **Contexto:** fase P0 del plan de profesionalización web/SEO

## Contexto

Pulsyr es una aplicación autenticada FastAPI/Jinja. La ruta `/` contiene datos del
proyecto activo y redirige a `/login` sin sesión. Esa superficie no es material de
marketing y no debe indexarse. Al mismo tiempo, el producto necesita páginas públicas
que expliquen qué hace, documenten la instalación y puedan competir en búsquedas.

Migrar el producto a otro framework no resolvería esa frontera. El SSR actual es
adecuado para la aplicación. El contenido público, en cambio, se beneficia de un
artefacto estático independiente, sin base de datos ni sesión.

## Decisión

1. `https://pulsyr.dev` será el origen público y canonical.
2. `https://app.pulsyr.dev` seguirá sirviendo la aplicación FastAPI autenticada.
3. El sitio público se generará con Astro en modo SSG dentro de `site/`. Node será una
   dependencia de build, no del runtime Python ni de la imagen actual de la app.
4. La versión pública inicial tendrá inglés en `/` como idioma principal y `x-default`,
   porque README, documentación técnica y audiencia de herramientas MCP son hoy
   principalmente globales. Español se publicará bajo `/es/` cuando cada página tenga
   traducción humana completa. Francés se difiere hasta contar con el mismo estándar.
5. No se harán redirecciones automáticas por IP o `Accept-Language`; el selector de
   idioma será explícito y cada variante tendrá canonical/hreflang propio.
6. `www.pulsyr.dev` redirigirá permanentemente a `pulsyr.dev`. HTTP redirigirá a HTTPS.
7. El sitio público será `index,follow` por defecto. La app, login, setup, APIs,
   webhooks, MCP, descargas y staging quedarán fuera del sitemap y declararán `noindex`
   cuando entreguen HTML.
8. `robots.txt` guiará crawling pero nunca actuará como control de acceso.
9. La documentación pública vivirá preferentemente en `/docs/` del dominio principal
   para concentrar señales y enlaces; no se crea un tercer host en esta fase.
10. No habrá despliegue automático asociado a las fases de implementación. Cada fase
    termina en PR y merge a `main`; el despliegue requerirá una acción posterior
    explícita fuera de este plan de ejecución.

## Consecuencias

### Positivas

- Se evita indexar contenido sensible o pantallas de poco valor para búsquedas.
- El marketing puede enviarse como HTML/CSS estático con JavaScript mínimo.
- El producto mantiene su arquitectura y runtime actuales.
- La política de idiomas es estable, rastreable y no depende de redirecciones ambiguas.

### Costos

- El repositorio incorpora tooling Node para build del sitio público.
- CI deberá validar dos artefactos: Python/FastAPI y Astro estático.
- Copy, traducciones y enlaces internos necesitan ownership editorial.
- Caddy/DNS deberán enrutar dos orígenes en una fase posterior autorizada; este ADR no
  cambia infraestructura ni despliega nada.

## Alternativas descartadas

- **Indexar el dashboard:** no responde intención pública y eleva riesgo de privacidad.
- **Convertir toda la app a Next.js:** migración costosa sin beneficio equivalente.
- **Marketing dentro del mismo layout autenticado:** mezcla políticas de caché,
  indexación y seguridad.
- **Publicar todos los idiomas mediante traducción automática:** aumenta páginas de
  baja calidad y riesgo de inconsistencias de producto.

## Criterios que habilitan P1

- Host canonical y frontera público/privado definidos.
- Framework y ubicación del sitio público definidos.
- Idioma principal y secuencia de traducción definidos.
- Política de indexación definida sin usar robots como seguridad.
