/* The life cycle of rap slang — todd.sh/RapAnalysis
 * Plain D3, no build step. Every section renders from the JSON files in data/.
 */
(() => {
  "use strict";

  const C = {
    paper: "#F4F1EA", paperDeep: "#E9E4D9", ink: "#1D1C19", stone: "#8D887E", line: "#D4CEC2",
    red: "#B64832", blue: "#31566B", ochre: "#B58A43", grid: "#E3DDD1",
  };
  const GENRE = {
    rap: { label: "Rap", color: C.blue, dash: null, width: 2.5 },
    rb: { label: "R&B", color: C.ochre, dash: null, width: 2 },
    pop: { label: "Pop", color: C.red, dash: null, width: 2 },
    rock: { label: "Rock", color: C.stone, dash: "5 4", width: 1.5 },
    country: { label: "Country", color: C.stone, dash: "1.5 3", width: 1.5 },
  };
  const STAGE = {
    faded: { label: "Faded", color: C.red },
    cooling: { label: "Cooling", color: C.ochre },
    rising: { label: "Rising", color: C.blue },
    staple: { label: "Staple", color: C.stone },
  };
  const CAT = {
    praise: "Praise & style", talk: "Talk", people: "People", diss: "Disses", money: "Money", drugs: "Drugs & drink",
    brands: "Brands", flex: "Flex", guns: "Guns", violence: "Conflict", tech: "Tech & media", adlib: "Ad-libs",
    dance: "Dances", party: "Party",
  };
  const REGION = {
    West: { color: C.blue, dash: null }, South: { color: C.red, dash: null }, East: { color: C.ochre, dash: null },
    Midwest: { color: C.ink, dash: null }, UK: { color: C.stone, dash: "5 4" }, Canada: { color: C.stone, dash: "1.5 3" },
  };
  const REGION_ORDER = ["West", "South", "East", "Midwest", "UK", "Canada"];

  const fmtPct = (v) => (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2)) + "%";
  const fmtInt = d3 => d3.format(",");
  const el = (tag, attrs = {}, html) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) n.setAttribute(k, v);
    }
    if (html !== undefined) n.innerHTML = html;
    return n;
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------------------------------------------------------------------------------
  // Tooltip
  const tip = document.getElementById("tip");
  function showTip(html, event) {
    tip.innerHTML = html;
    tip.style.opacity = 1;
    const pad = 14, r = tip.getBoundingClientRect();
    let x = event.pageX + pad, y = event.pageY + pad;
    if (x + r.width > window.scrollX + document.documentElement.clientWidth - 8) x = event.pageX - r.width - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  const hideTip = () => { tip.style.opacity = 0; };

  // In-page links: the page sets <base href="/RapAnalysis/">, so "#x" would navigate away.
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    const id = a.getAttribute("href").slice(1);
    const target = document.getElementById(id);
    if (!target) return;
    e.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    history.replaceState(null, "", location.pathname + location.search + "#" + id);
  });

  const renderers = [];
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - lastWidth) < 20) return;
    lastWidth = window.innerWidth;
    clearTimeout(window.__rz);
    window.__rz = setTimeout(() => renderers.forEach((f) => f()), 150);
  });
  const track = (name, data) => { try { window.va && window.va("event", { name, data }); } catch (_) { /* analytics is optional */ } };

  // ---------------------------------------------------------------------------------
  const load = (f) => fetch("data/" + f).then((r) => (r.ok ? r.json() : null)).catch(() => null);

  Promise.all(["meta.json", "terms.json", "meaning.json", "turnover.json", "regions.json"].map(load)).then(([meta, terms, meaning, turnover, regions]) => {
    const YEARS = terms.years;
    const T = new Map(terms.terms.map((t) => [t.id, t]));
    const state = { term: null };
    let explorerScale = "share";
    let explorerCat = "all";
    let reachGenre = "pop";
    let meaningTarget = "cap";
    let regionEra = null;
    const SENSE_STYLE = {
      cap: { lie: [C.blue, "lie (“no cap”)"], shoot: [C.red, "gunshot (“bust a cap”)"], hat: [C.ochre, "hat"], other: [C.stone, "other"] },
      drip: { style: [C.blue, "style, outfit"], liquid: [C.ochre, "liquid"] },
      ice: { jewelry: [C.blue, "diamonds, jewelry"], cold: [C.ochre, "cold, frozen"], name: [C.stone, "names (Ice Cube, Ice-T)"] },
      gas: { weed: [C.blue, "weed"], hype: [C.red, "hype (“gas me up”)"], fuel: [C.ochre, "fuel, driving"], poison: [C.stone, "gas chamber, fumes"] },
    };

    renderStats(meta, terms);
    renderMultiples();
    renderCalendar();
    initExplorer();
    renderCrossover();
    renderReach();
    renderCards();
    if (turnover) { renderCohorts(); renderChurn(); }
    if (meaning) initMeaning();
    if (regions) initRegions();

    const q = new URLSearchParams(location.search).get("term");
    selectTerm(T.has(q) ? q : "swag", false);

    // -------------------------------------------------------------------------------
    function renderStats(meta, terms) {
      const box = document.getElementById("stats");
      const f = d3.format(",");
      const items = [
        [f(meta.rap_songs), "rap songs"],
        [f(meta.rap_artists), "rap artists"],
        [f(meta.all_songs - meta.rap_songs), "pop, R&B, rock and country songs for comparison"],
        [terms.terms.length, "slang terms tracked"],
      ];
      box.innerHTML = items.map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");
    }

    // -------------------------------------------------------------------------------
    // Small multiples
    function renderMultiples() {
      const groups = [
        ["Came and went", C.red, ["phat", "wordup", "cristal", "jiggy", "crunk", "bling", "izzle", "dro", "hyphy", "myspace", "swag", "yolo"]],
        ["Still climbing", C.blue, ["drip", "opps", "nocap", "racks", "percs", "thot", "draco", "slatt", "baddie", "boutta", "glizzy", "wock"]],
        ["Built to last", C.ochre, ["homie", "haters", "glock", "henny", "ballin", "hella", "finna", "tryna"]],
      ];
      const box = document.getElementById("multiples");
      box.innerHTML = "";
      for (const [label, color, ids] of groups) {
        box.appendChild(el("div", { class: "group-label" }, label));
        for (const id of ids) {
          const t = T.get(id);
          if (!t) continue;
          const btn = el("button", { class: "mult", type: "button", "aria-label": `${t.label}: peak ${fmtPct(t.life.peak)} of rap artists in ${t.life.peak_year}. Open in explorer.` });
          btn.innerHTML = `<div class="mult-head"><span class="mult-name">${esc(t.label)}</span><span class="mult-meta">${fmtPct(t.life.peak)} · ${t.life.peak_year}</span></div><p class="mult-gloss">${esc(t.gloss)}</p>`;
          btn.appendChild(sparkline(t.rap, color, 170, 70, btn.querySelector(".mult-meta"), t));
          btn.addEventListener("click", () => { selectTerm(id, true); track("multiple_click", { term: id }); });
          box.appendChild(btn);
        }
      }
    }

    function sparkline(values, color, W, H, readout, t) {
      const svg = d3.create("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%").style("overflow", "visible");
      const x = d3.scaleLinear([YEARS[0], YEARS[YEARS.length - 1]], [2, W - 2]);
      const y = d3.scaleLinear([0, d3.max(values) || 1], [H - 14, 4]);
      svg.append("line").attr("x1", 2).attr("x2", W - 2).attr("y1", H - 14).attr("y2", H - 14).attr("stroke", C.line);
      [1990, 2005, 2020].forEach((yr) => svg.append("text").attr("x", x(yr)).attr("y", H - 2).attr("text-anchor", "middle").attr("font-size", 10).attr("class", "muted").text(yr));
      const area = d3.area().x((d, i) => x(YEARS[i])).y0(H - 14).y1((d) => y(d)).curve(d3.curveMonotoneX);
      const line = d3.line().x((d, i) => x(YEARS[i])).y((d) => y(d)).curve(d3.curveMonotoneX);
      svg.append("path").attr("d", area(values)).attr("fill", color).attr("fill-opacity", 0.1);
      svg.append("path").attr("d", line(values)).attr("fill", "none").attr("stroke", color).attr("stroke-width", 2).attr("stroke-linejoin", "round");
      const hl = svg.append("line").attr("class", "hover-line").attr("y1", 4).attr("y2", H - 14).style("display", "none");
      const dot = svg.append("circle").attr("r", 3.5).attr("fill", color).attr("stroke", C.paper).attr("stroke-width", 2).style("display", "none");
      const def = readout.textContent;
      svg.on("pointermove", (e) => {
        const [mx] = d3.pointer(e);
        const i = Math.max(0, Math.min(YEARS.length - 1, Math.round(x.invert(mx) - YEARS[0])));
        hl.attr("x1", x(YEARS[i])).attr("x2", x(YEARS[i])).style("display", null);
        dot.attr("cx", x(YEARS[i])).attr("cy", y(values[i])).style("display", null);
        readout.textContent = `${YEARS[i]}: ${fmtPct(values[i])}`;
      }).on("pointerleave", () => { hl.style("display", "none"); dot.style("display", "none"); readout.textContent = def; });
      return svg.node();
    }

    // -------------------------------------------------------------------------------
    // Calendar beeswarm
    function renderCalendar() {
      const box = document.getElementById("calendar-chart");
      const draw = () => {
        box.innerHTML = "";
        const W = box.clientWidth, narrow = W < 640;
        const cats = Object.keys(CAT).filter((c) => terms.terms.some((t) => t.cat === c));
        const rowH = narrow ? 58 : 66, M = { l: narrow ? 92 : 124, r: narrow ? 14 : 28, t: 10, b: 28 };
        const H = M.t + M.b + rowH * cats.length;
        const x = d3.scaleLinear([1987.5, 2021.5], [M.l, W - M.r]);
        const yb = d3.scaleBand(cats, [M.t, H - M.b]);
        // Standard-word spellings get a fixed small ring: their counts mix senses.
        const rs = d3.scaleSqrt([0, 20], [2.5, narrow ? 10 : 13]).clamp(true);
        const r = (t) => (t.poly ? 3.5 : rs(t.life.peak));
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img")
          .attr("aria-label", "Beeswarm of every tracked slang term at its peak year, grouped by category");
        svg.append("g").attr("class", "axis clean").attr("transform", `translate(0,${H - M.b})`)
          .call(d3.axisBottom(x).tickValues(d3.range(1990, 2022, narrow ? 10 : 5)).tickFormat(d3.format("d")).tickSize(0).tickPadding(8));
        svg.append("g").selectAll("line").data(cats).join("line").attr("x1", M.l).attr("x2", W - M.r)
          .attr("y1", (c) => yb(c) + yb.bandwidth()).attr("y2", (c) => yb(c) + yb.bandwidth()).attr("stroke", C.grid);
        svg.append("g").selectAll("text").data(cats).join("text").attr("x", 0).attr("y", (c) => yb(c) + yb.bandwidth() / 2 + 4)
          .attr("font-size", 12).attr("font-weight", 700).text((c) => CAT[c]);
        const nodes = terms.terms.map((t) => ({ t, x: x(t.life.peak_year), y: yb(t.cat) + yb.bandwidth() / 2, r: r(t) }));
        const sim = d3.forceSimulation(nodes)
          .force("x", d3.forceX((d) => x(d.t.life.peak_year)).strength(0.9))
          .force("y", d3.forceY((d) => yb(d.t.cat) + yb.bandwidth() / 2).strength(0.1))
          .force("c", d3.forceCollide((d) => d.r + 0.8)).stop();
        for (let i = 0; i < 220; i++) sim.tick();
        nodes.forEach((d) => { const b = yb(d.t.cat); d.y = Math.max(b + d.r, Math.min(b + yb.bandwidth() - d.r, d.y)); });
        svg.append("g").selectAll("circle").data(nodes).join("circle")
          .attr("cx", (d) => d.x).attr("cy", (d) => d.y).attr("r", (d) => d.r)
          .attr("fill", (d) => (d.t.poly ? C.paper : STAGE[d.t.life.stage].color))
          .attr("fill-opacity", (d) => (d.t.poly ? 1 : 0.85))
          .attr("stroke", (d) => (d.t.poly ? C.stone : C.paper)).attr("stroke-width", (d) => (d.t.poly ? 1.5 : 1))
          .style("cursor", "pointer")
          .on("pointermove", (e, d) => showTip(`<b>${esc(d.t.label)}</b> · ${esc(d.t.gloss)}<br>Peaked ${d.t.life.peak_year} at ${fmtPct(d.t.life.peak)} of rap artists<br>${STAGE[d.t.life.stage].label}${d.t.poly ? " · also a standard word" : ""}`, e))
          .on("pointerleave", hideTip)
          .on("click", (e, d) => { hideTip(); selectTerm(d.t.id, true); track("calendar_click", { term: d.t.id }); });
        // Label the biggest non-poly dots, skipping any label that would collide with one
        // already placed or run off the right edge.
        const placed = [];
        const labeled = nodes.filter((d) => !d.t.poly && d.t.life.peak >= (narrow ? 6 : 2.5))
          .sort((a, b) => b.t.life.peak - a.t.life.peak)
          .filter((d) => {
            const w = d.t.label.length * 6.1, box = { x0: d.x + d.r + 2, x1: d.x + d.r + 4 + w, y0: d.y - 7, y1: d.y + 7 };
            if (box.x1 > W) return false;
            if (placed.some((p) => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0)) return false;
            if (nodes.some((n) => n !== d && Math.abs(n.y - d.y) < n.r + 5 && n.x - n.r < box.x1 && n.x + n.r > box.x0)) return false;
            placed.push(box);
            return true;
          });
        svg.append("g").selectAll("text").data(labeled).join("text").attr("x", (d) => d.x + d.r + 3).attr("y", (d) => d.y + 4)
          .attr("font-size", 11).attr("pointer-events", "none").text((d) => d.t.label);
      };
      draw();
      renderers.push(draw);
    }

    // -------------------------------------------------------------------------------
    // Explorer
    function initExplorer() {
      const dl = document.getElementById("term-list");
      const sorted = [...terms.terms].sort((a, b) => a.label.localeCompare(b.label));
      dl.innerHTML = sorted.map((t) => `<option value="${esc(t.label)}"></option>`).join("");
      const input = document.getElementById("term-search");
      const pick = () => {
        const v = input.value.trim().toLowerCase();
        const t = terms.terms.find((t) => t.label.toLowerCase() === v || t.id === v);
        if (t) { selectTerm(t.id, false); track("term_search", { term: t.id }); }
      };
      input.addEventListener("change", pick);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
      const chips = document.getElementById("cat-chips");
      const cats = ["all", ...Object.keys(CAT)];
      chips.innerHTML = "";
      cats.forEach((c) => {
        const n = c === "all" ? terms.terms.length : terms.terms.filter((t) => t.cat === c).length;
        const b = el("button", { class: "chip", type: "button", "data-cat": c, "aria-pressed": c === "all" },
          `${c === "all" ? "All" : CAT[c]} <span class="chip-n">${n}</span>`);
        b.addEventListener("click", () => {
          setCategory(c);
          // Open the category's most widely used term so the chart responds, unless the
          // current term already belongs to it.
          if (c !== "all" && T.get(state.term).cat !== c) {
            const inCat = terms.terms.filter((t) => t.cat === c);
            const top = [...inCat].sort((a, b) => (a.poly - b.poly) || (b.life.peak - a.life.peak))[0];
            selectTerm(top.id, false);
          }
          track("category_filter", { category: c });
        });
        chips.appendChild(b);
      });
      renderIndex();
      renderers.push(() => state.term && renderTerm(state.term));
    }

    function setCategory(c) {
      explorerCat = c;
      document.querySelectorAll("#cat-chips .chip").forEach((x) => x.setAttribute("aria-pressed", x.dataset.cat === c));
      renderIndex();
    }

    function renderIndex() {
      const box = document.getElementById("term-index");
      const list = [...terms.terms].filter((t) => explorerCat === "all" || t.cat === explorerCat).sort((a, b) => a.label.localeCompare(b.label));
      box.innerHTML = "";
      list.forEach((t) => {
        const b = el("button", { type: "button", "aria-current": t.id === state.term }, esc(t.label));
        b.addEventListener("click", () => selectTerm(t.id, false));
        box.appendChild(b);
      });
    }

    function selectTerm(id, scroll) {
      state.term = id;
      // A term opened from elsewhere on the page (calendar, cards) switches the filter to its
      // category, so it shows up highlighted in the list.
      if (explorerCat !== "all" && T.get(id).cat !== explorerCat) setCategory(T.get(id).cat);
      renderTerm(id);
      document.querySelectorAll("#term-index button").forEach((b) => b.setAttribute("aria-current", b.textContent === T.get(id).label));
      document.getElementById("term-search").value = T.get(id).label;
      if (scroll) document.getElementById("explorer").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function renderTerm(id) {
      const t = T.get(id);
      const main = document.getElementById("term-main");
      const L = t.life;
      const stage = STAGE[L.stage];
      const pctOfPeak = L.peak ? Math.round((L.now / L.peak) * 100) : 0;
      main.innerHTML = `
        <div class="term-title"><h3>${esc(t.label)}</h3><span class="badge ${L.stage}">${stage.label}</span><span class="badge">${CAT[t.cat]}</span>${t.region_hint ? `<span class="badge">Associated with ${esc(t.region_hint)}</span>` : ""}</div>
        <p class="term-gloss">${esc(t.gloss)}</p>
        ${t.poly ? `<p class="warn">This spelling also has an older standard meaning, so the curve mixes senses and the early years may not be slang.</p>` : ""}
        <div class="facts">
          <div><b>${L.takeoff ?? "—"}</b><span>took off in rap</span></div>
          <div><b>${L.peak_year}</b><span>peaked, at ${fmtPct(L.peak)} of artists</span></div>
          <div><b>${fmtPct(L.now)}</b><span>in 2020–21 (${pctOfPeak}% of peak)</span></div>
          <div><b>${d3.format(",")(L.total_artists)}</b><span>rap artists ever used it</span></div>
        </div>
        <div class="fig-head" style="margin-top:14px">
          <div class="legend">${Object.entries(GENRE).map(([g, s]) => `<span><i class="${s.dash ? "dash" : ""}" style="border-color:${s.color};${s.dash === "1.5 3" ? "border-top-style:dotted" : ""}"></i>${s.label}</span>`).join("")}</div>
          <div class="controls" style="margin:0" id="scale-chips"></div>
        </div>
        <div id="term-chart"></div>
        <p class="note" id="term-cross"></p>`;
      const sc = main.querySelector("#scale-chips");
      [["share", "Share of artists"], ["index", "Each genre vs its own peak"]].forEach(([k, lab]) => {
        const b = el("button", { class: "chip", type: "button", "aria-pressed": explorerScale === k }, lab);
        b.addEventListener("click", () => { explorerScale = k; renderTerm(id); });
        sc.appendChild(b);
      });
      drawTermChart(t, main.querySelector("#term-chart"));
      const c = t.cross;
      const bits = ["rb", "pop", "country"].map((g) => {
        const x = c[g];
        if (x.lag !== null && x.lag !== undefined) return `${GENRE[g].label} took it up ${x.lag === 0 ? "the same year" : x.lag > 0 ? `${x.lag} year${x.lag > 1 ? "s" : ""} later` : `${-x.lag} year${x.lag < -1 ? "s" : ""} earlier`}`;
        return `${GENRE[g].label} never took it up widely`;
      });
      main.querySelector("#term-cross").textContent = t.poly ? "Crossover timing isn't meaningful for words with older standard meanings." : bits.join(". ") + ".";
      renderSide(t);
    }

    function drawTermChart(t, box) {
      const W = box.clientWidth || 640, H = Math.min(360, Math.max(240, W * 0.45)), M = { l: 44, r: 12, t: 12, b: 26 };
      const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img")
        .attr("aria-label", `Adoption of ${t.label} by genre, 1988 to 2021`);
      const series = Object.keys(GENRE).map((g) => {
        const v = g === "rap" ? t.rap : t.genres[g];
        const mx = d3.max(v) || 1;
        return { g, v: explorerScale === "index" ? v.map((d) => (d / mx) * 100) : v };
      });
      const ymax = d3.max(series, (s) => d3.max(s.v)) || 1;
      const x = d3.scaleLinear([YEARS[0], YEARS[YEARS.length - 1]], [M.l, W - M.r]);
      const y = d3.scaleLinear([0, ymax * 1.05], [H - M.b, M.t]).nice();
      svg.append("g").attr("class", "grid").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - M.l - M.r)).tickFormat(""));
      svg.append("g").attr("class", "axis clean").attr("transform", `translate(${M.l},0)`)
        .call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6).tickFormat((d) => (explorerScale === "index" ? d : d + "%")));
      svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(W < 500 ? 4 : 8).tickFormat(d3.format("d")).tickSizeOuter(0));
      const line = d3.line().x((d, i) => x(YEARS[i])).y((d) => y(d)).curve(d3.curveMonotoneX);
      [...series].reverse().forEach((s) => {
        const st = GENRE[s.g];
        svg.append("path").attr("d", line(s.v)).attr("fill", "none").attr("stroke", st.color).attr("stroke-width", st.width)
          .attr("stroke-dasharray", st.dash).attr("stroke-linejoin", "round").attr("stroke-linecap", "round");
      });
      if (t.life.takeoff) {
        const tx = x(t.life.takeoff);
        svg.append("line").attr("x1", tx).attr("x2", tx).attr("y1", M.t).attr("y2", H - M.b).attr("stroke", C.blue).attr("stroke-dasharray", "2 3");
        svg.append("text").attr("x", tx + 4).attr("y", M.t + 10).attr("font-size", 11).attr("fill", C.blue).text("take-off");
      }
      const hl = svg.append("line").attr("class", "hover-line").attr("y1", M.t).attr("y2", H - M.b).style("display", "none");
      svg.append("rect").attr("x", M.l).attr("y", M.t).attr("width", W - M.l - M.r).attr("height", H - M.t - M.b).attr("fill", "transparent")
        .on("pointermove", (e) => {
          const [mx] = d3.pointer(e);
          const i = Math.max(0, Math.min(YEARS.length - 1, Math.round(x.invert(mx) - YEARS[0])));
          hl.attr("x1", x(YEARS[i])).attr("x2", x(YEARS[i])).style("display", null);
          const rows = series.map((s) => `<span class="sw" style="background:${GENRE[s.g].color}"></span>${GENRE[s.g].label}: ${explorerScale === "index" ? Math.round(s.v[i]) : fmtPct(s.v[i])}`).join("<br>");
          showTip(`<b>${YEARS[i]}</b><br>${rows}${explorerScale === "share" ? `<br><span style="opacity:.7">${t.rap_n[i]} rap artists used it</span>` : ""}`, e);
        })
        .on("pointerleave", () => { hl.style("display", "none"); hideTip(); });
    }

    function renderSide(t) {
      const side = document.getElementById("term-side");
      const p = t.people || {};
      const li = (a, right, sub) => `<li><span>${esc(a)}${sub ? `<span class="t">${esc(sub)}</span>` : ""}</span><span>${right}</span></li>`;
      let html = "";
      html += `<h4>Early adopters</h4>` + (p.early && p.early.length ? `<ul class="plist">${p.early.map((e) => li(e.artist, e.year, `“${e.title}”`)).join("")}</ul>` : `<p class="note">No clear run-up in the data.</p>`);
      html += `<h4>Popularizers at take-off</h4>` + (p.popularizers && p.popularizers.length ? `<ul class="plist">${p.popularizers.map((e) => li(e.artist, e.year, `${e.songs} song${e.songs > 1 ? "s" : ""} around take-off`)).join("")}</ul>` : `<p class="note">No widely known artists around take-off.</p>`);
      html += `<h4>Used it most</h4>` + (p.signature && p.signature.length ? `<ul class="plist">${p.signature.map((e) => li(e.artist, `${e.songs} songs`, `${Math.round(e.share * 100)}% of their rap songs`)).join("")}</ul>` : `<p class="note">No notable artist used it in five or more songs.</p>`);
      if (t.regions) {
        const rows = REGION_ORDER.filter((r) => t.regions[r]).map((r) => [r, t.regions[r].ever]);
        const mx = d3.max(rows, (d) => d[1]) || 1;
        html += `<h4>Share of each region's artists who used it</h4><ul class="wordbars">${rows.map(([r, v]) => `<li><span class="bar" style="width:${(v / mx) * 100}%;background:${REGION[r].color};opacity:.22"></span><span>${r}</span><span>${fmtPct(v)}</span></li>`).join("")}</ul>`;
      }
      if (p.first_seen) html += `<p class="note" style="margin-top:12px">First seen in this dataset: ${p.first_seen}.</p>`;
      side.innerHTML = html;
    }

    // -------------------------------------------------------------------------------
    // Crossover dumbbells
    function renderCrossover() {
      const box = document.getElementById("crossover-chart");
      const rows = terms.terms.filter((t) => !t.poly && t.life.takeoff && t.cross.pop.takeoff && t.life.takeoff >= 1992 && t.cross.pop.lag >= -3)
        .sort((a, b) => a.life.takeoff - b.life.takeoff || a.cross.pop.lag - b.cross.pop.lag);
      const lags = rows.map((t) => t.cross.pop.lag);
      const rb = terms.terms.filter((t) => !t.poly && t.cross.rb.lag !== null && t.life.takeoff >= 1992 && t.cross.rb.lag >= -3).map((t) => t.cross.rb.lag);
      const lede = document.getElementById("crossover-lede");
      const yrs = (v) => (v === 0 ? "the same year" : `${v} year${v === 1 ? "" : "s"}`);
      const mPop = d3.median(lags), mRb = d3.median(rb);
      const slow = rows.filter((t) => t.cross.pop.lag >= 8).map((t) => `<em>${esc(t.label)}</em>`);
      lede.insertAdjacentHTML("afterend", `<p class="kicker">Among ${rows.length} terms with a clear pop take-off, the median gap was <b>${yrs(mPop)}</b>. R&amp;B moved faster, with a median gap of ${yrs(mRb)} across ${rb.length} terms. Most words that reach pop reach it quickly. The exceptions sat in rap for most of a decade first: ${slow.slice(0, 6).join(", ")}.</p>`);
      const draw = () => {
        box.innerHTML = "";
        const W = box.clientWidth, rowH = 20, M = { l: 110, r: 60, t: 24, b: 10 };
        const H = M.t + M.b + rowH * rows.length;
        const x = d3.scaleLinear([1991, 2021], [M.l, W - M.r]);
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img").attr("aria-label", "Dumbbell chart of rap, R&B and pop take-off years per term");
        svg.append("g").attr("class", "axis clean").attr("transform", `translate(0,${M.t - 6})`).call(d3.axisTop(x).ticks(W < 600 ? 4 : 8).tickFormat(d3.format("d")).tickSize(0));
        svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(W < 600 ? 4 : 8)).join("line")
          .attr("x1", (d) => x(d)).attr("x2", (d) => x(d)).attr("y1", M.t).attr("y2", H - M.b).attr("stroke", C.grid);
        const g = svg.append("g").selectAll("g").data(rows).join("g").attr("transform", (d, i) => `translate(0,${M.t + i * rowH + rowH / 2})`);
        g.append("text").attr("x", M.l - 10).attr("y", 4).attr("text-anchor", "end").attr("font-size", 12).text((d) => d.label);
        g.append("line").attr("x1", (d) => x(d.life.takeoff)).attr("x2", (d) => x(d.cross.pop.takeoff)).attr("stroke", C.line).attr("stroke-width", 3);
        g.filter((d) => d.cross.rb.takeoff).append("circle").attr("cx", (d) => x(d.cross.rb.takeoff)).attr("r", 4.5).attr("fill", C.ochre).attr("stroke", C.paper).attr("stroke-width", 1.5);
        g.append("circle").attr("cx", (d) => x(d.life.takeoff)).attr("r", 5).attr("fill", C.blue).attr("stroke", C.paper).attr("stroke-width", 1.5);
        g.append("circle").attr("cx", (d) => x(d.cross.pop.takeoff)).attr("r", 5).attr("fill", C.red).attr("stroke", C.paper).attr("stroke-width", 1.5);
        g.append("text").attr("x", (d) => x(Math.max(d.cross.pop.takeoff, d.life.takeoff, d.cross.rb.takeoff || 0)) + 9).attr("y", 4).attr("font-size", 11).attr("class", "muted")
          .text((d) => (d.cross.pop.lag === 0 ? "same year" : `${d.cross.pop.lag > 0 ? "+" : ""}${d.cross.pop.lag} yr`));
        g.append("rect").attr("x", 0).attr("y", -rowH / 2).attr("width", W).attr("height", rowH).attr("fill", "transparent").style("cursor", "pointer")
          .on("pointermove", (e, d) => showTip(`<b>${esc(d.label)}</b> · ${esc(d.gloss)}<br>Rap take-off ${d.life.takeoff}<br>R&amp;B ${d.cross.rb.takeoff ?? "—"} · Pop ${d.cross.pop.takeoff}<br>Pop peak reached ${Math.round(d.cross.pop.reach * 100)}% of rap's`, e))
          .on("pointerleave", hideTip).on("click", (e, d) => { hideTip(); selectTerm(d.id, true); });
      };
      draw();
      renderers.push(draw);
    }

    function renderReach() {
      const pick = document.getElementById("reach-genre");
      ["rb", "pop", "rock", "country"].forEach((g) => {
        const b = el("button", { class: "chip", type: "button", "aria-pressed": g === reachGenre }, GENRE[g].label);
        b.addEventListener("click", () => { reachGenre = g; pick.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", x === b)); draw(); });
        pick.appendChild(b);
      });
      const box = document.getElementById("reach-chart");
      const draw = () => {
        box.innerHTML = "";
        const rows = terms.terms.filter((t) => !t.poly && t.life.peak >= 1 && t.cross[reachGenre].peak > 0)
          .sort((a, b) => b.cross[reachGenre].reach - a.cross[reachGenre].reach).slice(0, 20);
        const W = box.clientWidth, rowH = 24, M = { l: 110, r: 60, t: 4, b: 4 };
        const H = M.t + M.b + rowH * rows.length;
        const x = d3.scaleLinear([0, d3.max(rows, (d) => d.cross[reachGenre].reach) || 1], [M.l, W - M.r]);
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img").attr("aria-label", `Top terms by ${GENRE[reachGenre].label} usage relative to rap`);
        const g = svg.selectAll("g").data(rows).join("g").attr("transform", (d, i) => `translate(0,${M.t + i * rowH})`);
        g.append("text").attr("x", M.l - 10).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("font-size", 12).text((d) => d.label);
        g.append("rect").attr("x", M.l).attr("y", 4).attr("height", rowH - 8).attr("rx", 2).attr("width", (d) => Math.max(1, x(d.cross[reachGenre].reach) - M.l)).attr("fill", GENRE[reachGenre].color).attr("fill-opacity", 0.85);
        g.append("text").attr("x", (d) => x(d.cross[reachGenre].reach) + 6).attr("y", rowH / 2 + 4).attr("font-size", 11).attr("class", "muted")
          .text((d) => `${Math.round(d.cross[reachGenre].reach * 100)}%`);
        g.style("cursor", "pointer").on("click", (e, d) => selectTerm(d.id, true))
          .on("pointermove", (e, d) => showTip(`<b>${esc(d.label)}</b><br>Rap peak ${fmtPct(d.life.peak)} (${d.life.peak_year})<br>${GENRE[reachGenre].label} peak ${fmtPct(d.cross[reachGenre].peak)} (${d.cross[reachGenre].peak_year})`, e))
          .on("pointerleave", hideTip);
      };
      draw();
      renderers.push(draw);
    }

    // -------------------------------------------------------------------------------
    // Who started it
    function renderCards() {
      const ids = ["swag", "crunk", "hyphy", "bling", "izzle", "shawty", "slatt", "drip", "opps", "nocap", "percs", "thot", "skrrt", "trill", "hella", "yolo"];
      const box = document.getElementById("cards");
      box.innerHTML = "";
      ids.map((id) => T.get(id)).filter(Boolean).forEach((t) => {
        const p = t.people || {};
        const names = (arr, f) => (arr && arr.length ? arr.slice(0, 4).map(f).join(", ") : `<span class="yr">—</span>`);
        const card = el("div", { class: "card" });
        card.innerHTML = `<h3>${esc(t.label)}</h3><p class="g">${esc(t.gloss)} · took off ${t.life.takeoff ?? "—"}, peaked ${t.life.peak_year}</p>
          <div class="row"><span>Early</span><span class="who">${names(p.early, (e) => `${esc(e.artist)} <span class="yr">${e.year}</span>`)}</span></div>
          <div class="row"><span>Popularized</span><span class="who">${names(p.popularizers, (e) => `${esc(e.artist)} <span class="yr">${e.year}</span>`)}</span></div>
          <div class="row"><span>Owned</span><span class="who">${names((p.signature || []).slice(0, 3), (e) => `${esc(e.artist)} <span class="yr">${e.songs} songs</span>`)}</span></div>`;
        card.style.cursor = "pointer";
        card.addEventListener("click", () => selectTerm(t.id, true));
        box.appendChild(card);
      });
    }

    // -------------------------------------------------------------------------------
    // Turnover
    function renderCohorts() {
      const box = document.getElementById("cohort-chart");
      const cols = [C.stone, C.ochre, C.red, C.ink];
      const co = turnover.cohorts;
      document.getElementById("cohort-legend").innerHTML = co.map((c, i) => `<span><i style="border-color:${cols[i]}"></i>Peaked ${c.cohort} (${c.n})</span>`).join("");
      const lede = document.getElementById("turnover-lede");
      if (!document.getElementById("turnover-kicker")) {
        const last = co[co.length - 1];
        const fmt = (v) => Math.round(v * 100) + "%";
        lede.insertAdjacentHTML("afterend", `<p class="kicker" id="turnover-kicker">It mostly isn't. Across every era, the typical word took ${d3.min(co, (c) => c.rise_median)}–${d3.max(co, (c) => c.rise_median)} years to climb from a quarter of its peak to the top, and three years after peaking it still had ${fmt(d3.min(co, (c) => c.keep3_median))}–${fmt(d3.max(co, (c) => c.keep3_median))} of its peak usage. Words that peaked in ${last.cohort} rose a little faster (${last.rise_median} years) and held on at least as well (${fmt(last.keep3_median)}).</p>`);
      }
      const draw = () => {
        box.innerHTML = "";
        const W = box.clientWidth, H = 300, M = { l: 44, r: 16, t: 12, b: 32 };
        const offs = turnover.offsets;
        const x = d3.scaleLinear(d3.extent(offs), [M.l, W - M.r]);
        const y = d3.scaleLinear([0, 1.02], [H - M.b, M.t]);
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img").attr("aria-label", "Median normalized life-cycle curves by peak era");
        svg.append("g").attr("class", "grid").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - M.l - M.r)).tickFormat(""));
        svg.append("g").attr("class", "axis clean").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6).tickFormat(d3.format(".0%")));
        svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(8).tickFormat((d) => (d === 0 ? "peak" : d > 0 ? `+${d}` : d)).tickSizeOuter(0));
        svg.append("text").attr("x", W - M.r).attr("y", H - 2).attr("text-anchor", "end").attr("font-size", 11).attr("class", "muted").text("years from peak");
        const line = d3.line().defined((d) => d !== null).x((d, i) => x(offs[i])).y((d) => y(d)).curve(d3.curveMonotoneX);
        co.forEach((c, i) => svg.append("path").attr("d", line(c.curve)).attr("fill", "none").attr("stroke", cols[i]).attr("stroke-width", i === co.length - 1 ? 2.5 : 2));
        const hl = svg.append("line").attr("class", "hover-line").attr("y1", M.t).attr("y2", H - M.b).style("display", "none");
        svg.append("rect").attr("x", M.l).attr("y", M.t).attr("width", W - M.l - M.r).attr("height", H - M.t - M.b).attr("fill", "transparent")
          .on("pointermove", (e) => {
            const [mx] = d3.pointer(e);
            const o = Math.round(x.invert(mx)), i = offs.indexOf(o);
            if (i < 0) return;
            hl.attr("x1", x(o)).attr("x2", x(o)).style("display", null);
            showTip(`<b>${o === 0 ? "Peak year" : (o > 0 ? "+" : "") + o + " years"}</b><br>` + co.map((c, k) => `<span class="sw" style="background:${cols[k]}"></span>${c.cohort}: ${c.curve[i] === null ? "—" : Math.round(c.curve[i] * 100) + "%"}`).join("<br>"), e);
          }).on("pointerleave", () => { hl.style("display", "none"); hideTip(); });
      };
      draw();
      renderers.push(draw);
      const ex = el("p", { class: "note" }, co.map((c) => `<b>${c.cohort}:</b> ${c.examples.slice(0, 8).map(esc).join(", ")}`).join("<br>"));
      box.after(ex);
    }

    function renderChurn() {
      const box = document.getElementById("churn-chart");
      const draw = () => {
        box.innerHTML = "";
        const d = turnover.churn, W = box.clientWidth, H = 200, M = { l: 44, r: 16, t: 12, b: 28 };
        const x = d3.scaleLinear(d3.extent(d, (r) => r.year), [M.l, W - M.r]);
        const y = d3.scaleLinear([0.5, 1], [H - M.b, M.t]);
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img").attr("aria-label", "Five-year vocabulary retention by year");
        svg.append("g").attr("class", "grid").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - M.l - M.r)).tickFormat(""));
        svg.append("g").attr("class", "axis clean").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6).tickFormat(d3.format(".0%")));
        svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(8).tickFormat((v) => `${v}→${String(v + turnover.gap).slice(2)}`).tickSizeOuter(0));
        svg.append("path").attr("d", d3.line().x((r) => x(r.year)).y((r) => y(r.kept)).curve(d3.curveMonotoneX)(d)).attr("fill", "none").attr("stroke", C.blue).attr("stroke-width", 2.5);
        svg.selectAll("circle").data(d).join("circle").attr("cx", (r) => x(r.year)).attr("cy", (r) => y(r.kept)).attr("r", 8).attr("fill", "transparent")
          .on("pointermove", (e, r) => showTip(`<b>${r.year} → ${r.year + turnover.gap}</b><br>${Math.round(r.kept * 100)}% of the top ${turnover.topk} still in the top ${turnover.topk}`, e)).on("pointerleave", hideTip);
      };
      draw();
      renderers.push(draw);
    }

    // -------------------------------------------------------------------------------
    // Meaning shift
    function initMeaning() {
      const tabs = document.getElementById("meaning-tabs");
      Object.keys(meaning.targets).forEach((k) => {
        const b = el("button", { class: "chip", type: "button", role: "tab", "aria-pressed": k === meaningTarget }, k);
        b.addEventListener("click", () => { meaningTarget = k; tabs.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", x === b)); drawMeaning(); track("meaning_tab", { target: k }); });
        tabs.appendChild(b);
      });
      drawMeaning();
      renderers.push(drawMeaning);
    }

    function drawMeaning() {
      const m = meaning.targets[meaningTarget];
      const box = document.getElementById("meaning-chart");
      box.innerHTML = "";
      const legend = document.getElementById("meaning-legend");
      const sub = document.getElementById("meaning-sub");
      const title = document.getElementById("meaning-title");
      const W = box.clientWidth, H = 300, M = { l: 44, r: 16, t: 12, b: 28 };
      if (m.senses) {
        const style = SENSE_STYLE[meaningTarget];
        const senses = m.senses.filter((s) => style[s]);
        const yrs = meaning.years;
        const smooth = (arr) => arr.map((_, i) => d3.sum(arr.slice(Math.max(0, i - 1), i + 2)));
        const sm = Object.fromEntries(senses.map((s) => [s, smooth(m.by_year[s])]));
        const tot = yrs.map((_, i) => d3.sum(senses, (s) => sm[s][i]));
        const rows = yrs.map((y, i) => ({ year: y, total: tot[i], ...Object.fromEntries(senses.map((s) => [s, tot[i] ? sm[s][i] / tot[i] : 0])) })).filter((r) => r.total >= 40);
        const unclear = d3.sum(m.by_year.unclear), all = unclear + d3.sum(senses, (s) => d3.sum(m.by_year[s]));
        title.textContent = `What “${meaningTarget}” meant, share of classified uses`;
        sub.textContent = `3-year window. Years with fewer than 40 classified uses are hidden. ${Math.round((unclear / all) * 100)}% of all uses had no clear signal and are left out.`;
        legend.innerHTML = senses.map((s) => `<span><i class="sq" style="background:${style[s][0]}"></i>${style[s][1]}</span>`).join("");
        const x = d3.scaleLinear(d3.extent(rows, (r) => r.year), [M.l, W - M.r]);
        const y = d3.scaleLinear([0, 1], [H - M.b, M.t]);
        const stack = d3.stack().keys(senses)(rows);
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img").attr("aria-label", `Stacked shares of the senses of ${meaningTarget} by year`);
        svg.append("g").selectAll("path").data(stack).join("path").attr("fill", (d) => style[d.key][0]).attr("fill-opacity", 0.88).attr("stroke", C.paper).attr("stroke-width", 1)
          .attr("d", d3.area().x((d) => x(d.data.year)).y0((d) => y(d[0])).y1((d) => y(d[1])).curve(d3.curveMonotoneX));
        svg.append("g").attr("class", "axis clean").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(0).tickPadding(6).tickFormat(d3.format(".0%")));
        svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(8).tickFormat(d3.format("d")).tickSizeOuter(0));
        const hl = svg.append("line").attr("class", "hover-line").attr("y1", M.t).attr("y2", H - M.b).style("display", "none");
        svg.append("rect").attr("x", M.l).attr("y", M.t).attr("width", W - M.l - M.r).attr("height", H - M.t - M.b).attr("fill", "transparent")
          .on("pointermove", (e) => {
            const [mx] = d3.pointer(e);
            const yr = Math.round(x.invert(mx)), r = rows.find((d) => d.year === yr);
            if (!r) return;
            hl.attr("x1", x(yr)).attr("x2", x(yr)).style("display", null);
            showTip(`<b>${yr}</b> · ${d3.format(",")(r.total)} classified uses<br>` + [...senses].reverse().map((s) => `<span class="sw" style="background:${style[s][0]}"></span>${style[s][1]}: ${Math.round(r[s] * 100)}%`).join("<br>"), e);
          }).on("pointerleave", () => { hl.style("display", "none"); hideTip(); });
      } else {
        const t = T.get(meaningTarget);
        title.textContent = `Share of rap artists using “${meaningTarget}”`;
        sub.textContent = "The senses of this word are too spread out for keyword rules, so only its usage curve and its context words are shown.";
        legend.innerHTML = "";
        if (t) {
          const x = d3.scaleLinear([YEARS[0], YEARS[YEARS.length - 1]], [M.l, W - M.r]);
          const y = d3.scaleLinear([0, d3.max(t.rap) * 1.05], [H - M.b, M.t]).nice();
          const svg = d3.select(box).append("svg").attr("width", W).attr("height", H);
          svg.append("g").attr("class", "grid").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - M.l - M.r)).tickFormat(""));
          svg.append("g").attr("class", "axis clean").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6).tickFormat((d) => d + "%"));
          svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(8).tickFormat(d3.format("d")).tickSizeOuter(0));
          svg.append("path").attr("d", d3.line().x((d, i) => x(YEARS[i])).y((d) => y(d)).curve(d3.curveMonotoneX)(t.rap)).attr("fill", "none").attr("stroke", C.blue).attr("stroke-width", 2.5);
        }
      }
      const col = document.getElementById("colloc");
      col.innerHTML = meaning.eras.map((era) => `<div><h5>${era}</h5><ol>${(m.collocates[era] || []).slice(0, 10).map((c) => `<li><span>${esc(c.w)}</span><span>${d3.format(",")(c.n)}</span></li>`).join("") || `<li><span class="note">too few uses</span></li>`}</ol></div>`).join("");
    }

    // -------------------------------------------------------------------------------
    // Regions
    function initRegions() {
      regionEra = regions.eras[regions.eras.length - 1];
      const pick = document.getElementById("region-era");
      regions.eras.forEach((e) => {
        const b = el("button", { class: "chip", type: "button", "aria-pressed": e === regionEra }, e);
        b.addEventListener("click", () => { regionEra = e; pick.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", x === b)); drawRegionCols(); });
        pick.appendChild(b);
      });
      drawRegionCols();
      initDiffusion();
    }

    function drawRegionCols() {
      const box = document.getElementById("region-cols");
      const dist = regions.distinctive[regionEra];
      box.innerHTML = regions.regions.filter((r) => dist[r.id] && dist[r.id].length).map((r) => {
        const words = dist[r.id].slice(0, 12);
        const mx = d3.max(words, (w) => w.share) || 1;
        const places = ((regions.places || {})[regionEra] || {})[r.id] || [];
        return `<div class="region-col"><h4>${r.id}</h4><p class="n">${d3.format(",")(r.artists_by_era[regionEra])} artists</p><ul class="wordbars">${words.map((w) => `<li title="${esc(w.w)}: ${fmtPct(w.share)} of ${r.id} artists vs ${fmtPct(w.rest)} elsewhere"><span class="bar" style="width:${(w.share / mx) * 100}%;background:${(REGION[r.id] || {}).color || C.blue};opacity:.2"></span><span>${esc(w.w)}</span><span>${(w.share / Math.max(w.rest, 0.01)).toFixed(1)}×</span></li>`).join("")}</ul>${places.length ? `<p class="note" style="margin-top:8px">Shout-outs: ${places.slice(0, 6).map((p) => esc(p.w)).join(", ")}</p>` : ""}</div>`;
      }).join("");
    }

    function initDiffusion() {
      const pick = document.getElementById("diffusion-pick");
      const list = regions.diffusion.filter((d) => T.has(d.id) && T.get(d.id).regions);
      if (!list.length) return;
      let cur = list[0].id;
      const sel = el("select", { class: "search", "aria-label": "Regional term" });
      sel.innerHTML = list.map((d) => `<option value="${d.id}">${esc(T.get(d.id).label)} — ${esc(d.note)}</option>`).join("");
      sel.addEventListener("change", () => { cur = sel.value; draw(); track("diffusion_pick", { term: cur }); });
      pick.appendChild(sel);
      const box = document.getElementById("diffusion-chart");
      const draw = () => {
        box.innerHTML = "";
        const t = T.get(cur);
        const W = box.clientWidth, H = 300, M = { l: 44, r: 70, t: 12, b: 28 };
        const regs = REGION_ORDER.filter((r) => t.regions[r] && t.regions[r].artists >= 200);
        const x = d3.scaleLinear([YEARS[0], YEARS[YEARS.length - 1]], [M.l, W - M.r]);
        const y = d3.scaleLinear([0, (d3.max(regs, (r) => d3.max(t.regions[r].curve)) || 1) * 1.05], [H - M.b, M.t]).nice();
        const svg = d3.select(box).append("svg").attr("width", W).attr("height", H).attr("role", "img").attr("aria-label", `Regional adoption of ${t.label}`);
        svg.append("g").attr("class", "grid").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - M.l - M.r)).tickFormat(""));
        svg.append("g").attr("class", "axis clean").attr("transform", `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6).tickFormat((d) => d + "%"));
        svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(8).tickFormat(d3.format("d")).tickSizeOuter(0));
        const line = d3.line().x((d, i) => x(YEARS[i])).y((d) => y(d)).curve(d3.curveMonotoneX);
        const ends = [];
        regs.forEach((r) => {
          const v = t.regions[r].curve;
          svg.append("path").attr("d", line(v)).attr("fill", "none").attr("stroke", REGION[r].color).attr("stroke-width", 2).attr("stroke-dasharray", REGION[r].dash);
          ends.push({ r, y: y(v[v.length - 1]) });
        });
        ends.sort((a, b) => a.y - b.y);
        for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;
        svg.append("g").selectAll("text").data(ends).join("text").attr("x", W - M.r + 6).attr("y", (d) => d.y + 4).attr("font-size", 11).text((d) => d.r);
        const hl = svg.append("line").attr("class", "hover-line").attr("y1", M.t).attr("y2", H - M.b).style("display", "none");
        svg.append("rect").attr("x", M.l).attr("y", M.t).attr("width", W - M.l - M.r).attr("height", H - M.t - M.b).attr("fill", "transparent")
          .on("pointermove", (e) => {
            const [mx] = d3.pointer(e);
            const i = Math.max(0, Math.min(YEARS.length - 1, Math.round(x.invert(mx) - YEARS[0])));
            hl.attr("x1", x(YEARS[i])).attr("x2", x(YEARS[i])).style("display", null);
            showTip(`<b>${YEARS[i]}</b><br>` + regs.map((r) => `<span class="sw" style="background:${REGION[r].color}"></span>${r}: ${fmtPct(t.regions[r].curve[i])}`).join("<br>"), e);
          }).on("pointerleave", () => { hl.style("display", "none"); hideTip(); });
      };
      draw();
      renderers.push(draw);
    }
  });
})();
