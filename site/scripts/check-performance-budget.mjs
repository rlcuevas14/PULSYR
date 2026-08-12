import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";

const dist = resolve("dist");
const errors = [];

function assertBudget(label, bytes, maximum) {
  if (bytes > maximum) errors.push(`${label}: ${bytes} bytes exceeds ${maximum}`);
}

for (const name of readdirSync(dist, { recursive: true })) {
  if (!name.endsWith(".html")) continue;
  assertBudget(name, statSync(resolve(dist, name)).size, 40 * 1024);
}

const astroRoot = resolve(dist, "_astro");
let publicCss = 0;
try {
  for (const name of readdirSync(astroRoot)) {
    if (name.endsWith(".css")) publicCss += statSync(resolve(astroRoot, name)).size;
    if (name.endsWith(".js")) errors.push(`public site must not ship executable JS: ${name}`);
    if (name.endsWith(".map")) errors.push(`public source map is forbidden: ${name}`);
  }
} catch (_) {
  // Astro may inline every style; the HTML budget still applies.
}
assertBudget("public CSS total", publicCss, 35 * 1024);

const manifest = JSON.parse(readFileSync(resolve("../app/static/asset-manifest.json"), "utf8"));
for (const [logical, url] of Object.entries(manifest)) {
  const size = statSync(resolve("../app", url.replace(/^\//, ""))).size;
  const maximum = logical === "app.css" ? 30 * 1024 : logical === "app.js" ? 12 * 1024 : 60 * 1024;
  assertBudget(`private ${logical}`, size, maximum);
}

assertBudget("social image", statSync(resolve(dist, "og/pulsyr-social.png")).size, 700 * 1024);

if (errors.length) {
  for (const error of errors) process.stderr.write(`${error}\n`);
  process.exit(1);
}
process.stdout.write("Performance budgets passed.\n");
