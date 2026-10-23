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
  const queue = page.getByRole("region", { name: "Pekerjaan mahasiswa" });
  await queue.getByLabel("Tugas", { exact: true }).selectOption({ label: assignmentTitle });
  const submissionRow = queue.locator(".queue-item").first();
  await expect(submissionRow).toBeVisible({ timeout: 30_000 });
  await submissionRow.getByRole("link", { name: "Buka pemeriksaan" }).click();
  await expect(page).toHaveURL(/\/courses\/[^/]+\/submissions\//);
  const reviewWorkspace = page.getByRole("region", { name: "Pemeriksaan dan feedback" });
  await expect(reviewWorkspace.getByLabel("Keputusan pemeriksaan", { exact: true })).toHaveValue("");
  await expect(reviewWorkspace.getByText("Manifest SHA-256:")).toHaveCount(0);
  await expect(reviewWorkspace.getByLabel("Pernyataan temuan", { exact: true })).not.toBeVisible();
  const evidencePanel = reviewWorkspace.getByRole("tabpanel", { name: "Bukti", exact: true });
  const files = evidencePanel.getByLabel("Berkas asli", { exact: true });
  await files.selectOption({ label: "native-two-pages.pdf" });
  const pdfViewer = evidencePanel.locator(".submission-viewer-panel");
  await expect(pdfViewer.locator(".source-viewer canvas")).toBeVisible({ timeout: 30_000 });
  const pdfOptions = pdfViewer.getByLabel("Lokasi dalam berkas").locator("option");
  const secondPage = await pdfOptions.evaluateAll((options) => options.find((option) => option.textContent?.includes("Halaman 2"))?.getAttribute("value"));
  if (!secondPage) throw new Error("PDF page-two anchor is missing.");
  await pdfViewer.getByLabel("Lokasi dalam berkas").selectOption(secondPage);
  await expect(pdfViewer).toContainText("Halaman 2 / 2");

  await files.selectOption({ label: "complex-submission.docx" });
  await expect(evidencePanel.locator(".document-structure-viewer")).toBeVisible();
  const docxFileId = await files.inputValue();
  const sourcePage = await page.request.get(`/api/v1/files/${docxFileId}/source-units?limit=100`);
  expect(sourcePage.ok()).toBeTruthy();
  const sourceData = await sourcePage.json() as { items: Array<{ id: string; unit_type: string; anchor: { part?: string } }> };
  const headerSource = sourceData.items.find((item) => item.unit_type === "paragraph" && item.anchor.part?.includes("header"));
  if (!headerSource) throw new Error("DOCX header source is missing from inventory.");
  const docxSourceSelect = evidencePanel.getByLabel("Lokasi dalam berkas");
  while (await docxSourceSelect.locator(`option[value="${headerSource.id}"]`).count() === 0) {
    await evidencePanel.getByRole("button", { name: "Muat lokasi berikutnya" }).click();
  }
  await docxSourceSelect.selectOption(headerSource.id);
  await expect(evidencePanel.locator(".document-structure-viewer")).toContainText("Header sintetis: B3 parsing source");

  await files.selectOption({ label: "complex-submission.xlsx" });
  const xlsxViewer = evidencePanel.locator(".submission-viewer-panel");
  await xlsxViewer.getByRole("tab", { name: /Catatan internal.*tersembunyi/ }).click();
  await expect(xlsxViewer.getByRole("region", { name: "Grid Catatan internal" })).toBeVisible();
  await xlsxViewer.getByRole("tab", { name: "Ringkasan", exact: true }).click();
  const sheetTabs = xlsxViewer.getByRole("tablist", { name: "Sheet workbook" }).getByRole("tab");
  await sheetTabs.first().focus(); await sheetTabs.first().press("ArrowRight");
  await expect(sheetTabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await xlsxViewer.getByRole("tab", { name: "Ringkasan", exact: true }).click();
  await xlsxViewer.getByRole("button", { name: /D3: formula =SUM/ }).click();
  await expect(xlsxViewer.locator(".spreadsheet-grid-detail")).toContainText("Nilai cache (belum diverifikasi)");
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    if (width < 768) await reviewWorkspace.getByRole("tab", { name: "Bukti", exact: true }).click();
    const dimensions = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth }));
    expect(dimensions.page, `XLSX review overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
    await reviewWorkspace.screenshot({ path: test.info().outputPath(`review-xlsx-${width}.png`), animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await files.selectOption({ label: "rotated.jpeg" });
  await expect(evidencePanel.locator(".image-source-viewer img")).toBeVisible();
  await expect(evidencePanel.locator(".image-anchor-overlay")).toBeVisible();

  await reviewWorkspace.getByText("Bantuan analisis (opsional)", { exact: true }).click();
  const release = reviewWorkspace.getByLabel("Konfigurasi bantuan analisis");
  const mockRelease = await release.locator("option").evaluateAll((options) => options.find((item) => item.textContent?.startsWith("Simulasi"))?.getAttribute("value"));
  if (!mockRelease) throw new Error("This regression requires an explicitly labelled mock release; live provider is prohibited.");
  await release.selectOption(mockRelease);
  page.once("dialog", (dialog) => dialog.accept());
  await reviewWorkspace.getByRole("button", { name: "Jalankan analisis", exact: true }).click();
  const adopt = reviewWorkspace.getByRole("button", { name: "Gunakan hasil pada pemeriksaan baru" });
  await expect(adopt).toBeVisible({ timeout: 240_000 });
  page.once("dialog", (dialog) => dialog.accept());
  await adopt.click();
  await expect(reviewWorkspace.getByText(/SIMULASI — bukan pemeriksaan semantik provider nyata/)).toBeVisible();

  await files.selectOption({ label: "native-two-pages.pdf" });
  await evidencePanel.getByRole("button", { name: "Pilih lokasi ini sebagai bukti" }).click();
  await evidencePanel.getByRole("button", { name: "Simpan bukti manual" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: /Bukti manual tersimpan/ })).toBeVisible();
  await evidencePanel.getByRole("button", { name: "Ubah tautan bukti" }).click();
  await evidencePanel.getByRole("button", { name: "Simpan tautan bukti" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Tautan bukti ke pemeriksaan tersimpan" })).toBeVisible();
  await evidencePanel.getByRole("button", { name: /Anotasi dan koreksi tersimpan/ }).click();
  await evidencePanel.getByLabel("Alasan anotasi atau koreksi").fill("Tautan sintetis diperbaiki setelah pemeriksaan sumber.");
  page.once("dialog", (dialog) => dialog.accept());
  await evidencePanel.getByRole("button", { name: "Nonaktifkan tautan", exact: true }).last().click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: /Anotasi dinonaktifkan/ })).toBeVisible();
  await evidencePanel.getByText(/Riwayat perubahan/).last().click();
  await evidencePanel.getByRole("button", { name: "Lihat lokasi revisi ini" }).last().click();
  await expect(evidencePanel.getByRole("region", { name: "Sumber anotasi terpilih" })).toContainText("Halaman");

  const questionSelect = reviewWorkspace.getByLabel("Pilih pertanyaan", { exact: true });
  const questionIds = await questionSelect.locator("option").evaluateAll((options) => options.map((item) => (item as HTMLOptionElement).value));
  // Unsaved decisions survive stage/mobile-tab changes; leaving an element must warn.
  await reviewWorkspace.getByLabel("Alasan atau keterbatasan", { exact: true }).fill("Catatan belum disimpan.");
  if (questionIds.length > 1) {
    page.once("dialog", (dialog) => dialog.dismiss());
    await questionSelect.selectOption(questionIds[1]);
    await expect(reviewWorkspace.getByLabel("Alasan atau keterbatasan", { exact: true })).toHaveValue("Catatan belum disimpan.");
  }
  page.once("dialog", (dialog) => dialog.accept());
  await reviewWorkspace.getByRole("button", { name: "Batalkan isian", exact: true }).click();
  for (const id of questionIds) {
    await questionSelect.selectOption(id);
    await reviewWorkspace.getByLabel("Keputusan pemeriksaan", { exact: true }).selectOption("observed_no_issue");
    await reviewWorkspace.getByRole("button", { name: "Simpan keputusan", exact: true }).click();
    await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Keputusan pemeriksaan tersimpan" })).toBeVisible();
  }
  await reviewWorkspace.getByRole("button", { name: "Tambahkan temuan", exact: true }).click();
  const manualFinding = reviewWorkspace.locator('[data-edit-scope="finding"]').first();
  await manualFinding.getByLabel("Pernyataan temuan", { exact: true }).fill("Bukti yang diminta belum dijelaskan pada bagian konsep.");
  await manualFinding.getByRole("checkbox").first().check();
  await manualFinding.getByRole("button", { name: "Simpan temuan", exact: true }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Temuan manual dosen ditambahkan" })).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Selesaikan pemeriksaan", exact: true }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Pemeriksaan diselesaikan" })).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Feedback", exact: true }).click();
  await reviewWorkspace.getByRole("button", { name: "Susun draf simulasi" }).click();
  await expect(reviewWorkspace.getByText(/Draf bantuan analisis/)).toBeVisible({ timeout: 120_000 });
  await reviewWorkspace.getByRole("button", { name: "Susun dari template" }).click();
  await expect(reviewWorkspace.getByText(/Draf template/)).toBeVisible();
  const feedbackText = reviewWorkspace.getByLabel("Teks feedback", { exact: true });
  await feedbackText.fill(`${await feedbackText.inputValue()}\n\nTinjau sumber yang ditautkan sesuai kewajiban tugas.`);
  await reviewWorkspace.getByRole("button", { name: "Simpan suntingan feedback" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Revisi draf feedback tersimpan" })).toBeVisible();
  const storedBody = await feedbackText.inputValue();
  await page.reload();
  await reviewWorkspace.getByRole("button", { name: "Feedback", exact: true }).click();
  await expect(feedbackText).toHaveValue(storedBody);
  await reviewWorkspace.getByRole("button", { name: "Setujui feedback", exact: true }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Feedback disetujui" })).toBeVisible();
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    const dimensions = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth }));
    expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport + 1);
    await reviewWorkspace.screenshot({ path: test.info().outputPath(`review-approved-${width}.png`), animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  page.once("dialog", async (dialog) => { expect(dialog.message()).toContain(storedBody); await dialog.accept(); });
  await reviewWorkspace.getByRole("button", { name: "Terbitkan hasil", exact: true }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: "Hasil resmi berhasil diterbitkan" })).toBeVisible();
  await page.reload();
  await reviewWorkspace.getByRole("button", { name: "Riwayat", exact: true }).click();
  await expect(reviewWorkspace.getByText(/Publikasi 1/)).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Lihat hasil tersimpan" }).first().click();
  await expect(reviewWorkspace.getByRole("heading", { name: "Hasil resmi tersimpan" })).toBeVisible();
  // UI-only response projections stress long labels and repeated limitations; original DB/files remain untouched.
  await page.route("**/api/v1/reviews/*", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const data = await response.json();
    if (Array.isArray(data.source_files)) for (const file of data.source_files) file.name = `Nama berkas sangat panjang ${"penjelasan-konsep-".repeat(20)}.docx`;
    await route.fulfill({ response, json: data });
  });
  await page.route("**/api/v1/files/*/source-units?*", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.limitations = Array.from({ length: 30 }, () => ({ code: "synthetic_ui_limitation", message: "Fixture tampilan: bagian sumber belum dapat dibaca; periksa original dan pertahankan batas cakupan." }));
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  await expect(reviewWorkspace.getByLabel("Pilih pertanyaan", { exact: true })).toBeVisible();
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    if (width < 768) await reviewWorkspace.getByRole("tab", { name: "Bukti", exact: true }).click();
    const limitation = reviewWorkspace.getByText("Ada 30 catatan keterbacaan sumber · 1 jenis", { exact: true });
    await expect(limitation).toBeVisible();
    if (!(await limitation.locator("..").getAttribute("open"))) await limitation.click();
    const dimensions = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth }));
    expect(dimensions.page, `Long labels and limitations overflow at ${width}px`).toBeLessThanOrEqual(dimensions.viewport + 1);
    await reviewWorkspace.screenshot({ path: test.info().outputPath(`review-long-labels-${width}.png`), animations: "disabled" });
  }
  await page.unroute("**/api/v1/reviews/*");
  await page.unroute("**/api/v1/files/*/source-units?*");
  await page.getByRole("link", { name: "← Kembali ke pengumpulan" }).click();
  await expect(queue.getByLabel("Tugas", { exact: true }).locator("option:checked")).toHaveText(assignmentTitle);
  await expect(queue.locator(".queue-item").first()).toContainText("Sudah diterbitkan");

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
    const touchQueue = touchPage.getByRole("region", { name: "Pekerjaan mahasiswa" });
    await touchQueue.getByLabel("Tugas", { exact: true }).selectOption({ label: assignmentTitle });
    await touchQueue.getByRole("link", { name: "Buka pemeriksaan" }).first().tap();
    const touchReview = touchPage.getByRole("region", { name: "Pemeriksaan dan feedback" });
    const touchTabs = touchReview.getByRole("tablist", { name: "Panel review" });
    await touchTabs.getByRole("tab", { name: "Pemeriksaan", exact: true }).focus();
    await touchTabs.getByRole("tab", { name: "Pemeriksaan", exact: true }).press("ArrowRight");
    await expect(touchTabs.getByRole("tab", { name: "Bukti", exact: true })).toHaveAttribute("aria-selected", "true");
    await touchTabs.getByRole("tab", { name: "Bukti", exact: true }).tap();
    const touchEvidence = touchReview.getByRole("tabpanel", { name: "Bukti", exact: true });
    await touchEvidence.getByLabel("Berkas asli", { exact: true }).selectOption({ label: "native-two-pages.pdf" });
    const sourceNavigation = touchEvidence.getByLabel("Lokasi dalam berkas");
    const pageTwoId = await sourceNavigation.locator("option").evaluateAll((options) => options.find((option) => option.textContent?.includes("Halaman 2"))?.getAttribute("value"));
    if (!pageTwoId) throw new Error("Second-page source anchor is unavailable for touch regression.");
    expect(await touchEvidence.ariaSnapshot()).toContain("Lokasi dalam berkas");
    await sourceNavigation.selectOption(pageTwoId);
    await expect(touchEvidence).toContainText("Halaman 2 / 2");

  } finally {
    await touchContext.close();
  }
});
