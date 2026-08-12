import { defineConfig, devices } from "@playwright/test";

const crossBrowser = process.env.FULL_BROWSER_MATRIX === "true";
const reportName = process.env.PLAYWRIGHT_REPORT_NAME ?? "results";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results",
  reporter: process.env.CI
    ? [
        ["html", { outputFolder: "playwright-report", open: "never" }],
        ["json", { outputFile: `artifacts/playwright/${reportName}.json` }],
        ["list"],
      ]
    : "list",
  use: {
    baseURL: "http://127.0.0.1:4321",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: crossBrowser
    ? [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
        { name: "firefox", use: { ...devices["Desktop Firefox"] } },
        { name: "webkit", use: { ...devices["Desktop Safari"] } },
      ]
    : [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4321",
    port: 4321,
    reuseExistingServer: !process.env.CI,
  },
});
