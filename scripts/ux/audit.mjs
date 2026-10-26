// UX audit: logs in to a running WattPost instance, screenshots every main page and
// Settings section at desktop and phone widths in light and dark, and measures
// accessibility and usability basics. Use it before and after UI work.
//
//   node scripts/ux/audit.mjs --base http://127.0.0.1:8421 --password SECRET --out /tmp/ux-before
//
// Needs Playwright (NODE_PATH=$(npm root -g) works). Pass --demo for a demo instance
// (no login). Writes <out>/metrics.json, <out>/summary.txt and PNGs under <out>/shots.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright");   // honours NODE_PATH, unlike ESM import

const arg = (k, d) => { const i = process.argv.indexOf("--" + k); return i < 0 ? d : (process.argv[i + 1]?.startsWith("--") || process.argv[i + 1] === undefined ? true : process.argv[i + 1]); };
const BASE = arg("base", "http://127.0.0.1:8421").replace(/\/$/, "");
const PASSWORD = arg("password", "");
const OUT = arg("out", "/tmp/ux-audit");
const DEMO = !!process.argv.includes("--demo");
const SUBS = (arg("subs", "") || "").split(",").filter(Boolean);
const PAGES = ["", "history", "devices", "settings"];
const VIEWPORTS = { desktop: [1440, 900], phone: [390, 844] };
const SCHEMES = ["light", "dark"];

// Runs inside the page. Returns plain numbers/lists so results can be diffed.
function measure() {
  const q = (s) => [...document.querySelectorAll(s)];
  const vis = (e) => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; };
  const nameOf = (e) => (e.getAttribute("aria-label") || (e.getAttribute("aria-labelledby") && document.getElementById(e.getAttribute("aria-labelledby"))?.innerText) || e.innerText || e.title || e.value || e.getAttribute("placeholder") || "").trim();
  const inter = q("a[href],button,input,select,textarea,[role=button],[role=tab],[tabindex]:not([tabindex='-1'])").filter(vis);
  const hasLabel = (e) => (e.labels && e.labels.length) || e.getAttribute("aria-label") || e.getAttribute("aria-labelledby");
  const unlabelled = q("input:not([type=hidden]),select,textarea").filter(vis).filter((e) => !hasLabel(e) && !(e.closest("label"))).map((e) => e.id || e.name || e.className || e.type);
  const noName = inter.filter((e) => !nameOf(e) && !hasLabel(e)).map((e) => e.outerHTML.slice(0, 80));
  const small = inter.filter((e) => { const r = e.getBoundingClientRect(); return r.width < 44 || r.height < 44; });
  const fonts = inter.map((e) => parseFloat(getComputedStyle(e).fontSize));
  const parse = (s) => s.match(/[\d.]+/g).map(Number);
  const bgOf = (e) => { while (e) { const m = parse(getComputedStyle(e).backgroundColor); if (m.length >= 3 && (m.length < 4 || m[3] > 0.5)) return m.slice(0, 3); e = e.parentElement; } return [255, 255, 255]; };
  const lum = (c) => { const a = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]; };
  const low = []; let tested = 0; const tiny = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode, t = n.textContent.trim(); if (t.length < 3) continue;
    const e = n.parentElement; if (!e || !vis(e)) continue;
    const cs = getComputedStyle(e), c = parse(cs.color), a = c[3] ?? 1, bg = bgOf(e);
    const f = c.slice(0, 3).map((v, i) => v * a + bg[i] * (1 - a));
    const L1 = lum(f), L2 = lum(bg), cr = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700, need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    tested++; if (cr < need) low.push({ t: t.slice(0, 28), cr: +cr.toFixed(2), size });
    if (size < 11) tiny.push(t.slice(0, 18) + ":" + size);
  }
  const heads = q("h1,h2,h3,h4,h5,h6").filter(vis).map((h) => +h.tagName[1]);
  const skips = heads.filter((l, i) => i > 0 && l > heads[i - 1] + 1).length;
  const de = document.documentElement;
  return {
    controls: inter.length, unlabelled: unlabelled.length, unlabelledList: unlabelled.slice(0, 10), noName: noName.length,
    smallTargets: small.length, minFont: fonts.length ? Math.min(...fonts) : null, tinyText: [...new Set(tiny)].slice(0, 8), tinyTextN: tiny.length,
    lowContrast: low.length, textNodes: tested, lowContrastEx: low.slice(0, 6), h1: heads.filter((l) => l === 1).length, headingSkips: skips,
    overflowX: de.scrollWidth > de.clientWidth + 1,
  };
}

mkdirSync(join(OUT, "shots"), { recursive: true });
const browser = await chromium.launch();
const results = {}; let errors = [];
for (const scheme of SCHEMES) for (const [vp, [w, h]] of Object.entries(VIEWPORTS)) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, hasTouch: vp === "phone" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${vp}/${scheme}: ${String(e).slice(0, 120)}`));
  if (!DEMO) {
    await page.goto(BASE + "/login"); await page.waitForTimeout(600);
    await page.fill("input[type=password]", String(PASSWORD)); await page.click("button[type=submit], button:has-text('Sign in')"); await page.waitForTimeout(1800);
  }
  const routes = [...PAGES.map((p) => p), ...SUBS.map((s) => "settings/" + s)];
  for (const r of routes) {
    await page.goto(`${BASE}/#/${r}`); await page.waitForTimeout(1600);
    const key = `${vp}/${scheme}/${r || "dashboard"}`;
    await page.screenshot({ path: join(OUT, "shots", key.replace(/\//g, "_") + ".png"), fullPage: true });
    results[key] = await page.evaluate(measure);
  }
  await ctx.close();
}
await browser.close();
writeFileSync(join(OUT, "metrics.json"), JSON.stringify({ errors, results }, null, 1));
const lines = [`console errors: ${errors.length}`];
for (const [k, v] of Object.entries(results)) lines.push(`${k.padEnd(34)} ctrls ${String(v.controls).padStart(3)}  unlabelled ${v.unlabelled}  <44px ${String(v.smallTargets).padStart(3)}  lowContrast ${String(v.lowContrast).padStart(3)}/${String(v.textNodes).padEnd(3)} minFont ${v.minFont?.toFixed(1)}  h1 ${v.h1} skips ${v.headingSkips}${v.overflowX ? "  OVERFLOW-X" : ""}`);
writeFileSync(join(OUT, "summary.txt"), lines.join("\n") + "\n");
console.log(lines.join("\n"));
