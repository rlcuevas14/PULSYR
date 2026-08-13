import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const routes = [
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
];

const viewports = [
  { width: 320, height: 720 },
  { width: 375, height: 812 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
];

for (const route of routes) {
  test(`${route} has no serious accessibility violations or browser errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) => errors.push(`page: ${error.message}`));
    page.on("response", (response) => {
      if (response.status() >= 400) errors.push(`${response.status()}: ${response.url()}`);
    });

    await page.goto(route, { waitUntil: "networkidle" });
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("#main-content")).toBeVisible();
    const images = page.locator("img");
    for (let index = 0; index < await images.count(); index += 1) {
      const item = images.nth(index);
      await expect(item).toHaveAttribute("alt");
      await expect(item).toHaveAttribute("width");
      await expect(item).toHaveAttribute("height");
    }
    const results = await new AxeBuilder({ page }).analyze();
    const serious = results.violations.filter((violation) =>
      violation.impact === "serious" || violation.impact === "critical"
    );
    expect(serious).toEqual([]);
    expect(errors).toEqual([]);
  });
}

for (const route of routes) {
  for (const viewport of viewports) {
    test(`${route} fits ${viewport.width}px without document overflow`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto(route, { waitUntil: "networkidle" });
      const dimensions = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    });
  }
}

test("skip link and mobile navigation work with the keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByLabel("Open navigation").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".mobile-panel")).toBeVisible();
});

test("language selector links equivalent English and Spanish pages", async ({ page }) => {
  await page.goto("/producto/");
  await page.locator(".language-menu").getByLabel("Select language").click();
  await expect(page.locator('.language-panel a[lang="es"]')).toHaveAttribute(
    "href",
    "/__language/es?next=%2Fproducto%2F",
  );

  await page.goto("/es/producto/");
  await page.locator(".language-menu").getByLabel("Seleccionar idioma").click();
  await expect(page.locator('.language-panel a[lang="en"]')).toHaveAttribute(
    "href",
    "/__language/en?next=%2Fes%2Fproducto%2F",
  );
});

test("ambient videos behave as decorative backgrounds", async ({ page }) => {
  await page.goto("/");
  const heroVideo = page.locator(".hero video");
  await expect(heroVideo).toHaveAttribute("poster", "/media/pulsyr-hero-poster.webp");
  await expect(heroVideo).not.toHaveAttribute("controls", "");
  await expect(heroVideo).toHaveAttribute("muted", "");
  await expect(heroVideo).toHaveAttribute("loop", "");
  await expect(heroVideo).toHaveAttribute("playsinline", "");
  await expect(heroVideo.locator("..")).toHaveAttribute("aria-hidden", "true");
  await expect(heroVideo.locator("source")).toHaveAttribute("src", "/media/pulsyr-hero.mp4");
  await expect.poll(() => heroVideo.evaluate((video) => !video.paused)).toBe(true);

  const signalVideo = page.locator(".workflow-band .ambient-video.section video");
  await expect(signalVideo).toHaveAttribute("poster", "/media/pulsyr-signal-poster.webp");
  await expect(signalVideo.locator("source")).toHaveAttribute("src", "/media/pulsyr-signal.mp4");
  await expect(page.locator(".workflow-actions a")).toHaveCount(2);
  await signalVideo.scrollIntoViewIfNeeded();
  await expect.poll(() => signalVideo.evaluate((video) => !video.paused)).toBe(true);

  await page.goto("/producto/");
  await expect(page.locator("video")).toHaveCount(0);
  await page.goto("/integraciones/mcp/");
  await expect(page.locator("video")).toHaveCount(0);
});

for (const width of [320, 1024, 1440]) {
  test(`Spanish hero renders its final character inside the frame at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/es/");
    const geometry = await page.locator(".hero h1").evaluate((heading) => {
      const textNode = heading.firstChild;
      if (!textNode?.textContent) throw new Error("Spanish hero heading has no text node");
      const range = document.createRange();
      range.setStart(textNode, textNode.textContent.length - 1);
      range.setEnd(textNode, textNode.textContent.length);
      const character = range.getBoundingClientRect();
      const frame = heading.closest(".hero-shell")?.getBoundingClientRect();
      return {
        character: { right: character.right, bottom: character.bottom },
        frame: frame ? { right: frame.right, bottom: frame.bottom } : null,
        text: textNode.textContent,
      };
    });
    expect(geometry.text).toBe("Tu agente escribe código. Pulsyr mantiene el trabajo comprensible.");
    expect(geometry.frame).not.toBeNull();
    expect(geometry.character.right).toBeLessThanOrEqual(geometry.frame!.right + 1);
    expect(geometry.character.bottom).toBeLessThanOrEqual(geometry.frame!.bottom + 1);
  });
}
