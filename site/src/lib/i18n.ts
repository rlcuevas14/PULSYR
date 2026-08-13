export type PublicLocale = "en" | "es";

export const PUBLIC_LOCALES: readonly PublicLocale[] = ["en", "es"];

export function localeFromPath(path: string): PublicLocale {
  return path === "/es" || path.startsWith("/es/") ? "es" : "en";
}

export function basePath(path: string): string {
  if (path === "/es" || path === "/es/") return "/";
  return path.startsWith("/es/") ? path.slice(3) : path;
}

export function localizedPath(path: string, locale: PublicLocale): string {
  const base = basePath(path);
  if (locale === "en") return base || "/";
  return base === "/" ? "/es/" : `/es${base.startsWith("/") ? base : `/${base}`}`;
}

export function languageChoiceUrl(locale: PublicLocale, currentPath: string): string {
  return `/__language/${locale}?next=${encodeURIComponent(currentPath)}`;
}
