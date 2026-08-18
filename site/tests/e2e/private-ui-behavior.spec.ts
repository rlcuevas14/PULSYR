import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

async function loadFixture(page: Page) {
  await page.setContent(`<!doctype html><html lang="en"><head><style>
    .hidden { display:none } dialog::backdrop { background:#0008 }
  </style></head><body>
    <button id="open" data-modal-open="example-modal">Open</button>
    <div id="example-modal" data-modal role="dialog" aria-modal="true" aria-labelledby="modal-title" class="hidden">
      <h2 id="modal-title">Example</h2><button id="first">First</button><button id="last">Last</button>
    </div>
    <div data-nav-menu>
      <a id="management-trigger" href="/management" data-nav-menu-trigger aria-haspopup="menu" aria-expanded="false">Management</a>
      <div data-nav-menu-panel role="menu" class="hidden">
        <a id="pending-link" href="/management/pendientes" role="menuitem">Pendings</a>
        <a id="plan-link" href="/management/plan" role="menuitem">Plan</a>
        <a id="documents-link" href="/management/documentos" role="menuitem">Documents</a>
      </div>
    </div>
    <form id="sample" data-confirm="Proceed?">
      <label for="email">Email</label><input id="email" name="email" type="email" required>
      <button id="submit" type="submit">Save</button>
    </form>
  </body></html>`);
  await page.addScriptTag({ path: resolve("app-assets/app.js") });
  await page.evaluate(() => {
    document.dispatchEvent(new Event("DOMContentLoaded"));
    (window as typeof window & { submitCount: number }).submitCount = 0;
    document.querySelector("#sample")?.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      (window as typeof window & { submitCount: number }).submitCount += 1;
    });
  });
}

test("modal traps focus, closes with Escape, and restores the trigger", async ({ page }) => {
  await loadFixture(page);
  await page.locator("#open").click();
  await expect(page.locator("#first")).toBeFocused();
  await page.locator("#last").focus();
  await page.keyboard.press("Tab");
  await expect(page.locator("#first")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#example-modal")).toHaveClass(/hidden/);
  await expect(page.locator("#open")).toBeFocused();
});

test("navigation disclosure supports keyboard, arrows, Escape, and outside click", async ({ page }) => {
  await loadFixture(page);
  await page.locator("#management-trigger").focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("#management-trigger")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#pending-link")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("#plan-link")).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(page.locator("#pending-link")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#management-trigger")).toBeFocused();
  await expect(page.locator("#management-trigger")).toHaveAttribute("aria-expanded", "false");

  await page.locator("#management-trigger").click();
  await expect(page.locator("#pending-link")).toBeVisible();
  await page.locator("body").click({ position: { x: 1, y: 1 } });
  await expect(page.locator("#pending-link")).toBeHidden();
});

test("typing an incomplete value keeps focus in the field", async ({ page }) => {
  await loadFixture(page);
  await page.locator("#email").click();
  await page.keyboard.type("person");

  await expect(page.locator("#email")).toHaveValue("person");
  await expect(page.locator("#email")).toBeFocused();
  await expect(page.locator("[data-form-error-summary]")).toHaveCount(0);
});

test("forms expose field errors, accessible confirmation, pending state, and double-submit protection", async ({ page }) => {
  await loadFixture(page);
  await page.locator("#sample").evaluate((form: HTMLFormElement) => { form.removeAttribute("data-confirm"); });
  await page.locator("#submit").click();
  await expect(page.locator("#email")).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#email-error")).toHaveAttribute("role", "alert");
  await expect(page.locator("[data-form-error-summary]")).toBeFocused();

  await page.locator("#email").fill("team@example.com");
  await page.locator("#sample").evaluate((form: HTMLFormElement) => { form.dataset.confirm = "Proceed?"; });
  await page.locator("#submit").click();
  await expect(page.locator("#p-confirm-dialog")).toBeVisible();
  await expect(page.locator("#p-confirm-title")).toHaveText("Proceed?");
  await expect.poll(() => page.evaluate(() => (window as typeof window & { submitCount: number }).submitCount)).toBe(0);
  await page.locator("[data-confirm-accept]").click();
  await expect(page.locator("#sample")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#submit")).toBeDisabled();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { submitCount: number }).submitCount)).toBe(1);
  await page.locator("#sample").dispatchEvent("submit");
  await expect.poll(() => page.evaluate(() => (window as typeof window & { submitCount: number }).submitCount)).toBe(1);
});
