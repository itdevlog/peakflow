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
  const z = state.zones || { green: 80, yellow: 60 };
  if (pct >= z.green) return "zone-green";
  if (pct >= z.yellow) return "zone-yellow";
  return "zone-red";
}

const state = {
  target: 0, role: null, zones: null,
  chart: { year: null, month: null }, chartData: null, history: { page: 1 },
  form: { open: false, step: "h", hundreds: null, mode: "add", editId: null, busy: false },
};

async function api(path, opts = {}) {
  const init = { headers: { "X-Telegram-Init-Data": tg.initData || "" } };
  if (opts.method) init.method = opts.method;
  if (opts.body) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    const b = await r.json().catch(() => ({ detail: "Ошибка сети" }));
    let detail = b.detail;
    if (typeof detail === "string") {
      try { detail = JSON.parse(detail); } catch (e) {}
    }
    if (detail && typeof detail === "object") {
      const err = new Error(detail.message || `HTTP ${r.status}`);
      err.detail = detail;
      err.status = r.status;
      throw err;
    }
    throw new Error(detail || `HTTP ${r.status}`);
  }
  return r.json();
}

async function download(path, fallbackName) {
  const r = await fetch(path, { headers: { "X-Telegram-Init-Data": tg.initData || "" } });
  if (!r.ok) {
    const b = await r.json().catch(() => ({ detail: "Ошибка" }));
    throw new Error(b.detail || `HTTP ${r.status}`);
  }
  const cd = r.headers.get("Content-Disposition") || "";
  let name = fallbackName;
  const star = /filename\*=utf-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  if (star) name = decodeURIComponent(star[1]);
  else if (plain) name = plain[1];
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}

function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.hidden = false;
}

function clearError() { $("error").hidden = true; }

function todLabel(tod) { return tod === "morning" ? "☀️ Утро" : "🌙 Вечер"; }

function pct(value) {
  return state.target ? Math.floor((value / state.target) * 100) : 100;
}

function measureCardInner(m) {
  const p = pct(m.pef_value);
  const auto = (m.source === "auto") ? ' <span class="auto">🤖</span>' : "";
  const note = m.note ? `<div class="note">ℹ️ ${esc(m.note)}</div>` : "";
  return `<div class="row">
      <span class="label">${todLabel(m.time_of_day)}</span>
      <span class="label">${esc(String(m.measured_at).slice(5, 16))}</span>
    </div>
    <div class="big ${zoneClass(p)}">${m.pef_value} <span class="label">${p}%</span>${auto}</div>
    ${note}`;
}

function measureCard(m) {
  return `<div class="card">${measureCardInner(m)}</div>`;
}

async function loadToday() {
  const s = await api("/api/status");
  state.target = s.target_pef;
  $("target-badge").textContent = `цель ${s.target_pef}`;
  const el = $("screen-today");
  const btn = `<button id="add-btn">+ Замер</button>`;
  const cards = s.today.length
    ? s.today.map(measureCard).join("")
    : (s.last ? `<div class="label">Последний замер</div>` + measureCard(s.last) : "") +
      `<div class="hint">Сегодня замеров ещё нет 💨</div>`;
  el.innerHTML = btn + cards;
  $("add-btn").onclick = () => openForm("add");
}

async function loadHistory(page = 1) {
  state.history.page = page;
  const h = await api(`/api/history?page=${page}&per_page=10`);
  const el = $("screen-history");
  if (!h.items.length) {
    if (page > 1) { return loadHistory(page - 1); }
    el.innerHTML = `<div class="hint">История пуста</div>`;
    return;
  }
  const isParent = state.role === "parent";
  el.innerHTML = h.items.map((m) => `<div class="card">
    ${measureCardInner(m)}
    ${isParent ? `<div class="row" style="margin-top:6px">
      <button class="mini" data-edit="${m.id}">✏️</button>
      <button class="mini" data-del="${m.id}">🗑️</button>
    </div>` : ""}
  </div>`).join("") + `<div class="pager">
    <button id="hist-prev" ${page <= 1 ? "disabled" : ""}>‹</button>
    <span class="label">${page} / ${h.total_pages}</span>
    <button id="hist-next" ${page >= h.total_pages ? "disabled" : ""}>›</button>
  </div>`;
  const prev = $("hist-prev"), next = $("hist-next");
  if (prev) prev.onclick = () => loadHistory(page - 1);
  if (next) next.onclick = () => loadHistory(page + 1);
  if (isParent) {
    el.querySelectorAll("[data-edit]").forEach((b) =>
      b.onclick = () => openForm("edit", Number(b.dataset.edit)));
    el.querySelectorAll("[data-del]").forEach((b) =>
      b.onclick = () => deleteMeasurement(Number(b.dataset.del)));
  }
}

async function deleteMeasurement(mid) {
  if (!window.confirm("Удалить запись? Действие необратимо.")) return;
  try {
    await api(`/api/measurements/${mid}`, { method: "DELETE" });
    await loadHistory(state.history.page);
  } catch (e) { showError(e.message); }
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
  const setTab = document.getElementById("tab-settings");
  if (setTab) setTab.hidden = (state.role !== "parent");
  clearError();
  try {
    if (name === "today") await loadToday();
    else if (name === "history") await loadHistory(1);
    else if (name === "stats") await loadStats();
    else if (name === "chart") await loadChart();
    else if (name === "settings") await loadSettings();
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
    const clamp = (y) => Math.max(padT, Math.min(cssH - padB, y));
    const yTop = clamp(yToPx((toPct / 100) * target));
    const yBot = clamp(yToPx((fromPct / 100) * target));
    if (yBot - yTop <= 0) return;
    ctx.fillStyle = color;
    ctx.fillRect(padL, yTop, plotW, yBot - yTop);
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
    state.chartData = null;
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
  state.chartData = data;
  drawChart(data, data.points);
}

let _resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    const d = state.chartData;
    if (d && d.points && d.points.length && $("chart").offsetParent) {
      drawChart(d, d.points);
    }
  }, 200);
});

$("chart-prev").onclick = () => shiftMonth(-1).catch((e) => showError(e.message));
$("chart-next").onclick = () => shiftMonth(1).catch((e) => showError(e.message));
$("chart").addEventListener("click", chartClick);

async function shiftMonth(delta) {
  const data = await api(`/api/chart?year=${state.chart.year}&month=${state.chart.month}`);
  const months = data.available_months;
  const cur = `${state.chart.year}-${state.chart.month}`;
  // Index in the sorted union of available months + the current one, so a
  // month with no data still navigates to the nearest real neighbour.
  const union = months.includes(cur) ? months.slice() : [...months, cur].sort();
  const idx = union.indexOf(cur);
  const nextIdx = idx + delta;
  if (nextIdx < 0 || nextIdx >= union.length) return;
  const target = union[nextIdx];
  if (target === cur) return;
  const [y, m] = target.split("-");
  await loadChart(Number(y), Number(m));
}

/* ---- add/edit form (stepwise hundreds → tens) ---- */
function renderForm() {
  const ov = $("form-overlay");
  const f = state.form;
  if (!f.open) { ov.hidden = true; ov.innerHTML = ""; return; }
  const title = f.mode === "edit" ? "Изменить ПСВ (л/мин)" : "Выбери ПСВ (л/мин)";
  let body;
  if (f.step === "h") {
    body = `<div class="grid grid-h">` +
      [1, 2, 3, 4, 5, 6].map((h) => `<button data-h="${h}">${h}</button>`).join("") +
      `</div>`;
  } else {
    body = `<div class="form-value">${f.hundreds}__</div><div class="grid">` +
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => `<button data-d="${d}">${String(d * 10).padStart(2, "0")}</button>`).join("") +
      `</div>`;
  }
  ov.innerHTML = `<div class="form-box">
    <h3>${title}</h3>${body}
    <div class="form-actions">
      <button class="secondary" id="form-cancel">Отмена</button>
      ${f.step === "t" ? `<button class="secondary" id="form-back">‹ Назад</button>` : ""}
    </div></div>`;
  ov.hidden = false;
  ov.querySelectorAll("[data-h]").forEach((b) => b.onclick = () => { f.hundreds = Number(b.dataset.h); f.step = "t"; renderForm(); });
  ov.querySelectorAll("[data-d]").forEach((b) => b.onclick = () => submitMeasurement(f.hundreds * 100 + Number(b.dataset.d) * 10, f.mode, f.editId));
  $("form-cancel").onclick = closeForm;
  const back = $("form-back");
  if (back) back.onclick = () => { f.step = "h"; f.hundreds = null; renderForm(); };
}

function closeForm() { state.form = { open: false, step: "h", hundreds: null, mode: "add", editId: null, busy: false }; renderForm(); }

function openForm(mode, editId = null) {
  state.form = { open: true, step: "h", hundreds: null, mode, editId, busy: false };
  renderForm();
}

async function submitMeasurement(pef, mode, editId, force = false) {
  if (state.form.busy) return;
  state.form.busy = true;
  $("form-overlay").style.pointerEvents = "none";
  try {
    if (mode === "edit") {
      await api(`/api/measurements/${editId}`, { method: "PATCH", body: { pef } });
      closeForm();
      await loadHistory(state.history.page);
    } else {
      const q = force ? "?force=1" : "";
      const res = await api(`/api/measurements${q}`, { method: "POST", body: { pef } });
      renderResult(res);
    }
  } catch (e) {
    if (e.status === 409 && e.detail && e.detail.tod) {
      state.form.busy = false;
      renderDuplicatePrompt(pef, e.detail);
      return;
    }
    closeForm();
    showError(e.message);
  } finally { $("form-overlay").style.pointerEvents = ""; }
}

function renderDuplicatePrompt(pef, detail) {
  const ov = $("form-overlay");
  ov.innerHTML = `<div class="form-box">
    <h3>⚠️ Уже есть замер</h3>
    <div class="label center">${esc(detail.message)}</div>
    <div class="label center">Добавить ещё один?</div>
    <div class="form-actions">
      <button id="dup-force">✅ Всё равно</button>
      <button class="secondary" id="dup-cancel">Отмена</button>
    </div></div>`;
  ov.hidden = false;
  $("dup-force").onclick = () => submitMeasurement(pef, state.form.mode, state.form.editId, true);
  $("dup-cancel").onclick = async () => { closeForm(); await switchTo("today"); };
}

function renderResult(res) {
  state.form.busy = false;
  const ov = $("form-overlay");
  ov.innerHTML = `<div class="form-box">
    <h3>${res.tod === "morning" ? "☀️ Утро" : "🌙 Вечер"}</h3>
    <div class="form-value ${zoneClass(res.pct)}">${res.pef} <span class="label">${res.pct}%</span></div>
    ${res.diff != null ? `<div class="center label">Изменение: ${res.diff > 0 ? "+" : ""}${res.diff}</div>` : ""}
    <div class="label center">Добавить заметку?</div>
    <div class="form-actions">
      <button id="res-note">📝 Да</button>
      <button class="secondary" id="res-skip">Пропустить</button>
    </div></div>`;
  ov.hidden = false;
  $("res-note").onclick = () => openNote(res.id);
  $("res-skip").onclick = async () => { closeForm(); await switchTo("today"); };
}

function openNote(mid) {
  const ov = $("form-overlay");
  ov.innerHTML = `<div class="form-box">
    <h3>Заметка (до 200 символов)</h3>
    <textarea id="note-input" maxlength="200" rows="3" style="width:100%;box-sizing:border-box"></textarea>
    <div class="form-actions">
      <button id="note-save">Сохранить</button>
      <button class="secondary" id="note-cancel">Отмена</button>
    </div></div>`;
  ov.hidden = false;
  $("note-save").onclick = async () => {
    const text = $("note-input").value;
    try {
      await api(`/api/measurements/${mid}/note`, { method: "POST", body: { note: text } });
      closeForm();
      await switchTo("today");
    } catch (e) { showError(e.message); }
  };
  $("note-cancel").onclick = () => { closeForm(); switchTo("today"); };
}

/* ---- settings tab (parents only) ---- */
async function loadSettings() {
  const s = await api("/api/settings");
  const el = $("screen-settings");
  const h = s.reminder_hours;
  const periods = (await api("/api/export/periods")).months;
  const periodBtns = [`<button class="mini" data-csv="all">Всё время</button>`]
    .concat(periods.slice().reverse().map((p) => `<button class="mini" data-csv="${p}">${p}</button>`))
    .join(" ");
  el.innerHTML = `
    <div class="card"><div class="label">Ребёнок</div><div class="big">${esc(s.child_name)}</div></div>
    <div class="card">
      <div class="label">🎯 Целевая ПСВ</div>
      <div class="big">${s.target_pef} <span class="label">л/мин</span></div>
      <button class="mini" id="set-target">Изменить</button>
    </div>
    <div class="card">
      <div class="label">⏰ Напоминания</div>
      <div class="row"><span>🌅 Ребёнку утром</span><button class="mini" data-hour="child_morning">${h.child_morning}:00</button></div>
      <div class="row"><span>🌙 Ребёнку вечером</span><button class="mini" data-hour="child_evening">${h.child_evening}:00</button></div>
      <div class="row"><span>🌅 Родителям (утро)</span><button class="mini" data-hour="parent_morning">${h.parent_morning}:00</button></div>
      <div class="row"><span>🌙 Родителям (вечер)</span><button class="mini" data-hour="parent_evening">${h.parent_evening}:00</button></div>
    </div>
    <div class="card">
      <div class="label">📥 Экспорт CSV</div>
      <div class="row" style="flex-wrap:wrap;gap:6px">${periodBtns}</div>
    </div>
    <div class="card">
      <div class="label">💾 Бэкап БД</div>
      <button class="mini" id="set-backup">Скачать бэкап</button>
    </div>`;
  $("set-target").onclick = changeTarget;
  $("set-backup").onclick = () => download("/api/backup", "peakflow_backup.db").catch((e) => showError(e.message));
  el.querySelectorAll("[data-csv]").forEach((b) =>
    b.onclick = () => download(`/api/export/csv?period=${b.dataset.csv}`, "peakflow.csv").catch((e) => showError(e.message)));
  el.querySelectorAll("[data-hour]").forEach((b) =>
    b.onclick = () => changeHour(b.dataset.hour, h));
}

function openNumberEditor(title, current, min, max, onSave) {
  const ov = $("form-overlay");
  ov.innerHTML = `<div class="form-box">
    <h3>${esc(title)}</h3>
    <input id="num-input" type="number" inputmode="numeric" min="${min}" max="${max}"
      value="${Number(current)}" style="width:100%;box-sizing:border-box;font-size:22px;padding:8px">
    <div class="label center">Диапазон: ${min}–${max}</div>
    <div class="form-actions">
      <button id="num-save">Сохранить</button>
      <button class="secondary" id="num-cancel">Отмена</button>
    </div></div>`;
  ov.hidden = false;
  const input = $("num-input");
  input.focus();
  $("num-cancel").onclick = () => { ov.hidden = true; ov.innerHTML = ""; };
  $("num-save").onclick = async () => {
    const raw = input.value;
    if (raw === "") { showError(`Диапазон: ${min}–${max}`); return; }
    const val = Number(raw);
    if (!(val >= min && val <= max)) { showError(`Диапазон: ${min}–${max}`); return; }
    ov.hidden = true; ov.innerHTML = "";
    await onSave(val);
  };
}

async function changeTarget() {
  try {
    const cur = await api("/api/settings");
    openNumberEditor("Целевая ПСВ (л/мин)", cur.target_pef, 100, 800, async (val) => {
      try {
        await api("/api/settings/target", { method: "PUT", body: { target_pef: val } });
        await loadSettings();
      } catch (e) { showError(e.message); }
    });
  } catch (e) { showError(e.message); }
}

async function changeHour(key, hours) {
  openNumberEditor("Час напоминания (0–23)", hours[key], 0, 23, async (val) => {
    const body = { ...hours, [key]: val };
    try {
      await api("/api/settings/reminders", { method: "PUT", body });
      await loadSettings();
    } catch (e) { showError(e.message); }
  });
}

async function boot() {
  try {
    const me = await api("/api/me");
    state.role = me.role;
    state.zones = me.zones || state.zones;
    if (me.target_pef) state.target = me.target_pef;
    $("child-name").textContent = me.child_name || "Дневник";
  } catch (e) {
    showError("Откройте приложение через Telegram");
    return;
  }
  await switchTo("today");
}

boot();
