import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("lecturer builds and publishes a B2 task from shared rubric parser evidence", async ({ page }) => {
  test.setTimeout(240_000);
  page.setDefaultTimeout(10_000);
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E flow.");

  await page.goto("/login");
  await page.getByLabel("Email").fill("dosen@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("link", { name: /IF101/ }).click();

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
  const assignmentTitle = `B2 Playwright ${Date.now()}`;
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
  await workspace.getByRole("button", { name: "Siapkan editor draft" }).click();
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
  await page.getByLabel("Email").fill("mahasiswa@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("link", { name: /IF101/ }).click();
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
});
