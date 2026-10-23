import { expect, test } from "./fixtures";

async function checkAssignmentLayout(page: import("@playwright/test").Page, workspace: import("@playwright/test").Locator, label: string) {
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    const dimensions = await workspace.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      const outside = Array.from(element.querySelectorAll("button,input,select,textarea,fieldset,table,.assignment-step,.assignment-stepper,.structured-card,.lint-panel,.rubric-evidence,.student-task-preview,.published-detail-section,.published-source-ref,.parser-inventory,.notice,.form-error,.form-success"))
        .filter((item) => item.getClientRects().length > 0)
        .filter((item) => {
          const rect = item.getBoundingClientRect();
          return rect.left < bounds.left - 1 || rect.right > bounds.right + 1;
        });
      return {
        viewport: document.documentElement.clientWidth,
        page: document.documentElement.scrollWidth,
        panelRight: bounds.right,
        outside: outside.map((item) => `${item.tagName.toLowerCase()}.${String(item.className)}`),
        pageOverflowElements: Array.from(document.querySelectorAll("body *"))
          .map((item) => ({ item, rect: item.getBoundingClientRect() }))
          .filter(({ rect }) => rect.right > document.documentElement.clientWidth + 1)
          .sort((left, right) => right.rect.right - left.rect.right)
          .slice(0, 8)
          .map(({ item, rect }) => `${item.tagName.toLowerCase()}.${String(item.className)} right=${Math.round(rect.right)} ${item.outerHTML.slice(0, 100)}`),
      };
    });
    expect(dimensions.page, `${label}: page overflow at ${width}px ${JSON.stringify(dimensions.pageOverflowElements)}`).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(dimensions.panelRight, `${label}: workspace overflow at ${width}px`).toBeLessThanOrEqual(width + 1);
    expect(dimensions.outside, `${label}: controls outside workspace at ${width}px`).toEqual([]);
    await workspace.screenshot({ path: test.info().outputPath(`assignment-${label}-${width}.png`), animations: "disabled" });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

test("lecturer assignment flow is staged, manual-first, responsive and gated", async ({ page }) => {
  test.setTimeout(240_000);
  page.setDefaultTimeout(15_000);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const password = process.env.ALIGN_SEED_PASSWORD;
  const lecturerEmail = process.env.ALIGN_DEMO_LECTURER_EMAIL ?? "dosen@align.local";
  const courseCode = process.env.ALIGN_DEMO_COURSE_CODE ?? "IF101";
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E flow.");

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/login");
  await page.getByLabel("Email").fill(lecturerEmail);
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("link", { name: new RegExp(courseCode) }).click();

  // A synthetic, approved context is required for an assignment. Keep it as
  // test data; this test intentionally performs no cleanup or provider call.
  await page.getByRole("tab", { name: "Konteks" }).click();
  await page.getByLabel("Deskripsi konteks").fill("Konteks sintetis untuk uji alur UX tugas.");
  await page.getByRole("button", { name: "Buat draft", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Draft konteks manual dibuat" })).toBeVisible();
  await page.getByLabel(/Capaian pembelajaran/).fill("CPMK-UX-01 | Menganalisis hubungan karakteristik data dan tujuan pertanyaan dengan metode yang sesuai.");
  await page.getByRole("button", { name: "Simpan perubahan" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Perubahan draft disimpan" })).toBeVisible();
  const contextApprovalResponse = page.waitForResponse((response) => response.request().method() === "POST"
    && response.url().includes("/context-versions/") && response.url().endsWith("/approve"));
  await page.getByRole("button", { name: "Sahkan versi ini" }).click();
  await expect(page.getByRole("status").filter({ hasText: "disahkan" })).toBeVisible();
  const approvedContext = (await (await contextApprovalResponse).json()).context as {
    id: string;
    learning_outcomes: Array<{ code: string; resolution_status: string }>;
  };
  expect(approvedContext.learning_outcomes).toContainEqual(expect.objectContaining({ code: "CPMK-UX-01", resolution_status: "confirmed" }));

  // UI-only parser fixture stresses repeated limits and long filenames. It
  // does not write evidence, alter coverage, or stand in for parser execution.
  const visualFileId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const visualEvidenceId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const visualFileName = "rubrik-analisis-penjualan-sintetis-dengan-nama-file-sangat-panjang-dan-versi-final-untuk-uji-responsive-dan-limitations.docx";
  const visualQuote = "Mahasiswa menjelaskan alasan pemilihan metode dengan merujuk pada distribusi data, ukuran sampel, dan keterbatasan dataset.";
  const visualEvidenceText = `📈 Catatan rubrik: ${visualQuote}`;
  const visualLimitations = Array.from({ length: 12 }, (_, index) => index % 3 === 0
    ? { code: "unit_unreadable", message: "Satu unit tabel belum dapat dibaca utuh." }
    : index % 3 === 1
      ? { code: "unsupported_shape", message: "Bentuk visual tertanam tidak didukung parser." }
      : { code: "partial_structure", message: "Struktur tabel lintas bagian belum lengkap." });
  await page.route("**/api/v1/assignment-versions/*/rubric-parse", async (route) => {
    const versionId = route.request().url().match(/assignment-versions\/([^/]+)\/rubric-parse/)?.[1] ?? "synthetic-version";
    await route.fulfill({ json: {
      assignment_version_id: versionId,
      status: "ready_for_review",
      job_id: null,
      files: [{
        id: visualFileId,
        name: visualFileName,
        sha256: "a".repeat(64),
        parse_status: "partially_parsed",
        limitations: visualLimitations,
        evidence_count: 34,
        evidence_truncated: true,
        evidence: [{
          id: visualEvidenceId,
          type: "paragraph",
          anchor: { format: "docx", part: "word/document.xml", item_kind: "paragraph", item_index: 2 },
          text: visualEvidenceText,
          text_truncated: false,
          source_sha256: "a".repeat(64),
          parse_status: "partially_parsed",
          limitations: [],
        }],
      }],
    } });
  });

  await page.getByRole("tab", { name: "Tugas" }).click();
  const workspace = page.getByRole("region", { name: "Kelola tugas" });
  await expect(workspace.getByRole("heading", { name: "Daftar tugas" })).toBeVisible();
  await expect(workspace.getByRole("button", { name: "Buat tugas baru" })).toBeVisible();
  await checkAssignmentLayout(page, workspace, "list");

  const assignmentTitle = `UX sintetis ${Date.now()}`;
  const createTask = workspace.getByRole("button", { name: "Buat tugas baru" });
  await createTask.focus();
  await expect(createTask).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(workspace.getByRole("heading", { name: "Informasi awal tugas" })).toBeVisible();
  const contextSelector = workspace.getByLabel("Konteks mata kuliah yang digunakan");
  await expect(contextSelector).toHaveValue(approvedContext.id);
  await workspace.getByLabel("Judul tugas").fill(assignmentTitle);
  await expect(workspace.getByText(/Konteks mata kuliah · versi/)).toBeVisible();
  await checkAssignmentLayout(page, workspace, "create");
  await workspace.getByRole("button", { name: "Buat draf tugas" }).click();
  await expect(workspace.getByRole("heading", { name: "Draf tugas" })).toBeVisible();

  const instruction = "Jelaskan konsep yang dipilih dengan merujuk pada bukti dalam dokumen sintetis.";
  const instructionField = workspace.getByLabel("Instruksi asli (maks. 30.000 karakter)");
  await expect(instructionField).toHaveValue("");
  await expect(workspace.getByLabel("Ringkasan opsional")).toHaveCount(0);
  const saveInfo = workspace.getByRole("button", { name: "Simpan informasi tugas" });
  await expect(saveInfo).toBeDisabled();
  await instructionField.fill(instruction);
  const due = workspace.getByLabel("Tenggat pengumpulan");
  const validDue = await due.inputValue();
  await due.fill("2020-01-01T00:00");
  await expect(saveInfo).toBeDisabled();
  await due.fill(validDue);

  const criteriaStep = workspace.getByRole("button", { name: /Kriteria pemeriksaan/ });
  await criteriaStep.focus();
  await expect(criteriaStep).toBeFocused();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.keyboard.press("Enter");
  const infoStep = workspace.getByRole("button", { name: /Informasi tugas/ });
  await infoStep.focus();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.keyboard.press("Enter");
  await expect(instructionField).toHaveValue(instruction);
  await checkAssignmentLayout(page, workspace, "info");
  const versionUpdates: Array<Record<string, unknown>> = [];
  page.on("request", (request) => {
    if (request.method() !== "PUT" || !request.url().includes("/assignment-versions/")) return;
    const body = request.postData();
    if (body) versionUpdates.push(JSON.parse(body) as Record<string, unknown>);
  });
  await saveInfo.click();
  await expect(workspace.getByRole("status").filter({ hasText: "Versi tugas tersimpan" })).toBeVisible();
  expect(versionUpdates.at(-1)?.instruction_summary).toBeNull();
  await workspace.getByRole("button", { name: "Lanjut ke kriteria" }).click();

  await expect(workspace).toContainText("Mode mock aktif untuk data sintetis");
  await expect(workspace).toContainText("mock tidak menyusun kriteria semantik");
  await expect(workspace.getByRole("button", { name: "Sahkan paket kriteria" })).toBeDisabled();
  await checkAssignmentLayout(page, workspace, "criteria");
  await workspace.getByRole("button", { name: /Buka formulir kriteria|Perbarui sumber rubrik/ }).click();

  const criteria = workspace.locator('section[aria-labelledby="criteria-title"]');
  const criterion = criteria.locator("fieldset.structured-card").first();
  await checkAssignmentLayout(page, workspace, "criteria-editor");
  await criterion.getByLabel("Kode kriteria", { exact: true }).fill("C1");
  await criterion.getByLabel("Judul", { exact: true }).fill("Penjelasan konsep");
  await criterion.getByLabel("Deskripsi resmi kriteria").fill("Penjelasan konsep harus merujuk pada instruksi tugas.");
  const outcomeMapping = criterion.getByRole("checkbox", { name: /CPMK-UX-01/ });
  await expect(outcomeMapping).toBeVisible();
  await outcomeMapping.check();
  const requirement = criterion.locator(".structured-subsection").first();
  await requirement.getByLabel("Kode kewajiban").fill("R1");
  await requirement.getByLabel("Teks kewajiban bukti").fill("Jelaskan konsep dengan alasan yang dapat ditinjau.");
  const citationSource = requirement.getByLabel("Teks sumber untuk memilih kutipan");
  const instructionQuote = "konsep yang dipilih";
  const instructionStart = instruction.indexOf(instructionQuote);
  expect(instructionStart).toBeGreaterThanOrEqual(0);
  await citationSource.scrollIntoViewIfNeeded();
  const quoteCoordinates = await citationSource.evaluate((element, { start, quote }) => {
    const textarea = element as HTMLTextAreaElement;
    const style = getComputedStyle(textarea);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas text metrics are unavailable.");
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const bounds = textarea.getBoundingClientRect();
    return {
      startX: bounds.left + Number.parseFloat(style.paddingLeft) + context.measureText(textarea.value.slice(0, start)).width + 1,
      endX: bounds.left + Number.parseFloat(style.paddingLeft) + context.measureText(textarea.value.slice(0, start + quote.length)).width + 1,
      y: bounds.top + Number.parseFloat(style.paddingTop) + 8,
    };
  }, { start: instructionStart, quote: instructionQuote });
  await page.mouse.move(quoteCoordinates.startX, quoteCoordinates.y);
  await page.mouse.down();
  await page.mouse.move(quoteCoordinates.endX, quoteCoordinates.y, { steps: 12 });
  await page.mouse.up();
  const selection = await citationSource.evaluate((element) => {
    const textarea = element as HTMLTextAreaElement;
    return [textarea.selectionStart, textarea.selectionEnd];
  });
  expect(selection, JSON.stringify(quoteCoordinates)).toEqual([instructionStart, instructionStart + instructionQuote.length]);
  await expect(requirement.locator(".selected-source-quote")).toContainText(instructionQuote);
  const expectedInstructionStart = Array.from(instruction.slice(0, instructionStart)).length;
  await requirement.getByText("Detail teknis provenance").click();
  await expect(requirement.getByText(`Indeks code point: ${expectedInstructionStart}–${expectedInstructionStart + Array.from(instructionQuote).length} (akhir tidak termasuk)`)).toBeVisible();
  await criteria.getByRole("button", { name: "Simpan kriteria" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Kriteria dan kewajiban tersimpan" })).toBeVisible();
  await criteria.getByRole("button", { name: "Sahkan paket kriteria" }).click();
  await expect(workspace.getByText("Paket kriteria disahkan pada")).toBeVisible();
  await checkAssignmentLayout(page, workspace, "approved-criteria");
  await workspace.getByRole("button", { name: "Lanjut ke rencana pemeriksaan" }).click();

  await expect(workspace.locator('section[aria-labelledby="numeric-title"]')).toHaveCount(0);
  await checkAssignmentLayout(page, workspace, "plan");
  await workspace.getByRole("button", { name: "Buat draf rencana pemeriksaan" }).click();
  const plan = workspace.locator('section[aria-labelledby="spec-title"]');
  const element = plan.locator("fieldset.spec-element").first();
  await expect(element).toBeVisible();
  await checkAssignmentLayout(page, workspace, "plan-editor");
  await element.getByLabel("Pertanyaan keputusan positif").fill("Apakah penjelasan didukung bukti yang dikutip?");
  await plan.getByRole("button", { name: "Simpan rencana dan jalankan pemeriksaan" }).click();
  await expect.poll(async () => plan.innerText(), { timeout: 30_000 }).toContain("lint: lulus");
  const approvePlan = plan.getByRole("button", { name: "Sahkan rencana dengan konfirmasi kesetiaan" });
  await expect(approvePlan, await workspace.innerText()).toBeEnabled();
  await approvePlan.click();
  await expect(workspace.getByText("Spesifikasi pemeriksaan v2 disahkan", { exact: false })).toBeVisible();
  await checkAssignmentLayout(page, workspace, "approved-plan");

  await workspace.getByRole("button", { name: "Lanjut ke pratinjau" }).click();
  await expect(workspace.getByRole("button", { name: "Tampilkan pratinjau mahasiswa" })).toBeEnabled();
  const publishButton = workspace.getByRole("button", { name: "Terbitkan versi ini" });
  await expect(publishButton).toBeDisabled();
  await workspace.getByRole("button", { name: "Tampilkan pratinjau mahasiswa" }).click();
  const preview = workspace.getByRole("article", { name: "Preview paket publik mahasiswa" });
  await expect(preview).toContainText(assignmentTitle);
  await expect(preview).toContainText(instruction);
  await expect(preview).toContainText("Penjelasan konsep");
  await expect(preview).toContainText("Jelaskan konsep dengan alasan yang dapat ditinjau.");
  await expect(publishButton).toBeEnabled();
  await checkAssignmentLayout(page, workspace, "publish");
  await publishButton.click();
  await expect(workspace.getByRole("heading", { name: assignmentTitle })).toBeVisible();
  await expect(workspace.getByRole("heading", { name: "Kriteria pemeriksaan dan kewajiban mahasiswa" })).toBeVisible();
  await expect(workspace).toContainText("CPMK-UX-01");
  await expect(workspace).toContainText("Instruksi asli");
  await expect(workspace).not.toContainText("Simpan informasi tugas");
  await expect(workspace.getByRole("navigation", { name: "Langkah penyusunan tugas" })).toHaveCount(0);
  await checkAssignmentLayout(page, workspace, "published");

  let publishedVersionId = "";
  let activeVersionAfterNewDraft: string | null | undefined;
  await workspace.getByRole("button", { name: "Daftar tugas" }).click();
  const taskCard = workspace.locator(".assignment-list-card").filter({ hasText: assignmentTitle });
  await expect(taskCard).toContainText("Terbit · versi 1");
  await taskCard.getByRole("button", { name: `Buka tugas ${assignmentTitle}` }).click();
  await page.route("**/api/v1/assignments/*", async (route) => {
    const response = await route.fetch();
    const payload = await response.json() as {
      title?: string;
      active_version_id?: string | null;
      version?: {
        id?: string;
        state?: string;
        version_no?: number;
        instruction_summary?: string | null;
        rubric_sources?: Array<{ id: string; name: string; sha256: string }>;
        criterion_set?: { criteria?: Array<Record<string, unknown>> } | null;
      } | null;
    };
    if (payload.title === assignmentTitle && payload.version?.state === "published") {
      publishedVersionId = payload.version.id ?? "";
      payload.version.rubric_sources = [...(payload.version.rubric_sources ?? []), {
        id: visualFileId,
        name: visualFileName,
        sha256: "a".repeat(64),
      }];
      const criterion = payload.version.criterion_set?.criteria?.[0];
      const requirements = criterion && Array.isArray(criterion.requirements)
        ? criterion.requirements as Array<Record<string, unknown>>
        : [];
      if (requirements[0]) {
        const refs = Array.isArray(requirements[0].normative_source_refs) ? requirements[0].normative_source_refs : [];
        const prefix = "📈 Catatan rubrik: ";
        requirements[0].normative_source_refs = [...refs, {
          source_kind: "rubric_evidence",
          source_id: visualEvidenceId,
          span: { start: Array.from(prefix).length, end: Array.from(visualEvidenceText).length },
          quote: visualQuote,
          source_hash: "a".repeat(64),
          source_sha256: "a".repeat(64),
          file_id: visualFileId,
          anchor: { format: "docx", part: "word/document.xml", item_kind: "paragraph", item_index: 2 },
        }];
      }
      await route.fulfill({ response, json: payload });
      return;
    }
    if (payload.title === assignmentTitle && payload.version?.state === "draft" && payload.version.version_no === 2) {
      activeVersionAfterNewDraft = payload.active_version_id;
      payload.version.instruction_summary = "Ringkasan warisan sintetis untuk uji kompatibilitas.";
      await route.fulfill({ response, json: payload });
      return;
    }
    await route.fulfill({ response });
  });
  await workspace.getByRole("button", { name: "Daftar tugas" }).click();
  const publishedTaskCard = workspace.locator(".assignment-list-card").filter({ hasText: assignmentTitle });
  await publishedTaskCard.getByRole("button", { name: `Buka tugas ${assignmentTitle}` }).click();
  await expect(workspace.getByRole("heading", { name: assignmentTitle })).toBeVisible();
  await expect(workspace).toContainText(visualFileName);
  await expect(workspace).toContainText("Paragraf 2");
  await expect(workspace).toContainText(visualQuote);
  await expect(workspace.locator(".parser-limitations")).toContainText("12 catatan limitation");
  await expect(workspace.locator(".parser-limitations")).toContainText("3 jenis");
  await workspace.locator(".parser-limitations").getByText("12 catatan limitation · 3 jenis · buka detail").click();
  await expect(workspace.locator(".parser-limitations")).toContainText("Bentuk visual tertanam tidak didukung parser.");
  await checkAssignmentLayout(page, workspace, "published-source-and-limitations");
  await workspace.getByRole("button", { name: "Buat versi baru" }).click();
  await expect(workspace.getByRole("heading", { name: "Draf tugas" })).toBeVisible();
  expect(publishedVersionId).toBeTruthy();
  expect(activeVersionAfterNewDraft).toBe(publishedVersionId);
  await expect(instructionField).toHaveValue(instruction);
  await expect(workspace.getByLabel("Ringkasan opsional")).toHaveCount(0);
  await instructionField.fill(`${instruction} Instruksi tambahan untuk versi baru.`);
  await workspace.getByRole("button", { name: "Simpan informasi tugas" }).click();
  await expect(workspace.getByRole("status").filter({ hasText: "Versi tugas tersimpan" })).toBeVisible();
  await expect(instructionField).toHaveValue(`${instruction} Instruksi tambahan untuk versi baru.`);
  expect(versionUpdates.at(-1)?.instruction_summary).toBe("Ringkasan warisan sintetis untuk uji kompatibilitas.");

  await workspace.getByRole("button", { name: /Kriteria pemeriksaan/ }).click();
  await workspace.getByRole("button", { name: "Salin teks kutipan" }).click();
  await expect(workspace.getByRole("status")).toContainText(`Teks kutipan dari ${visualFileName} disalin`);
  await workspace.getByRole("button", { name: "Daftar tugas" }).click();
  await expect(taskCard).toContainText("Draf · versi 2 · snapshot terbit versi 1");
  await taskCard.getByRole("button", { name: `Lihat versi terbit ${assignmentTitle}` }).click();
  await expect(workspace.getByRole("heading", { name: assignmentTitle })).toBeVisible();
  await expect(workspace.getByRole("button", { name: "Buka draf versi 2" })).toBeVisible();
  await checkAssignmentLayout(page, workspace, "published-with-followup-draft");
  await workspace.getByRole("button", { name: "Daftar tugas" }).click();
  await workspace.getByRole("button", { name: "Buat tugas baru" }).click();
  const nextTaskTitle = `Tugas berikutnya ${Date.now()}`;
  await workspace.getByLabel("Judul tugas").fill(nextTaskTitle);
  await workspace.getByRole("button", { name: "Buat draf tugas" }).click();
  await expect(workspace.getByRole("heading", { name: "Draf tugas" })).toBeVisible();
  await expect(workspace.getByRole("status")).not.toContainText(`Teks kutipan dari ${visualFileName} disalin`);
});
