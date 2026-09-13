import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["{apps,packages}/**/*.test.ts"],
    coverage: { reporter: ["text", "json-summary"] },
  },
});
