const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const qaDir = "/run/b9-qa";
const fixture = JSON.parse(fs.readFileSync(path.join(qaDir, "moodle-fixtures.json"), "utf8"));
const publicationFixturePath = path.join(qaDir, "publication-fixture.json");
const moodleOrigin = fixture.platform.moodle_origin;
const alignOrigin = process.env.ALIGN_B9_ORIGIN || "https://align-b9.localhost:8443";
const testPassword = process.env.MOODLE_B9_TEST_PASSWORD;
const proxyIp = process.env.B9_PROXY_IP;
if (!testPassword || !proxyIp) throw new Error("B9 synthetic browser credentials or proxy are unavailable");

const viewportCases = [360, 768, 1440];
const goodLaunches = [
  { username: "b9teacher1", course: "B9-IF101", activity: "individual", role: "lecturer" },
  { username: "b9student1", course: "B9-IF101", activity: "individual", role: "student" },
  { username: "b9teacher1", course: "B9-IF101", activity: "group", role: "lecturer" },
  { username: "b9student1", course: "B9-IF101", activity: "group", role: "student" },
  { username: "b9teacher2", course: "B9-IF202", activity: "individual", role: "lecturer" },
  { username: "b9student3", course: "B9-IF202", activity: "individual", role: "student" },
];

function courseFixture(shortname) {
  const course = fixture.moodle.courses[shortname];
  if (!course) throw new Error(`Missing actual Moodle fixture course ${shortname}`);
  return course;
}

function activityName(kind) {
  if (kind === "individual") return "ALIGN B9 LTI Launch Activity";
  if (kind === "group") return "ALIGN B9 LTI Group Launch Activity";
  if (kind === "unmapped") return "ALIGN B9 LTI Unmapped Activity";
  throw new Error("Unknown B9 activity kind");
}

async function login(page, username) {
  await page.goto(`${moodleOrigin}/login/index.php`, { waitUntil: "domcontentloaded" });
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(testPassword);
  await page.locator('button[type="submit"],input[type="submit"]').first().click();
  await page.waitForLoadState("domcontentloaded");
  if (new URL(page.url()).pathname === "/login/index.php") {
    throw new Error(`Synthetic Moodle login failed for ${username}`);
  }
}

async function findLaunchLink(page, name) {
  const byRole = page.getByRole("link", { name: new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") });
  if (await byRole.count()) return byRole.first();
  const byHref = page.locator('a[href*="/mod/lti/view.php"]').filter({ hasText: name });
  if (await byHref.count()) return byHref.first();
  throw new Error(`Moodle course page does not expose the LTI activity ${name}`);
}

async function launchActivity(page, courseKey, activityKind, keyboard = false, captureReplay = false) {
  const course = courseFixture(courseKey);
  let replayForm = null;
  if (captureReplay) {
    page.on("request", (request) => {
      if (request.method() !== "POST" || !request.url().startsWith(`${alignOrigin}/api/v1/lti/launch`)) return;
      const form = new URLSearchParams(request.postData() || "");
      const state = form.get("state");
      const idToken = form.get("id_token");
      if (state && idToken) replayForm = { state, id_token: idToken };
    });
  }
  await page.goto(`${moodleOrigin}/course/view.php?id=${course.id}`, { waitUntil: "domcontentloaded" });
  const link = await findLaunchLink(page, activityName(activityKind));
  if (keyboard) {
    await link.focus();
    await page.keyboard.press("Enter");
  } else {
    await link.click();
  }
  await page.waitForURL((url) => url.hostname === "align-b9.localhost", { timeout: 30_000 });
  await page.waitForLoadState("domcontentloaded");
  return { destination: page.url(), replayForm };
}

async function capturePendingLaunch(browser) {
  const context = await browser.newContext({ viewport: { width: 768, height: 900 } });
  const page = await context.newPage();
  let resolveCaptured;
  const capturedForm = new Promise((resolve) => { resolveCaptured = resolve; });
  await page.route("**/api/v1/lti/launch*", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") return route.continue();
    const form = Object.fromEntries(new URLSearchParams(request.postData() || ""));
    if (form.state && form.id_token) {
      resolveCaptured(form);
      return route.abort("aborted");
    }
    return route.continue();
  });
  await login(page, "b9teacher1");
  await page.goto(`${moodleOrigin}/course/view.php?id=${courseFixture("B9-IF101").id}`, {
    waitUntil: "domcontentloaded",
  });
  const link = await findLaunchLink(page, activityName("individual"));
  try {
    await link.click({ timeout: 30_000 });
  } catch (error) {
    if (!page.url().includes("moodle-b9.localhost")) throw error;
  }
  let timeout;
  const form = await Promise.race([
    capturedForm,
    new Promise((resolve) => { timeout = setTimeout(() => resolve(null), 30_000); }),
  ]);
  clearTimeout(timeout);
  if (!form) {
    await context.close();
    throw new Error("Could not intercept a pending signed Moodle launch");
  }
  return { context, form };
}

async function expectTamperedLaunchDenied(browser, results, scenario, mutate) {
  const { context, form } = await capturePendingLaunch(browser);
  try {
    const candidate = { ...form };
    mutate(candidate);
    const response = await context.request.post(`${alignOrigin}/api/v1/lti/launch`, {
      form: candidate,
      headers: { Origin: moodleOrigin },
    });
    const setCookie = response.headers()["set-cookie"] || "";
    if (response.status() !== 401 || setCookie.includes("align_session=")) {
      throw new Error(`${scenario} tampered Moodle launch was not denied without a session`);
    }
    results.push({ scenario, outcome: "denied-401-no-session" });
  } finally {
    await context.close();
  }
}

async function expectLaunchDenied(browser, username, courseKey, activityKind, expectedStatus) {
  const context = await browser.newContext({ viewport: { width: 768, height: 900 } });
  const page = await context.newPage();
  let launchStatus = null;
  page.on("response", (response) => {
    if (response.url().startsWith(`${alignOrigin}/api/v1/lti/launch`)) launchStatus = response.status();
  });
  try {
    await login(page, username);
    await page.goto(`${moodleOrigin}/course/view.php?id=${courseFixture(courseKey).id}`, { waitUntil: "domcontentloaded" });
    const link = await findLaunchLink(page, activityName(activityKind));
    await link.click();
    await page.waitForURL((url) => url.hostname === "align-b9.localhost", { timeout: 30_000 });
    await page.waitForLoadState("domcontentloaded");
    if (launchStatus !== expectedStatus) {
      throw new Error(`${username} expected LTI status ${expectedStatus}; received ${launchStatus}`);
    }
  } finally {
    await context.close();
  }
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--no-sandbox",
      `--host-resolver-rules=MAP align-b9.localhost ${proxyIp},MAP moodle-b9.localhost ${proxyIp}`,
    ],
  });
  const results = [];
  try {
    for (const width of viewportCases) {
      for (const launch of goodLaunches) {
        const context = await browser.newContext({ viewport: { width, height: 900 } });
        const page = await context.newPage();
        try {
          await login(page, launch.username);
          const captureReplay = width === 360 && launch.username === "b9teacher1" && launch.activity === "individual";
          const launchResult = await launchActivity(
            page,
            launch.course,
            launch.activity,
            width === 360,
            captureReplay,
          );
          const url = new URL(launchResult.destination);
          const expectedPrefix = launch.role === "lecturer" ? "/courses/" : "/feedback/";
          if (!url.pathname.startsWith(expectedPrefix)) {
            throw new Error(`${launch.username} reached an unexpected ALIGN destination`);
          }
          const hasHorizontalOverflow = await page.evaluate(
            () => document.documentElement.scrollWidth > window.innerWidth,
          );
          if (hasHorizontalOverflow) throw new Error(`Horizontal overflow at viewport ${width}`);
          if (width === 360 && launch.username === "b9teacher1" && launch.activity === "individual") {
            await page.screenshot({ path: path.join(qaDir, "artifacts", "b9-launch-teacher-360.png"), fullPage: true });
          }
          if (width === 1440 && launch.username === "b9student1" && launch.activity === "group") {
            await page.screenshot({ path: path.join(qaDir, "artifacts", "b9-launch-student-group-1440.png"), fullPage: true });
          }
          results.push({ width, username: launch.username, course: launch.course, activity: launch.activity, outcome: "allowed" });
          if (captureReplay) {
            if (!launchResult.replayForm) throw new Error("Moodle callback form could not be captured in memory");
            const replay = await page.request.post(`${alignOrigin}/api/v1/lti/launch`, {
              form: launchResult.replayForm,
              headers: { Origin: moodleOrigin },
            });
            if (replay.status() !== 401 || (replay.headers()["set-cookie"] || "").includes("align_session=")) {
              throw new Error("A consumed Moodle state replay created or preserved an ALIGN session");
            }
            results.push({ scenario: "consumed-state-replay", outcome: "denied-401-no-session" });
          }
        } finally {
          await context.close();
        }
      }
    }
    await expectTamperedLaunchDenied(browser, results, "invalid-signature", (form) => {
      const segments = form.id_token.split(".");
      const signature = segments[2];
      segments[2] = `${signature.startsWith("A") ? "B" : "A"}${signature.slice(1)}`;
      form.id_token = segments.join(".");
    });
    await expectTamperedLaunchDenied(browser, results, "invalid-state", (form) => {
      form.state = "b9-invalid-state";
    });
    if (fs.existsSync(publicationFixturePath)) {
      const publication = JSON.parse(fs.readFileSync(publicationFixturePath, "utf8"));
      if (["released", "withdrawn"].includes(publication.status)) {
        const context = await browser.newContext({ viewport: { width: 360, height: 900 } });
        const page = await context.newPage();
        try {
          await login(page, "b9student1");
          const result = await launchActivity(page, "B9-IF101", "individual", true);
          const expectedPath = `/feedback/${publication.submission_id}`;
          if (new URL(result.destination).pathname !== expectedPath) {
            throw new Error("Published Moodle student launch did not open its own feedback route");
          }
          const content = await page.locator("body").innerText();
          if (publication.status === "released") {
            if (
              !content.includes("Feedback dosen") ||
              !content.includes("Dalam cakupan yang telah ditelaah, tidak ada masalah yang dikonfirmasi.") ||
              !content.includes(`VERSI ${publication.first_publication.submission_version}`)
            ) {
              throw new Error("Student Moodle launch did not render the released source-version snapshot");
            }
            await page.screenshot({
              path: path.join(qaDir, "artifacts", "b9-ac05-student-released.png"),
              fullPage: true,
            });
          } else {
            if (!content.includes("Feedback ditarik") || content.includes("Dalam cakupan yang telah ditelaah")) {
              throw new Error("Withdrawn Moodle student launch still renders feedback content");
            }
            await page.screenshot({
              path: path.join(qaDir, "artifacts", "b9-ac05-student-withdrawn.png"),
              fullPage: true,
            });
          }
          results.push({
            scenario: `ac-sbx-05-student-${publication.status}`,
            viewport: 360,
            outcome: "passed",
          });
        } finally {
          await context.close();
        }
      }
    }
    await expectLaunchDenied(browser, "b9unbound", "B9-IF101", "individual", 403);
    results.push({ scenario: "missing-binding", outcome: "denied-403" });
    await expectLaunchDenied(browser, "b9rolemismatch", "B9-IF101", "individual", 403);
    results.push({ scenario: "role-mismatch", outcome: "denied-403" });
    await expectLaunchDenied(browser, "b9teacher1", "B9-IF101", "unmapped", 403);
    results.push({ scenario: "unmapped-resource", outcome: "denied-403" });
    fs.writeFileSync(path.join(qaDir, "browser-acceptance.json"), `${JSON.stringify({ status: "passed", results }, null, 2)}\n`);
    console.log(JSON.stringify({ status: "passed", allowedLaunches: results.filter((item) => item.outcome === "allowed").length, deniedLaunches: results.filter((item) => String(item.outcome).startsWith("denied-")).length, viewports: viewportCases }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(`B9 browser acceptance failed: ${error.message}`);
  process.exitCode = 1;
});
