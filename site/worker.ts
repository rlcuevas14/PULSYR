const SPANISH_SPEAKING_COUNTRIES = new Set([
  "AR", "BO", "CL", "CO", "CR", "CU", "DO", "EC", "ES", "GQ",
  "GT", "HN", "MX", "NI", "PA", "PE", "PR", "PY", "SV", "UY", "VE",
]);

type Locale = "en" | "es";
type CloudflareRequest = Request & { cf?: { country?: string } };

function cookieLocale(request: Request): Locale | null {
  const match = request.headers.get("Cookie")?.match(/(?:^|;\s*)pulsyr_lang=(en|es)(?:;|$)/);
  return match?.[1] as Locale | undefined ?? null;
}

function countryLocale(request: CloudflareRequest): Locale {
  const country = request.cf?.country ?? request.headers.get("CF-IPCountry") ?? "";
  return SPANISH_SPEAKING_COUNTRIES.has(country.toUpperCase()) ? "es" : "en";
}

function basePath(path: string): string {
  if (path === "/es" || path === "/es/") return "/";
  return path.startsWith("/es/") ? path.slice(3) : path;
}

function localizedPath(path: string, locale: Locale): string {
  const base = basePath(path);
  if (locale === "en") return base || "/";
  return base === "/" ? "/es/" : `/es${base.startsWith("/") ? base : `/${base}`}`;
}

function safeNext(requestUrl: URL): string {
  const value = requestUrl.searchParams.get("next") ?? "/";
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/";
  try {
    const parsed = new URL(value, requestUrl.origin);
    return parsed.origin === requestUrl.origin ? parsed.pathname : "/";
  } catch {
    return "/";
  }
}

function redirect(location: string, locale?: Locale): Response {
  const headers = new Headers({
    Location: location,
    "Cache-Control": "private, no-store",
    Vary: "Cookie",
  });
  if (locale) {
    headers.set(
      "Set-Cookie",
      `pulsyr_lang=${locale}; Path=/; Max-Age=31536000; Secure; HttpOnly; SameSite=Lax`,
    );
  }
  return new Response(null, { status: 302, headers });
}

export default {
  async fetch(request: CloudflareRequest, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const languageChoice = url.pathname.match(/^\/__language\/(en|es)\/?$/);

    if (languageChoice && (request.method === "GET" || request.method === "HEAD")) {
      const locale = languageChoice[1] as Locale;
      return redirect(localizedPath(safeNext(url), locale), locale);
    }

    if (url.pathname === "/" && (request.method === "GET" || request.method === "HEAD")) {
      const locale = cookieLocale(request) ?? countryLocale(request);
      if (locale === "es") return redirect("/es/");
    }

    return env.ASSETS.fetch(request);
  },
};

export { basePath, cookieLocale, countryLocale, localizedPath, safeNext };
