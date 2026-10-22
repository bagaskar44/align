import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("lecturer builds and publishes a B2 task from shared rubric parser evidence", async ({ page }) => {
  test.setTimeout(900_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  page.setDefaultTimeout(10_000);
  const password = process.env.ALIGN_SEED_PASSWORD;
  const lecturerEmail = process.env.ALIGN_DEMO_LECTURER_EMAIL ?? "dosen@align.local";
  const studentEmail = process.env.ALIGN_DEMO_STUDENT_EMAIL ?? "mahasiswa@align.local";
  const courseCode = process.env.ALIGN_DEMO_COURSE_CODE ?? "IF101";
  const demoLabel = process.env.ALIGN_DEMO_LABEL ?? "B2 Playwright";
  const courseLink = new RegExp(courseCode);
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E flow.");

  await page.goto("/login");
  await page.getByLabel("Email").fill(lecturerEmail);
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("link", { name: courseLink }).click();

  // The synthetic seed intentionally contains no approved context. Create one
  // through the B1 lecturer flow, then reload so B2 receives that approved ID.
  await page.getByLabel("Deskripsi konteks").fill("Konteks sintetis untuk verifikasi tugas B2.");
  await page.getByRole("button", { name: "Buat draft", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Draft konteks manual dibuat" })).toBeVisible();
  await page.getByRole("button", { name: "Sahkan versi ini" }).click();
  await expect(page.getByRole("status").filter({ hasText: "disahkan" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Tugas dan spesifikasi" })).toBeVisible();

  const workspace = page.getByRole("region", { name: "Tugas dan spesifikasi" });
  await expect(workspace).toBeVisible();
  const assignmentTitle = `${demoLabel} ${Date.now()}`;
  await workspace.getByLabel("Judul tugas").fill(assignmentTitle);
  await workspace.getByRole("button", { name: "Buat draf tugas" }).click();
  await expect(workspace.getByRole("heading", { name: "Draf assignment" })).toBeVisible();

  const instruction = "Jelaskan ringkasan konsep dari sumber rubrik sintetis.";
  await workspace.getByLabel("Instruksi asli (maks. 30.000 karakter)").fill(instruction);
  await workspace.getByRole("button", { name: "Simpan versi" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Versi tugas tersimpan" })).toBeVisible();

  const rubric = resolve(test.info().project.testDir, "fixtures", "kk_context.docx");
  await workspace.locator('input[type="file"][accept*=".docx"]').setInputFiles(rubric);
  await expect(workspace.getByRole("status").filter({ hasText: "File rubrik tersimpan privat" })).toBeVisible();
  await workspace.getByLabel("Usulkan kutipan instruksi persis (mock, tanpa menafsirkan kewajiban)").check();
  await workspace.getByRole("button", { name: "Siapkan editor draft" }).click();
  const draftingPanel = workspace.getByRole("region", { name: "Bantuan drafting provider" });
  await expect(draftingPanel.getByText("mock_abstention")).toBeVisible({ timeout: 60_000 });
  await expect(draftingPanel.getByText("SIMULASI • data sintetis")).toBeVisible();
  await expect(draftingPanel.getByText("tidak ada usulan semantik dari provider mock.")).toBeVisible();
  await expect(workspace.getByRole("heading", { name: "Hasil parser rubrik · ready_for_review" })).toBeVisible({ timeout: 180_000 });
  const rubricExcerpt = workspace.locator(".rubric-evidence pre").first();
  await expect(rubricExcerpt).toContainText("Kode Mata Kuliah");
  const excerptText = (await rubricExcerpt.textContent()) ?? "";
  expect(Array.from(excerptText).length).toBeGreaterThan(0);

  const criteriaSection = workspace.locator('section[aria-labelledby="criteria-title"]');
  const criterion = criteriaSection.locator("fieldset.structured-card").first();
  await criterion.getByLabel("Kode", { exact: true }).fill("C1");
  await criterion.getByLabel("Judul", { exact: true }).fill("Uraian konsep");
  await criterion.getByLabel("Deskripsi criterion resmi").fill("Uraian konsep berdasarkan sumber yang ditinjau dosen.");
  const requirement = criterion.locator(".structured-subsection").first();
  await requirement.getByLabel("Kode requirement").fill("R1");
  await requirement.getByLabel("Obligation key").fill("concept.explanation");
  await requirement.getByLabel("Teks requirement").fill(excerptText);
  const sourceSelect = requirement.getByLabel("Jenis sumber");
  const rubricOption = (await sourceSelect.locator("option").allTextContents()).find((label) => label.startsWith("Rubrik ·"));
  expect(rubricOption).toBeTruthy();
  await sourceSelect.selectOption({ label: rubricOption! });
  await requirement.getByLabel("Akhir", { exact: true }).fill(String(Array.from(excerptText).length));
  await expect(requirement.getByLabel("Cuplikan yang dirujuk")).toHaveValue(excerptText);

  await criteriaSection.getByRole("button", { name: "Simpan criteria" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Criteria dan requirements tersimpan" })).toBeVisible();
  await criteriaSection.getByRole("button", { name: "Sahkan paket normatif" }).click();
  await expect(workspace.getByText("Paket normatif disahkan pada")).toBeVisible();

  const numericSection = workspace.locator('section[aria-labelledby="numeric-title"]');
  await numericSection.getByRole("button", { name: "Tambah konversi" }).click();
  await numericSection.getByLabel("Dari").selectOption("percent");
  await numericSection.getByLabel("Ke").selectOption("ratio");
  await numericSection.getByRole("button", { name: "Buat NumericPolicy draft" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "NumericPolicy draft dibuat" })).toBeVisible();
  const numericPolicyRow = numericSection.locator(".list-row").filter({ hasText: "numeric-comparison v1" });
  await expect(numericPolicyRow).toContainText("draft");
  await numericPolicyRow.getByRole("button", { name: "Sahkan" }).click();
  await expect(numericPolicyRow).toContainText("approved");

  const specSection = workspace.locator('section[aria-labelledby="spec-title"]');
  await specSection.getByRole("button", { name: "Buat spec draft" }).click();
  await expect(draftingPanel.getByText("mock_abstention")).toBeVisible({ timeout: 60_000 });
  await expect(specSection.getByLabel("Editor CheckSpecification terstruktur")).toBeVisible();
  const specElement = specSection.locator("fieldset.spec-element").first();
  await specElement.getByLabel("Pertanyaan keputusan positif").fill("Apakah uraian mahasiswa menjelaskan konsep yang ditetapkan dosen?");
  await specElement.getByRole("checkbox", { name: "table" }).check();
  const factEditor = specSection.getByRole("group", { name: "Fakta tugas terdaftar" });
  await factEditor.getByRole("button", { name: "Tambah fact" }).click();
  await factEditor.getByLabel("Kunci").fill("submission_variant");
  await factEditor.getByLabel("Tipe").selectOption("string");
  await factEditor.getByLabel("Allowed values (satu per baris)").fill("original\nalternate");
  const applicability = specElement.locator(".structured-subsection").filter({ hasText: "Applicability" });
  await applicability.locator("select").first().selectOption("fact_rule");
  const factRule = applicability.locator(".rule-node").first();
  await factRule.locator("select").nth(1).selectOption("task.submission_variant");
  await factRule.locator("input").last().fill("original");
  await specSection.getByRole("button", { name: "Simpan spec dan jalankan lint" }).click();
  await expect.poll(async () => specSection.innerText()).toContain("lint: passed");
  await specSection.getByRole("button", { name: "Sahkan spec dengan konfirmasi fidelity" }).click();
  await expect(workspace.getByRole("button", { name: "Terbitkan versi" })).toBeVisible();
  await workspace.getByRole("button", { name: "Tampilkan preview mahasiswa" }).click();
  const publishPreview = workspace.getByRole("article", { name: "Preview paket publik mahasiswa" });
  await expect(publishPreview).toContainText(instruction);
  await expect(publishPreview).toContainText(excerptText);
  await expect(publishPreview).toContainText("paragraph");
  await expect(publishPreview).toContainText("table");
  await expect(publishPreview).not.toContainText("fact_rule");
  await expect(publishPreview).not.toContainText("grounding_contract");
  await workspace.getByRole("button", { name: "Terbitkan versi" }).click();
  await expect(workspace.getByRole("heading", { name: "Snapshot terbit" })).toBeVisible();

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
  await expect(studentTask).toContainText(excerptText);
  await expect(studentTask).toContainText("paragraph");
  await expect(studentTask).toContainText("table");
  await expect(studentTask).not.toContainText("fact_rule");
  await expect(studentTask).not.toContainText("grounding_contract");

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

  await queue.locator(".submission-file-select").filter({ hasText: "rotated.jpeg" }).click();
  const imageViewer = queue.locator(".submission-detail-card > .submission-viewer-panel");
  await expect(imageViewer.locator(".image-source-viewer img")).toBeVisible();
  await expect(imageViewer.locator(".image-anchor-overlay")).toBeVisible();

  const reviewWorkspace = queue.getByRole("region", { name: "Pemeriksaan dan publikasi" });
  await expect(reviewWorkspace.getByRole("heading", { name: "Pemeriksaan dan publikasi" })).toBeVisible();
  await reviewWorkspace.getByRole("button", { name: "Jalankan analisis" }).click();
  await expect(reviewWorkspace.getByRole("status").filter({ hasText: /Run (partial|completed)/ })).toBeVisible({ timeout: 240_000 });
  await expect(reviewWorkspace).toContainText("provider mock · data synthetic");
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
