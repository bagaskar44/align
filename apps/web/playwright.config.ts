import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const envPath = resolve(process.cwd(), "../../.env");
const chromiumExecutable = process.env.ALIGN_PLAYWRIGHT_CHROMIUM_EXECUTABLE;
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator < 1) continue;
    const name = trimmed.slice(0, separator);
    if (process.env[name] === undefined) process.env[name] = trimmed.slice(separator + 1);
  }
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.ALIGN_WEB_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    ...(chromiumExecutable ? { launchOptions: { executablePath: chromiumExecutable } } : {}),
  },
  webServer: {
    command: "corepack pnpm start",
    url: new URL("/login", process.env.ALIGN_WEB_BASE_URL ?? "http://localhost:3000").href,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});

