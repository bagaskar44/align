import { expect, test } from "./fixtures";
import type { Locator, Page } from "@playwright/test";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";

async function selectQuote(page: Page, textarea: Locator, sourceText: string, quote: string) {
  const start = sourceText.indexOf(quote);
  expect(start).toBeGreaterThanOrEqual(0);
  await textarea.scrollIntoViewIfNeeded();
  const coordinates = await textarea.evaluate((element, selection) => {
    const input = element as HTMLTextAreaElement;
    const style = getComputedStyle(input);
    const context = document.createElement("canvas").getContext("2d");
    if (!context) throw new Error("Canvas text metrics are unavailable.");
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const bounds = input.getBoundingClientRect();
    const paddingLeft = Number.parseFloat(style.paddingLeft);
    return {
      startX: bounds.left + paddingLeft + context.measureText(input.value.slice(0, selection.start)).width + 1,
      endX: bounds.left + paddingLeft + context.measureText(input.value.slice(0, selection.start + selection.quote.length)).width + 1,
      y: bounds.top + Number.parseFloat(style.paddingTop) + 8,
    };
  }, { start, quote });
  await page.mouse.move(coordinates.startX, coordinates.y);
  await page.mouse.down();
  await page.mouse.move(coordinates.endX, coordinates.y, { steps: 12 });
  await page.mouse.up();
  const range = await textarea.evaluate((element) => {
    const input = element as HTMLTextAreaElement;
    return [input.selectionStart, input.selectionEnd];
  });
  expect(range, JSON.stringify(coordinates)).toEqual([start, start + quote.length]);
  return {
    start: Array.from(sourceText.slice(0, start)).length,
    end: Array.from(sourceText.slice(0, start + quote.length)).length,
  };
}

test("lecturer builds and publishes a B2 task from shared rubric parser evidence", async ({ page }) => {
  test.setTimeout(900_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  page.setDefaultTimeout(10_000);
  const password = process.env.ALIGN_SEED_PASSWORD;
  const lecturerEmail = process.env.ALIGN_DEMO_LECTURER_EMAIL ?? "dosen@align.local";
  const studentEmail = process.env.ALIGN_DEMO_STUDENT_EMAIL ?? "mahasiswa@align.local";
  const courseCode = process.env.ALIGN_DEMO_COURSE_CODE ?? "IF101";
  const demoLabel = process.env.ALIGN_DEMO_LABEL ?? "B2 Playwright";
  const demo = JSON.parse(readFileSync(resolve(test.info().project.testDir, "fixtures", "b2-meaningful-demo.json"), "utf8")) as {
    title: string;
    instruction: string;
    criterion: { code: string; title: string; descriptor: string; requirement_code: string; requirement_text: string; rubric_quote: string };
  };
  const courseLink = new RegExp(courseCode);
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E flow.");

  await page.goto("/login");
  await page.getByLabel("Email").fill(lecturerEmail);
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("link", { name: courseLink }).click();
  await page.getByRole("tab", { name: "Konteks" }).click();

  // The synthetic seed intentionally contains no approved context. Create one
  // through the B1 lecturer flow, then reload so B2 receives that approved ID.
  await page.getByLabel("Deskripsi konteks").fill("Konteks sintetis untuk verifikasi tugas B2.");
  await page.getByRole("button", { name: "Buat draft", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Draft konteks manual dibuat" })).toBeVisible();
  await page.getByRole("button", { name: "Sahkan versi ini" }).click();
  await expect(page.getByRole("status").filter({ hasText: "disahkan" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Kelola tugas" })).toBeVisible();

  const workspace = page.getByRole("region", { name: "Kelola tugas" });
  await expect(workspace).toBeVisible();
  const assignmentTitle = `${demo.title} ${demoLabel} ${Date.now()}`;
  await expect(workspace.getByLabel("Judul tugas")).toHaveCount(0);
  await workspace.getByRole("button", { name: "Buat tugas baru" }).click();
  await workspace.getByLabel("Judul tugas").fill(assignmentTitle);
  await expect(workspace.getByLabel("Konteks mata kuliah yang digunakan")).toBeVisible();
  await workspace.getByRole("button", { name: "Buat draf tugas" }).click();
  await expect(workspace.getByRole("heading", { name: "Draf tugas" })).toBeVisible();

  const instruction = demo.instruction;
  await expect(workspace.getByLabel("Ringkasan opsional")).toHaveCount(0);
  const saveInfoButton = workspace.getByRole("button", { name: "Simpan informasi tugas" });
  await expect(saveInfoButton).toBeDisabled();
  await workspace.getByLabel("Instruksi asli (maks. 30.000 karakter)").fill(instruction);
  const dueInput = workspace.getByLabel("Tenggat pengumpulan");
  const validDue = await dueInput.inputValue();
  await dueInput.fill("2020-01-01T00:00");
  await expect(saveInfoButton).toBeDisabled();
  await dueInput.fill(validDue);
  page.once("dialog", (dialog) => void dialog.accept());
  await workspace.getByRole("button", { name: /Kriteria pemeriksaan/ }).click();
  page.once("dialog", (dialog) => void dialog.accept());
  await workspace.getByRole("button", { name: /Informasi tugas/ }).click();
  await expect(workspace.getByLabel("Instruksi asli (maks. 30.000 karakter)")).toHaveValue(instruction);
  await workspace.getByRole("button", { name: "Simpan informasi tugas" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Versi tugas tersimpan" })).toBeVisible();
  await workspace.getByRole("button", { name: "Lanjut ke kriteria" }).click();
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const dimensions = await workspace.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      const outside = Array.from(element.querySelectorAll("button,input,select,textarea,.assignment-step,.assignment-stepper,.context-summary-card"))
        .filter((item) => item.getClientRects().length > 0)
        .filter((item) => { const rect = item.getBoundingClientRect(); return rect.left < bounds.left - 1 || rect.right > bounds.right + 1; });
      return { viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth, outside: outside.map((item) => item.tagName.toLowerCase() + (item.getAttribute("aria-label") ?? "")), right: bounds.right };
    });
    expect(dimensions.page, `assignment info overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(dimensions.right, `assignment panel overflow at ${width}px`).toBeLessThanOrEqual(width + 1);
    expect(dimensions.outside, `assignment control overflow at ${width}px`).toEqual([]);
    await workspace.screenshot({ path: test.info().outputPath(`assignment-info-${width}.png`), animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });

  const rubric = resolve(test.info().project.testDir, "fixtures", "b2-meaningful-rubric.docx");
  await workspace.locator('input[type="file"][accept*=".docx"]').setInputFiles(rubric);
  await expect(workspace.getByRole("status").filter({ hasText: "File rubrik tersimpan privat" })).toBeVisible();
  await workspace.getByLabel("Siapkan kutipan instruksi persis untuk ditinjau").check();
  await workspace.getByRole("button", { name: /Buka formulir kriteria|Perbarui sumber rubrik/ }).click();
  const draftingPanel = workspace.getByRole("region", { name: "Status bantuan AI" });
  await expect(draftingPanel.getByText("Mock tidak membuat usulan semantik")).toBeVisible({ timeout: 60_000 });
  await expect(draftingPanel.getByText("SIMULASI • data sintetis")).toBeVisible();
  await expect(draftingPanel.getByText("tidak ada usulan semantik dari provider mock.")).toBeVisible();
  await expect(workspace.getByRole("heading", { name: "Hasil parser rubrik · Siap ditinjau" })).toBeVisible({ timeout: 180_000 });
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const dimensions = await workspace.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      const outside = Array.from(element.querySelectorAll("button,input,select,textarea,.assignment-step,.assignment-stepper,.structured-card,.lint-panel,.rubric-evidence"))
        .filter((item) => item.getClientRects().length > 0)
        .filter((item) => { const rect = item.getBoundingClientRect(); return rect.left < bounds.left - 1 || rect.right > bounds.right + 1; });
      return { viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth, outside: outside.map((item) => item.tagName.toLowerCase() + "." + item.className), right: bounds.right };
    });
    expect(dimensions.page, `assignment criteria overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(dimensions.right, `assignment criteria panel overflow at ${width}px`).toBeLessThanOrEqual(width + 1);
    expect(dimensions.outside, `assignment criteria controls overflow at ${width}px`).toEqual([]);
    await workspace.screenshot({ path: test.info().outputPath(`assignment-criteria-${width}.png`), animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  const rubricExcerpt = workspace.locator(".rubric-evidence pre").filter({ hasText: demo.criterion.rubric_quote }).first();
  await expect(rubricExcerpt).toContainText(demo.criterion.rubric_quote);
  const excerptText = (await rubricExcerpt.textContent()) ?? "";
  expect(Array.from(excerptText).length).toBeGreaterThan(0);

  const criteriaSection = workspace.locator('section[aria-labelledby="criteria-title"]');
  const criterion = criteriaSection.locator("fieldset.structured-card").first();
  await criterion.getByLabel("Kode kriteria", { exact: true }).fill(demo.criterion.code);
  await criterion.getByLabel("Judul", { exact: true }).fill(demo.criterion.title);
  await criterion.getByLabel("Deskripsi resmi kriteria").fill(demo.criterion.descriptor);
  const requirement = criterion.locator(".structured-subsection").first();
  await requirement.getByLabel("Kode kewajiban").fill(demo.criterion.requirement_code);
  await requirement.getByLabel("Teks kewajiban bukti").fill(demo.criterion.requirement_text);
  const sourceSelect = requirement.getByLabel("Sumber rujukan");
  const rubricOption = (await sourceSelect.locator("option").allTextContents()).find((label) => label.startsWith("Rubrik · b2-meaningful-rubric.docx"));
  expect(rubricOption).toBeTruthy();
  await sourceSelect.selectOption({ label: rubricOption! });
  const citationSource = requirement.getByLabel("Teks sumber untuk memilih kutipan");
  const selectedRange = await selectQuote(page, citationSource, excerptText, demo.criterion.rubric_quote);
  const selectedQuote = requirement.locator(".selected-source-quote").first();
  await expect(selectedQuote).toContainText(demo.criterion.rubric_quote);
  await requirement.getByText("Detail teknis provenance").click();
  await expect(requirement.getByText(`Indeks code point: ${selectedRange.start}–${selectedRange.end} (akhir tidak termasuk)`)).toBeVisible();

  await criteriaSection.getByRole("button", { name: "Simpan kriteria" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Kriteria dan kewajiban tersimpan" })).toBeVisible();
  await criteriaSection.getByRole("button", { name: "Sahkan paket normatif" }).click();
  await expect(workspace.getByText("Paket kriteria disahkan pada")).toBeVisible();
  await workspace.getByRole("button", { name: "Lanjut ke rencana pemeriksaan" }).click();
  await expect(workspace.locator('section[aria-labelledby="numeric-title"]')).toHaveCount(0);

  const specSection = workspace.locator('section[aria-labelledby="spec-title"]');
  await specSection.getByRole("button", { name: "Buat draf rencana pemeriksaan" }).click();
  await expect(draftingPanel.getByText("Mock tidak membuat usulan semantik")).toBeVisible({ timeout: 60_000 });
  await expect(specSection.getByLabel("Editor rencana pemeriksaan terstruktur")).toBeVisible();
  const specElement = specSection.locator("fieldset.spec-element").first();
  await specElement.getByLabel("Pertanyaan keputusan positif").fill("Apakah uraian mahasiswa menjelaskan konsep yang ditetapkan dosen?");
  await specElement.getByRole("checkbox", { name: "Tabel" }).check();
  const factEditor = specSection.getByRole("group", { name: "Fakta tugas terdaftar" });
  await factEditor.getByRole("button", { name: "Tambah fakta tugas" }).click();
  await factEditor.getByLabel("Nama fakta").fill("submission_variant");
  await factEditor.getByLabel("Bentuk fakta").selectOption("string");
  await factEditor.getByLabel("Nilai yang diizinkan (satu per baris)").fill("original\nalternate");
  const applicability = specElement.locator(".structured-subsection").filter({ hasText: "Kapan pemeriksaan ini berlaku" });
  await applicability.locator("select").first().selectOption("fact_rule");
  const factRule = applicability.locator(".rule-node").first();
  await factRule.locator("select").nth(1).selectOption("task.submission_variant");
  await factRule.locator("input").last().fill("original");
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const dimensions = await workspace.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      const outside = Array.from(element.querySelectorAll("button,input,select,textarea,.assignment-step,.assignment-stepper,.structured-card,.lint-panel"))
        .filter((item) => item.getClientRects().length > 0)
        .filter((item) => { const rect = item.getBoundingClientRect(); return rect.left < bounds.left - 1 || rect.right > bounds.right + 1; });
      return { viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth, outside: outside.map((item) => item.tagName.toLowerCase() + "." + item.className), right: bounds.right };
    });
    expect(dimensions.page, `assignment plan overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(dimensions.right, `assignment plan panel overflow at ${width}px`).toBeLessThanOrEqual(width + 1);
    expect(dimensions.outside, `assignment plan controls overflow at ${width}px`).toEqual([]);
    await workspace.screenshot({ path: test.info().outputPath(`assignment-plan-${width}.png`), animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await specSection.getByRole("button", { name: "Simpan rencana dan jalankan pemeriksaan" }).click();
  await expect.poll(async () => specSection.innerText()).toContain("lint: lulus");
  await specSection.getByRole("button", { name: "Sahkan rencana dengan konfirmasi kesetiaan" }).click();
  await workspace.getByLabel("submission_variant nilai").selectOption("original");
  await workspace.getByLabel("submission_variant asal").fill("Dosen memverifikasi ketentuan varian pada instruksi tugas sintetis.");
  await workspace.getByRole("button", { name: "Simpan dan sahkan fakta dosen" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Fakta tugas disahkan dosen" })).toBeVisible();
  await expect(workspace).toContainText("Fakta tersimpan: 1");
  await workspace.getByRole("button", { name: "Lanjut ke pratinjau" }).click();
  await workspace.getByRole("button", { name: "Tampilkan pratinjau mahasiswa" }).click();
  const publishPreview = workspace.getByRole("article", { name: "Preview paket publik mahasiswa" });
  await expect(publishPreview).toContainText(instruction);
  await expect(publishPreview).toContainText(demo.criterion.requirement_text);
  await expect(publishPreview).toContainText("paragraph");
  await expect(publishPreview).toContainText("table");
  await expect(publishPreview).not.toContainText("fact_rule");
  await expect(publishPreview).not.toContainText("grounding_contract");
  await workspace.getByRole("button", { name: "Terbitkan versi ini" }).click();
  await expect(workspace.getByRole("heading", { name: "Snapshot terbit" })).toBeVisible();
  await workspace.getByRole("button", { name: "Daftar tugas" }).click();
  await workspace.getByRole("button", { name: `Buka tugas ${assignmentTitle}` }).click();
  await expect(workspace.getByRole("heading", { name: "Snapshot terbit" })).toBeVisible();
  await workspace.getByRole("button", { name: "Buat versi baru" }).click();
  await expect(workspace.getByRole("heading", { name: "Draf tugas" })).toBeVisible();
  await expect(workspace.getByLabel("Instruksi asli (maks. 30.000 karakter)")).toHaveValue(instruction);
  await expect(workspace.getByLabel("Ringkasan opsional")).toHaveCount(0);

  await page.getByRole("button", { name: "Keluar" }).click();
  await expect(page.getByLabel("Email")).toBeVisible();
  await page.getByLabel("Email").fill(studentEmail);
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("link", { name: courseLink }).click();
  const studentWorkspace = page.getByRole("region", { name: "Tugas mata kuliah" });
  await expect(studentWorkspace.getByRole("heading", { name: "Tugas mata kuliah" })).toBeVisible();
  await studentWorkspace.getByRole("button", { name: assignmentTitle }).click();
  const studentTask = page.locator("article.student-task-preview");
  await expect(studentTask).toContainText(instruction);
  await expect(studentTask).toContainText(demo.criterion.requirement_text);
  await expect(studentTask).toContainText("paragraph");
  await expect(studentTask).toContainText("table");
  await expect(studentTask).not.toContainText("fact_rule");
  await expect(studentTask).not.toContainText("grounding_contract");

  await page.getByRole("tab", { name: "Pengumpulan" }).click();
  const submission = page.getByRole("region", { name: "Kumpulkan tugas" });
  await submission.getByRole("button", { name: assignmentTitle }).click();
  const repositoryRoot = resolve(test.info().project.testDir, "../../..");
  const submissionFixtures = [
    resolve(repositoryRoot, "tests/fixtures/b1/native-two-pages.pdf"),
    resolve(repositoryRoot, "fixtures/b3/complex-submission.docx"),
    resolve(repositoryRoot, "fixtures/b3/complex-submission.xlsx"),
    resolve(repositoryRoot, "tests/fixtures/b1/rotated.jpeg"),
    resolve(repositoryRoot, "tests/fixtures/b1/printed.png"),
  ];
  await submission.locator('input[type="file"]').setInputFiles(submissionFixtures);
  await expect(submission.getByRole("status").filter({ hasText: "berhasil diunggah dan divalidasi" })).toBeVisible({ timeout: 60_000 });
  await submission.getByRole("checkbox", { name: /memeriksa berkas/ }).check();
  await submission.getByRole("button", { name: "Finalisasi pengumpulan" }).click();
  await expect(submission.getByText(/Receipt finalisasi v1/)).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => {
    const text = await submission.innerText();
    return text.match(/Status teknis parser: ([\w]+)/)?.[1] ?? "";
  }, { timeout: 480_000, intervals: [5_000, 10_000, 15_000] }).toBe("ready_for_review");
  await expect(submission).toContainText("complex-submission.xlsx");

  await page.getByRole("button", { name: "Keluar" }).click();
  await page.getByLabel("Email").fill(lecturerEmail);
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await page.getByRole("link", { name: courseLink }).click();
  await page.getByRole("tab", { name: "Pengumpulan" }).click();
  const queue = page.getByRole("region", { name: "Submission mahasiswa" });
  await queue.getByLabel("Pilih tugas").selectOption({ label: assignmentTitle });
  const submissionRow = queue.locator(".submission-list-row").first();
  await expect(submissionRow).toBeVisible({ timeout: 30_000 });
  await submissionRow.click();

  await queue.locator(".submission-file-select").filter({ hasText: "native-two-pages.pdf" }).click();
  const pdfViewer = queue.locator(".submission-detail-card > .submission-viewer-panel");
  await expect(pdfViewer.locator(".source-viewer canvas")).toBeVisible({ timeout: 30_000 });
  await pdfViewer.getByRole("navigation", { name: "Unit sumber" }).getByRole("button", { name: /^page PDF p\.2/ }).click();
  await expect(pdfViewer).toContainText("Halaman 2 / 2");

  await queue.locator(".submission-file-select").filter({ hasText: "complex-submission.docx" }).click();
  const docxViewer = queue.locator(".submission-detail-card > .submission-viewer-panel");
  const headerUnit = docxViewer.getByRole("navigation", { name: "Unit sumber" }).getByRole("button", { name: /paragraph word\/header/ }).first();
  await headerUnit.click();
  await expect(docxViewer.locator(".document-structure-viewer")).toContainText("Header sintetis: B3 parsing source");

  await queue.locator(".submission-file-select").filter({ hasText: "complex-submission.xlsx" }).click();
  const xlsxViewer = queue.locator(".submission-detail-card > .submission-viewer-panel");
  const hiddenSheetTab = xlsxViewer.getByRole("tab", { name: /Catatan internal.*tersembunyi/ });
  await hiddenSheetTab.click();
  await expect(xlsxViewer.getByRole("region", { name: "Grid Catatan internal" })).toBeVisible();
  const summarySheetTab = xlsxViewer.getByRole("tab", { name: "Ringkasan" });
  const sheetTabs = xlsxViewer.getByRole("tablist", { name: "Sheet workbook" }).getByRole("tab");
  const sheetNames = (await sheetTabs.allTextContents()).map((name) => name.trim());
  const selectedSheetIndex = await sheetTabs.evaluateAll((tabs) => tabs.findIndex((tab) => tab.getAttribute("aria-selected") === "true"));
  const nextSheet = sheetTabs.nth((selectedSheetIndex + 1) % sheetNames.length);
  await sheetTabs.nth(selectedSheetIndex).focus();
  await sheetTabs.nth(selectedSheetIndex).press("ArrowRight");
  await expect(nextSheet).toHaveAttribute("aria-selected", "true");
  await summarySheetTab.click();
  await expect(summarySheetTab).toHaveAttribute("aria-selected", "true");
  await expect(xlsxViewer.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", /tab-/);
  const formulaCell = xlsxViewer.getByRole("button", { name: /D3: formula =SUM/ });
  await expect(formulaCell).toBeVisible();
  await formulaCell.click();
  await expect(xlsxViewer.locator(".spreadsheet-grid-detail")).toContainText("Nilai cache (belum diverifikasi)");
  await expect(
    xlsxViewer.getByRole("tablist", { name: "Sheet workbook" }).getByRole("tab", { name: /Rekap Nilai Semester Akademik/ }),
  ).toBeVisible();
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const dimensions = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      page: document.documentElement.scrollWidth,
    }));
    expect(dimensions.page, `XLSX viewer overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
    const viewerBounds = await xlsxViewer.evaluate((element) => {
      const panel = element.querySelector('[role="tabpanel"]');
      const grid = element.querySelector(".spreadsheet-grid-scroll");
      const tabs = element.querySelector('[role="tablist"]');
      return {
        viewerRight: Math.ceil(element.getBoundingClientRect().right),
        panelRight: panel ? Math.ceil(panel.getBoundingClientRect().right) : 0,
        gridClientWidth: grid?.clientWidth ?? 0,
        gridScrollWidth: grid?.scrollWidth ?? 0,
        tabsClientWidth: tabs?.clientWidth ?? 0,
        tabsScrollWidth: tabs?.scrollWidth ?? 0,
      };
    });
    expect(viewerBounds.viewerRight).toBeLessThanOrEqual(width + 1);
    expect(viewerBounds.panelRight).toBeLessThanOrEqual(width + 1);
    expect(viewerBounds.gridScrollWidth).toBeGreaterThanOrEqual(viewerBounds.gridClientWidth);
    if (width === 360) expect(viewerBounds.tabsScrollWidth).toBeGreaterThan(viewerBounds.tabsClientWidth);
    await xlsxViewer.screenshot({
      path: test.info().outputPath(`b7-xlsx-${width}.png`),
      animations: "disabled",
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });

  await queue.locator(".submission-file-select").filter({ hasText: "rotated.jpeg" }).click();
  const imageViewer = queue.locator(".submission-detail-card > .submission-viewer-panel");
  await expect(imageViewer.locator(".image-source-viewer img")).toBeVisible();
  await expect(imageViewer.locator(".image-anchor-overlay")).toBeVisible();

  const reviewWorkspace = queue.getByRole("region", { name: "Pemeriksaan dan publikasi" });
  await expect(reviewWorkspace.getByRole("heading", { name: "Pemeriksaan dan publikasi" })).toBeVisible();
  const releasesResponse = await page.request.get("/api/v1/runtime/releases");
  expect(releasesResponse.ok()).toBeTruthy();
  const releases = (await releasesResponse.json()) as { releases: Array<Record<string, unknown>> };
  const activeBaseline = releases.releases.find((release) => release.state === "active");
  expect(activeBaseline).toMatchObject({
    version_label: "align-b5-synthetic-baseline-v2",
    provider_mode: "mock",
    data_mode: "synthetic",
  });
  expect(activeBaseline?.capabilities_available).toContain("b5_executor");
  expect(activeBaseline?.paid_calls).toBe("blocked_before_network");
  await reviewWorkspace.getByRole("button", { name: "Jalankan analisis" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: /Run (partial|completed)/ })).toBeVisible({ timeout: 240_000 });
  await expect(reviewWorkspace).toContainText("provider mock · data synthetic");
  await expect(reviewWorkspace).toContainText("Applicability: applicable");
  await expect(reviewWorkspace).toContainText("human_judgment_required");
  await expect(reviewWorkspace).toContainText("semantic human_required");
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const viewportWidth = await page.evaluate(() => document.documentElement.clientWidth);
    const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const overflowingElements = documentWidth > viewportWidth + 1
      ? await page.evaluate(() => Array.from(document.querySelectorAll("body *"))
        .map((element) => {
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return {
            tag: element.tagName.toLowerCase(),
            className: typeof element.className === "string" ? element.className : "",
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            scrollWidth: element.scrollWidth,
            clientWidth: element.clientWidth,
            overflowX: style.overflowX,
          };
        })
        .filter((item) => item.right > window.innerWidth + 1 || item.left < -1)
        .sort((left, right) => right.right - left.right)
        .slice(0, 12))
      : [];
    expect(documentWidth, `horizontal overflow at ${width}px: ${JSON.stringify(overflowingElements)}`)
      .toBeLessThanOrEqual(viewportWidth + 1);
    if (width < 768) {
      const reviewTabs = reviewWorkspace.getByRole("tablist", { name: "Panel review" });
      const inspectTab = reviewTabs.getByRole("tab", { name: "Pemeriksaan" });
      const evidenceTab = reviewTabs.getByRole("tab", { name: "Bukti" });
      await inspectTab.focus();
      await inspectTab.press("ArrowRight");
      await expect(evidenceTab).toHaveAttribute("aria-selected", "true");
      await expect(reviewWorkspace.getByRole("tabpanel", { name: "Bukti" })).toBeVisible();
      await evidenceTab.press("Home");
      await expect(inspectTab).toHaveAttribute("aria-selected", "true");
    }
    await reviewWorkspace.screenshot({
      path: test.info().outputPath(`b7-review-${width}.png`),
      animations: "disabled",
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await queue.locator(".submission-file-select").filter({ hasText: "native-two-pages.pdf" }).click();
  const originalViewer = queue.locator(".submission-detail-card > .submission-viewer-panel");
  await originalViewer.getByRole("navigation", { name: "Unit sumber" }).getByRole("button", { name: /page PDF p\.1/ }).click();
  await expect(originalViewer.locator(".source-unit-content")).toContainText("PDF p.1");
  await originalViewer.getByRole("button", { name: "Gunakan lokasi ini sebagai evidence manual" }).click();
  await expect(reviewWorkspace).toContainText("Lokasi sumber terpilih: PDF hlm. 1");
  await reviewWorkspace.getByRole("button", { name: "Simpan evidence manual" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Evidence manual ditambahkan dari lokasi unit sumber" })).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Ikat evidence ke elemen" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Binding sumber ke elemen" })).toBeVisible();
  const annotationReason = reviewWorkspace.getByLabel("Alasan anotasi / koreksi");
  await annotationReason.fill("Binding sintetis dihapus setelah pemeriksaan viewer.");
  await reviewWorkspace.getByRole("button", { name: "Nonaktifkan anotasi" }).last().click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Anotasi dinonaktifkan" })).toBeVisible();
  await expect(reviewWorkspace.getByText("Lihat histori append-only").first()).toBeVisible();
  const bindingAnnotation = reviewWorkspace.locator(".b4-annotation-card").last();
  await bindingAnnotation.getByText("Lihat histori append-only").click();
  await bindingAnnotation.locator("details").getByRole("button", { name: "Buka sumber asli untuk revisi #1" }).click();
  await expect(reviewWorkspace.getByRole("region", { name: "Sumber anotasi terpilih" })).toContainText("PDF hlm. 1");
  await expect(reviewWorkspace.locator(".source-unit-content")).toContainText("PDF p.1");
  const elementCount = await reviewWorkspace.locator(".b4-element-button").count();
  expect(elementCount).toBeGreaterThan(0);
  for (let index = 0; index < elementCount; index += 1) {
    await reviewWorkspace.locator(".b4-element-button").nth(index).click();
    await reviewWorkspace.getByLabel("Resolusi").selectOption("observed_no_issue");
    const sourceChecklist = reviewWorkspace.locator(".b4-review-columns .b4-review-panel").first()
      .locator("fieldset.b4-select-list").first().getByRole("checkbox");
    if (await sourceChecklist.count()) await sourceChecklist.first().check();
    const saveDecision = reviewWorkspace.getByRole("button", { name: "Simpan keputusan elemen" });
    await saveDecision.scrollIntoViewIfNeeded();
    await saveDecision.click();
    await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Keputusan elemen disimpan" })).toBeVisible();
  }
  const manualFinding = reviewWorkspace.locator(".b4-action-card").filter({ hasText: "Kategori resmi" });
  await expect(manualFinding).toBeVisible();
  await manualFinding.getByLabel("Kategori resmi").selectOption("missing_requirement");
  await manualFinding.getByLabel("Pernyataan temuan").fill("Dosen menilai jawaban belum menjelaskan konsep yang diminta.");
  await manualFinding.locator("fieldset.b4-select-list").first().getByRole("checkbox").first().check();
  await manualFinding.getByRole("button", { name: "Simpan temuan manual" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Temuan manual dosen ditambahkan" })).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Selesaikan pemeriksaan" }).click();
  await expect(reviewWorkspace.getByText(/Pemeriksaan lengkap dan hash review dibekukan/)).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Buat draft dengan provider" }).click();
  await expect(reviewWorkspace.getByText(/Draft model · draft/)).toBeVisible({ timeout: 120_000 });
  await expect(reviewWorkspace.getByText("SIMULASI • data sintetis")).toBeVisible();
  await expect(reviewWorkspace.getByText(/SIMULASI • data sintetis: Dosen menilai jawaban/)).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Buat feedback template" }).click();
  await expect(reviewWorkspace.getByText(/Draft template · draft/)).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Setujui feedback" }).click();
  await expect(reviewWorkspace.getByText(/Draft template · approved/)).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await reviewWorkspace.getByRole("button", { name: "Terbitkan hasil" }).click();
  await expect(reviewWorkspace.getByText("Snapshot publikasi immutable berhasil dibuat.")).toBeVisible();

  await page.getByRole("button", { name: "Keluar" }).click();
  await page.getByLabel("Email").fill(studentEmail);
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await page.getByRole("link", { name: courseLink }).click();
  const releasedStudentWorkspace = page.getByRole("region", { name: "Tugas mata kuliah" });
  await releasedStudentWorkspace.getByRole("button", { name: assignmentTitle }).click();
  await page.getByRole("tab", { name: "Pengumpulan" }).click();
  const studentSubmission = page.getByRole("region", { name: "Kumpulkan tugas" });
  await studentSubmission.getByRole("button", { name: assignmentTitle }).click();
  const officialResult = studentSubmission.locator("section.student-publication");
  await expect(officialResult.getByText(/Publikasi/)).toBeVisible();
  await expect(officialResult.getByRole("link", { name: "Unduh JSON" })).toBeVisible();
  await expect(officialResult.getByRole("link", { name: "Unduh Markdown" })).toBeVisible();
  const markdownDownload = page.waitForEvent("download");
  await officialResult.getByRole("link", { name: "Unduh Markdown" }).click();
  expect((await markdownDownload).suggestedFilename()).toMatch(/\.md$/);

  const browser = page.context().browser();
  if (!browser) throw new Error("Playwright browser context is unavailable for the touch/a11y check.");
  const touchContext = await browser.newContext({ viewport: { width: 360, height: 800 }, hasTouch: true });
  const touchPage = await touchContext.newPage();
  try {
    await touchPage.goto("/login");
    await touchPage.getByLabel("Email").fill(lecturerEmail);
    await touchPage.getByLabel("Kata sandi", { exact: true }).fill(password);
    await touchPage.getByRole("button", { name: "Masuk" }).tap();
    await expect(touchPage.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
    await touchPage.getByRole("link", { name: courseLink }).tap();
    await touchPage.getByRole("tab", { name: "Pengumpulan" }).tap();
    const touchQueue = touchPage.getByRole("region", { name: "Submission mahasiswa" });
    await touchQueue.getByLabel("Pilih tugas").selectOption({ label: assignmentTitle });
    const touchSubmission = touchQueue.locator(".submission-list-row").first();
    await expect(touchSubmission).toBeVisible();
    await touchSubmission.tap();
    const touchFile = touchQueue.locator(".submission-file-select").filter({ hasText: "native-two-pages.pdf" });
    await touchFile.tap();
    const touchViewer = touchQueue.locator(".submission-detail-card > .submission-viewer-panel");
    const sourceNavigation = touchViewer.getByRole("navigation", { name: "Unit sumber" });
    const pageTwo = sourceNavigation.getByRole("button", { name: /^page PDF p\.2/ });
    const accessibleTree = await sourceNavigation.ariaSnapshot();
    expect(accessibleTree).toContain('button "page PDF p.2');
    await pageTwo.tap();
    await expect(touchViewer).toContainText("Halaman 2 / 2");
  } finally {
    await touchContext.close();
  }
});
