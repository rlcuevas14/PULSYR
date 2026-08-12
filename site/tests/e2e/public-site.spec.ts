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
