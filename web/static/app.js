/* Mini App «Дневник ПСВ»: vanilla JS + Telegram WebApp SDK. */
const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : { initData: "" };
try { tg.ready && tg.ready(); tg.expand && tg.expand(); } catch (e) {}

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function zoneClass(pct) {
  if (pct >= 80) return "zone-green";
  if (pct >= 60) return "zone-yellow";
  return "zone-red";
}

const state = { target: 0, chart: { year: null, month: null }, history: { page: 1 } };

async function api(path) {
  const r = await fetch(path, { headers: { "X-Telegram-Init-Data": tg.initData || "" } });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: "Ошибка сети" }));
    throw new Error(body.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.hidden = false;
}

function clearError() { $("error").hidden = true; }

function todLabel(tod) { return tod === "morning" ? "☀️ Утро" : "🌙 Вечер"; }

function pct(value) {
  return state.target ? Math.round((value / state.target) * 100) : 100;
}

function measureCard(m) {
  const p = pct(m.pef_value);
  const auto = (m.source === "auto") ? ' <span class="auto">🤖</span>' : "";
  const note = m.note ? `<div class="note">ℹ️ ${esc(m.note)}</div>` : "";
  return `<div class="card">
    <div class="row">
      <span class="label">${todLabel(m.time_of_day)}</span>
      <span class="label">${esc(String(m.measured_at).slice(5, 16))}</span>
    </div>
    <div class="big ${zoneClass(p)}">${m.pef_value} <span class="label">${p}%</span>${auto}</div>
    ${note}
  </div>`;
}

async function loadToday() {
  const s = await api("/api/status");
  state.target = s.target_pef;
  $("target-badge").textContent = `цель ${s.target_pef}`;
  const el = $("screen-today");
  if (!s.today.length) {
    el.innerHTML = (s.last ? `<div class="label">Последний замер</div>` + measureCard(s.last) : "") +
      `<div class="hint">Сегодня замеров ещё нет 💨</div>`;
    return;
  }
  el.innerHTML = s.today.map(measureCard).join("");
}

async function loadHistory(page = 1) {
  state.history.page = page;
  const h = await api(`/api/history?page=${page}&per_page=10`);
  const el = $("screen-history");
  if (!h.items.length) {
    el.innerHTML = `<div class="hint">История пуста</div>`;
    return;
  }
  el.innerHTML = h.items.map(measureCard).join("") + `<div class="pager">
    <button id="hist-prev" ${page <= 1 ? "disabled" : ""}>‹</button>
    <span class="label">${page} / ${h.total_pages}</span>
    <button id="hist-next" ${page >= h.total_pages ? "disabled" : ""}>›</button>
  </div>`;
  const prev = $("hist-prev"), next = $("hist-next");
  if (prev) prev.onclick = () => loadHistory(page - 1);
  if (next) next.onclick = () => loadHistory(page + 1);
}

async function loadStats() {
  const s = await api("/api/stats");
  state.target = s.target_pef || state.target;
  const el = $("screen-stats");
  if (!s.total) { el.innerHTML = `<div class="hint">Недостаточно данных</div>`; return; }
  const trend = (s.trend == null) ? "—"
    : s.trend > 0 ? `↑ +${s.trend.toFixed(1)}`
    : s.trend < 0 ? `↓ ${Math.abs(s.trend).toFixed(1)}`
    : "→ 0";
  const avg = (v) => (v == null ? "—" : Math.round(v));
  el.innerHTML = `
    <div class="card"><div class="label">Всего замеров</div><div class="big">${s.total}</div></div>
    <div class="card"><div class="label">Среднее</div><div class="big">${avg(s.avg)}</div></div>
    <div class="card"><div class="label">Мин / Макс</div><div class="big">${s.min} / ${s.max}</div></div>
    <div class="card"><div class="label">Утро (сред.)</div><div class="big">${avg(s.morning_avg)} <span class="label">×${s.morning_count}</span></div></div>
    <div class="card"><div class="label">Вечер (сред.)</div><div class="big">${avg(s.evening_avg)} <span class="label">×${s.evening_count}</span></div></div>
    <div class="card"><div class="label">Тренд (3 vs 3)</div><div class="big">${trend}</div></div>`;
}

async function switchTo(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.screen === name));
  document.querySelectorAll(".screen").forEach((s) =>
    s.hidden = (s.id !== `screen-${name}`));
  clearError();
  try {
    if (name === "today") await loadToday();
    else if (name === "history") await loadHistory(1);
    else if (name === "stats") await loadStats();
    else if (name === "chart") await loadChart();
  } catch (e) { showError(e.message); }
}

document.querySelectorAll(".tab").forEach((t) =>
  t.onclick = () => switchTo(t.dataset.screen));

/* ---- chart (canvas, no libraries) ---- */
function initChart() {
  const c = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const cssW = c.clientWidth || c.parentElement.clientWidth || 320;
  const cssH = 320;
  c.width = Math.round(cssW * dpr);
  c.height = Math.round(cssH * dpr);
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { c, ctx, cssW, cssH };
}

function drawChart(data, points) {
  const { c, ctx, cssW, cssH } = initChart();
  ctx.clearRect(0, 0, cssW, cssH);
  const padL = 38, padR = 12, padT = 12, padB = 24;
  const plotW = cssW - padL - padR;
  const plotH = cssH - padT - padB;

  const values = points.map((p) => p.pef);
  const target = data.target_pef || 0;
  const all = values.concat([target]).filter((v) => v > 0);
  let yMax = all.length ? Math.max(...all) : 300;
  let yMin = all.length ? Math.min(...all) : 0;
  if (yMax === yMin) { yMax += 20; yMin = Math.max(0, yMin - 20); }
  const margin = Math.round((yMax - yMin) * 0.15) || 10;
  yMax += margin; yMin = Math.max(0, yMin - margin);

  const yToPx = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
  const n = points.length;
  const xToPx = (i) => (n <= 1 ? padL + plotW / 2 : padL + (i / (n - 1)) * plotW);

  const green = data.zones.green, yellow = data.zones.yellow;
  const band = (fromPct, toPct, color) => {
    if (!target) return;
    const y1 = yToPx((toPct / 100) * target);
    const y2 = yToPx((fromPct / 100) * target);
    ctx.fillStyle = color;
    ctx.fillRect(padL, Math.max(padT, y1), plotW, Math.min(cssH - padB, y2) - Math.max(padT, y1));
  };
  ctx.globalAlpha = 0.10;
  band(0, yellow, "#e5484d");
  band(yellow, green, "#e8a600");
  band(green, 200, "#2fb344");
  ctx.globalAlpha = 1;

  ctx.strokeStyle = "#999";
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, cssH - padB); ctx.lineTo(cssW - padR, cssH - padB);
  ctx.stroke();

  ctx.fillStyle = "#777"; ctx.font = "10px sans-serif";
  for (let k = 0; k <= 4; k++) {
    const v = yMin + ((yMax - yMin) * k) / 4;
    const y = yToPx(v);
    ctx.fillText(String(Math.round(v)), 4, y + 3);
  }

  if (target) {
    ctx.strokeStyle = "#2ea6ff"; ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(padL, yToPx(target)); ctx.lineTo(cssW - padR, yToPx(target)); ctx.stroke();
    ctx.setLineDash([]);
  }

  if (n >= 2) {
    ctx.strokeStyle = "#555"; ctx.lineWidth = 1.5; ctx.beginPath();
    points.forEach((p, i) => { const x = xToPx(i), y = yToPx(p.pef);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
  }

  ctx.lineWidth = 1;
  points.forEach((p, i) => {
    ctx.fillStyle = p.tod === "morning" ? "#e8a600" : "#7a5cff";
    ctx.beginPath(); ctx.arc(xToPx(i), yToPx(p.pef), 3.5, 0, Math.PI * 2); ctx.fill();
  });

  c._points = points.map((p, i) => ({ x: xToPx(i), y: yToPx(p.pef), p }));
}

function chartClick(ev) {
  const c = $("chart");
  const pts = c._points || [];
  const rect = c.getBoundingClientRect();
  const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
  let best = null, bestD = 1e9;
  for (const q of pts) {
    const d = (q.x - x) ** 2 + (q.y - y) ** 2;
    if (d < bestD) { bestD = d; best = q; }
  }
  const tip = $("chart-tip");
  if (best && bestD < 900) {
    tip.hidden = false;
    tip.textContent = `${best.p.date} · ${todLabel(best.p.tod)} · ${best.p.pef}`;
  } else { tip.hidden = true; }
}

async function loadChart(year, month) {
  const q = (year && month) ? `?year=${year}&month=${month}` : "";
  const data = await api(`/api/chart${q}`);
  state.target = data.target_pef || state.target;
  state.chart = { year: data.month.slice(0, 4), month: data.month.slice(5, 7) };
  $("chart-title").textContent = data.title;
  $("chart-prev").disabled = !data.can_prev;
  $("chart-next").disabled = !data.can_next;
  $("chart-tip").hidden = true;
  if (!data.points.length) {
    initChart().ctx.clearRect(0, 0, 9999, 9999);
    $("chart").style.display = "none";
    $("screen-chart").querySelector(".hint")?.remove();
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent = "В этом месяце замеров нет";
    $("screen-chart").appendChild(hint);
    return;
  }
  $("chart").style.display = "block";
  $("screen-chart").querySelector(".hint")?.remove();
  drawChart(data, data.points);
}

$("chart-prev").onclick = () => shiftMonth(-1);
$("chart-next").onclick = () => shiftMonth(1);
$("chart").addEventListener("click", chartClick);

async function shiftMonth(delta) {
  const data = await api(`/api/chart?year=${state.chart.year}&month=${state.chart.month}`);
  const months = data.available_months;
  const cur = `${state.chart.year}-${state.chart.month}`;
  let idx = months.indexOf(cur);
  if (idx === -1) {
    const sorted = [...months, cur].sort();
    idx = sorted.indexOf(cur);
  }
  const nextIdx = idx + delta;
  if (nextIdx < 0 || nextIdx >= months.length) return;
  const [y, m] = months[nextIdx].split("-");
  await loadChart(Number(y), Number(m));
}

async function boot() {
  try {
    const me = await api("/api/me");
    $("child-name").textContent = me.child_name || "Дневник";
  } catch (e) {
    showError("Откройте приложение через Telegram");
    return;
  }
  await switchTo("today");
}

boot();
