// Renders the dashboard views to SVG files (docs/figures/dash_*.svg) using the real D3 code in jsdom.
// Used for report figures because the build environment has no browser.  Usage: node scripts/render_views.js
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..", "dashboard");
const out = path.join(__dirname, "..", "docs", "figures");
fs.mkdirSync(out, { recursive: true });
const dom = new JSDOM(fs.readFileSync(path.join(root, "index.html"), "utf8"), { runScripts: "outside-only", pretendToBeVisual: true, url: "http://localhost/" });
const w = dom.window;
w.fetch = (p) => { const f = path.join(root, p); const ok = fs.existsSync(f); return Promise.resolve({ ok, status: ok ? 200 : 404, json: () => Promise.resolve(JSON.parse(fs.readFileSync(f, "utf8"))) }); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const STYLE = "<style>.axis text{font-size:10px;font-family:sans-serif}.axis path,.axis line{stroke:#777;fill:none}text{font-family:sans-serif}.district{stroke:#fff;stroke-width:.5}.district.sel{stroke:#000;stroke-width:2.2}</style>";
const DEFS = '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#eeeeee"/><line x1="0" y1="0" x2="0" y2="6" stroke="#888888" stroke-width="2"/></pattern></defs>';
function dump(sel, name) {
  const el = w.document.querySelector(sel + " svg"); if (!el) return;
  const vb = el.getAttribute("viewBox").split(" ").map(Number);
  const c = el.cloneNode(true);
  c.setAttribute("width", vb[2] * 2); c.setAttribute("height", vb[3] * 2);
  let s = new w.XMLSerializer().serializeToString(c);
  s = s.replace(/>/, ">" + STYLE + DEFS);
  fs.writeFileSync(path.join(out, name + ".svg"), s);
}
(async () => {
  w.eval(fs.readFileSync(path.join(root, "vendor", "d3.min.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "app.js"), "utf8"));
  await sleep(2500);
  const E = w.EPISCOPE;
  w.document.querySelector("#ewt tr.row").dispatchEvent(new w.Event("click")); await sleep(300);
  E.state.budget = 0.6; E.state.alpha = 0.4; E.dispatch.call("change"); await sleep(400);
  dump("#map", "dash_map"); dump("#ts", "dash_ts"); dump("#heat", "dash_heat"); dump("#alloc", "dash_alloc"); dump("#eq-chart", "dash_eq"); dump("#det-chart", "dash_det"); dump("#cal", "dash_cal");
  console.log("selected district:", E.state.district, "disease:", E.state.disease, "method:", E.state.method);
  process.exit(0);
})();
