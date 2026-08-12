import { createHash } from "node:crypto";
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const staticRoot = resolve(siteRoot, "../app/static");
const outputRoot = resolve(staticRoot, "assets");
mkdirSync(outputRoot, { recursive: true });

const sources = {
  "app.css": resolve(staticRoot, ".build/app.css"),
  "app.js": resolve(siteRoot, "app-assets/app.js"),
  "htmx.js": resolve(siteRoot, "node_modules/htmx.org/dist/htmx.min.js"),
  "sortable.js": resolve(siteRoot, "node_modules/sortablejs/Sortable.min.js"),
};

for (const name of readdirSync(outputRoot)) {
  if (/^(app|htmx|sortable)\.[a-f0-9]{12}\.(css|js)$/.test(name)) {
    rmSync(resolve(outputRoot, name));
  }
}

const manifest = {};
for (const [logicalName, source] of Object.entries(sources)) {
  const content = readFileSync(source);
  const hash = createHash("sha256").update(content).digest("hex").slice(0, 12);
  const extension = logicalName.split(".").at(-1);
  const filename = `${basename(logicalName, `.${extension}`)}.${hash}.${extension}`;
  cpSync(source, resolve(outputRoot, filename));
  manifest[logicalName] = `/static/assets/${filename}`;
}

writeFileSync(resolve(staticRoot, "asset-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
rmSync(resolve(staticRoot, ".build"), { recursive: true, force: true });
