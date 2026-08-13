import assert from "node:assert/strict";

import worker, { localizedPath, safeNext } from "../worker.ts";

const assets = {
  fetch: async () => new Response("asset", { status: 200 }),
};

async function invoke(path: string, init: RequestInit = {}) {
  return worker.fetch(
    new Request(`https://pulsyr.dev${path}`, init),
    { ASSETS: assets } as unknown as Env,
  );
}

assert.equal(localizedPath("/es/producto/", "en"), "/producto/");
assert.equal(localizedPath("/producto/", "es"), "/es/producto/");
assert.equal(safeNext(new URL("https://pulsyr.dev/?next=https://evil.example")), "/");

const chile = await invoke("/", { headers: { "CF-IPCountry": "CL" }, redirect: "manual" });
assert.equal(chile.status, 302);
assert.equal(chile.headers.get("Location"), "/es/");

const unitedStates = await invoke("/", { headers: { "CF-IPCountry": "US" } });
assert.equal(unitedStates.status, 200);

const explicitEnglish = await invoke("/", {
  headers: { Cookie: "pulsyr_lang=en", "CF-IPCountry": "CL" },
});
assert.equal(explicitEnglish.status, 200);

const chooseSpanish = await invoke("/__language/es?next=%2Fproducto%2F", { redirect: "manual" });
assert.equal(chooseSpanish.status, 302);
assert.equal(chooseSpanish.headers.get("Location"), "/es/producto/");
assert.match(chooseSpanish.headers.get("Set-Cookie") ?? "", /^pulsyr_lang=es;/);

const chooseEnglish = await invoke("/__language/en?next=%2Fes%2Fproducto%2F", { redirect: "manual" });
assert.equal(chooseEnglish.headers.get("Location"), "/producto/");

console.log("Worker language-routing contract passed.");
