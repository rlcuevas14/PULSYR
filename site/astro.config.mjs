import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://pulsyr.dev",
  output: "static",
  trailingSlash: "always",
  build: {
    format: "directory"
  }
});
