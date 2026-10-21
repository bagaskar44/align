import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("synthetic lecturer can log in and sees only the course landing page", async ({ page }) => {
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");

  await page.goto("/login");
  await page.getByLabel("Email").fill("dosen@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await expect(page.getByText("IF101")).toBeVisible();
  await expect(page.getByRole("status").getByText("SIMULASI • data sintetis")).toBeVisible();
  await expect(page.getByRole("heading", { name: /dashboard|analitik/i })).toHaveCount(0);
});

test("invalid credentials return a generic message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("tidak-ada@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill("password-yang-tidak-benar");
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Email atau kata sandi tidak sesuai" }),
  ).toContainText("Email atau kata sandi tidak sesuai");
  await expect(page).toHaveURL(/\/login$/);
});

test("login rejects a cross-origin browser mutation", async ({ request }) => {
  const response = await request.post("/api/v1/auth/login", {
    headers: {
      Origin: "https://attacker.invalid",
      "Content-Type": "application/json",
    },
    data: { email: "dosen@align.local", password: "any password" },
  });
  expect(response.status()).toBe(403);
  expect(await response.json()).toMatchObject({ error: { code: "FORBIDDEN" } });
});

test("logout revokes the browser session", async ({ page }) => {
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");
  await page.goto("/login");
  await page.getByLabel("Email").fill("dosen@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await page.getByRole("button", { name: "Keluar" }).click();
  await expect(page).toHaveURL(/\/login$/);
  const me = await page.request.get("/api/v1/me");
  expect(me.status()).toBe(401);
});

test("course landing remains readable at the B0 target widths", async ({ page }) => {
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");
  await page.goto("/login");
  await page.getByLabel("Email").fill("dosen@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      page: document.documentElement.scrollWidth,
    }));
    expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
  }
});

test("admin without lecturer enrollment sees course metadata and minimal roster only", async ({ page }) => {
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Mata kuliah" })).toBeVisible();
  await expect(page.getByText("IF101")).toBeVisible();

  const profileResponse = await page.request.get("/api/v1/me");
  expect(profileResponse.ok()).toBeTruthy();
  const profile = await profileResponse.json();
  const origin = new URL(page.url()).origin;
  const createResponse = await page.request.post("/api/v1/courses", {
    headers: { Origin: origin, "X-CSRF-Token": profile.csrf_token },
    data: { code: `ADMIN-${Date.now()}`, title: "Kursus privat" },
  });
  expect(createResponse.status()).toBe(201);
  const createdCourse = await createResponse.json();

  expect((await page.request.get(`/api/v1/courses/${createdCourse.id}`)).status()).toBe(200);
  expect((await page.request.get(`/api/v1/courses/${createdCourse.id}/context-versions`)).status()).toBe(404);
  const rosterResponse = await page.request.get(`/api/v1/courses/${createdCourse.id}/enrollments`);
  expect(rosterResponse.status()).toBe(200);
  expect(await rosterResponse.json()).toMatchObject({ items: [] });

  const csrfRejected = await page.request.post("/api/v1/courses", {
    headers: { Origin: "https://attacker.invalid", "X-CSRF-Token": profile.csrf_token },
    data: { code: `CSRF-${Date.now()}`, title: "Harus ditolak" },
  });
  expect(csrfRejected.status()).toBe(403);
});

test("lecturer can create and approve a manual context without learning outcomes", async ({ page }) => {
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");
  await page.goto("/login");
  await page.getByLabel("Email").fill("dosen@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await page.getByRole("link", { name: /IF101/ }).click();
  await expect(page.getByRole("heading", { name: "Konteks tersimpan" })).toBeVisible();

  await page.getByLabel("Deskripsi konteks", { exact: true }).first().fill("Konteks manual untuk validasi B1.");
  await page.getByRole("button", { name: "Buat draft" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Draft konteks manual dibuat." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sahkan versi ini" })).toBeVisible();

  await page.getByRole("button", { name: "Sahkan versi ini" }).click();
  await expect(page.getByRole("status").filter({ hasText: /Versi konteks \d+ disahkan\./ })).toBeVisible();
  await expect(page.getByText("DISAHKAN", { exact: true })).toBeVisible();
  const courseId = new URL(page.url()).pathname.split("/").at(-1);
  expect(courseId).toBeTruthy();
  const versionsResponse = await page.request.get(`/api/v1/courses/${courseId}/context-versions`);
  expect(versionsResponse.ok()).toBeTruthy();
  const versions = (await versionsResponse.json()) as { items: Array<{ id: string }> };
  const contextResponse = await page.request.get(`/api/v1/context-versions/${versions.items[0].id}`);
  expect(contextResponse.ok()).toBeTruthy();
  const approvedContext = await contextResponse.json();
  expect(approvedContext.fields.outcomes_status).toBe("not_provided");
  expect(approvedContext.fields.learning_outcomes).toEqual([]);
});

test("lecturer parses KK and RPP locally, resolves a sourced conflict, then approves", async ({ page }) => {
  test.setTimeout(150_000);
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");
  await page.goto("/login");
  await page.getByLabel("Email").fill("dosen@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await page.getByRole("link", { name: /IF101/ }).click();

  const sourceInput = page.locator('input[name="source-file"]');
  await sourceInput.setInputFiles(resolve(test.info().project.testDir, "fixtures", "kk_context.docx"));
  await page.getByRole("button", { name: "Unggah dan validasi" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Berkas tervalidasi dan siap dimasukkan ke draft." })).toBeVisible();
  await page.getByLabel("Peran sumber").selectOption("rpp");
  await sourceInput.setInputFiles(resolve(test.info().project.testDir, "fixtures", "rpp_context.docx"));
  await page.getByRole("button", { name: "Unggah dan validasi" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Berkas tervalidasi dan siap dimasukkan ke draft." })).toBeVisible();

  await page.getByLabel("Deskripsi konteks", { exact: true }).first().fill("Konteks lintas dokumen sintetis.");
  const draftResponsePromise = page.waitForResponse(
    (response) => response.url().includes("/context-drafts") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Buat draft" }).click();
  const draftResponse = await draftResponsePromise;
  const draftPayload = await draftResponse.json();
  expect(draftResponse.ok(), JSON.stringify(draftPayload.error ?? draftPayload)).toBeTruthy();
  await expect(page.getByRole("status").filter({ hasText: "Draft dibuat; ekstraksi berjalan di antrean lokal." })).toBeVisible();

  const courseId = new URL(page.url()).pathname.split("/").at(-1);
  expect(courseId).toBeTruthy();
  const versionsResponse = await page.request.get(`/api/v1/courses/${courseId}/context-versions`);
  expect(versionsResponse.ok()).toBeTruthy();
  const versions = (await versionsResponse.json()) as { items: Array<{ id: string }> };
  const contextId = versions.items[0]?.id;
  expect(contextId).toBeTruthy();
  const unsavedDescription = "Perubahan lokal yang belum disimpan.";
  await page.locator(".context-form").getByLabel("Deskripsi konteks", { exact: true }).fill(unsavedDescription);

  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/context-versions/${contextId}`);
    if (!response.ok()) return "unavailable";
    const context = (await response.json()) as {
      parse_artifacts: Array<{ status: string }>;
      evidence: Array<{ content: Record<string, unknown> }>;
      conflicts: Array<{ field_path: string }>;
    };
    return context.parse_artifacts.length === 2 &&
      context.parse_artifacts.every((artifact) => artifact.status === "parsed") &&
      context.evidence.some((item) => JSON.stringify(item.content).includes("Kode Mata Kuliah")) &&
      context.conflicts.some((conflict) => conflict.field_path === "course_code")
      ? "parsed-conflict-ready"
      : context.parse_artifacts.map((artifact) => artifact.status).join(",");
  }, { timeout: 120_000, intervals: [1000, 2000, 5000] }).toBe("parsed-conflict-ready");

  await expect(page.locator(".context-form").getByLabel("Deskripsi konteks", { exact: true })).toHaveValue(unsavedDescription);
  await expect(page.getByRole("button", { name: "Muat revisi terbaru" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Muat revisi terbaru" }).click();
  await expect(page.getByRole("heading", { name: "Usulan yang berkonflik" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Bukti dan anchor" })).toBeVisible();
  const unresolvedApprovalPromise = page.waitForResponse(
    (response) => response.url().includes(`/context-versions/${contextId}/approve`) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sahkan versi ini" }).click();
  const unresolvedApproval = await unresolvedApprovalPromise;
  expect(unresolvedApproval.status()).toBe(422);
  await expect(page.locator(".workspace-message.form-error")).toContainText("Selesaikan konflik sumber");
  const codeConflict = page.locator(".conflict-card").filter({ has: page.getByRole("heading", { name: "Kode mata kuliah" }) });
  await codeConflict.getByLabel("Nilai yang dipilih").selectOption("IF999");
  await codeConflict.getByLabel("Alasan keputusan").fill("RPP sintetis menjadi sumber yang dipilih untuk uji konflik.");
  await codeConflict.getByRole("button", { name: "Simpan keputusan" }).click();
  await expect(page.getByRole("status").filter({ hasText: /Konflik .* diselesaikan\./ })).toBeVisible();
  const latestContextResponse = await page.request.get(`/api/v1/context-versions/${contextId}`);
  const latestContext = await latestContextResponse.json() as {
    state: string;
    revision: number;
    conflicts: Array<{ resolution: unknown }>;
    parse_artifacts: Array<{ status: string }>;
  };
  const finalApprovalPromise = page.waitForResponse(
    (response) => response.url().includes(`/context-versions/${contextId}/approve`) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sahkan versi ini" }).click();
  const finalApproval = await finalApprovalPromise;
  expect(finalApproval.status(), JSON.stringify({
    server_state: latestContext.state,
    server_revision: latestContext.revision,
    submitted_revision: finalApproval.request().postDataJSON().expected_revision,
    resolved_conflicts: latestContext.conflicts.every((conflict) => Boolean(conflict.resolution)),
    artifact_statuses: latestContext.parse_artifacts.map((artifact) => artifact.status),
  })).toBe(200);
  await expect(page.getByRole("status").filter({ hasText: /Versi konteks \d+ disahkan\./ })).toBeVisible();

  const contextResponse = await page.request.get(`/api/v1/context-versions/${contextId}`);
  expect(contextResponse.ok()).toBeTruthy();
  const approvedContext = await contextResponse.json();
  expect(approvedContext.state).toBe("approved");
  expect(approvedContext.fields.course_code.value).toBe("IF999");
  expect(approvedContext.fields.outcomes_status).toBe("not_provided");
  expect(approvedContext.evidence.length).toBeGreaterThan(0);
});

test("admin provisions an account and sees the one-time temporary password", async ({ page }) => {
  const password = process.env.ALIGN_SEED_PASSWORD;
  if (!password) throw new Error("ALIGN_SEED_PASSWORD must be loaded from the local .env before the E2E smoke.");
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@align.local");
  await page.getByLabel("Kata sandi", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await page.getByRole("link", { name: "Kelola akun, mata kuliah, dan roster" }).click();
  await expect(page.getByRole("heading", { name: "Akun pengguna" })).toBeVisible();

  const email = `b1-${Date.now()}@align.local`;
  const accountsRegion = page.getByRole("region", { name: "Akun pengguna" });
  await accountsRegion.getByLabel("Nama", { exact: true }).fill("Pengguna B1 sintetis");
  await accountsRegion.getByLabel("Email", { exact: true }).fill(email);
  await page.getByRole("button", { name: "Buat akun" }).click();
  await expect(page.getByRole("status").filter({ hasText: email })).toBeVisible();
  const temporaryPassword = await page.locator(".credential-box code").textContent();
  expect(temporaryPassword).toBeTruthy();
  expect(temporaryPassword?.length).toBeGreaterThanOrEqual(20);
  const temporaryContext = await page.context().browser()!.newContext();
  try {
    const temporaryPage = await temporaryContext.newPage();
    await temporaryPage.goto(new URL("/login", page.url()).href);
    await temporaryPage.getByLabel("Email").fill(email);
    await temporaryPage.getByLabel("Kata sandi", { exact: true }).fill(temporaryPassword!);
    await temporaryPage.getByRole("button", { name: "Masuk" }).click();
    await expect(temporaryPage).toHaveURL(/\/change-password$/);
    expect((await temporaryPage.request.get("/api/v1/courses")).status()).toBe(403);
    const newPassword = `Synthetic-B1-password-${Date.now()}`;
    await temporaryPage.getByLabel("Kata sandi sementara", { exact: true }).fill(temporaryPassword!);
    await temporaryPage.getByLabel(/^Kata sandi baru/).fill(newPassword);
    await temporaryPage.getByLabel("Ulangi kata sandi baru", { exact: true }).fill(newPassword);
    await temporaryPage.getByRole("button", { name: "Simpan kata sandi" }).click();
    await expect(temporaryPage).toHaveURL(new URL("/", page.url()).href);
    const profile = await temporaryPage.request.get("/api/v1/me");
    expect(profile.status()).toBe(200);
    expect((await profile.json()).user.must_change_password).toBe(false);
  } finally { await temporaryContext.close(); }
});

