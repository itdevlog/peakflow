/* Mini App «Дневник ПСВ»: vanilla JS + Telegram WebApp SDK. */
const tg = window.Telegram.WebApp;
try { tg.ready(); tg.expand(); } catch (e) {}

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
    el.innerHTML = `<div class="hint">Сегодня замеров ещё нет 💨</div>`;
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
  const trend = (s.trend == null) ? "—" : (s.trend > 0 ? `↑ +${s.trend.toFixed(1)}` : `↓ ${s.trend.toFixed(1)}`);
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

/* ---- chart (Task 7 fills this in) ---- */
async function loadChart() {
  $("screen-chart").innerHTML = `<div class="hint">График загружается…</div>`;
}

switchTo("today");
