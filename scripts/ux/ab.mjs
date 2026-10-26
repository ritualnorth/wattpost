// A/B visual check for CSS-only refactors: loads each page twice in the same browser session,
// once with stylesheet A and once with stylesheet B (swapped in by request interception), so HTML,
// JS and live data are identical. Shots go to <out>/a and <out>/b; compare with compare.py.
//
//   node scripts/ux/ab.mjs --base http://127.0.0.1:8421 --password SECRET \
//        --css-a /tmp/old/styles.css --css-b solar_monitor/web/styles.css --out /tmp/ux-ab
import { createRequire } from "node:module";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
const { chromium } = createRequire(import.meta.url)("playwright");
const arg = (k, d) => { const i = process.argv.indexOf("--" + k); return i < 0 ? d : process.argv[i + 1]; };
const BASE = arg("base", "http://127.0.0.1:8421"), PASSWORD = arg("password", ""), OUT = arg("out", "/tmp/ux-ab");
const CSS = { a: readFileSync(arg("css-a"), "utf8"), b: readFileSync(arg("css-b"), "utf8") };
const SUBS = (arg("subs", "devices,alerts,integrations,backup,privacy,advanced,about")).split(",").filter(Boolean);
const ROUTES = ["", "history", "devices", "settings", ...SUBS.map((s) => "settings/" + s)];
const VIEWPORTS = { desktop: [1440, 900], phone: [390, 844] };
for (const v of ["a", "b"]) mkdirSync(join(OUT, v), { recursive: true });
const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) for (const [vp, [w, h]] of Object.entries(VIEWPORTS)) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, hasTouch: vp === "phone" });
  const page = await ctx.newPage();
  let which = "a";
  await page.route(/\/web\/styles\.css/, (r) => r.fulfill({ status: 200, contentType: "text/css", body: CSS[which] }));
  await page.goto(BASE + "/login"); await page.waitForTimeout(500);
  await page.fill("input[type=password]", PASSWORD); await page.click("button[type=submit], button:has-text('Sign in')"); await page.waitForTimeout(1500);
  for (const r of ROUTES) for (const v of ["a", "b", "a2"]) {
    which = v === "b" ? "b" : "a";
    await page.goto(`${BASE}/#/${r}`); await page.reload(); await page.waitForTimeout(1500);
    await page.screenshot({ path: join(OUT, v === "a2" ? "a2" : v, `${vp}_${scheme}_${(r || "dashboard").replace(/\//g, "_")}.png`), fullPage: true }).catch(async () => { mkdirSync(join(OUT, "a2"), { recursive: true }); await page.screenshot({ path: join(OUT, "a2", `${vp}_${scheme}_${(r || "dashboard").replace(/\//g, "_")}.png`), fullPage: true }); });
  }
  await ctx.close();
}
await browser.close();
console.log("done", OUT);
