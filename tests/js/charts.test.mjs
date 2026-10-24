import test from "node:test";
import assert from "node:assert"; // loose deepEqual: values come from another vm context
import { load } from "./helpers.mjs";

const hist = load("06-history.js", ["chartWindow", "computeRangeStats", "makeForecastInterp"]);
const core = load("01-core.js", ["unitFromKey", "prettyKey"]);

const DAY = 86400;

test("chartWindow shows the requested window, not just where the data is", () => {
  // 30 days asked for, one hour of samples: the axis must span the 30 days.
  const until = 1_800_000_000, since = until - 30 * DAY;
  assert.deepEqual(hist.chartWindow(since, until, until - 3600, until), [since, until]);
});

test("chartWindow widens to cover data that lies outside the window", () => {
  assert.deepEqual(hist.chartWindow(1000, 2000, 900, 2100), [900, 2100]);
});

test("chartWindow falls back to the data span when there is no usable window", () => {
  assert.deepEqual(hist.chartWindow(undefined, undefined, 500, 900), [500, 900]);
  assert.deepEqual(hist.chartWindow(NaN, 2000, 500, 900), [500, 900]);
  assert.deepEqual(hist.chartWindow(2000, 1000, 500, 900), [500, 900]); // until before since
});

test("chartWindow ignores an absurd window (over ~6 years)", () => {
  assert.deepEqual(hist.chartWindow(1, 1 + 7 * 366 * DAY, 500, 900), [500, 900]);
});

test("chartWindow returns null when there is nothing to draw", () => {
  assert.strictEqual(hist.chartWindow(undefined, undefined, null, null), null);
  assert.strictEqual(hist.chartWindow(undefined, undefined, 700, 700), null);
});

test("chartWindow uses the window alone when there is no data yet", () => {
  assert.deepEqual(hist.chartWindow(1000, 2000, null, null), [1000, 2000]);
});

test("computeRangeStats reports min/max/avg/count inside the visible range only", () => {
  const ts = [1, 2, 3, 4, 5], vals = [10, null, 30, 50, 90];
  assert.deepEqual(hist.computeRangeStats(ts, vals, 1, 4), { min: 10, max: 50, avg: 30, count: 3 });
  assert.strictEqual(hist.computeRangeStats(ts, vals, 100, 200), null);
});

test("makeForecastInterp interpolates linearly and returns null outside the forecast", () => {
  const f = hist.makeForecastInterp([{ ts: 200, pv_w: 100 }, { ts: 100, pv_w: 0 }]); // unsorted on purpose
  assert.strictEqual(f(150, "pv_w"), 50);
  assert.strictEqual(f(100, "pv_w"), 0);
  assert.strictEqual(f(200, "pv_w"), 100);
  assert.strictEqual(f(99, "pv_w"), null);
  assert.strictEqual(f(201, "pv_w"), null);
  assert.strictEqual(hist.makeForecastInterp([])(1, "pv_w"), null);
});

test("makeForecastInterp tolerates a missing band value", () => {
  const f = hist.makeForecastInterp([{ ts: 0, pv_w: 10, pv_w_p90: null }, { ts: 10, pv_w: 20, pv_w_p90: 40 }]);
  assert.strictEqual(f(5, "pv_w_p90"), 40);
});

test("unitFromKey maps metric suffixes to units", () => {
  const cases = { battery_v: "V", load_a: "A", pv_w: "W", today_wh: "Wh", cap_ah: "Ah", temp_c: "°C",
    line_hz: "Hz", battery_percentage: "%", mystery: "" };
  for (const [k, u] of Object.entries(cases)) assert.strictEqual(core.unitFromKey(k), u, k);
});

test("prettyKey makes readable labels with units", () => {
  assert.strictEqual(core.prettyKey("battery_voltage_v"), "Battery Voltage (V)");
  assert.strictEqual(core.prettyKey("pv_power_w"), "Pv Power (W)");
});

test("sinceForRange returns the window start and a bucket that keeps ~300 points", () => {
  const NOW = 1_800_000_000_000;
  const FakeDate = { now: () => NOW };
  const { sinceForRange } = load("06-history.js", ["sinceForRange"], { Date: FakeDate });
  const now = NOW / 1000;
  assert.deepEqual(sinceForRange("1h"), [now - 3600, 5]);
  assert.deepEqual(sinceForRange("30d"), [now - 30 * DAY, 3600]);
  assert.deepEqual(sinceForRange("nonsense"), [now - DAY, 120]); // unknown falls back to 24h
});
