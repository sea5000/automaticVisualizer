/* Automatic Pro Trips — dashboard frontend.
 * Consumes /api/trips and /api/meta. Route coordinates arrive pre-decoded
 * (server extracts them from the exporter's Google-encoded polylines).
 */
(() => {
  "use strict";

  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const MONTH_COLORS = [
    "#4d9fff", "#2ca02c", "#ffb02e", "#d62728", "#9b5de5", "#00b4d8",
    "#f15bb5", "#80ed99", "#fee440", "#f97316", "#38bdf8", "#a3a3a3",
  ];
  const MPG_LO = "#d62728", MPG_MID = "#ffb02e", MPG_HI = "#2ca02c";

  let trips = [];
  let meta = { count: 0, total_dist: 0, total_fuel: 0 };
  let filtered = [];
  let map = null, cluster = null, routeLayer = null;
  let chartCat = null, chartMonth = null, chartMpg = null;
  let sortKey = "ts", sortAsc = true;

  const $ = (s) => document.querySelector(s);
  const tripId = (t) => t.ts + "-" + t.dist;
  const fmtDate = (iso) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const monthIdx = (iso) => new Date(iso).getMonth();
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  function hex2rgb(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
  function mix(a, b, t) {
    const A = hex2rgb(a), B = hex2rgb(b);
    return "rgb(" + A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(",") + ")";
  }
  function mpgBounds() {
    const v = filtered.filter((t) => t.mpg > 0).map((t) => t.mpg);
    return [v.length ? Math.min.apply(null, v) : 0, v.length ? Math.max.apply(null, v) : 0];
  }
  function mpgColor(mpg, lo, hi) {
    if (!lo || !hi || !(mpg > 0)) return MPG_MID;
    const t = Math.max(0, Math.min(1, (mpg - lo) / (hi - lo)));
    return t < 0.5 ? mix(MPG_LO, MPG_MID, t * 2) : mix(MPG_MID, MPG_HI, (t - 0.5) * 2);
  }
  const mpgColorFor = (t) => mpgColor(t.mpg).bind ? mpgColor(t.mpg, ...mpgBounds()) : MPG_MID;

  /* ---------- filters + render pipeline ---------- */

  function applyFilters() {
    const from = $("#fromDate").value, to = $("#toDate").value,
          category = $("#categorySel").value,
          q = $("#searchBox").value.trim().toLowerCase();
    filtered = trips.filter((t) => {
      if (from && t.start < from + "T00:00") return false;
      if (to && t.start > to + "T23:59:59") return false;
      if (category && t.dest_cat !== category) return false;
      if (q && !(t.dest_label.toLowerCase().includes(q) || t.start_label.toLowerCase().includes(q))) return false;
      return true;
    });
    renderKpis();
    renderMap();
    renderTable();
    renderCharts();
  }

  /* ---------- KPIs ---------- */

  function renderKpis() {
    const n = filtered.length;
    const dist = filtered.reduce((s, t) => s + t.dist, 0);
    const fuel = filtered.reduce((s, t) => s + t.fuel, 0);
    const cost = filtered.reduce((s, t) => s + t.cost, 0);
    const hardA = filtered.reduce((s, t) => s + t.hard_acc, 0);
    const hardB = filtered.reduce((s, t) => s + t.hard_brakes, 0);
    const mpgs = filtered.filter((t) => t.mpg > 0).map((t) => t.mpg);
    const avgMpg = mpgs.length ? mpgs.reduce((a, b) => a + b, 0) / mpgs.length : 0;
    $("#kTrips").textContent = String(n);
    $("#kTripsSub").textContent = n ? "of " + meta.count + " total" : "";
    $("#kDist").textContent = dist.toFixed(1) + " mi";
    $("#kDistSub").textContent = n ? (dist / n).toFixed(1) + " mi avg per trip" : "";
    $("#kMpg").textContent = avgMpg ? avgMpg.toFixed(1) : "–";
    $("#kMpgSub").textContent = mpgs.length ? mpgs.length + " rated trips" : "no ratings";
    $("#kFuel").textContent = fuel.toFixed(1) + " gal";
    $("#kFuelSub").textContent = n ? (fuel / n).toFixed(1) + " gal avg" : "";
    $("#kCost").textContent = "$" + cost.toFixed(0);
    $("#kCostSub").textContent = n ? "$" + (cost / n).toFixed(2) + " per trip" : "";
    $("#kHard").textContent = String(hardA + hardB);
    $("#kHardSub").textContent = hardA + " accel · " + hardB + " brake";
  }

  /* ---------- map ---------- */

  function renderMap() {
    if (!map) return;
    cluster.clearLayers();
    routeLayer.clearLayers();
    const showRoutes = document.querySelector("#mapModeSeg button.active").dataset.mode === "routes";
    const [lo, hi] = mpgBounds();
    if (showRoutes) {
      filtered.forEach((t) => {
        if (!t.route || t.route.length < 2) return;
        const pts = t.route.map((p) => [p[0], p[1]]);
        // Casing under the colored line: without it, routes vanish on the
        // inverted dark tiles (and at WhatsApp thumbnail size).
        L.polyline(pts, { color: "#f2f0e9", weight: 10, opacity: 0.45 }).addTo(routeLayer);
        L.polyline(pts, {
          color: mpgColor(t.mpg, lo, hi), weight: 5, opacity: 1,
        }).addTo(routeLayer)
          .bindTooltip(t.dest_label, { className: "sel-tip", direction: "top", sticky: true })
          .on("click", () => selectTrip(tripId(t), false));
      });
    }
    filtered.forEach((t) => {
      const mi = monthIdx(t.start);
      const m = L.marker([t.end_lat, t.end_lng], {
        icon: L.divIcon({
          html: '<div style="width:16px;height:16px;border-radius:50%;background:' + MONTH_COLORS[mi] + ';border:2px solid rgba(255,255,255,.85)"></div>',
          className: "", iconSize: [16, 16],
        }),
      });
      m.bindTooltip(t.dest_label + " — " + fmtDate(t.start), { className: "sel-tip", direction: "top" });
      m.on("click", () => selectTrip(tripId(t), false));
      cluster.addLayer(m);
    });
    // Fit the map to the trip ROUTES, not the end markers. End points
    // cluster into ~3 places, so endpoint-bounds fit zoomed out onto the
    // wrong centre; the route extents are where the data actually lives.
    const routePts = [];
    filtered.forEach((t) => {
      (t.route || []).forEach((p) => routePts.push(p));
      if (!t.route && t.end_lat) routePts.push([t.end_lat, t.end_lng]);
    });
    if (routePts.length) {
      map.fitBounds(
        L.featureGroup(routePts.map((p) => L.marker(p))).getBounds().pad(0.15),
      );
    }
  }

  /* ---------- detail panel ---------- */

  function selectTrip(id, panMap) {
    const t = filtered.find((x) => tripId(x) === id) || trips.find((x) => tripId(x) === id);
    if (!t) return;
    const [lo, hi] = mpgBounds();
    $("#detailPanel").innerHTML =
      '<div style="font-size:15px;font-weight:700;color:var(--accent)">' + t.dest_label +
      ' <span class="d-cat">' + t.dest_cat + "</span></div>" +
      '<div style="color:var(--muted);font-size:12px;margin:3px 0 10px">from <b>' + t.start_label + "</b></div>" +
      '<div class="detail-grid">' +
      '<div><div class="d-label">Departed</div><div class="d-val">' + fmtDate(t.start) + " " + fmtTime(t.start) + "</div></div>" +
      '<div><div class="d-label">Returned</div><div class="d-val">' + fmtDate(t.end) + " " + fmtTime(t.end) + "</div></div>" +
      '<div><div class="d-label">Distance</div><div class="d-val">' + t.dist + " mi</div></div>" +
      '<div><div class="d-label">Duration</div><div class="d-val">' + Math.round(t.dur_min) + " min</div></div>" +
      '<div><div class="d-label">Fuel economy</div><div class="d-val" style="color:' + mpgColor(t.mpg, lo, hi) + '">' + t.mpg + " MPG</div></div>" +
      '<div><div class="d-label">Fuel cost</div><div class="d-val">$' + t.cost.toFixed(2) + "</div></div>" +
      '<div><div class="d-label">Hard accelerations</div><div class="d-val">' + t.hard_acc + "</div></div>" +
      '<div><div class="d-label">Hard brakes</div><div class="d-val">' + t.hard_brakes + "</div></div>" +
      '<div><div class="d-label">Time over 70/75/80</div><div class="d-val">' + t.over_70 + " / " + t.over_75 + " / " + t.over_80 + " min</div></div>" +
      '<div><div class="d-label">Fuel used</div><div class="d-val">' + t.fuel + " gal</div></div>" +
      "</div>" +
      (t.end_link ? '<div class="detail-links"><a href="' + t.end_link + '" target="_blank" rel="noopener">Destination map &nearr;</a></div>' : "") ;
    if (panMap) map.flyTo([t.end_lat, t.end_lng], Math.max(map.getZoom(), 15));
  }

  /* ---------- charts ---------- */

  function renderCharts() {
    const catCount = {};
    filtered.forEach((t) => { catCount[t.dest_cat] = (catCount[t.dest_cat] || 0) + 1; });
    const catLabels = Object.keys(catCount).sort((a, b) => catCount[b] - catCount[a]);

    const byMonth = MONTHS.map((m, i) =>
      Math.round(filtered.filter((t) => monthIdx(t.start) === i).reduce((s, t) => s + t.dist, 0) * 10) / 10
    );

    const [lo, hi] = mpgBounds();
    const span = (hi - lo) || 1;
    const buckets = new Array(8).fill(0);
    filtered.forEach((t) => {
      if (t.mpg > 0) buckets[Math.min(7, Math.floor(((t.mpg - lo) / span) * 8))]++;
    });

    if (chartCat) chartCat.destroy();
    if (chartMonth) chartMonth.destroy();
    if (chartMpg) chartMpg.destroy();
    Chart.defaults.color = "#7f8db0";
    Chart.defaults.borderColor = "#26324d";

    chartCat = new Chart($("#chartCategory"), {
      type: "bar",
      data: { labels: catLabels, datasets: [{
        label: "Trips", data: catLabels.map((c) => catCount[c]),
        backgroundColor: "rgba(77,159,255,.55)", borderColor: "#4d9fff", borderWidth: 1,
      }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { ticks: { maxRotation: 60, minRotation: 30, autoSkip: false } } } },
    });

    chartMonth = new Chart($("#chartMonthly"), {
      type: "bar",
      data: { labels: MONTHS, datasets: [{
        label: "Miles driven", data: byMonth,
        backgroundColor: "rgba(0,180,216,.5)", borderColor: "#00b4d8", borderWidth: 1,
      }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { ticks: { maxRotation: 0 } } } },
    });

    chartMpg = new Chart($("#chartMpg"), {
      type: "bar",
      data: { labels: ["", "", "", "", "", "", "", ""], datasets: [{
        label: "Trips", data: buckets,
        backgroundColor: buckets.map((_, i) => mpgColor(lo + ((i + 0.5) * span) / 8, lo, hi) + "cc"),
        borderColor: "#26324d", borderWidth: 1,
      }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: {
            callbacks: {
              title: () => (lo ? lo.toFixed(0) : 0) + "–" + (hi ? hi.toFixed(0) : 0) + " mpg range of filtered trips",
            },
          } },
        scales: { x: { display: false }, y: { beginAtZero: true } } },
    });

    $("#mpgMin").textContent = lo ? lo.toFixed(0) + " mpg" : "–";
    $("#mpgMid").textContent = (lo && hi) ? ((lo + hi) / 2).toFixed(0) + " mpg" : "–";
    $("#mpgMax").textContent = hi ? hi.toFixed(0) + " mpg" : "–";
  }

  /* ---------- table ---------- */

  function renderTable() {
    const rows = filtered.slice().sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = (typeof av === "string") ? av.localeCompare(bv) : (av < bv ? -1 : av > bv ? 1 : 0);
      return sortAsc ? cmp : -cmp;
    });
    const [lo, hi] = mpgBounds();
    $("#tripsBody").innerHTML = rows.map((t) => {
      const ovm = t.over_70
        ? '<span class="badge">' + t.over_70 + " / " + t.over_75 + " / " + t.over_80 + "</span>"
        : '<span class="badge">–</span>';
      return '<tr data-id="' + tripId(t) + '">' +
        "<td>" + fmtDate(t.start) + " " + fmtTime(t.start) + "</td>" +
        "<td>" + Math.round(t.dur_min) + " min</td>" +
        "<td>" + t.dist.toFixed(1) + "</td>" +
        '<td style="color:' + mpgColor(t.mpg, lo, hi) + '">' + t.mpg + "</td>" +
        "<td>$" + t.cost.toFixed(2) + "</td>" +
        "<td>" + t.hard_acc + "</td>" +
        "<td>" + t.hard_brakes + "</td>" +
        "<td>" + ovm + "</td>" +
        '<td class="dest-cell" title="' + t.dest_label + '">' + t.dest_label + "</td>" +
        "</tr>";
    }).join("");
  }

  /* ---------- controls + init ---------- */

  function initControls() {
    const cats = [...new Set(trips.map((t) => t.dest_cat))].sort();
    $("#categorySel").innerHTML =
      '<option value="">All categories</option>' + cats.map((c) => "<option>" + c + "</option>").join("");

    const first = trips[0] ? trips[0].start.slice(0, 10) : "";
    const last = trips.length ? trips[trips.length - 1].start.slice(0, 10) : "";
    $("#fromDate").min = $("#toDate").min = first;
    $("#fromDate").max = $("#toDate").max = last;
    $("#fromDate").value = first;
    $("#toDate").value = last;

    $("#legendMonth").innerHTML = MONTH_COLORS.map((c, i) =>
      '<div class="legend-item"><span class="swatch" style="background:' + c + '"></span>' + MONTHS[i] + "</div>"
    ).join("");

    $("#searchBox").addEventListener("input", debounce(applyFilters, 150));
    $("#categorySel").addEventListener("change", applyFilters);
    $("#fromDate").addEventListener("change", applyFilters);
    $("#toDate").addEventListener("change", applyFilters);
    $("#resetBtn").addEventListener("click", () => {
      $("#fromDate").value = first; $("#toDate").value = last;
      $("#categorySel").value = ""; $("#searchBox").value = "";
      applyFilters();
    });
    document.querySelectorAll("#mapModeSeg button").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll("#mapModeSeg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      renderMap();
    }));
    document.querySelectorAll("thead th").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.key;
      sortAsc = sortKey === k ? !sortAsc : true;
      sortKey = k;
      renderTable();
    }));
    $("#tripsBody").addEventListener("click", (ev) => {
      const tr = ev.target.closest("tr[data-id]");
      if (tr) selectTrip(tr.dataset.id, true);
    });

    const f = new Date(first + "T12:00"), l = new Date(last + "T12:00");
    $("#sub").innerHTML = meta.count + " trips · " +
      f.toLocaleDateString(undefined, { month: "short", year: "numeric" }) + " – " +
      l.toLocaleDateString(undefined, { month: "short", year: "numeric" }) +
      " · " + (meta.total_dist || 0).toFixed(0) + " mi · " + (meta.total_fuel || 0).toFixed(0) +
      " gal &mdash; synthetic sample data";
  }

  async function main() {
    const [tripData, metaData] = await Promise.all([
      fetch("/api/trips").then((r) => r.json()),
      fetch("/api/meta").then((r) => r.json()),
    ]);
    trips = tripData || [];
    meta = metaData || meta;

    map = L.map("map", { preferCanvas: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
      className: "osm-dark",
    }).addTo(map);
    routeLayer = L.layerGroup().addTo(map);
    cluster = L.markerClusterGroup({ showCoverageOnHover: false });
    cluster.on("clusterclick", (e) => e.layer.zoomToShowLayer(e.layer));
    map.addLayer(cluster);

    initControls();
    applyFilters();
  }

  main().catch((e) => {
    $("#sub").textContent = "Failed to load trips: " + e.message;
  });
})();
