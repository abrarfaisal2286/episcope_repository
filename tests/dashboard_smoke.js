// Headless smoke test: loads the dashboard in jsdom against dashboard/data, exercises linked interaction
// and both roles, and fails on any script error or empty view.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..", "dashboard");
const errors = [];
const dom = new JSDOM(fs.readFileSync(path.join(root, "index.html"), "utf8"), { runScripts: "outside-only", pretendToBeVisual: true, url: "http://localhost/" });
const w = dom.window;
w.addEventListener("error", (e) => errors.push("window error: " + (e.error && e.error.stack || e.message)));
w.console.error = (...a) => errors.push("console.error: " + a.join(" "));
w.fetch = (p) => {
  const f = path.join(root, p);
  const ok = fs.existsSync(f);
  return Promise.resolve({ ok, status: ok ? 200 : 404, json: () => Promise.resolve(JSON.parse(fs.readFileSync(f, "utf8"))) });
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const q = (sel) => w.document.querySelectorAll(sel).length;
function check(name, cond) { if (!cond) errors.push("FAILED: " + name); else console.log("ok  ", name); }
(async () => {
  w.eval(fs.readFileSync(path.join(root, "vendor", "d3.min.js"), "utf8"));
  try { w.eval(fs.readFileSync(path.join(root, "app.js"), "utf8")); } catch (e) { errors.push("eval app.js: " + e.stack); }
  await sleep(2500);
  const E = w.EPISCOPE;
  check("error banner hidden", w.document.getElementById("error").hidden);
  check("map has 64 districts", q("#map path.district") === 64);
  check("time series drawn", q("#ts svg path") >= 2);
  check("heatmap drawn", q("#heat svg rect") > 50);
  check("allocation bars drawn", q("#alloc svg rect") > 4);
  check("ranking table rows", q("#ewt tr.row") === 64);
  check("what-if charts", q("#eq-chart circle") >= 5 && q("#det-chart circle") > 10);
  check("data quality panel", w.document.getElementById("dq").textContent.length > 50);
  check("calendar drawn", q("#cal rect") > 8);
  check("freshness stamp on each panel", Array.from(w.document.querySelectorAll(".stamp")).every((s) => s.textContent.includes("Data as of")));
  // linked interaction: selecting a district updates map, table and heatmap highlights
  const first = w.document.querySelector("#ewt tr.row");
  first.dispatchEvent(new w.Event("click"));
  await sleep(300);
  check("district selection propagates", E.state.district === first.dataset.id && q("#ewt tr.sel") === 1 && q("#map path.district.sel") === 1);
  for (const d of ["diarrhoea", "ili", "malaria", "dengue"]) { E.state.disease = d; E.dispatch.call("change"); await sleep(200); }
  for (const m of ["fl", "cusum", "c2"]) { E.state.method = m; E.dispatch.call("change"); await sleep(200); }
  E.state.heatMode = "district"; E.dispatch.call("change"); await sleep(200);
  check("district heatmap", q("#heat svg rect") > 500);
  E.state.range = [100, 140]; E.dispatch.call("change"); await sleep(200);
  for (const [b, a] of [[0.2, 0], [0.6, 0.4], [1, 0.8]]) { E.state.budget = b; E.state.alpha = a; E.dispatch.call("change"); await sleep(150); }
  E.state.thr = 5; E.dispatch.call("change"); await sleep(150);
  // district officer role: own district in detail, others aggregated
  const roleSel = w.document.getElementById("role");
  roleSel.value = "district"; roleSel.dispatchEvent(new w.Event("change"));
  await sleep(1500);
  check("district role loads", E.state.role === "district" && w.document.getElementById("error").hidden);
  check("district role table shows only own district", q("#ewt tr.row") === 1);
  check("district role map still shows 64 districts", q("#map path.district") === 64);
  if (errors.length) { console.error("\n" + errors.join("\n")); process.exit(1); }
  console.log("dashboard smoke test passed");
  process.exit(0);
})();
