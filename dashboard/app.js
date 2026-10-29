/* EpiScope dashboard: D3.js v7, five coordinated views plus what-if, data-quality and calendar panels.
   All views read one shared state object; any change dispatches "change" and every view re-renders. */
(function () {
  "use strict";
  const LABEL = { dengue: "Dengue", diarrhoea: "Acute diarrhoeal disease", ili: "Influenza-like illness", malaria: "Malaria" };
  const RES_LABEL = { iv_saline: "IV saline (bags)", ors: "ORS (sachets)", ns1_kits: "NS1 test kits", antimalarials: "Antimalarial courses" };
  const OKABE = { demand: "#E69F00", stock: "#56B4E9", alloc: "#009E73", base: "#999999", obs: "#0072B2", rep: "#999999", fc: "#D55E00" };
  const state = { role: "national", own: null, district: null, disease: "dengue", method: "c2", range: [0, 0],
                  budget: 0.6, alpha: 0.4, thr: 3, resource: "iv_saline", heatMode: "year" };
  const dispatch = d3.dispatch("change");
  const fmt = d3.format(",.0f"), f1 = d3.format(".1f"), f2 = d3.format(".2f");
  const parse = d3.utcParse("%Y-%m-%d"), show = d3.utcFormat("%d %b %Y");
  let META, GEO, NAT = null, DIV = null, OWN = null, QUAL, CAL, EVAL, WEEKS = [], ALERT_OFF = 0, MATURE = 0;

  const $ = (id) => document.getElementById(id);
  const tip = $("tip");
  function showTip(html, ev) { tip.hidden = false; tip.innerHTML = html; tip.style.left = Math.min(window.innerWidth - 300, ev.clientX + 12) + "px"; tip.style.top = (ev.clientY + 12) + "px"; }
  function hideTip() { tip.hidden = true; }
  const getJSON = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(p + " " + r.status); return r.json(); });

  /* ---------------------------------------------------------------- data access */
  const bundle = () => NAT || DIV;
  const districts = () => (NAT ? NAT.districts : DIV.districts);
  const popOf = (id) => districts().find((d) => d.id === id).population;
  const divisionOf = (id) => districts().find((d) => d.id === id).division;
  function ser(id) {
    if (NAT) return NAT.series[id] || null;
    return OWN && OWN.district.id === id ? OWN.series : null;
  }
  const sc = (id) => { const s = ser(id); return s ? s[state.disease] : null; };
  const divTotals = (name) => DIV.divisions.find((d) => d.name === name);
  function divSeries(name) { return NAT ? NAT.division_totals[name][state.disease] : divTotals(name).totals[state.disease]; }
  function divPop(name) { return d3.sum(districts().filter((d) => d.division === name), (d) => d.population); }
  function sumRange(arr, r0, r1) { let s = 0, known = 0, n = 0; for (let i = r0; i <= r1; i++) { n++; if (arr[i] != null) { s += arr[i]; known++; } } return { sum: s, known, n }; }
  function incidence(id) {
    const s = sc(id), [r0, r1] = state.range;
    if (s) { const a = sumRange(s.cases, r0, r1); return { v: a.known ? a.sum / popOf(id) * 1e5 : null, partial: a.known < a.n, agg: false }; }
    const dn = divisionOf(id), a = sumRange(divSeries(dn), r0, r1);
    return { v: a.known ? a.sum / divPop(dn) * 1e5 : null, partial: a.known < a.n, agg: true };
  }
  function isAlert(id, j) {
    const s = sc(id); if (!s) return false;
    const val = mval(s, j);
    const rep = s.reported[ALERT_OFF + j];
    return val != null && val >= state.thr && rep != null && rep >= META.operating_point.min_count && !s.art[j];
  }
  const latestJ = () => (bundle().alert_weeks || OWN.alert_weeks).length - 1;
  const mval = (s, j) => (state.method === "c2" ? s.z[j] : state.method === "fl" ? s.zf[j] : s.cusum[j]);
  const score = (id) => { const s = sc(id); if (!s) return null; return mval(s, latestJ()); };
  const sid = () => "b" + String(Math.round(state.budget * 100)).padStart(3, "0") + "_a" + String(Math.round(state.alpha * 100)).padStart(3, "0");
  const stampText = () => "Data as of " + META.latest_report_date + " (built " + META.built_at.replace("T", " ") + " UTC)";
  function epiKey(iso) { const d = parse(iso), wed = new Date(d.getTime() + 3 * 864e5); const y = wed.getUTCFullYear();
    const doy = Math.floor((wed - Date.UTC(y, 0, 1)) / 864e5) + 1; return { y, w: Math.ceil(doy / 7) }; }

  /* ---------------------------------------------------------------- 1. map */
  const mapSvg = d3.select("#map").append("svg").attr("viewBox", "0 0 420 520").attr("width", "100%");
  let proj, pathGen, mapPaths, mapMarks;
  function initMap() {
    proj = d3.geoMercator().fitSize([420, 520], GEO); pathGen = d3.geoPath(proj);
    mapPaths = mapSvg.append("g").selectAll("path").data(GEO.features).join("path").attr("class", "district").attr("d", pathGen)
      .on("click", (e, d) => select(d.properties.district_id))
      .on("mousemove", (e, d) => { const id = d.properties.district_id, inc = incidence(id), s = sc(id), j = latestJ();
        let h = "<b>" + d.properties.district + "</b> (" + d.properties.division + ")<br>" + LABEL[state.disease] + "<br>";
        h += inc.v == null ? "Incidence: suppressed (small counts)" : "Incidence: " + (inc.partial ? "at least " : "") + f1(inc.v) + " per 100,000" + (inc.agg ? " (division aggregate)" : "");
        if (s) h += "<br>Alert (latest week): " + (isAlert(id, j) ? "yes" : "no"); else h += "<br>Detail restricted for this role";
        showTip(h, e); }).on("mouseleave", hideTip);
    mapMarks = mapSvg.append("g");
  }
  function renderMap() {
    const vals = GEO.features.map((f) => incidence(f.properties.district_id).v).filter((v) => v != null).sort(d3.ascending);
    const top = vals.length ? d3.quantile(vals, 0.95) || vals[vals.length - 1] : 1;
    const color = d3.scaleSequential(d3.interpolateYlGnBu).domain([0, top || 1]).clamp(true);
    mapPaths.attr("fill", (d) => { const v = incidence(d.properties.district_id).v; return v == null ? "url(#hatch)" : color(v); })
      .classed("sel", (d) => d.properties.district_id === state.district);
    mapPaths.filter((d) => d.properties.district_id === state.district).raise();
    const j = latestJ();
    const al = GEO.features.filter((f) => isAlert(f.properties.district_id, j));
    mapMarks.selectAll("path").data(al, (d) => d.properties.district_id).join("path")
      .attr("d", d3.symbol().type(d3.symbolTriangle).size(70)).attr("fill", "#d55e00").attr("stroke", "#000").attr("stroke-width", 0.8)
      .attr("transform", (d) => "translate(" + pathGen.centroid(d) + ")").style("pointer-events", "none");
    const stops = [0, 0.25, 0.5, 0.75, 1].map((p) => { const v = p * top; return '<span><span class="sw" style="background:' + color(v) + '"></span>' + f1(v) + "</span>"; }).join("");
    $("map-legend").innerHTML = stops + '<span><span class="sw" style="background:url(#hatch);background:repeating-linear-gradient(45deg,#eee,#eee 3px,#888 3px,#888 4px)"></span>suppressed</span><span>&#9650; alert in latest week (' + (state.method === "cusum" ? "CUSUM" : "z") + " &ge; " + state.thr + ")</span><span>Weeks " + show(parse(WEEKS[state.range[0]])) + " to " + show(parse(WEEKS[state.range[1]])) + "</span>";
  }

  /* ---------------------------------------------------------------- 2. time series */
  const TSW = 640, TSH = 250, M = { l: 48, r: 14, t: 10, b: 26 };
  const tsSvg = d3.select("#ts").append("svg").attr("viewBox", "0 0 " + TSW + " " + TSH).attr("width", "100%");
  const ctxSvg = d3.select("#ctx").append("svg").attr("viewBox", "0 0 " + TSW + " 62").attr("width", "100%");
  let brush, brushG, ctxX;
  function tsData() {
    const id = state.district, s = id ? sc(id) : null;
    if (id && s) return { name: districts().find((d) => d.id === id).name, cases: s.cases, reported: s.reported, fc: s.fc, s, id };
    if (id && !s) { const dn = divisionOf(id); return { name: dn + " division (aggregate)", cases: divSeries(dn), reported: null, fc: null, s: null, id: null }; }
    const nt = (NAT || DIV).national_totals[state.disease];
    return { name: "Whole country", cases: nt, reported: null, fc: null, s: null, id: null };
  }
  function renderTS() {
    const D = tsData(), [r0, r1] = state.range, W = WEEKS.length;
    $("ts-title").textContent = D.name + ": " + LABEL[state.disease] + ". Grey dashed tail = weeks still receiving late reports.";
    const fw = (bundle().forecast_weeks || OWN.forecast_weeks).map(parse);
    const xs = d3.range(r0, r1 + 1).map((i) => parse(WEEKS[i]));
    const x = d3.scaleUtc().domain([xs[0], r1 === W - 1 && D.fc ? fw[fw.length - 1] : xs[xs.length - 1]]).range([M.l, TSW - M.r]);
    const ymax = d3.max([d3.max(D.cases.slice(r0, r1 + 1), (v) => v || 0), D.reported ? d3.max(D.reported.slice(r0, r1 + 1), (v) => v || 0) : 0, D.fc ? d3.max(D.fc.hi, (v) => v || 0) : 0, 5]);
    const y = d3.scaleLinear().domain([0, ymax * 1.08]).nice().range([TSH - M.b, M.t]);
    tsSvg.selectAll("*").remove();
    tsSvg.append("g").attr("class", "axis").attr("transform", "translate(0," + (TSH - M.b) + ")").call(d3.axisBottom(x).ticks(7));
    tsSvg.append("g").attr("class", "axis").attr("transform", "translate(" + M.l + ",0)").call(d3.axisLeft(y).ticks(5));
    tsSvg.append("text").attr("x", M.l + 6).attr("y", 12).attr("font-size", 10).text("Weekly cases");
    const pts = (arr) => d3.range(r0, r1 + 1).map((i) => ({ i, x: parse(WEEKS[i]), v: arr[i] }));
    const line = d3.line().defined((p) => p.v != null).x((p) => x(p.x)).y((p) => y(p.v));
    if (D.reported) tsSvg.append("path").datum(pts(D.reported)).attr("d", line).attr("fill", "none").attr("stroke", OKABE.rep).attr("stroke-width", 1);
    const all = pts(D.cases);
    tsSvg.append("path").datum(all.filter((p) => p.i <= MATURE)).attr("d", line).attr("fill", "none").attr("stroke", OKABE.obs).attr("stroke-width", 1.8);
    tsSvg.append("path").datum(all.filter((p) => p.i >= MATURE)).attr("d", line).attr("fill", "none").attr("stroke", "#777").attr("stroke-dasharray", "4 3").attr("stroke-width", 1.5);
    tsSvg.selectAll("circle.sup").data(all.filter((p) => p.v == null)).join("circle").attr("class", "sup").attr("cx", (p) => x(p.x)).attr("cy", TSH - M.b - 2).attr("r", 1.8).attr("fill", "#888");
    let alertText = "";
    if (D.s) {
      const aw = (bundle().alert_weeks || OWN.alert_weeks), ja0 = Math.max(0, r0 - ALERT_OFF);
      const thr = [];
      aw.forEach((w, j) => { const i = ALERT_OFF + j; if (i < r0 || i > r1) return; const bmv = state.method === "fl" ? D.s.bmf : D.s.bm, bsv = state.method === "fl" ? D.s.bsdf : D.s.bsd; thr.push({ x: parse(w), v: state.method !== "cusum" && bmv[j] != null ? bmv[j] + state.thr * bsv[j] : null }); });
      tsSvg.append("path").datum(thr).attr("d", line).attr("fill", "none").attr("stroke", "#000").attr("stroke-dasharray", "2 3").attr("stroke-width", 1.2);
      const al = [];
      aw.forEach((w, j) => { const i = ALERT_OFF + j; if (i >= r0 && i <= r1 && isAlert(D.id, j)) al.push({ x: parse(w), v: D.reported[i] }); });
      tsSvg.selectAll("path.al").data(al).join("path").attr("class", "al").attr("d", d3.symbol().type(d3.symbolTriangle).size(60)).attr("fill", "#d55e00").attr("stroke", "#000")
        .attr("transform", (p) => "translate(" + x(p.x) + "," + (y(p.v) - 7) + ")");
      alertText = al.length + " alert week(s) in range";
      if (D.s.art.some((v, j) => v && ALERT_OFF + j >= r0 && ALERT_OFF + j <= r1)) alertText += "; reporting artefact weeks are excluded from alerts";
    }
    if (D.fc && r1 === W - 1) {
      const fp = fw.map((w, k) => ({ x: w, m: D.fc.mean[k], lo: D.fc.lo[k], hi: D.fc.hi[k] }));
      const area = d3.area().defined((p) => p.lo != null && p.hi != null).x((p) => x(p.x)).y0((p) => y(p.lo)).y1((p) => y(p.hi));
      tsSvg.append("path").datum(fp).attr("d", area).attr("fill", OKABE.fc).attr("opacity", 0.25);
      tsSvg.append("path").datum(fp).attr("d", d3.line().defined((p) => p.m != null).x((p) => x(p.x)).y((p) => y(p.m))).attr("fill", "none").attr("stroke", OKABE.fc).attr("stroke-width", 2);
      if (fp.some((p) => p.m == null)) alertText += (alertText ? "; " : "") + "forecast values below 5 are suppressed";
    }
    tsSvg.append("rect").attr("x", M.l).attr("y", M.t).attr("width", TSW - M.l - M.r).attr("height", TSH - M.t - M.b).attr("fill", "none").style("pointer-events", "all")
      .on("mousemove", (e) => { const p = d3.pointer(e, tsSvg.node()), t = x.invert(p[0] * (TSW / tsSvg.node().clientWidth) / (TSW / tsSvg.node().clientWidth)), i = d3.bisector((q) => q.x).center(all, t), q = all[i];
        if (q) showTip(show(q.x) + "<br>Cases (onset week): " + (q.v == null ? "suppressed (<5)" : fmt(q.v)) + (D.reported && D.reported[q.i] != null ? "<br>Reported that week: " + fmt(D.reported[q.i]) : "") + (q.i > MATURE ? "<br>Incomplete: late reports pending" : ""), e); })
      .on("mouseleave", hideTip);
    $("ts-legend").innerHTML = '<span><span class="sw" style="background:' + OKABE.obs + '"></span>Cases by onset week</span><span><span class="sw" style="background:' + OKABE.rep + '"></span>Cases as reported (alert basis)</span><span><span class="sw" style="background:' + OKABE.fc + ';opacity:.5"></span>Forecast and 80% band</span><span>Dotted: alert threshold</span><span>&#9650; alert</span><span>' + alertText + "</span>";
    drawContext(D);
  }
  function drawContext(D) {
    const W = WEEKS.length;
    ctxX = d3.scaleUtc().domain([parse(WEEKS[0]), parse(WEEKS[W - 1])]).range([M.l, TSW - M.r]);
    const mx = d3.max(D.cases, (v) => v || 0) || 1, cy = d3.scaleLinear().domain([0, mx]).range([40, 4]);
    ctxSvg.selectAll("*").remove();
    ctxSvg.append("path").datum(D.cases.map((v, i) => ({ x: parse(WEEKS[i]), v }))).attr("d", d3.area().defined((p) => p.v != null).x((p) => ctxX(p.x)).y0(40).y1((p) => cy(p.v))).attr("fill", "#bbbbbb");
    ctxSvg.append("g").attr("class", "axis").attr("transform", "translate(0,40)").call(d3.axisBottom(ctxX).ticks(8));
    brush = d3.brushX().extent([[M.l, 2], [TSW - M.r, 40]]).on("end", (e) => {
      if (!e.sourceEvent) return;
      if (!e.selection) return;
      const bis = d3.bisector((w) => parse(w)).center, a = bis(WEEKS, ctxX.invert(e.selection[0])), b = bis(WEEKS, ctxX.invert(e.selection[1]));
      if (b - a >= 3 && (a !== state.range[0] || b !== state.range[1])) { state.range = [a, b]; dispatch.call("change"); }
    });
    brushG = ctxSvg.append("g").call(brush);
    brushG.call(brush.move, [ctxX(parse(WEEKS[state.range[0]])), ctxX(parse(WEEKS[state.range[1]]))]);
  }

  /* ---------------------------------------------------------------- 3. heatmap */
  const heatSvg = d3.select("#heat").append("svg").attr("width", "100%");
  function renderHeat() {
    heatSvg.selectAll("*").remove();
    const mode = state.heatMode;
    if (mode === "year") {
      const id = state.district, s = id ? sc(id) : null;
      let arr, pop, name;
      if (s) { arr = s.cases; pop = popOf(id); name = districts().find((d) => d.id === id).name; }
      else { const dn = id ? divisionOf(id) : null; arr = dn ? divSeries(dn) : (NAT || DIV).national_totals[state.disease]; pop = dn ? divPop(dn) : d3.sum(districts(), (d) => d.population); name = dn ? dn + " division (aggregate)" : "Whole country"; }
      const cells = WEEKS.map((w, i) => ({ ...epiKey(w), i, v: arr[i] == null ? null : arr[i] / pop * 1e5 }));
      const years = Array.from(new Set(cells.map((c) => c.y))).sort(), cw = 9, ch = 20, L = 40, T = 22;
      heatSvg.attr("viewBox", "0 0 " + (L + 53 * cw + 10) + " " + (T + years.length * ch + 22));
      const mx = d3.quantile(cells.map((c) => c.v).filter((v) => v != null).sort(d3.ascending), 0.97) || 1, col = d3.scaleSequential(d3.interpolateViridis).domain([0, mx]).clamp(true);
      heatSvg.append("text").attr("x", L).attr("y", 12).attr("font-size", 10).text(name + " (incidence per 100,000 per week; columns are epi-weeks)");
      heatSvg.selectAll("rect").data(cells).join("rect").attr("x", (c) => L + (c.w - 1) * cw).attr("y", (c) => T + years.indexOf(c.y) * ch).attr("width", cw - 1).attr("height", ch - 1)
        .attr("fill", (c) => (c.v == null ? "url(#hatch)" : col(c.v))).attr("stroke", (c) => (c.i >= state.range[0] && c.i <= state.range[1] ? "#d55e00" : "none")).attr("stroke-width", 1.2)
        .on("mousemove", (e, c) => showTip("Epi-week " + c.w + ", " + c.y + "<br>" + (c.v == null ? "suppressed (<5)" : f2(c.v) + " per 100,000") + (c.i > MATURE ? "<br>incomplete" : ""), e)).on("mouseleave", hideTip)
        .on("click", (e, c) => { state.range = [Math.max(0, c.i - 4), Math.min(WEEKS.length - 1, c.i + 4)]; dispatch.call("change"); });
      heatSvg.selectAll("text.y").data(years).join("text").attr("class", "y").attr("x", L - 4).attr("y", (y) => T + years.indexOf(y) * ch + 13).attr("text-anchor", "end").attr("font-size", 10).text((y) => y);
      heatSvg.selectAll("text.x").data(d3.range(1, 54, 4)).join("text").attr("class", "x").attr("x", (w) => L + (w - 1) * cw).attr("y", T + years.length * ch + 12).attr("font-size", 9).text((w) => w);
      legend("heat-legend", col, mx, "per 100,000");
    } else {
      let rows;
      if (NAT) rows = districts().slice().sort((a, b) => d3.ascending(a.division + a.name, b.division + b.name)).map((d) => ({ id: d.id, label: d.name, arr: NAT.series[d.id][state.disease].cases, pop: d.population }));
      else rows = DIV.divisions.map((d) => ({ id: null, label: d.name + " (division)", arr: d.totals[state.disease], pop: d.population }))
        .concat(OWN ? [{ id: OWN.district.id, label: OWN.district.name + " (own)", arr: OWN.series[state.disease].cases, pop: OWN.district.population }] : []);
      const cw = 12, ch = 7, L = 90, T = 18;
      heatSvg.attr("viewBox", "0 0 " + (L + 53 * cw + 10) + " " + (T + rows.length * ch + 22));
      const cells = [];
      rows.forEach((r, ri) => { const acc = {}; WEEKS.forEach((w, i) => { const k = epiKey(w).w; if (r.arr[i] != null) { (acc[k] = acc[k] || []).push(r.arr[i] / r.pop * 1e5); } });
        for (let w = 1; w <= 53; w++) cells.push({ ri, w, id: r.id, label: r.label, v: acc[w] ? d3.mean(acc[w]) : null }); });
      const mx = d3.quantile(cells.map((c) => c.v).filter((v) => v != null).sort(d3.ascending), 0.97) || 1, col = d3.scaleSequential(d3.interpolateViridis).domain([0, mx]).clamp(true);
      heatSvg.append("text").attr("x", L).attr("y", 11).attr("font-size", 10).text("Mean weekly incidence per 100,000 by epi-week across years");
      heatSvg.selectAll("rect").data(cells).join("rect").attr("x", (c) => L + (c.w - 1) * cw).attr("y", (c) => T + c.ri * ch).attr("width", cw - 1).attr("height", ch - 1).attr("fill", (c) => (c.v == null ? "url(#hatch)" : col(c.v)))
        .attr("stroke", (c) => (c.id && c.id === state.district ? "#d55e00" : "none")).style("cursor", "pointer")
        .on("mousemove", (e, c) => showTip(c.label + ", epi-week " + c.w + "<br>" + (c.v == null ? "suppressed (<5)" : f2(c.v) + " per 100,000"), e)).on("mouseleave", hideTip)
        .on("click", (e, c) => { if (c.id) select(c.id); });
      heatSvg.selectAll("text.r").data(rows.filter((r, i) => i % 4 === 0)).join("text").attr("class", "r").attr("x", L - 3).attr("y", (r) => T + rows.indexOf(r) * ch + 6).attr("text-anchor", "end").attr("font-size", 7).text((r) => r.label);
      legend("heat-legend", col, mx, "per 100,000");
    }
  }
  function legend(elId, col, mx, unit) {
    $(elId).innerHTML = [0, 0.25, 0.5, 0.75, 1].map((p) => '<span><span class="sw" style="background:' + col(p * mx) + '"></span>' + f1(p * mx) + "</span>").join("") + "<span>" + unit + '</span><span><span class="sw" style="background:repeating-linear-gradient(45deg,#eee,#eee 3px,#888 3px,#888 4px)"></span>suppressed (&lt;5 cases)</span>';
  }

  /* ---------------------------------------------------------------- 4. allocation */
  const allocSvg = d3.select("#alloc").append("svg").attr("width", "100%");
  function renderAlloc() {
    const r = state.resource, id = sid();
    const alloc = NAT ? NAT.allocation.grid[id] : null;
    const demand = NAT ? NAT.demand : OWN.demand;
    let rows;
    if (NAT) {
      const dm = {}; NAT.demand.filter((d) => d.r === r).forEach((d) => (dm[d.d] = d));
      rows = Object.keys(alloc).map((d) => { const a = alloc[d][r]; return { id: d, name: districts().find((x) => x.id === d).name, demand: dm[d] ? dm[d].upper : 0, stock: a[1], alloc: a[2], base: a[3], need: a[0] }; });
      rows.sort((a, b) => b.need - a.need); let top = rows.slice(0, 12);
      if (state.district && !top.some((t) => t.id === state.district)) top.push(rows.find((t) => t.id === state.district));
      rows = top.filter(Boolean);
    } else {
      const a = OWN.allocation[id] ? OWN.allocation[id][r] : null, dm = demand.find((d) => d.r === r);
      rows = a ? [{ id: OWN.district.id, name: OWN.district.name + " (own district)", demand: dm ? dm.upper : 0, stock: a[1], alloc: a[2], base: a[3], need: a[0] }] : [];
    }
    const keys = [["demand", "Forecast demand (upper, 4 weeks)"], ["stock", "Current stock"], ["alloc", "Recommended allocation"], ["base", "Population-proportional baseline"]];
    const rh = 44, L = 120, W = 560, H = rows.length * rh + 30;
    allocSvg.attr("viewBox", "0 0 " + W + " " + H).selectAll("*").remove();
    const mx = d3.max(rows, (d) => d3.max(keys, (k) => d[k[0]])) || 1, x = d3.scaleLinear().domain([0, mx]).nice().range([L, W - 10]);
    allocSvg.append("g").attr("class", "axis").attr("transform", "translate(0," + (H - 22) + ")").call(d3.axisBottom(x).ticks(5).tickFormat(d3.format("~s")));
    rows.forEach((d, i) => { const g = allocSvg.append("g").attr("transform", "translate(0," + (i * rh + 4) + ")");
      g.append("text").attr("x", L - 4).attr("y", 18).attr("text-anchor", "end").attr("font-size", 10).attr("font-weight", d.id === state.district ? "bold" : "normal").text(d.name);
      keys.forEach((k, j) => g.append("rect").attr("x", L).attr("y", j * 9).attr("width", Math.max(0, x(d[k[0]]) - L)).attr("height", 8).attr("fill", OKABE[k[0]])
        .style("cursor", "pointer").on("click", () => NAT && select(d.id)).on("mousemove", (e) => showTip("<b>" + d.name + "</b><br>" + k[1] + ": " + fmt(d[k[0]]) + " " + (RES_LABEL[r] || r).replace(/.*\(|\)/g, ""), e)).on("mouseleave", hideTip)); });
    $("alloc-legend").innerHTML = keys.map((k) => '<span><span class="sw" style="background:' + OKABE[k[0]] + '"></span>' + k[1] + "</span>").join("");
    const sce = (NAT || DIV).allocation.scenarios.find((s) => s.scenario_id === id);
    $("alloc-note").textContent = sce ? "Budget " + Math.round(state.budget * 100) + "% of national need, equity floor alpha " + state.alpha + ". " + (NAT ? "Top districts by unmet need shown." : "Own district only; other districts are restricted.") : "";
  }

  /* ---------------------------------------------------------------- 5. ranking table */
  function renderTable() {
    const j = latestJ(), t = $("ewt");
    let rows;
    const bedOf = (id) => { const b = (NAT ? NAT.beds : OWN.beds).find((x) => x.d === id); return b ? b.gap : null; };
    if (NAT) rows = districts().map((d) => { const s = NAT.series[d.id][state.disease]; return { id: d.id, name: d.name, div: d.division, rep: s.reported[ALERT_OFF + j], sc: score(d.id), alert: isAlert(d.id, j), fc: s.fc ? s.fc.mean : null, bed: bedOf(d.id) }; });
    else rows = OWN ? [{ id: OWN.district.id, name: OWN.district.name, div: OWN.district.division, rep: OWN.series[state.disease].reported[ALERT_OFF + j], sc: score(OWN.district.id), alert: isAlert(OWN.district.id, j), fc: OWN.series[state.disease].fc ? OWN.series[state.disease].fc.mean : null, bed: bedOf(OWN.district.id) }] : [];
    rows.sort((a, b) => d3.descending(a.sc == null ? -1e9 : a.sc, b.sc == null ? -1e9 : b.sc));
    const hdr = "<tr><th>#</th><th>District</th><th>Division</th><th>Reported (latest wk)</th><th>" + (state.method === "cusum" ? "CUSUM" : "z-score") + "</th><th>Alert</th><th>Forecast 4 wk</th><th>Bed gap</th></tr>";
    const body = rows.slice(0, 64).map((r, i) => '<tr class="row' + (r.id === state.district ? " sel" : "") + '" data-id="' + r.id + '"><td>' + (i + 1) + "</td><td>" + r.name + "</td><td>" + r.div + "</td><td>" + (r.rep == null ? "&lt;5" : fmt(r.rep)) + "</td><td>" + (r.sc == null ? "n/a" : f2(r.sc)) + "</td><td>" + (r.alert ? "&#9650; ALERT" : "") + "</td><td>" + (r.fc && r.fc.every((v) => v != null) ? fmt(d3.sum(r.fc)) : "suppressed") + "</td><td>" + (r.bed == null ? "n/a" : f1(r.bed)) + "</td></tr>").join("");
    const note = NAT ? "" : '<tr><td colspan="8">Other districts are available only in aggregate for this role (see the map and heatmap division values).</td></tr>';
    t.innerHTML = hdr + body + note;
    t.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => select(tr.dataset.id)));
  }

  /* ---------------------------------------------------------------- what-if, quality, calendar */
  const eqSvg = d3.select("#eq-chart").append("svg").attr("viewBox", "0 0 300 170").attr("width", "100%");
  const detSvg = d3.select("#det-chart").append("svg").attr("viewBox", "0 0 300 170").attr("width", "100%");
  function renderWhatIf() {
    $("v-budget").textContent = Math.round(state.budget * 100) + "%"; $("v-alpha").textContent = state.alpha; $("v-thr").textContent = state.thr;
    const sc_ = (NAT || DIV).allocation.scenarios, cur = sc_.find((s) => s.scenario_id === sid()), rec = (NAT || DIV).allocation.recommended[state.budget.toFixed(1)];
    $("whatif-kpi").innerHTML = cur ? "Weighted unmet demand: <b>" + d3.format(",.0f")(cur.weighted_unmet) + "</b> (baseline " + d3.format(",.0f")(cur.baseline_weighted_unmet) + "; " + d3.format(".0%")(cur.improvement_vs_baseline) + " lower). Inequality of coverage across districts (Gini, lower is fairer): <b>" + f2(cur.gini_coverage) + "</b> (baseline " + f2(cur.baseline_gini) + "). Recommended alpha at this budget: <b>" + (rec ? rec.alpha : "n/a") + "</b>." : "No scenario for this setting.";
    const pts = sc_.filter((s) => s.budget_fraction === state.budget).sort((a, b) => a.alpha - b.alpha);
    eqSvg.selectAll("*").remove();
    const x = d3.scaleLinear().domain([0, d3.max(pts, (p) => Math.max(p.gini_coverage, p.baseline_gini)) * 1.1 || 1]).range([40, 290]), y = d3.scaleLinear().domain([0, d3.max(pts, (p) => Math.max(p.weighted_unmet, p.baseline_weighted_unmet)) * 1.1 || 1]).range([140, 10]);
    eqSvg.append("g").attr("class", "axis").attr("transform", "translate(0,140)").call(d3.axisBottom(x).ticks(5).tickFormat(d3.format(".2f")));
    eqSvg.append("g").attr("class", "axis").attr("transform", "translate(40,0)").call(d3.axisLeft(y).ticks(4).tickFormat(d3.format("~s")));
    eqSvg.append("text").attr("x", 165).attr("y", 166).attr("font-size", 9).attr("text-anchor", "middle").text("Inequality of coverage across districts (Gini; left is fairer)");
    eqSvg.append("text").attr("x", 4).attr("y", 8).attr("font-size", 9).text("Weighted unmet (efficiency)");
    eqSvg.append("path").datum(pts).attr("d", d3.line().x((p) => x(p.gini_coverage)).y((p) => y(p.weighted_unmet))).attr("fill", "none").attr("stroke", OKABE.alloc);
    eqSvg.selectAll("circle").data(pts).join("circle").attr("cx", (p) => x(p.gini_coverage)).attr("cy", (p) => y(p.weighted_unmet)).attr("r", (p) => (p.alpha === state.alpha ? 6 : 3.5)).attr("fill", (p) => (p.alpha === state.alpha ? "#000" : OKABE.alloc))
      .on("mousemove", (e, p) => showTip("alpha " + p.alpha + ": weighted unmet " + d3.format(",.0f")(p.weighted_unmet) + ", Gini of coverage " + f2(p.gini_coverage), e)).on("mouseleave", hideTip).on("click", (e, p) => { state.alpha = p.alpha; $("alpha").value = p.alpha; dispatch.call("change"); });
    if (pts.length) eqSvg.append("rect").attr("x", x(pts[0].baseline_gini) - 4).attr("y", y(pts[0].baseline_weighted_unmet) - 4).attr("width", 8).attr("height", 8).attr("fill", OKABE.base).append("title").text("Population-proportional baseline");
    const ev = EVAL.detection;
    detSvg.selectAll("*").remove();
    const dx = d3.scaleLinear().domain([0, d3.max(ev, (d) => d.false_per_district_year) * 1.05 || 1]).range([40, 290]), dy = d3.scaleLinear().domain([0, 1]).range([140, 10]);
    detSvg.append("g").attr("class", "axis").attr("transform", "translate(0,140)").call(d3.axisBottom(dx).ticks(5));
    detSvg.append("g").attr("class", "axis").attr("transform", "translate(40,0)").call(d3.axisLeft(dy).ticks(4).tickFormat(d3.format(".0%")));
    detSvg.append("text").attr("x", 165).attr("y", 166).attr("font-size", 9).attr("text-anchor", "middle").text("False alert episodes per district-year");
    detSvg.append("text").attr("x", 4).attr("y", 8).attr("font-size", 9).text("Sensitivity (injected outbreaks found)");
    const cols = { c2: "#0072B2", fl: "#CC79A7", cusum: "#009E73", c2_naive: "#D55E00" };
    ["c2", "fl", "cusum", "c2_naive"].forEach((m) => { const d = ev.filter((e) => e.method === m).sort((a, b) => a.threshold - b.threshold);
      detSvg.append("path").datum(d).attr("d", d3.line().x((p) => dx(p.false_per_district_year)).y((p) => dy(p.sensitivity))).attr("fill", "none").attr("stroke", cols[m]).attr("stroke-dasharray", m === "c2_naive" ? "3 3" : null);
      detSvg.selectAll("circle." + m).data(d).join("circle").attr("class", m).attr("cx", (p) => dx(p.false_per_district_year)).attr("cy", (p) => dy(p.sensitivity)).attr("r", (p) => (m === state.method && Math.abs(p.threshold - state.thr) < 0.26 ? 6 : 2.5)).attr("fill", cols[m])
        .on("mousemove", (e, p) => showTip(m + " threshold " + p.threshold + "<br>sensitivity " + d3.format(".0%")(p.sensitivity) + ", false alerts " + f2(p.false_per_district_year) + " per district-year", e)).on("mouseleave", hideTip); });
    detSvg.append("text").attr("x", 292).attr("y", 20).attr("text-anchor", "end").attr("font-size", 9).html("").text("blue C2, pink seasonal, green CUSUM, orange C2 no guard");
  }
  function renderDQ() {
    const rules = QUAL.rules.filter((r) => r.source === "encounters" && r.fail > 0).sort((a, b) => b.fail - a.fail), rows = QUAL.rules.filter((r) => r.source === "encounters");
    const total = rows.length ? rows[0].rows : 0, mx = d3.max(rules, (r) => r.fail / r.rows) || 1;
    $("dq").innerHTML = "<div class='hint'>Encounter records received: " + fmt(total) + ". Rule failures (a record can fail more than one rule). Failing rows go to quarantine with a reason code.</div>" +
      rules.map((r) => "<div style='display:flex;align-items:center;gap:6px;font-size:11px'><span style='width:170px'>" + r.rule + "</span><span style='display:inline-block;height:9px;background:#0072B2;width:" + (200 * (r.fail / r.rows) / mx) + "px'></span><span>" + d3.format(".2%")(r.fail / r.rows) + " (" + fmt(r.fail) + ")</span></div>").join("") +
      "<div class='hint' style='margin-top:6px'>Other sources: " + QUAL.rules.filter((r) => r.source !== "encounters" && r.fail > 0).length + " rules with failures (weather and stock).</div>";
  }
  function renderCal() {
    const rows = CAL.filter((c) => c.disease_group === state.disease), W = 640, rh = 24, L = 90, T = 20, x = d3.scaleLinear().domain([1, 53]).range([L, W - 10]);
    const svg = d3.select("#cal").html("").append("svg").attr("viewBox", "0 0 " + W + " " + (T + rows.length * rh + 24)).attr("width", "100%");
    svg.append("g").attr("class", "axis").attr("transform", "translate(0," + (T + rows.length * rh) + ")").call(d3.axisBottom(x).ticks(13));
    svg.append("text").attr("x", L).attr("y", 12).attr("font-size", 10).text("Epi-week of the year. Bar = high season; square = pre-position supplies; circle = start prevention campaign");
    rows.forEach((c, i) => { const y0 = T + i * rh, g = svg.append("g");
      g.append("text").attr("x", L - 4).attr("y", y0 + 14).attr("text-anchor", "end").attr("font-size", 10).text(c.division);
      const segs = c.season_end_week >= c.season_start_week ? [[c.season_start_week, c.season_end_week]] : [[c.season_start_week, 53], [1, c.season_end_week]];
      segs.forEach((s) => g.append("rect").attr("x", x(s[0])).attr("y", y0 + 4).attr("width", Math.max(2, x(s[1] + 1) - x(s[0]))).attr("height", 12).attr("fill", c.marked_season ? "#0072B2" : "#bbbbbb")
        .on("mousemove", (e) => showTip(c.division + ": " + (c.marked_season ? "high season" : "weak seasonality") + " weeks " + c.season_start_week + " to " + c.season_end_week + " (peak week " + c.peak_week + ")<br>Pre-position by " + c.preposition_date + "; start campaign by " + c.campaign_start_date, e)).on("mouseleave", hideTip));
      g.append("rect").attr("x", x(c.preposition_week) - 4).attr("y", y0 + 6).attr("width", 8).attr("height", 8).attr("fill", "#E69F00").attr("stroke", "#000");
      g.append("circle").attr("cx", x(c.campaign_start_week)).attr("cy", y0 + 10).attr("r", 4.5).attr("fill", "#D55E00").attr("stroke", "#000"); });
  }

  /* ---------------------------------------------------------------- control flow */
  function select(id) { state.district = id; dispatch.call("change"); }
  function renderAll() {
    d3.selectAll(".stamp").text(stampText());
    $("fresh").textContent = stampText() + ". Forecast origin " + META.forecast_origin + ". Counts older than " + META.mature_until + " are complete.";
    renderMap(); renderTS(); renderHeat(); renderAlloc(); renderTable(); renderWhatIf(); renderDQ(); renderCal();
  }
  async function loadRole() {
    try {
      $("error").hidden = true;
      if (state.role === "national") { NAT = await getJSON("data/national.json"); DIV = null; OWN = null; }
      else { DIV = await getJSON("data/division_view.json"); OWN = await getJSON("data/district/" + state.own + ".json"); NAT = null; state.district = state.own; }
      WEEKS = bundle().weeks; const aw = (bundle().alert_weeks || OWN.alert_weeks); ALERT_OFF = WEEKS.length - aw.length;
      if (DIV && !DIV.allocation) DIV.allocation = { scenarios: (EVAL.allocation || {}).scenarios || [], recommended: META.recommended };
      MATURE = d3.bisector((w) => w).right(WEEKS, META.mature_until) - 1;
      state.range = [Math.max(0, WEEKS.length - 53), WEEKS.length - 1];
      renderAll();
    } catch (e) { $("error").hidden = false; $("error").textContent = "Could not load dashboard data: " + e.message + ". Run the pipeline (make all) and serve this folder with python -m http.server."; }
  }
  async function boot() {
    try {
      [META, GEO, QUAL, CAL, EVAL] = await Promise.all([getJSON("data/meta.json"), getJSON("data/districts.geojson"), getJSON("data/quality.json"), getJSON("data/calendar.json"), getJSON("data/evaluation.json")]);
      const sceF = await getJSON("data/scenarios.json"); EVAL.allocation = sceF;
    } catch (e) { $("error").hidden = false; $("error").textContent = "Could not load dashboard data: " + e.message + ". Run the pipeline (make all) and serve this folder with python -m http.server."; return; }
    $("disease").innerHTML = META.diseases.map((d) => '<option value="' + d + '">' + LABEL[d] + "</option>").join("");
    $("resource").innerHTML = Object.keys(META.resources).map((r) => '<option value="' + r + '">' + (RES_LABEL[r] || r) + "</option>").join("");
    const names = GEO.features.map((f) => f.properties).sort((a, b) => d3.ascending(a.district, b.district));
    $("own").innerHTML = names.map((p) => '<option value="' + p.district_id + '">' + p.district + "</option>").join(""); state.own = names[0].district_id;
    state.method = ["c2", "fl", "cusum"].includes(META.operating_point.method) ? META.operating_point.method : "c2"; state.thr = META.operating_point.threshold; $("method").value = state.method;
    $("thr").value = state.thr; $("budget").value = state.budget; $("alpha").value = state.alpha;
    initMap();
    $("role").onchange = (e) => { state.role = e.target.value; $("own-wrap").hidden = state.role === "national"; if (state.role === "national") state.district = null; loadRole(); };
    $("own").onchange = (e) => { state.own = e.target.value; loadRole(); };
    $("disease").onchange = (e) => { state.disease = e.target.value; dispatch.call("change"); };
    $("method").onchange = (e) => { state.method = e.target.value; dispatch.call("change"); };
    $("resource").onchange = (e) => { state.resource = e.target.value; dispatch.call("change"); };
    $("heat-mode").onchange = (e) => { state.heatMode = e.target.value; dispatch.call("change"); };
    $("budget").oninput = (e) => { state.budget = +e.target.value; dispatch.call("change"); };
    $("alpha").oninput = (e) => { state.alpha = +e.target.value; dispatch.call("change"); };
    $("thr").oninput = (e) => { state.thr = +e.target.value; dispatch.call("change"); };
    dispatch.on("change", renderAll);
    await loadRole();
  }
  window.EPISCOPE = { state, dispatch, boot };
  boot();
})();
