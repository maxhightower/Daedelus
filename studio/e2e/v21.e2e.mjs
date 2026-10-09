// V2.1 walkthrough: browser sessions (no token in URL or storage), CSRF, live updates over the
// session cookie, worker isolation display, run preflight with confirmation, budget summary.
//   STUDIO_URL=http://127.0.0.1:8765 STUDIO_TOKEN=... WORKER_TOKEN=... OUT=../docs/screenshots/v2_1 node e2e/v21.e2e.mjs
// The backend must run with DAEDELUS_API_TOKENS containing STUDIO_TOKEN and DAEDELUS_WORKER_TOKENS
// containing WORKER_TOKEN (a join token: the worker enrols and receives its own credential).
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwright = require("playwright");
const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-v21");
const TOKEN = process.env.STUDIO_TOKEN;
const WORKER_TOKEN = process.env.WORKER_TOKEN;
const DAEDELUS = process.env.DAEDELUS_BIN || "daedelus";
fs.mkdirSync(OUT, { recursive: true });
const results = [];
let scenario = "setup";
const check = (name, ok, detail = "") => {
  results.push({ scenario, name, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
  console.log(`${ok ? "PASS" : "FAIL"} [${scenario}] ${name}${!ok && detail ? " - " + String(detail).slice(0, 300) : ""}`);
  return !!ok;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const auth = { authorization: `Bearer ${TOKEN}` };
const api = async (p, init = {}) => {
  const r = await fetch(BASE + p, { ...init, headers: { ...auth, ...(init.headers || {}) } });
  if (!r.ok) throw new Error(`${p}: ${r.status} ${await r.text()}`);
  return r.json();
};
const post = (p, body) => api(p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
async function until(fn, timeout = 60000, step = 400) {
  const t0 = Date.now();
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      /* retry */
    }
    if (Date.now() - t0 > timeout) throw new Error("timeout waiting for condition");
    await sleep(step);
  }
}
const workers = [];
function startWorker(name) {
  const p = spawn(DAEDELUS, ["worker", "--control", BASE, "--name", name, "--adapters", "spreadsheet,document,presentation"], {
    env: { ...process.env, DAEDELUS_WORKER_TOKEN: WORKER_TOKEN },
    stdio: "ignore",
    detached: true,
  });
  workers.push(p);
  return p;
}

const launchOpts = { headless: true, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] };
if (fs.existsSync("/opt/pw-browsers")) {
  const exe = fs
    .readdirSync("/opt/pw-browsers")
    .filter((d) => d.startsWith("chromium-"))
    .map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`)
    .find((p) => fs.existsSync(p));
  if (exe) launchOpts.executablePath = exe;
}
const browser = await playwright.chromium.launch(launchOpts);
const context = await browser.newContext({ viewport: { width: 1680, height: 1000 } });
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
const requests = [];
page.on("request", (r) => requests.push({ url: r.url(), method: r.method(), headers: r.headers() }));
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

try {
  if (!TOKEN || !WORKER_TOKEN) throw new Error("STUDIO_TOKEN and WORKER_TOKEN are required");

  // ---------------------------------------------------------------- 1. unauthenticated
  scenario = "1 refusal";
  const anon = await fetch(BASE + "/api/projects");
  check("the API refuses requests without credentials (401)", anon.status === 401, anon.status);
  const q = await fetch(BASE + `/api/projects?token=${encodeURIComponent(TOKEN)}`);
  check("a token in the query string is refused (401)", q.status === 401, q.status);
  const project = await post("/api/projects", { name: "Hardened studio", description: "V2.1 walkthrough" });
  const PID = project.id;
  const P = (p) => `/api/projects/${PID}${p}`;

  // ---------------------------------------------------------------- 2. login
  scenario = "2 login";
  await page.goto(BASE);
  await page.locator("[data-testid=login]").waitFor({ timeout: 20000 });
  check("the studio shows the sign-in screen when the backend requires a token", true);
  await shot("v21_01_login");
  await page.fill("[data-testid=login-token]", "not-the-token");
  await page.click("[data-testid=login-submit]");
  await page.locator("[data-testid=login] .error").waitFor({ timeout: 10000 });
  check("a wrong token is rejected on the sign-in screen", true);
  await page.fill("[data-testid=login-token]", TOKEN);
  await page.click("[data-testid=login-submit]");
  await page.locator("select[aria-label=project]").waitFor({ timeout: 20000 });
  check("the correct token opens the studio", true);

  const cookies = await context.cookies();
  const sc = cookies.find((c) => c.name === "dd_session");
  check("a session cookie is set", !!sc, JSON.stringify(cookies));
  check("the session cookie is HttpOnly and SameSite=Strict, scoped to /api/", sc && sc.httpOnly && sc.sameSite === "Strict" && sc.path === "/api/", JSON.stringify(sc));
  const store = await page.evaluate(() => ({
    local: JSON.stringify({ ...localStorage }),
    session: JSON.stringify({ ...sessionStorage }),
    cookie: document.cookie,
    href: location.href,
  }));
  check("the token is not in localStorage or sessionStorage", !store.local.includes(TOKEN) && !store.session.includes(TOKEN), store);
  check("the session cookie is not readable from script", !store.cookie.includes("dd_session"), store.cookie);

  // ---------------------------------------------------------------- 3. project, worker, live updates
  scenario = "3 live";
  startWorker("isolated-worker-1");
  const w = await until(async () => (await api("/api/cluster/status")).workers.find((x) => x.name === "isolated-worker-1" && x.alive), 60000);
  check("a worker enrolled with the join token and authenticates with its own credential", w.credential_kind === "enrolled", JSON.stringify(w).slice(0, 300));
  await page.selectOption("select[aria-label=project]", PID);
  await page.locator("[data-testid=execution-badge]").waitFor({ timeout: 30000 });
  await page.locator("[data-testid=execution-badge]").click();
  await page.locator("[data-testid=execution-settings]").waitFor({ timeout: 15000 });
  await until(async () => /live updates/.test(await page.locator("[data-testid=execution-settings]").innerText()), 15000);
  check("the event stream connects in session mode (cookie, no ticket)", true);
  await page.locator("[data-testid=target-cloud_cpu]").check();
  await until(async () => (await api(P("/execution"))).target === "cloud_cpu", 10000);
  const wtext = await page.locator("[data-testid=worker-row]").first().innerText();
  check("the worker row shows the worker's isolation profile and self-test result", /isolation/.test(wtext) && /(self-test passed|unverified)/.test(wtext), wtext);
  const created = await post(P("/artifacts"), { name: "Remote book", adapter: "spreadsheet", template: "data", params: { sheets: [{ id: "data", title: "Data", rows: [["x", "y"], [1, 2]] }] } });
  await until(async () => (await page.locator("[data-testid=job-list] .job-row").count()) >= 1, 30000);
  const jl = await page.locator("[data-testid=job-list]").innerText();
  check("remote jobs arrive live over the cookie-authenticated stream", /spreadsheet\./.test(jl), jl.slice(0, 300));
  await shot("v21_02_worker_isolation");

  // ---------------------------------------------------------------- 4. CSRF
  scenario = "4 csrf";
  const noCsrf = await page.evaluate(async (pid) => {
    const r = await fetch(`/api/projects/${pid}/workflows`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "forged" }) });
    return r.status;
  }, PID);
  check("a cookie-authenticated POST without the CSRF header is refused (403)", noCsrf === 403, noCsrf);
  const sessionInfo = await page.evaluate(async () => (await fetch("/api/auth/session")).json());
  const crossOrigin = await fetch(BASE + P("/workflows"), {
    method: "POST",
    headers: { cookie: `dd_session=${sc.value}`, "x-csrf-token": sessionInfo.csrf, origin: "https://evil.example", "content-type": "application/json" },
    body: JSON.stringify({ name: "forged" }),
  });
  check("a request with a valid cookie and CSRF token from a foreign Origin is refused (403)", crossOrigin.status === 403, crossOrigin.status);
  const forged = (await api(P("/workflows"))).filter((x) => x.name === "forged");
  check("no forged workflow was created", forged.length === 0, forged.length);

  // ---------------------------------------------------------------- 5. preflight + budget
  scenario = "5 preflight";
  const wf = await post(P("/workflows"), {
    name: "Remote edit",
    parameters: { budget: { max_model_calls: 10, max_cost_usd: 1, max_seconds: 600 } },
    nodes: [
      { id: "a", type: "artifact", label: "Book", config: { artifact_id: created.artifact.id } },
      { id: "g", type: "agent", label: "Remote agent", config: { instructions: "Edit the book.", fan_out: false } },
    ],
    edges: [{ id: "e", source: "a", source_port: "artifact", target: "g", target_port: "artifact" }],
  });
  const pf = await api(P(`/workflows/${wf.id}/preflight`));
  check("preflight reports remote execution and requires confirmation", pf.needs_confirmation === true, JSON.stringify(pf).slice(0, 400));
  await page.locator('[data-testid="ex-workflows"] .ex-row', { hasText: "Remote edit" }).first().click();
  await page.getByRole("button", { name: "▶ Run" }).click();
  await page.locator("[data-testid=preflight]").waitFor({ timeout: 15000 });
  const pft = await page.locator("[data-testid=preflight]").innerText();
  check("the preflight shows where nodes run and the budget limits before running", /cloud_cpu|worker|remote/i.test(pft) && /10/.test(pft), pft);
  await shot("v21_03_preflight");
  const before = (await api(P(`/executions?workflow_id=${wf.id}`)).catch(() => [])).length ?? 0;
  await sleep(800);
  const stillNone = (await api(P(`/executions?workflow_id=${wf.id}`)).catch(() => [])).length ?? 0;
  check("nothing runs until the user confirms", stillNone === before, `${before} -> ${stillNone}`);
  await page.click("[data-testid=preflight-confirm]");
  const ex = await until(async () => {
    const list = await api(P(`/executions?workflow_id=${wf.id}`));
    const last = list.sort((x, y) => String(y.created_at).localeCompare(String(x.created_at)))[0];
    const e = last && (await api(P(`/executions/${last.id}`)));
    return e && !["pending", "running"].includes(e.status) ? e : null;
  }, 180000, 1000);
  check("the confirmed run completes", ex.status === "succeeded", ex.status);
  check("the execution records its budget usage", ex.budget && ex.budget.limits && ex.budget.limits.max_model_calls === 10, JSON.stringify(ex.budget).slice(0, 300));
  await page.getByRole("button", { name: "Execution details" }).click();
  await page.locator("[data-testid=budget-summary]").waitFor({ timeout: 15000 });
  const bt = await page.locator("[data-testid=budget-summary]").innerText();
  check("the run view shows model calls and cost against the budget", /call/i.test(bt) && /\$|cost|unknown/i.test(bt), bt);
  await shot("v21_04_budget");

  // ---------------------------------------------------------------- 6. no credentials leaked
  scenario = "6 leaks";
  const leaky = requests.filter((r) => r.url.includes(TOKEN) || r.url.includes("token="));
  check("no request URL carried the API token", leaky.length === 0, leaky.map((r) => r.url).join("\n"));
  const ev = requests.filter((r) => /\/events/.test(r.url));
  check("event-stream URLs carry no ticket in session mode", ev.length > 0 && ev.every((r) => !/ticket=/.test(r.url)), ev.map((r) => r.url).join("\n"));
  const bearerAfterLogin = requests.filter((r) => r.headers.authorization && !/\/api\/auth\/session$/.test(r.url));
  check("after sign-in the browser sends no bearer token (cookie only)", bearerAfterLogin.length === 0, bearerAfterLogin.map((r) => r.url).join("\n"));

  // ---------------------------------------------------------------- 7. sign out
  scenario = "7 sign out";
  await page.click("[data-testid=sign-out]");
  await page.locator("[data-testid=login]").waitFor({ timeout: 15000 });
  check("signing out returns to the sign-in screen", true);
  const replay = await fetch(BASE + "/api/projects", { headers: { cookie: `dd_session=${sc.value}` } });
  check("the old session cookie no longer authenticates (401)", replay.status === 401, replay.status);
  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join("\n"));
} catch (e) {
  check("walkthrough completed", false, e.stack || String(e));
  await shot("v21_error").catch(() => {});
} finally {
  for (const p of workers) {
    try {
      process.kill(-p.pid, "SIGTERM");
    } catch {
      /* gone */
    }
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, "v21_results.json"), JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
}
