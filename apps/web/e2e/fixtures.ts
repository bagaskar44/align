import { expect, test as base, type Page } from "@playwright/test";

const browserIssues = new WeakMap<Page, string[]>();
const expectedHttpErrors = new WeakMap<Page, number[]>();

export function expectBrowserHttpError(page: Page, status: number): void {
  const expected = expectedHttpErrors.get(page) ?? [];
  expected.push(status);
  expectedHttpErrors.set(page, expected);
}

base.beforeEach(async ({ page }) => {
  const issues: string[] = [];
  browserIssues.set(page, issues);
  expectedHttpErrors.set(page, []);
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const status = message.text().match(/status of (\d{3})\b/)?.[1];
    if (status) {
      const expected = expectedHttpErrors.get(page) ?? [];
      const index = expected.indexOf(Number(status));
      if (index >= 0) {
        expected.splice(index, 1);
        return;
      }
    }
    issues.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => issues.push(`pageerror: ${error.message}`));
});

base.afterEach(async ({ page }) => {
  expect(browserIssues.get(page) ?? [], "browser console and page errors").toEqual([]);
});

export { expect, base as test };
