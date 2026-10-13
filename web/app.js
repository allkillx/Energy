/* NEM Outage Monitor front end. Plain JS + Leaflet; all data comes from /api/state. */
(function () {
  "use strict";

  const REGIONS = ["NSW1", "QLD1", "VIC1", "SA1", "TAS1", "Unknown"];
  const FOCUS = ["NSW1", "QLD1", "VIC1"];
  const PHASES = { active: "Active", upcoming: "Upcoming", cancelled: "Cancelled/withdrawn", ended: "Ended", unknown: "Time unknown" };
  const CHANGE_KINDS = { added: "Added", removed: "Removed", time: "Time change", status: "Status change", constraint_sets: "Constraint set change", equipment: "Equipment change" };
  const TIME_FIELDS = { start: "Planned start", end: "Planned end", actual_start: "Actual start", actual_end: "Actual end" };

  const store = {
    get(k, d) { try { const v = localStorage.getItem("nos." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("nos." + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
  };

  const state = {
    data: null,
    selected: null,
    filters: Object.assign({
      regions: FOCUS.slice(), phases: ["active", "upcoming"], window: 168,
      attention: false, conset: false, unmapped: false, search: "",
    }, store.get("filters", {})),
    changeKinds: store.get("changeKinds", Object.keys(CHANGE_KINDS)),
    interval: store.get("interval", 300),
    nextCheckAt: null,
    timer: null,
  };

  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid instanceof Node ? kid : String(kid));
    return n;
  };

  // ---- time helpers: server sends NEM ISO strings (+10:00); show them as NEM wall time ----
  const fmt = (iso, withYear) => {
    if (!iso) return "—";
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
    if (!m) return iso;
    return (withYear ? m[1] + "-" : "") + m[2] + "-" + m[3] + " " + m[4] + ":" + m[5];
  };
  const ageText = (iso) => {
    if (!iso) return "";
    const min = (Date.now() - new Date(iso).getTime()) / 60000;
    if (isNaN(min)) return "";
    if (min < 0) return "(ahead of local clock)";
    if (min < 1) return "(<1 min ago)";
    if (min < 90) return `(${Math.round(min)} min ago)`;
    if (min < 48 * 60) return `(${(min / 60).toFixed(1)} h ago)`;
    return `(${Math.round(min / 1440)} d ago)`;
  };

  const toast = (msg) => {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 3500);
  };

  // ---- data loading ----
  async function load(refresh, force) {
    const btn = $("#btn-refresh");
    if (refresh) btn.disabled = true;
    try {
      const res = await fetch(refresh ? `/api/refresh${force ? "?force=1" : ""}` : "/api/state", { method: refresh ? "POST" : "GET" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
      if (refresh && state.data.refresh_result) reportRefresh(state.data.refresh_result);
      render();
    } catch (e) {
      toast("Cannot reach local server: " + e.message + ". Keeping the last loaded data.");
      $("#st-badge").className = "badge err"; $("#st-badge").textContent = "Local server unavailable";
    } finally {
      btn.disabled = false;
      scheduleNext();
    }
  }

  function reportRefresh(r) {
    if (r.skipped) return toast("Skipped: " + r.skipped);
    const n = r.network;
    if (!n) return toast("Offline mode: upstream is not contacted.");
    if (!n.ok) toast((state.data.source ? "Fetch failed, keeping last good data: " : "Fetch failed, no data yet: ") + n.error);
    else if (n.new) toast(`Fetched new file ${n.file}: ${n.records} outages, ${n.changes} changes`);
    else toast("No new upstream file (latest: " + n.file + ")");
  }

  function scheduleNext() {
    clearTimeout(state.timer);
    state.nextCheckAt = null;
    if (state.interval > 0) {
      state.nextCheckAt = Date.now() + state.interval * 1000;
      state.timer = setTimeout(() => load(true, false), state.interval * 1000);
    }
    renderCountdown();
  }

  function renderCountdown() {
    const n = $("#next-check");
    if (!state.nextCheckAt) { n.textContent = "Auto check off"; return; }
    const s = Math.max(0, Math.round((state.nextCheckAt - Date.now()) / 1000));
    n.textContent = `Next check in ~${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  // ---- filtering ----
  function regionOf(o) { return o.regions.length ? o.regions : ["Unknown"]; }

  function filtered() {
    const f = state.filters, d = state.data;
    if (!d) return [];
    const now = Date.now();
    const horizon = f.window > 0 ? now + f.window * 3600e3 : Infinity;
    const q = f.search.trim().toLowerCase();
    return d.outages.filter((o) => {
      if (!regionOf(o).some((r) => f.regions.includes(r))) return false;
      if (!f.phases.includes(o.phase)) return false;
      if (o.phase === "upcoming" && o.start && new Date(o.start).getTime() > horizon) return false;
      if (f.attention && !o.attention) return false;
      if (f.conset && !o.constraint_sets.length) return false;
      if (f.unmapped && o.mapped) return false;
      if (q) {
        const hay = [o.outage_id, o.reason, o.status_code, ...o.constraint_sets.map((c) => c.id),
          ...o.equipment.flatMap((e) => [e.substation_id, e.substation_name, e.equipment_id, e.equipment_desc, e.equipment_type])].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort((a, b) => {
      const rank = (o) => (o.phase === "active" ? 0 : o.phase === "upcoming" ? 1 : 2);
      return rank(a) - rank(b) || (b.attention - a.attention) || String(a.start).localeCompare(String(b.start));
    });
  }

  function saveFilters() { store.set("filters", state.filters); }

  // ---- render ----
  function render() {
    const d = state.data;
    $("#demo-banner").hidden = !d.demo;
    renderStatus(d);
    const rows = filtered();
    renderKpis(rows);
    renderTable(rows);
    renderMap(rows);
    renderSchematic(rows);
    renderChanges();
    renderMeta(d);
    if (state.selected && !d.outages.some((o) => o.outage_id === state.selected)) state.selected = null;
    renderDetail();
  }

  function renderStatus(d) {
    const s = d.source;
    const badge = $("#st-badge");
    if (!s) { badge.className = "badge err"; badge.textContent = "No data"; }
    else if (d.last_error) { badge.className = "badge err"; badge.textContent = "Fetch failed · showing old data"; }
    else if (d.stale) { badge.className = "badge stale"; badge.textContent = "Stale / not live"; }
    else { badge.className = "badge ok"; badge.textContent = "OK"; }

    const file = $("#st-file");
    file.replaceChildren(s ? (s.url && s.kind === "nemweb" ? el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.file) : s.file) : "—");
    const info = s && s.info;
    if (!renderStatus.aemoNote) renderStatus.aemoNote = $("#st-note").textContent;
    $("#st-note").textContent = info && info.provider === "NEOpoint"
      ? "NEOpoint gives no publish time for this report and its update frequency has not been observed yet. \"Auto check\" is only how often this page asks NEOpoint; unchanged data is not stored twice. All times are shown as NEM time (AEST, UTC+10); NEOpoint does not state a time zone."
      : renderStatus.aemoNote;
    if (info && info.provider === "NEOpoint") {
      $("#st-upstream-label").textContent = "Source data time";
      $("#st-upstream").textContent = `Latest submitteddate ${fmt(info.latest_submitted, true)} ${ageText(info.latest_submitted)}`;
      $("#st-upstream").title = "NEOpoint gives no publish time for this report; the newest submission date in the data is shown instead.";
    } else {
      $("#st-upstream-label").textContent = "Upstream file published";
      $("#st-upstream").textContent = s ? `${fmt(s.upstream_time, true)} ${ageText(s.upstream_time)}` : "—";
      $("#st-upstream").title = s ? `File name time: ${s.name_time || "—"}\nDirectory listing time: ${s.listing_time || "—"}\nCSV report time: ${s.report_time || "—"}` : "";
    }
    $("#st-fetched").textContent = s ? `${fmt(s.fetched_at, true)} ${ageText(s.fetched_at)}` : "—";
    const lc = d.last_check, ok = d.last_success_check;
    $("#st-check").textContent = lc ? `${fmt(lc.at)} ${lc.ok ? "OK" : "failed"}${!lc.ok && ok ? `; last OK ${fmt(ok.at)}` : ""}` : (d.config.auto_fetch ? "Not checked yet" : "Offline mode (no automatic fetch)");
    const m = d.market;
    $("#st-market").textContent = m ? `${m.source_file} · interval ${fmt(m.settlement_date && m.settlement_date.replace(/\//g, "-").replace(" ", "T"))}${m.stale ? " · stale" : ""}` : (s && s.info && s.info.provider === "NEOpoint" ? "Not available · NEOpoint constraint/interconnector/price feeds not mapped yet" : "None · market observation cannot be confirmed");

    const err = $("#st-error");
    err.hidden = !d.last_error;
    const ei = d.last_error_info;
    err.replaceChildren(...(d.last_error ? [
      el("strong", null, "Latest fetch failed"), ei ? ` at ${fmt(ei.at, true)}` : "", el("br"),
      ei && ei.url ? el("span", null, "URL: ", el("span", { class: "mono" }, ei.url), el("br")) : "",
      ei ? `Stage: ${ei.stage || "—"} · HTTP status: ${ei.http_status || "none"}` : "", el("br"),
      el("span", { class: "small" }, d.last_error),
      s ? el("div", { class: "small" }, `Showing the last successful data (${s.file}), not live data.`)
        : el("div", { class: "small" }, "No successful data yet. Nothing is shown rather than demo data."),
    ] : []));
    const st = $("#st-stale");
    st.hidden = !d.stale_reasons.length;
    st.textContent = d.stale_reasons.join("; ");
  }

  function renderKpis(rows) {
    const d = state.data;
    const inRegion = d.outages.filter((o) => regionOf(o).some((r) => state.filters.regions.includes(r)));
    const soon = Date.now() + 24 * 3600e3;
    const k = [
      ["Active", inRegion.filter((o) => o.phase === "active").length, () => setPhases(["active"])],
      ["Starting within 24 h", inRegion.filter((o) => o.phase === "upcoming" && o.start && new Date(o.start) <= soon).length, () => { setPhases(["upcoming"]); state.filters.window = 24; }],
      ["Needs attention", inRegion.filter((o) => o.attention).length, () => { state.filters.attention = true; }],
      ["Location unmapped", inRegion.filter((o) => !o.mapped && (o.phase === "active" || o.phase === "upcoming")).length, () => { state.filters.unmapped = true; }],
      ["Changes in latest snapshot", d.changes.filter((c) => d.source && c.snapshot_id === d.source.snapshot_id).length, () => $(".changes-panel").scrollIntoView({ behavior: "smooth" })],
    ];
    $("#kpis").replaceChildren(...k.map(([l, n, fn]) => el("button", { class: "kpi", type: "button", onclick: () => { fn(); saveFilters(); syncFilterUi(); render(); } },
      el("div", { class: "n" }, n), el("div", { class: "l" }, l + " (selected regions)"))));
  }
  function setPhases(p) { state.filters.phases = p; }

  function equipmentText(o) {
    const e = o.equipment[0];
    if (!e) return "—";
    const more = o.equipment.length > 1 ? ` +${o.equipment.length - 1}` : "";
    return `${e.equipment_type_label || e.equipment_type} ${e.equipment_id}${more}`;
  }

  // Fields the current source does not carry (e.g. NEOpoint NOS report), keyed as in source.info.not_provided.
  const notProvided = () => (state.data && state.data.source && state.data.source.info && state.data.source.info.not_provided) || {};
  const NP = "Not provided by source";
  const orNP = (key, value) => (key in notProvided() ? el("span", { class: "muted" }, NP) : value);
  const statusMismatch = (o) => o.phase === "active" && /COMPLET/i.test(o.status_code + " " + o.status_desc);

  function tags(o) {
    const t = [];
    if (o.attention) t.push(el("span", { class: "tag attention", title: o.attention_reasons.join(", ") }, "Attention"));
    if (o.phase === "active") t.push(el("span", { class: "tag active" }, o.phase_basis === "actual" ? "Actually started" : "In planned window"));
    if (!o.mapped) t.push(el("span", { class: "tag unmapped" }, "Location unmapped"));
    if (o.is_secondary === "1") t.push(el("span", { class: "tag" }, "secondary"));
    if (o.interconnectors.length) t.push(el("span", { class: "tag" }, o.interconnectors.join(" ")));
    if (statusMismatch(o)) t.push(el("span", { class: "tag attention", title: "Status says completed but the planned end is still in the future. Shown as reported; not interpreted." }, "Status/time mismatch"));
    return t;
  }

  function renderTable(rows) {
    const tb = $("#outage-table tbody");
    tb.replaceChildren(...rows.map((o) => el("tr", {
      class: o.outage_id === state.selected ? "sel" : null, "data-id": o.outage_id,
      onclick: () => select(o.outage_id, true),
    },
      el("td", null, el("span", { class: `dot ${o.phase}${o.attention ? " attention" : ""}`, title: PHASES[o.phase] })),
      el("td", { class: "mono" }, o.outage_id),
      el("td", null, regionOf(o).join(" ")),
      el("td", { title: o.equipment.map((e) => e.substation_name).filter(Boolean).join("; ") }, o.substations.join(", ") || "—"),
      el("td", { title: o.equipment.map((e) => `${e.equipment_type} ${e.equipment_id} ${e.equipment_desc}`).join("\n") }, equipmentText(o)),
      el("td", { title: o.status_desc }, o.status_code || "—"),
      el("td", null, fmt(o.actual_start || o.start)),
      el("td", null, fmt(o.actual_end || o.end)),
      el("td", null, o.constraint_sets.length || ""),
      el("td", null, tags(o)),
    )));
    $("#list-empty").hidden = rows.length > 0;
    $("#list-count").textContent = `${rows.length} / ${state.data.outages.length}`;
  }

  // ---- map ----
  let map, markerLayer, lineLayer, markersBySub = {};
  function initMap() {
    if (typeof L === "undefined") { $("#map").textContent = "Map library failed to load."; return; }
    map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([-32.5, 146], 4);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 12, attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    lineLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    const legend = L.control({ position: "bottomleft" });
    legend.onAdd = () => {
      const div = L.DomUtil.create("div", "legend");
      div.innerHTML = '<div><i style="background:var(--active)"></i>Active</div>' +
        '<div><i style="background:var(--planned)"></i>Planned (upcoming)</div>' +
        '<div><i style="background:transparent;box-shadow:0 0 0 3px var(--attention)"></i>Needs attention</div>' +
        '<div><i style="background:var(--unmapped)"></i>Location unmapped: <span id="legend-unmapped">0</span> (not plotted)</div>';
      return div;
    };
    legend.addTo(map);
  }

  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  function colorFor(o) { return o.phase === "active" ? cssVar("--active") : o.phase === "upcoming" ? cssVar("--planned") : cssVar("--ended"); }

  function renderMap(rows) {
    if (!map) return;
    markerLayer.clearLayers(); lineLayer.clearLayers(); markersBySub = {};
    const bySub = {};
    let unmapped = 0;
    for (const o of rows) {
      if (!o.mapped) { unmapped++; continue; }
      for (const p of o.map_points) (bySub[p.substation_id] = bySub[p.substation_id] || { p, list: [] }).list.push(o);
      for (const f of o.map_lines) {
        L.geoJSON(f, { style: { color: colorFor(o), weight: o.outage_id === state.selected ? 6 : 4, opacity: 0.85 } })
          .on("click", () => select(o.outage_id, false)).bindTooltip(`${o.outage_id} · ${f.properties.name || f.properties.equipment_id}<br>Source: ${escapeHtml(f.properties.source)}`)
          .addTo(lineLayer);
      }
    }
    for (const [sid, { p, list }] of Object.entries(bySub)) {
      const worst = list.find((o) => o.phase === "active") || list[0];
      const attention = list.some((o) => o.attention);
      const sel = list.some((o) => o.outage_id === state.selected);
      const m = L.circleMarker([p.lat, p.lon], {
        radius: sel ? 10 : 7, color: attention ? cssVar("--attention") : colorFor(worst), weight: attention ? 4 : 2,
        fillColor: colorFor(worst), fillOpacity: 0.85,
      });
      const html = `<strong>${escapeHtml(sid)}</strong> ${escapeHtml(p.name || "")}<br><span class="muted">Coordinate source: ${escapeHtml(p.source)}</span><br>` +
        list.map((o) => `<a data-oid="${escapeHtml(o.outage_id)}">${escapeHtml(o.outage_id)}</a> ${PHASES[o.phase]} ${fmt(o.start)}–${fmt(o.end)}`).join("<br>");
      m.bindPopup(html);
      m.on("click", () => { if (list.length === 1) select(list[0].outage_id, false); });
      m.on("popupopen", (ev) => ev.popup.getElement().querySelectorAll("a[data-oid]").forEach((a) =>
        a.addEventListener("click", () => select(a.dataset.oid, false))));
      m.addTo(markerLayer);
      markersBySub[sid] = m;
    }
    const lu = document.getElementById("legend-unmapped");
    if (lu) lu.textContent = unmapped;
    const locs = state.data.locations;
    $("#map-note").textContent = `Mapped substations ${locs.substations} · lines ${locs.lines}` + (locs.substations === 0 ? " · coordinate table is empty, see README" : "");
  }

  function focusOnMap(o) {
    if (!map) return;
    if (!o.mapped) { toast(`${o.outage_id}: location unmapped (no coordinates for ${o.substations.join(", ") || "this substation"})`); return; }
    const pts = o.map_points.map((p) => [p.lat, p.lon]);
    o.map_lines.forEach((f) => L.geoJSON(f).getBounds().isValid() && pts.push(...[L.geoJSON(f).getBounds().getSouthWest(), L.geoJSON(f).getBounds().getNorthEast()]));
    if (pts.length === 1) map.flyTo(pts[0], Math.max(map.getZoom(), 7));
    else map.flyToBounds(L.latLngBounds(pts).pad(0.4), { maxZoom: 9 });
    const first = o.map_points[0] && markersBySub[o.map_points[0].substation_id];
    if (first) setTimeout(() => first.openPopup(), 400);
  }

  // ---- schematic (non-geographic) ----
  const BOX = { QLD1: [250, 10], NSW1: [250, 110], VIC1: [250, 210], SA1: [10, 210], TAS1: [250, 320] };
  const ICS = [
    ["NSW1-QLD1", "NSW1", "QLD1", -28], ["N-Q-MNSP1", "NSW1", "QLD1", 28],
    ["VIC1-NSW1", "VIC1", "NSW1", 0], ["V-SA", "VIC1", "SA1", -12], ["V-S-MNSP1", "VIC1", "SA1", 12], ["T-V-MNSP1", "TAS1", "VIC1", 0],
  ];
  function renderSchematic(rows) {
    const NS = "http://www.w3.org/2000/svg";
    const s = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text !== undefined) n.textContent = text; return n; };
    const svg = s("svg", { viewBox: "0 0 470 380", role: "img", "aria-label": "NEM regions and interconnectors schematic" });
    const counts = {};
    for (const o of rows) if (o.phase === "active" || o.phase === "upcoming") regionOf(o).forEach((r) => { counts[r] = (counts[r] || 0) + 1; });
    const sel = state.data.outages.find((o) => o.outage_id === state.selected);
    const flows = (state.data.market && state.data.market.interconnectors) || {};
    const center = (r) => [BOX[r][0] + 60, BOX[r][1] + 25];
    for (const [id, a, b, off] of ICS) {
      const [x1, y1] = center(a), [x2, y2] = center(b);
      const horiz = Math.abs(x1 - x2) > Math.abs(y1 - y2);
      const dx = horiz ? 0 : off, dy = horiz ? off : 0;
      const cls = sel && sel.interconnectors.includes(id) ? "ic mapped" : sel && sel.adjacent_interconnectors.includes(id) ? "ic adjacent" : "ic";
      const g = s("g", { class: cls });
      g.append(s("line", { x1: x1 + dx, y1: y1 + dy, x2: x2 + dx, y2: y2 + dy }));
      const f = flows[id];
      const label = `${id}${f && f.mwflow !== null ? ` ${Math.round(f.mwflow)}MW` : ""}`;
      const mx = (x1 + x2) / 2 + dx, my = (y1 + y2) / 2 + dy;
      // Label on the outer side of the line so parallel interconnectors don't collide.
      const lx = horiz ? mx : mx + (off < 0 ? -6 : 6);
      const ly = horiz ? my + (off < 0 ? -5 : 13) : my + 4;
      g.append(s("text", { x: lx, y: ly, "text-anchor": horiz ? "middle" : off < 0 ? "end" : "start" }, label));
      g.append(s("title", {}, `${id}: ${a}→${b}` + (f ? `\nFlow ${f.mwflow} MW, limits ${f.import_limit} to ${f.export_limit}` : "\nNo DispatchIS data")));
      svg.append(g);
    }
    for (const [r, [x, y]] of Object.entries(BOX)) {
      const n = counts[r] || 0;
      const shade = n === 0 ? 0 : n < 3 ? 1 : n < 8 ? 2 : 3;
      const g = s("g", { class: "region" });
      g.append(s("rect", { x, y, width: 120, height: 50, fill: `var(--region-${shade})`, stroke: sel && sel.regions.includes(r) ? cssVar("--accent") : "", "stroke-width": sel && sel.regions.includes(r) ? 3 : 1 }));
      g.append(s("text", { x: x + 8, y: y + 20, "font-weight": 600 }, r));
      const price = state.data.market && state.data.market.prices ? state.data.market.prices[r] : null;
      g.append(s("text", { x: x + 8, y: y + 38 }, `${n} outages${price !== null && price !== undefined ? ` · $${price.toFixed(0)}` : ""}`));
      svg.append(g);
    }
    $("#schematic").replaceChildren(svg);
  }

  // ---- detail ----
  function select(id, fromList) {
    state.selected = id;
    try { history.replaceState(null, "", "#outage=" + encodeURIComponent(id)); } catch (e) { /* ignore */ }
    const rows = filtered();
    renderTable(rows); renderMap(rows); renderSchematic(rows); renderDetail();
    const o = state.data.outages.find((x) => x.outage_id === id);
    if (o && fromList) focusOnMap(o);
    if (!fromList) {
      const tr = document.querySelector(`#outage-table tr[data-id="${CSS.escape(id)}"]`);
      if (tr) tr.scrollIntoView({ block: "nearest" });
      $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  const kv = (pairs) => el("dl", { class: "kv" }, pairs.flatMap(([k, v]) => [el("dt", null, k), el("dd", null, v === "" || v === null || v === undefined ? "—" : v)]));

  function renderDetail() {
    const body = $("#detail-body");
    const o = state.selected && state.data && state.data.outages.find((x) => x.outage_id === state.selected);
    $("#btn-close-detail").hidden = !o;
    if (!o) { body.className = "muted"; body.replaceChildren("Select an outage in the list or on the map."); return; }
    body.className = "";
    const nos = el("div", { class: "card" },
      el("h3", null, "1. NOS record"), el("p", { class: "hint" }, "Fields supplied directly by the source (" + (state.data.source ? state.data.source.file : "") + ")"),
      kv([
        ["Outage ID", el("span", { class: "mono" }, o.outage_id)],
        ["Status", `${o.status_code}${o.status_desc ? " — " + o.status_desc : ""}`],
        ["Page category", `${PHASES[o.phase]} (based on ${o.phase_basis === "actual" ? "actual times" : o.phase_basis === "status" ? "status code" : "planned times"})`],
        ["Regions", regionOf(o).join(", ")],
        ["Planned start / end", `${fmt(o.start, true)} → ${fmt(o.end, true)}`],
        ["Actual start / end", orNP("actual_start", `${fmt(o.actual_start, true)} → ${fmt(o.actual_end, true)}`)],
        ["Reason", orNP("reason", o.reason)],
        ["Secondary", orNP("is_secondary", o.is_secondary === "1" ? "Yes" : o.is_secondary === "0" ? "No" : o.is_secondary)],
        ["Recall day / night", `${o.recall_time_day || "—"} / ${o.recall_time_night || "—"}${state.data.source && state.data.source.info ? " (minutes, as reported)" : ""}`],
        ["Resubmission", orNP("resubmit_reason", [o.resubmit_reason, o.resubmit_outage_id].filter(Boolean).join(" · "))],
        ["Submitted", fmt(o.submitted, true)], ["Last changed", orNP("last_changed", fmt(o.last_changed, true))], ["Company ref", o.company_ref],
        ["Planned/unplanned", orNP("outage_type", "—")],
        ["Needs attention", o.attention ? o.attention_reasons.join(", ") : "No"],
      ]),
      el("h3", { style: "margin-top:8px" }, "Equipment"),
      el("table", null, el("thead", null, el("tr", null, ["Substation", "Name", "Region", "Type", "Equipment", "Description", "Voltage", "Location"].map((h) => el("th", null, h)))),
        el("tbody", null, o.equipment.map((e) => el("tr", null,
          el("td", { class: "mono" }, e.substation_id), el("td", null, e.substation_name || "—"), el("td", null, e.region || "—"),
          el("td", null, `${e.equipment_type_label}${e.equipment_type_label !== e.equipment_type ? ` (${e.equipment_type})` : ""}`),
          el("td", { class: "mono" }, e.equipment_id), el("td", null, e.equipment_desc || "—"), el("td", null, e.voltage ? e.voltage + " kV" : orNP("voltage", "—")),
          el("td", null, e.location ? el("span", { title: e.location.source }, `${e.location.lat.toFixed(3)}, ${e.location.lon.toFixed(3)}`) : el("span", { class: "tag unmapped" }, "Location unmapped")),
        )))),
      el("details", null, el("summary", null, "Raw source rows"), el("pre", { class: "raw" }, JSON.stringify(o.raw, null, 1))),
    );

    const cons = el("div", { class: "card" },
      el("h3", null, "2. Expected constraints"), el("p", { class: "hint" }, "Constraint sets linked to this outage in NETWORK_OUTAGECONSTRAINTSET, i.e. the sets AEMO expects to invoke during the outage."),
      o.constraint_sets.length ? (() => {
        const srcTimes = o.constraint_sets.some((c) => "source_actual_start" in c);
        const head = ["Constraint set ID", "Description", ...(srcTimes ? ["Source actual_start*", "Source actual_end*"] : []), "Start interval", "End interval"];
        return el("div", null, el("table", null, el("thead", null, el("tr", null, head.map((h) => el("th", null, h)))),
          el("tbody", null, o.constraint_sets.map((c) => el("tr", null, el("td", { class: "mono" }, c.id), el("td", null, c.description || "—"),
            ...(srcTimes ? [el("td", null, fmt(c.source_actual_start, true)), el("td", null, fmt(c.source_actual_end, true))] : []),
            el("td", null, orNP("constraint_set_interval", fmt(c.start_interval, true))), el("td", null, orNP("constraint_set_interval", fmt(c.end_interval, true))))))),
          srcTimes ? el("p", { class: "hint" }, "* NEOpoint's actual_starttime/actual_endtime differ between constraint-set rows of one outage, so they are shown per set. Their exact meaning is unconfirmed; they are not used as outage actual times.") : null);
      })()
        : el("p", { class: "muted" }, "No constraint set is linked in the data."),
      el("p", { class: "hint" }, `Interconnector mapping: ${o.interconnectors.length ? o.interconnectors.join(", ") : "none (substation not in interconnector_substations.csv)"}`),
      el("p", { class: "hint" }, `Interconnectors adjacent to the region (geographic adjacency only, not a confirmed impact): ${o.adjacent_interconnectors.join(", ") || "—"}`),
    );

    const mk = o.market;
    const market = el("div", { class: "card" }, el("h3", null, "3. Market observation (concurrent, not causal)"));
    if (!mk.available) market.append(el("p", { class: "warn" }, state.data.source && state.data.source.info && state.data.source.info.provider === "NEOpoint"
      ? "Not available: the NEOpoint Constraints and sets, Interconnectors and Local Price feeds have no confirmed field mapping yet (no real sample), so binding status, flows and prices cannot be shown."
      : mk.note));
    else {
      market.append(el("p", { class: "hint" }, `Source ${mk.source_file} · dispatch interval ${mk.settlement_date || "—"}`));
      market.append(el("h3", null, "Binding status of linked constraints"));
      if (!mk.constraint_sets.length) market.append(el("p", { class: "muted" }, "No linked constraint sets."));
      market.append(...mk.constraint_sets.map((c) => el("div", null,
        el("span", { class: "mono" }, c.id), ": ",
        c.status === "unknown" ? el("span", { class: "tag unmapped" }, "Cannot be confirmed") : c.status === "binding"
          ? el("span", null, el("span", { class: "tag attention" }, "Member constraint binding"), " ", c.binding.map((b) => `${b.constraint_id} (MV ${b.marginal_value})`).join(", "))
          : el("span", { class: "tag" }, `No member constraint binding (${c.members} members)`),
        c.note ? el("div", { class: "hint" }, c.note) : null)));
      const prices = Object.entries(mk.prices).filter(([, v]) => v !== null && v !== undefined);
      market.append(el("h3", { style: "margin-top:6px" }, "Regional prices and spreads"),
        kv([...prices.map(([r, v]) => [r + " RRP", `$${v.toFixed(2)}/MWh`]),
          ...mk.spreads.map((s) => [s.pair + " spread", `$${s.spread.toFixed(2)}`])]));
      const ics = Object.entries(mk.interconnectors).filter(([, v]) => v);
      market.append(el("h3", { style: "margin-top:6px" }, "Adjacent interconnector flows"),
        ics.length ? kv(ics.map(([id, f]) => [id, `${f.mwflow} MW (limits ${f.import_limit} to ${f.export_limit}${f.marginal_value ? `, MV ${f.marginal_value}` : ""})`])) : el("p", { class: "muted" }, "No data"));
      market.append(el("p", { class: "hint" }, mk.note));
    }

    const changes = state.data.changes.filter((c) => c.outage_id === o.outage_id);
    const hist = el("div", { class: "card" }, el("h3", null, "Change history for this outage"),
      changes.length ? changes.map(changeLine) : el("p", { class: "muted" }, "No changes recorded in saved snapshots."));

    body.replaceChildren(el("div", { class: "detail-grid" }, nos, cons, market, hist));
  }

  // ---- changes ----
  function changeDetail(c) {
    return c.details.map((d) => {
      if (d.field in TIME_FIELDS) return `${TIME_FIELDS[d.field]} ${fmt(d.old)} → ${fmt(d.new)}`;
      if (d.field === "constraint_set") return d.new ? `+${d.new}` : `−${d.old}`;
      if (d.field === "equipment") return d.new ? `+${d.new}` : `−${d.old}`;
      return `${d.old ?? "—"} → ${d.new ?? "—"}`;
    }).join("; ");
  }
  function changeLine(c) {
    return el("div", { class: "chg" },
      el("span", { class: "kind " + c.kind }, c.label),
      el("a", { class: "mono", onclick: () => select(c.outage_id, true) }, c.outage_id),
      el("span", { class: "muted" }, (c.regions || []).join(" ")),
      el("span", null, changeDetail(c)),
      el("span", { class: "muted small" }, `${c.source_file}`));
  }

  function renderChanges() {
    const kinds = $("#f-change-kinds");
    kinds.replaceChildren(...Object.entries(CHANGE_KINDS).map(([k, l]) => el("button", {
      type: "button", class: "chip", "aria-pressed": String(state.changeKinds.includes(k)),
      onclick: () => { toggle(state.changeKinds, k); store.set("changeKinds", state.changeKinds); renderChanges(); },
    }, l)));
    const f = state.filters;
    const list = state.data.changes.filter((c) => state.changeKinds.includes(c.kind) &&
      ((c.regions || []).length ? c.regions.some((r) => f.regions.includes(r)) : f.regions.includes("Unknown")));
    const bySnap = new Map();
    for (const c of list) { if (!bySnap.has(c.snapshot_id)) bySnap.set(c.snapshot_id, []); bySnap.get(c.snapshot_id).push(c); }
    const box = $("#changes");
    if (!bySnap.size) { box.replaceChildren(el("p", { class: "muted" }, state.data.snapshots.length < 2 ? "At least two snapshots are needed to compare changes." : "No changes for the selected regions/types.")); return; }
    box.replaceChildren(...[...bySnap.entries()].slice(0, 12).map(([, cs]) => el("div", { class: "snap" },
      el("h4", null, `${cs[0].source_file} · file time ${fmt(cs[0].name_time, true)} · fetched ${fmt(cs[0].fetched_at, true)} · ${cs.length} changes`),
      cs.slice(0, 200).map(changeLine))));
  }

  // ---- meta ----
  function renderMeta(d) {
    const s = d.source;
    const parts = [];
    if (s) {
      parts.push(el("h3", null, "Current snapshot"), kv([
        ["Source file", s.url && s.kind === "nemweb" ? el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.file) : s.file],
        ["Source type", { nemweb: "NEMweb automatic fetch", neopoint: "NEOpoint automatic fetch", import: "Manual import", sample: "Synthetic sample" }[s.kind] || s.kind],
        ...(s.info ? [["Provider / report", `${s.info.provider} · ${s.info.report}`], ["Regions in data", (s.info.regions || []).join(", ")],
          ["Latest submitteddate", fmt(s.info.latest_submitted, true)], ["Source rows (deduplicated)", s.info.rows],
          ...(s.info.files && s.info.files.length ? [["Imported files", s.info.files.join(", ")]] : []),
          ...(s.url && s.kind !== "nemweb" ? [["Request URL(s), key redacted", el("span", { class: "mono small" }, s.url)]] : [])] : []),
        ["File name time", fmt(s.name_time, true)], ["Directory listing time", fmt(s.listing_time, true)], ["CSV report time", fmt(s.report_time, true)],
        ["Local fetch time", fmt(s.fetched_at, true)], ["Outages", s.record_count], ["Parse warnings", s.warning_count],
      ]));
      parts.push(el("h3", { style: "margin-top:8px" }, "Tables in file"), el("table", null,
        el("thead", null, el("tr", null, el("th", null, "Table"), el("th", null, "Version"), el("th", null, "Rows"), el("th", null, "Columns"))),
        el("tbody", null, Object.entries(s.tables).map(([n, t]) => el("tr", null, el("td", { class: "mono" }, n), el("td", null, t.version), el("td", null, t.rows), el("td", { class: "small muted" }, t.columns.join(", ")))))));
      if (s.info && s.info.not_provided) {
        parts.push(el("h3", { style: "margin-top:8px" }, "Fields not provided by this source"),
          el("ul", { class: "small" }, Object.values(s.info.not_provided).map((t) => el("li", null, t))));
        if (s.info.notes) parts.push(el("ul", { class: "small muted" }, s.info.notes.map((t) => el("li", null, t))));
      }
      const v = s.validation;
      if (v) {
        parts.push(el("h3", { style: "margin-top:8px" }, `Field validation: ${v.ok ? "passed" : "failed"}`),
          kv([["Outage rows / IDs", `${v.stats.outage_rows ?? "—"} / ${v.stats.outage_ids ?? "—"}`],
            ["Status codes used", (v.stats.status_codes_used || []).join(", ") || "—"],
            ["Codes without description", (v.stats.status_codes_without_description || []).join(", ") || "none"],
            ["Constraint set rows / distinct", `${v.stats.constraint_set_rows ?? 0} / ${v.stats.constraint_sets_distinct ?? 0}`],
            ["Outages with constraint sets", v.stats.records_with_constraint_sets ?? "—"],
            ["Substations without region", (v.stats.substations_without_region || []).length]]));
        if (v.errors.length || v.warnings.length)
          parts.push(el("details", null, el("summary", null, `Validation messages (${v.errors.length + v.warnings.length})`),
            el("pre", { class: "raw" }, [...v.errors.map((e) => "error: " + e), ...v.warnings.map((w) => "warning: " + w)].join("\n"))));
      }
      if (s.warnings.length) parts.push(el("details", null, el("summary", null, `Parse warnings (${s.warning_count})`), el("pre", { class: "raw" }, s.warnings.join("\n"))));
    }
    if (d.feeds) parts.push(el("h3", { style: "margin-top:8px" }, "NEOpoint feeds"), el("table", null,
      el("thead", null, el("tr", null, ["Feed", "Report", "Configured", "In use", "Status"].map((h) => el("th", null, h)))),
      el("tbody", null, d.feeds.map((f) => el("tr", null, el("td", { class: "mono" }, f.feed), el("td", null, f.report),
        el("td", null, f.configured ? "yes" : "no"), el("td", null, f.active ? "yes" : "no"), el("td", { class: "small" }, f.status))))));
    parts.push(el("h3", { style: "margin-top:8px" }, "Constraint set membership (GENCONSET)"),
      el("p", { class: "small" }, d.genconset.sets ? `${d.genconset.sets} constraint sets, source ${d.genconset.source}` : "Not imported. Without this mapping, constraint set binding status is always shown as \"Cannot be confirmed\"."));
    parts.push(el("h3", { style: "margin-top:8px" }, "Coordinate mapping"),
      el("p", { class: "small" }, `Substations ${d.locations.substations}, lines ${d.locations.lines}, interconnector substations ${d.locations.interconnector_substations}. Only explicitly maintained, sourced coordinates under data/ are used.`));
    if (d.locations.problems.length) parts.push(el("details", null, el("summary", null, `Mapping table problems (${d.locations.problems.length})`), el("pre", { class: "raw" }, d.locations.problems.join("\n"))));
    parts.push(el("h3", { style: "margin-top:8px" }, "Fetch log"), el("table", null,
      el("thead", null, el("tr", null, el("th", null, "Time"), el("th", null, "Feed"), el("th", null, "Result"), el("th", null, "Stage"), el("th", null, "HTTP"), el("th", null, "Message"))),
      el("tbody", null, d.fetch_log.map((l) => el("tr", null, el("td", null, fmt(l.at, true)), el("td", null, l.feed), el("td", null, l.ok ? "OK" : "Failed"), el("td", null, l.stage || ""), el("td", null, l.http_status || ""), el("td", { class: "small" }, l.message))))));
    parts.push(el("h3", { style: "margin-top:8px" }, "Saved snapshots"), el("table", null,
      el("thead", null, el("tr", null, el("th", null, "#"), el("th", null, "File"), el("th", null, "File time"), el("th", null, "Fetched"), el("th", null, "Outages"), el("th", null, "Changes"))),
      el("tbody", null, d.snapshots.map((x) => el("tr", null, el("td", null, x.id), el("td", { class: "mono small" }, x.source_file), el("td", null, fmt(x.name_time, true)), el("td", null, fmt(x.fetched_at, true)), el("td", null, x.record_count), el("td", null, x.change_count))))));
    $("#meta").replaceChildren(...parts);
  }

  // ---- filters UI ----
  function toggle(arr, v) { const i = arr.indexOf(v); if (i >= 0) arr.splice(i, 1); else arr.push(v); }

  function syncFilterUi() {
    const f = state.filters;
    $("#f-regions").replaceChildren(...REGIONS.map((r) => el("button", {
      type: "button", class: "chip", "aria-pressed": String(f.regions.includes(r)),
      onclick: () => { toggle(f.regions, r); saveFilters(); syncFilterUi(); render(); },
    }, r)));
    $("#f-phases").replaceChildren(...Object.entries(PHASES).map(([k, l]) => el("button", {
      type: "button", class: "chip", "aria-pressed": String(f.phases.includes(k)),
      onclick: () => { toggle(f.phases, k); saveFilters(); syncFilterUi(); render(); },
    }, l)));
    $("#f-window").value = String(f.window);
    $("#f-attention").checked = f.attention;
    $("#f-conset").checked = f.conset;
    $("#f-unmapped").checked = f.unmapped;
    $("#f-search").value = f.search;
  }

  function bind() {
    $("#btn-refresh").addEventListener("click", () => load(true, true));
    const sel = $("#sel-interval");
    sel.value = String(state.interval);
    sel.addEventListener("change", () => { state.interval = Number(sel.value); store.set("interval", state.interval); scheduleNext(); });
    $("#f-window").addEventListener("change", (e) => { state.filters.window = Number(e.target.value); saveFilters(); render(); });
    for (const [id, key] of [["#f-attention", "attention"], ["#f-conset", "conset"], ["#f-unmapped", "unmapped"]])
      $(id).addEventListener("change", (e) => { state.filters[key] = e.target.checked; saveFilters(); render(); });
    let t;
    $("#f-search").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { state.filters.search = e.target.value; saveFilters(); render(); }, 200); });
    $("#btn-close-detail").addEventListener("click", () => { state.selected = null; render(); });
    setInterval(() => { renderCountdown(); if (state.data) renderStatus(state.data); }, 1000 * 15);
    setInterval(renderCountdown, 1000);
  }

  function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  const m = /#outage=([^&]+)/.exec(location.hash);
  if (m) state.selected = decodeURIComponent(m[1]);
  bind(); syncFilterUi(); initMap();
  load(false);
})();
