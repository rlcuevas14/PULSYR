export const SITE_ORIGIN = "https://pulsyr.dev";
export const DEFAULT_SOCIAL_IMAGE = "/og/pulsyr-social.png";

export const INDEXABLE_ROUTES = [
  "/",
  "/producto/",
  "/integraciones/mcp/",
  "/open-source/",
  "/docs/primeros-pasos/",
  "/seguridad/",
  "/privacidad/",
  "/terminos/",
  "/contacto/",
  "/es/",
  "/es/producto/",
  "/es/integraciones/mcp/",
  "/es/open-source/",
  "/es/docs/primeros-pasos/",
  "/es/seguridad/",
  "/es/privacidad/",
  "/es/terminos/",
  "/es/contacto/",
] as const;

export function absoluteUrl(path: string): string {
  return new URL(path, SITE_ORIGIN).href;
}

export function structuredData(
  path: string,
  title: string,
  description: string,
  locale: "en" | "es" = "en",
) {
  const graph: Record<string, unknown>[] = [];

  if (path === "/" || path === "/es/") {
    graph.push(
      {
        "@type": "Organization",
        "@id": `${SITE_ORIGIN}/#organization`,
        name: "Pulsyr",
        url: `${SITE_ORIGIN}/`,
        logo: absoluteUrl("/favicon.svg"),
        sameAs: ["https://github.com/rlcuevas14/PULSYR"],
      },
      {
        "@type": "WebSite",
        "@id": `${SITE_ORIGIN}/#website`,
        name: "Pulsyr",
        url: `${SITE_ORIGIN}/`,
        inLanguage: locale,
        publisher: { "@id": `${SITE_ORIGIN}/#organization` },
      },
    );
  }

  if (path === "/producto/" || path === "/es/producto/") {
    graph.push({
      "@type": "SoftwareApplication",
      "@id": `${SITE_ORIGIN}/producto/#software`,
      name: "Pulsyr",
      description,
      url: absoluteUrl(path),
      applicationCategory: "DeveloperApplication",
      operatingSystem: "Web, Linux",
      license: "https://github.com/rlcuevas14/PULSYR/blob/main/LICENSE",
      codeRepository: "https://github.com/rlcuevas14/PULSYR",
      inLanguage: locale,
    });
  }

  if (path !== "/" && path !== "/es/") {
    graph.push({
      "@type": "BreadcrumbList",
      itemListElement: [
        {
          "@type": "ListItem",
          position: 1,
          name: locale === "es" ? "Inicio" : "Home",
          item: locale === "es" ? `${SITE_ORIGIN}/es/` : `${SITE_ORIGIN}/`,
        },
        {
          "@type": "ListItem",
          position: 2,
          name: title,
          item: absoluteUrl(path),
        },
      ],
    });
  }

  return { "@context": "https://schema.org", "@graph": graph };
}
