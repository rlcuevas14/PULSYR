import { expect, test } from "@playwright/test";

for (const route of ["/", "/producto/", "/docs/primeros-pasos/"]) {
  test(`${route} renders its primary content without browser errors`, async ({ page }) => {
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
    expect(errors).toEqual([]);
  });
}
