/* Copilot по проектированию ИИ-автоматизации — экран.
   Без фреймворков: состояние приходит с сервера целиком после каждого действия. */
"use strict";

const S = { meta: null, view: null, pid: null, step: 1, screen: "projects", chat: {}, options: {}, reviews: {}, openQ: {} };
/* Ответы уходят по одному: пока Senior Pilot проверяет, следующий ответ не отправляется —
   иначе проверка прежнего ответа теряется («ответ изменили, пока он читал»). */
let answerInFlight = false;
/* Вид рабочей области: классика (исходный), лента, мягкие карточки. Полётный план во всех видах один и тот же. */
const UI_MODES = { classic: "классика", lenta: "лента", cards: "карточки", work: "рабочее место", workdark: "рабочее место · тёмное" };
/* Короткая реплика в поле ответа = команда. Только реплика целиком: фраза «не знаю, как считать, но…» — это ответ, а не команда. */
const INTENTS = [
  [/^(не знаю|незнаю|не знаю пока|\?)$/i, "unknown", "не знаю"],
  [/^(опускаю|опускаю сейчас|пропускаю|пропустить|позже|потом)$/i, "skipped", "опускаю сейчас"],
  [/^(варианты|вариант|подскажи варианты|пример|примеры)$/i, "options", "варианты от Senior Pilot"],
];
function intentOf(text) {
  const t = String(text || "").trim().replace(/[.!…]+$/, "");
  const hit = INTENTS.find(([rx]) => rx.test(t));
  return hit ? { act: hit[1], label: hit[2] } : null;
}
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDT(iso, withTime = true) {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
  if (!m) return String(iso);
  return `${m[3]}.${m[2]}.${m[1]}` + (withTime && m[4] ? ` ${m[4]}:${m[5]}` : "");
}
const VERDICT = { passed: "пройдено", failed: "нужно усилить", manual: "вручную", inconclusive: "не доказано", unreachable: "не проверено: стенд недоступен" };

function store(key, val) { try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (e) { return null; } }

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await r.json(); } catch (e) { /* пустой ответ */ }
  if (!r.ok && r.status !== 503) throw new Error((data && (data.detail || data.error)) || `Ошибка ${r.status}`);
  return data;
}
function toast(msg, ms = 3500) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
const spin = '<span class="spinner"></span>';

/* ---------- «Подсказка»: пояснение по запросу (решения владелицы 05.11.2026) ----------
   На экране остаётся то, что нужно для действия; подсказка, пример, «зачем», правила заполнения и прочие
   пояснения — под кнопкой «Подсказка» рядом с тем, к чему относятся. Сначала был значок «i» — владелица:
   непонятно, что это подсказка, а подсказки и есть ценность Copilot. Теперь лампа и слово «Подсказка»;
   в строках списков (рекомендации, находки, атаки) — одна лампа, слово там повторялось бы в каждой строке.
   Нажатие (и касание на телефоне) раскрывает и сворачивает, фокус с клавиатуры раскрывает, Escape закрывает.
   Наведение ничего не раскрывает. */
const LAMP = '<svg class="info-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 12.5h4M6.6 14.5h2.8M8 1.5a4.5 4.5 0 0 0-2.7 8.1c.5.4.7.9.7 1.4v.3h4V11c0-.5.2-1 .7-1.4A4.5 4.5 0 0 0 8 1.5z"/></svg>';
let INFO_N = 0;
function info(html, label = "Подсказка", compact = false) {
  if (!html) return { btn: "", pop: "" };
  const id = "info-" + (++INFO_N);
  return {
    btn: `<button type="button" class="info-btn${compact ? " compact" : ""}" aria-expanded="false" aria-controls="${id}" aria-label="${esc(label)}" title="${esc(label)}">${LAMP}${compact ? "" : '<span class="info-t">Подсказка</span>'}</button>`,
    pop: `<div class="info-pop" id="${id}" role="note" hidden>${html}</div>`,
  };
}
/* Кнопка и текст одной строкой — когда подсказка стоит сразу за элементом. */
function infoInline(html, label, compact = false) { const i = info(html, label, compact); return i.btn + i.pop; }
const ip = (t) => (t ? `<p>${esc(t)}</p>` : "");
function setInfo(b, open) {
  const pop = document.getElementById(b.getAttribute("aria-controls")); if (!pop) return;
  b.setAttribute("aria-expanded", open ? "true" : "false"); pop.hidden = !open;
  if (!open) delete b.dataset.byFocus;
}
function bindInfoOnce() {
  // перехват на стадии погружения: нажатие на «i» не доходит до строки или карточки, внутри которой стоит значок
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest(".info-btn"); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    setInfo(b, b.getAttribute("aria-expanded") !== "true");
  }, true);
  document.addEventListener("focusin", (e) => {
    const b = e.target; if (!b.classList || !b.classList.contains("info-btn")) return;
    if (b.matches(":focus-visible") && b.getAttribute("aria-expanded") !== "true") { setInfo(b, true); b.dataset.byFocus = "1"; }
  });
  document.addEventListener("focusout", (e) => {
    const b = e.target; if (b.classList && b.classList.contains("info-btn") && b.dataset.byFocus) setInfo(b, false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = [...document.querySelectorAll('.info-btn[aria-expanded="true"]')]; if (!open.length) return;
    e.preventDefault(); e.stopImmediatePropagation();  // сначала закрывается пояснение, выдвижная панель остаётся
    const back = open.find((b) => b === document.activeElement || document.getElementById(b.getAttribute("aria-controls"))?.contains(document.activeElement));
    open.forEach((b) => setInfo(b, false));
    if (back) back.focus({ preventScroll: true });
  }, true);
}
/* Заголовок со значком «i»: текст пояснения — сразу под заголовком, а не внутри него. */
function headInfo(tag, title, html, label) { const i = info(html, label); return `<${tag}>${title}${i.btn}</${tag}>${i.pop}`; }
/* Всё, что поясняет вопрос: зачем он, как ответить, наводящие вопросы, пример, зачем это в проде. */
function qExplain(q, rec) {
  return [q.why ? `<p><b>Зачем:</b> ${esc(q.why)}</p>` : "", ip(q.hint),
    (q.guide || []).length ? `<p class="ip-h">Спросите себя</p><ul>${q.guide.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : "",
    q.no_sample ? ip("Это ваше решение — Senior Pilot предложит варианты, а не готовый ответ.")
      : q.example ? `<div class="ip-ex"><div class="qx-lbl">ПРИМЕР</div>${esc(q.example)}</div>` : "",
    rec && q.mentor_note && prodNote(q) ? `<p><b>Зачем это в проде:</b> ${esc(prodNote(q))}</p>` : ""].join("");
}
/* «Зачем это в проде» без предложений, которые уже сказаны в «Зачем», — каждый факт один раз. */
function prodNote(q) {
  const norm = (x) => x.replace(/[«»"]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const said = new Set((String(q.why || "").match(/[^.!?]+[.!?]?/g) || []).map(norm));
  return (String(q.mentor_note || "").match(/[^.!?]+[.!?]?/g) || []).filter((x) => !said.has(norm(x))).join("").trim();
}

/* ---------- запуск ---------- */
async function init() {
  bindInfoOnce();
  // пояснения постоянных панелей — под значком «i» у заголовка
  const chatHelp = info(ip("Спросите о текущем шаге: как ответить, что выбрать, чем рискуем.") + ip("Например: какой паттерн подойдёт, если входы двух типов?"), "Подсказка: о чём спросить Senior Pilot");
  $("#chatHead").insertAdjacentHTML("beforeend", chatHelp.btn); $("#chatHead").insertAdjacentHTML("afterend", chatHelp.pop);
  const leg = $("#legend"), legHelp = info(leg.outerHTML, "Подсказка: цвета табло"); leg.remove();
  $("#annHead").insertAdjacentHTML("beforeend", legHelp.btn); $("#annHead").insertAdjacentHTML("afterend", legHelp.pop);
  const theme = store("theme"); if (theme) document.documentElement.dataset.theme = theme;
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next; store("theme", next); renderMain();
  };
  document.querySelectorAll(".navbtn[data-view]").forEach((b) => (b.onclick = () => go(b.dataset.view)));
  const uiParam = new URLSearchParams(location.search).get("ui");  // прямая ссылка: /?ui=work — включить вид и запомнить
  if (UI_MODES[uiParam]) store("ui", uiParam);
  // S.uiKey — пункт меню «Вид»; S.ui — раскладка. «Рабочее место · тёмное» = раскладка work + тёмная тема.
  S.uiKey = UI_MODES[store("ui")] ? store("ui") : "classic";
  const applyUi = () => {
    S.ui = S.uiKey === "workdark" ? "work" : S.uiKey;
    const de = document.documentElement; de.dataset.ui = S.ui;
    if (S.uiKey === "workdark") de.dataset.theme = "dark";
    else { const t = store("theme"); if (t) de.dataset.theme = t; else delete de.dataset.theme; }
    $("#uiBtn").textContent = "Дизайн экрана: " + UI_MODES[S.uiKey];
  };
  applyUi();
  $("#uiBtn").onclick = () => { const k = Object.keys(UI_MODES); S.uiKey = k[(k.indexOf(S.uiKey) + 1) % k.length]; store("ui", S.uiKey); applyUi(); closeDrawer(); renderAll(); };
  // выдвижная панель для вида «рабочее место»: рекомендации (левая колонка) и Senior Pilot (правая)
  for (const sel of [".leftcol", ".rightcol"]) {
    const col = $(sel); if (!col) continue;
    col.insertAdjacentHTML("afterbegin", `<div class="drawer-bar"><span>${sel === ".leftcol" ? "Сводка и рекомендации" : "Senior Pilot"}</span><button class="b-quiet" data-close-drawer>Закрыть ✕</button></div>`);
  }
  document.querySelectorAll("[data-close-drawer]").forEach((b) => (b.onclick = closeDrawer));
  // строка «Рекомендации · Спросить Senior Pilot» закреплена под шапкой: высота шапки меняется с шириной окна
  const topH = () => document.documentElement.style.setProperty("--topbar-h", $(".topbar").offsetHeight + "px");
  topH(); addEventListener("resize", topH);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("#chatSend").onclick = sendChat;
  $("#recPlanBtn").onclick = () => go("plan");
  S.meta = await api("GET", "/api/meta");
  const pidParam = new URLSearchParams(location.search).get("pid");  // прямая ссылка на проект: /?ui=work&pid=<id>
  if (pidParam) { store("pid", pidParam); store("screen", "step"); }
  const pid = store("pid");
  if (pid) { try { await loadProject(pid); } catch (e) { store("pid", ""); } }
  if (!S.view) go("projects"); else go(store("screen") || "step");
}

async function loadProject(pid) {
  S.view = await api("GET", `/api/projects/${pid}`);
  if (S.pid !== pid) {
    // чужое состояние прежнего проекта не переносится: проверки, варианты, раскрытые вопросы и чат — свои у каждого
    S.drafts = {}; S.lastEval = null; S.lastAttack = null;
    S.reviews = {}; S.options = {}; S.openQ = {}; S.chat = {};
  }
  S.pid = pid; store("pid", pid);
  // проект запоминается в адресе страницы: у каждой вкладки свой, перезагрузка возвращает свою, а не последнюю чужую
  try {
    const u = new URL(location.href);
    if (u.searchParams.get("pid") !== pid) { u.searchParams.set("pid", pid); history.replaceState(null, "", u); }
  } catch (e) { /* адрес не трогаем — проект всё равно загружен */ }
  const remembered = Number(store("step:" + pid));
  S.step = remembered >= 1 && remembered <= S.meta.steps.length ? remembered : (S.view.project.current_step || S.view.project.start_step || 1);
}

function go(screen) {
  saveDrafts();
  if (screen !== "projects" && screen !== "models" && !S.view) screen = "projects";
  S.screen = screen; store("screen", screen);
  document.querySelectorAll(".navbtn[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === screen);
    if (b.dataset.view === screen) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  renderAll();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function rememberStep() { if (S.pid) store("step:" + S.pid, String(S.step)); }

function renderAll() {
  document.documentElement.dataset.screen = S.screen || "";  // ширина рабочей области зависит от вкладки
  rememberStep();
  renderRecs();
  const dom = S.view && (S.meta.domains || []).find((d) => d.id === S.view.project.domain);
  $("#projectChip").textContent = S.view ? S.view.project.name + (dom ? " · " + dom.title : "") : "проект не выбран";
  renderFlightplan(); renderInstruments(); renderRisks(); renderHints(); renderChat(); renderMain();
}

function stepMeta(id) { return S.meta.steps.find((s) => s.id === Number(id)); }
function stepStatus(id) { return S.view ? S.view.steps.find((s) => s.step === Number(id)) : null; }
function answerOf(step, qid) { return S.view?.project.answers?.[String(step)]?.[qid] || null; }
/* Маршрут берётся из ядра: число шагов, коды и slug — из /api/meta, номера шагов в экране не держатся. */
function stepBySlug(slug) { return S.meta.steps.find((s) => s.slug === slug); }
function stepByCode(code) { return S.meta.steps.find((s) => s.code === code); }
function qStep(qid) { return (S.meta.steps.find((s) => s.questions.some((q) => q.id === qid)) || {}).id; }
function segOf(s, qid) { const segs = s.segments || []; const i = segs.findIndex((g) => g.questions.includes(qid)); return i < 0 ? null : i; }
/* Разделитель отрезков внутри шага: тонкая линия и подпись с числом закрытых вопросов отрезка. */
function segDivider(s, i) {
  const g = (s.segments || [])[i]; if (!g) return "";
  const done = g.questions.filter((id) => { const r = answerOf(s.id, id); return r && r.status !== "needs_detail"; }).length;
  return `<div class="seg-div"><span><b>${esc(g.title)}</b></span><span class="seg-n">отрезок ${i + 1} из ${s.segments.length} · закрыто ${done} из ${g.questions.length}</span></div>`;
}
function segFirst(s, q) { const g = segOf(s, q.id); return g !== null && s.segments[g].questions[0] === q.id; }
/* Где на маршруте появляются числа: первый шаг не на «наброске»; если в нём есть отрезок «без чисел» — следующий отрезок. */
function numbersFrom() {
  const st = S.meta.steps.find((x) => x.level !== "sketch"); if (!st) return "позже";
  const k = st.no_numbers_segment, g = k && (st.segments || [])[k];
  return g ? `в отрезке «${g.title}» шага ${st.code}` : `с шага ${st.code}`;
}
function prefillBtn(q, cls = "b-link") {
  if (q.type === "table" && (q.prefill_from || []).length)
    return `<button class="${cls}" data-prefill="${q.id}" title="Строки из ваших прошлых ответов — поправьте и нажмите «Ответить»">Заполнить из прошлых ответов</button>`;
  if (q.prefill_pattern)
    return `<button class="${cls}" data-prefill="${q.id}" title="Черновик из карточки выбранного паттерна — поправьте и нажмите «Ответить»">Заполнить из карточки паттерна</button>`;
  return "";
}
function emptyTable(q) { const t = requiredRowsTemplate(q); return t.length ? t : [Array(q.columns.length).fill("")]; }
function requiredRowsTemplate(q) { return (q.required_rows || []).map((l) => [l, ...Array(q.columns.length - 1).fill("")]); }

/* ---------- полётный план ---------- */
function renderFlightplan() {
  const el = $("#flightplan");
  const n = S.meta.steps.length, W = 1200, y = 64, x0 = 60, dx = (W - 2 * x0) / Math.max(n - 1, 1), r = 17, C = 2 * Math.PI * r;
  const xs = S.meta.steps.map((_, i) => x0 + i * dx);
  const oneRow = dx >= 160;  // точек мало — названия целиком в одну строку; много — укорачиваются и идут через одну
  let svg = `<svg viewBox="0 0 ${W} 134" role="img" aria-label="Маршрут из ${n} шагов">`;
  for (const p of S.meta.phases) {
    const a = Math.max(8, xs[p.steps[0] - 1] - dx / 2 + 8), b = Math.min(W - 8, xs[p.steps[p.steps.length - 1] - 1] + dx / 2 - 8);
    const single = p.steps.length === 1;
    svg += `<line class="fp-phase-line" x1="${a}" x2="${b}" y1="16" y2="16"/><text class="fp-phase" x="${single ? b : (a + b) / 2}" y="12" text-anchor="${single ? "end" : "middle"}">${esc(p.title)}</text>`;
  }
  svg += `<path class="fp-route" d="M${xs[0]} ${y} L${xs[n - 1]} ${y}"/>`;
  const lamps = S.view ? Object.fromEntries(S.view.annunciator.map((l) => [l.step, l.light])) : {};
  for (let i = 0; i < n - 1; i++) {
    const a = stepStatus(i + 1), b = stepStatus(i + 2);
    if (a && b && a.label !== "not_started" && b.label !== "not_started") svg += `<path class="fp-route-done" d="M${xs[i] + r} ${y} L${xs[i + 1] - r} ${y}"/>`;
  }
  S.meta.steps.forEach((s, i) => {
    const st = stepStatus(s.id) || { closed_pct: 0, label: "not_started" };
    const cur = S.view && S.step === s.id ? " current" : "";
    const off = C * (1 - st.closed_pct / 100);
    const lampColor = { warning: "var(--lamp-warning)", caution: "var(--lamp-caution)", off: "var(--lamp-ok)", idle: "var(--border)" }[lamps[s.id] || "idle"];
    const title = oneRow || s.title.length <= 24 ? s.title : s.title.slice(0, 23) + "…";
    const ty = y + (oneRow || !(i % 2) ? 34 : 54);
    svg += `<g class="wp ${st.label}${cur}" data-step="${s.id}" tabindex="0"><title>${esc(s.code)} ${esc(s.title)} — закрыто ${st.closed_pct} %</title>
      <circle class="ring-bg" cx="${xs[i]}" cy="${y}" r="${r}"/>
      <circle class="ring" cx="${xs[i]}" cy="${y}" r="${r}" stroke-dasharray="${C}" stroke-dashoffset="${off}" transform="rotate(-90 ${xs[i]} ${y})"/>
      <text class="code" x="${xs[i]}" y="${y + 4}" text-anchor="middle">${String(s.id).padStart(2, "0")}</text>
      <circle class="lamp" cx="${xs[i] + 13}" cy="${y - 13}" r="5" fill="${lampColor}"/>
      <text class="title" x="${xs[i]}" y="${ty}" text-anchor="middle">${esc(title)}</text></g>`;
  });
  if (S.view) {
    const px = xs[S.step - 1];
    svg += `<g class="plane" transform="translate(${px - 11} ${y - 50})"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z" transform="rotate(180 11.5 12)"/></g>`;
  }
  svg += "</svg>";
  el.innerHTML = svg;
  el.querySelectorAll(".wp").forEach((g) => {
    const open = () => { if (!S.view) return toast("Сначала выберите или создайте проект"); S.step = Number(g.dataset.step); go("step"); };
    g.onclick = open; g.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
}

/* ---------- приборы ---------- */
function renderInstruments() {
  if (S.ui !== "classic") return renderInstrumentsNew();
  const annBox = $("#annunciator").closest(".instruments"); if (annBox) annBox.querySelector("h2 .h-t").textContent = "Табло рекомендаций";
  const ov = S.view?.overall || { prod_readiness_pct: 0, closed_pct: 0, steps_ready: 0, steps_started: 0, warnings: 0, cautions: 0 };
  const R = 74, cx = 100, cy = 92, L = Math.PI * R, val = L * (1 - ov.prod_readiness_pct / 100);
  let ticks = "";
  for (let k = 0; k <= 10; k++) {
    const a = Math.PI * (1 - k / 10), x1 = cx + Math.cos(a) * (R + 10), y1 = cy - Math.sin(a) * (R + 10), x2 = cx + Math.cos(a) * (R + (k % 5 ? 14 : 18)), y2 = cy - Math.sin(a) * (R + (k % 5 ? 14 : 18));
    ticks += `<line class="tick" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  $("#gauge").innerHTML = `<svg class="gauge" width="200" height="112" viewBox="0 0 200 112">
    ${ticks}
    <path class="g-bg" d="M${cx - R} ${cy} A${R} ${R} 0 0 1 ${cx + R} ${cy}" fill="none" stroke-width="12" stroke-linecap="round"/>
    <path class="g-val" d="M${cx - R} ${cy} A${R} ${R} 0 0 1 ${cx + R} ${cy}" fill="none" stroke-width="12" stroke-linecap="round" stroke-dasharray="${L}" stroke-dashoffset="${val}"/>
    <text class="g-num" x="${cx}" y="${cy - 8}" text-anchor="middle">${ov.prod_readiness_pct} %</text>
    <text class="g-lbl" x="${cx}" y="${cy + 12}" text-anchor="middle">полнота ответов и документов</text></svg>`;
  $("#readouts").innerHTML = `
    <div class="readout"><b>${ov.closed_pct} %</b><span>вопросов закрыто</span></div>
    <div class="readout"><b>${ov.steps_ready}/${S.meta.steps.length}</b><span>шагов готово</span></div>
    <div class="readout"><b style="color:var(--lamp-warning)">${ov.warnings}</b><span>важно до запуска</span></div>
    <div class="readout"><b style="color:var(--lamp-caution)">${ov.cautions}</b><span>на развитие</span></div>`;
  const lamps = S.view?.annunciator || S.meta.steps.map((s) => ({ step: s.id, code: s.code, title: s.title, light: "idle", count: 0 }));
  $("#annunciator").innerHTML = lamps.map((l) => `<div class="lamp ${l.light}" data-step="${l.step}" title="${esc(l.title)}${l.count ? " — открыто " + l.count : ""}">${l.code}<small>${esc(l.title)}</small></div>`).join("");
  $("#annunciator").querySelectorAll(".lamp").forEach((d) => (d.onclick = () => { if (S.view) { S.step = Number(d.dataset.step); go("step"); } }));
}

/* Прибор в духе Apple Gauge и табло «где я на маршруте» — для видов «лента» и «карточки». */
function renderInstrumentsNew() {
  const ov = S.view?.overall || { prod_readiness_pct: 0, closed_pct: 0, steps_ready: 0, steps_started: 0, warnings: 0, cautions: 0 };
  const pct = ov.prod_readiness_pct, R = 80, cx = 110, cy = 104, L = Math.PI * R, end = Math.PI * (1 - pct / 100);
  const mx = cx + R * Math.cos(end), my = cy - R * Math.sin(end);
  $("#gauge").innerHTML = `<svg class="gauge g2" width="208" height="128" viewBox="0 0 220 134" aria-label="Готовность к проду ${pct} %">
    <path class="g2-track" d="M${cx - R} ${cy} A${R} ${R} 0 0 1 ${cx + R} ${cy}" fill="none" stroke-width="12" stroke-linecap="round"/>
    <path class="g2-val" d="M${cx - R} ${cy} A${R} ${R} 0 0 1 ${cx + R} ${cy}" fill="none" stroke-width="12" stroke-linecap="round" stroke-dasharray="${L}" stroke-dashoffset="${L * (1 - pct / 100)}"/>
    ${pct > 0 ? `<circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="3" fill="#fff"/>` : ""}
    <text class="g2-end" x="${cx - R}" y="${cy + 22}" text-anchor="middle">0</text><text class="g2-end" x="${cx + R}" y="${cy + 22}" text-anchor="middle">100</text>
    <text class="g2-num" x="${cx - 6}" y="${cy - 6}" text-anchor="middle">${pct}<tspan class="g2-pct" dx="2">%</tspan></text>
    <text class="g2-lbl" x="${cx}" y="${cy + 20}" text-anchor="middle">готовность</text></svg>`;
  $("#readouts").innerHTML = `
    <div class="readout"><b>${ov.closed_pct} %</b><span>вопросов закрыто</span></div>
    <div class="readout"><b>${ov.steps_ready}/${S.meta.steps.length}</b><span>шагов готово</span></div>` + (S.ui === "work" ? "" : `
    <div class="readout"><b style="color:var(--lamp-warning)">${ov.warnings}</b><span>важно до запуска</span></div>
    <div class="readout"><b style="color:var(--lamp-caution)">${ov.cautions}</b><span>на развитие</span></div>`);
  const lamps = S.view?.annunciator || S.meta.steps.map((s) => ({ step: s.id, code: s.code, title: s.title, light: "idle", count: 0 }));
  $("#annunciator").innerHTML = lamps.map((l) => {
    const now = S.view && l.step === S.step, st = now ? "now" : l.light === "idle" ? "ahead" : "past";
    const dot = !now && (l.light === "warning" || l.light === "caution") ? `<i class="rt-dot ${l.light}"></i>` : "";
    return `<div class="rt ${st}" data-step="${l.step}" title="${esc(l.title)}${l.count ? " — открыто " + l.count : ""}">${dot}<span class="rt-c">${st === "past" ? "✓ " : ""}${l.code}</span><span class="rt-t">${now ? "сейчас здесь" : esc(l.title)}</span></div>`;
  }).join("");
  const box = $("#annunciator").closest(".instruments"); if (box) box.querySelector("h2 .h-t").textContent = "Где я на маршруте";
  $("#annunciator").querySelectorAll(".rt").forEach((d) => (d.onclick = () => { if (S.view) { S.step = Number(d.dataset.step); go("step"); } }));
}

function openDrawer(which) { document.documentElement.dataset.drawer = which; }
function closeDrawer() { delete document.documentElement.dataset.drawer; }

/* Сводка вместо тёмной «кабины» — одна строка под полётным планом (вид «рабочее место»). */
function summaryStrip(s, ss) {
  const ov = S.view?.overall || {};
  // каждый факт — один раз на экране: текущая точка уже в полётном плане, «на развитие» — у ссылки на рекомендации
  // прогресс показывает полётный план — в строке только то, чего там нет
  return `<div class="sx-strip">${ov.warnings ? `<span class="sx-warn">важно до запуска <b>${ov.warnings}</b></span>` : ""}
    <span class="sx-links"><button class="b-link sx-btn" data-drawer-open="recs" title="Открыть рекомендации: что сделать дальше и план развития"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M3 8h10M3 12h6"/></svg>Рекомендации${ov.cautions ? ` <span class="sx-count">${ov.cautions}</span>` : ""}</button><button class="b-link sx-btn" data-drawer-open="chat" title="Открыть чат с Senior Pilot: задать вопрос по проекту"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z"/></svg>Спросить Senior Pilot</button></span></div>`;
}

/* ---------- вид «рабочее место»: шаг как диалог с ИИ-агентом ---------- */
function msgSenior(html, cls = "") { return `<div class="wk-msg wk-sp ${cls}"><div class="wk-who">Senior Pilot</div><div class="wk-text">${html}</div></div>`; }
function msgMe(html) { return `<div class="wk-msg wk-me"><div class="wk-text">${html}</div></div>`; }
function viewWorkHtml(s, ss, phase, openId) {
  let t = "", segShown = -1;
  const idx = openId ? s.questions.findIndex((q) => q.id === openId) : s.questions.length;
  const segMark = (qid) => { const g = segOf(s, qid); if (g === null || g === segShown) return ""; segShown = g; return segDivider(s, g); };
  s.questions.forEach((q, i) => {
    const rec = answerOf(s.id, q.id);
    if (q.id === openId) return;
    if (!rec && i > idx) return;          // будущие вопросы не показываем — их видно в мини-маршруте
    if (!rec) return;
    t += segMark(q.id) + `<div class="wk-pair ${rec.status === "answered" ? "ok" : "open"}" data-openq="${q.id}" title="Изменить ответ">` + msgSenior(esc(q.text), "wk-past")
      + `<span class="wk-lead" aria-hidden="true"></span>` + msgMe(rec.status === "answered" || rec.status === "needs_detail" ? esc(shortValue(q, rec.value)) : `<span class="muted">${Q_WORD[rec.status]}</span>`);
    const rv = rec.senior;
    if (rv && rv.comment) t += msgSenior(esc(rv.comment) + (rv.verdict === "needs_detail" && rv.follow_up ? `<div class="wk-follow">${esc(rv.follow_up)}</div>` : ""), "wk-past");
    t += "</div>";
  });
  let current = "", composer = "";
  if (openId) {
    const q = s.questions.find((x) => x.id === openId), rec = answerOf(s.id, q.id);
    const rv0 = S.reviews[`${s.id}.${q.id}`] || (rec && rec.senior ? { review: rec.senior } : rec && rec.senior_error ? { error: rec.senior_error } : null);
    const rv = rv0 ? { ...rv0, qid: q.id, synthesis: rec?.synthesis } : null;
    const textual = q.type === "text" || q.type === "number";
    const qi = info(qExplain(q, rec), "Подсказка к вопросу");
    current = segMark(q.id) + msgSenior(`<div class="wk-q">${esc(q.text)}${qi.btn}</div>${qi.pop}
      ${textual ? "" : `<div class="qx-input">${inputHtml(s.id, q, rec)}</div>`}
      ${prefillBtn(q, "b-link")}
      ${reviewHtml(rv)}<div id="opts-${q.id}">${optionsHtml(q, S.options[`${s.id}.${q.id}`])}</div>`, "wk-current") + `<div id="q-${q.id}"></div>`;
    const v = rec && (rec.status === "answered" || rec.status === "needs_detail") && textual ? rec.value : "";
    const ci = info((textual ? "" : ip(`Заполните ${q.type === "table" ? "таблицу" : "выбор"} выше и отправьте. Поле — для комментария к ответу.`))
      + ip(`Вместо ответа можно написать «не знаю» или «опускаю» — вопрос закроется и уйдёт в рекомендации${textual ? "; «варианты» — Senior Pilot предложит варианты ответа" : ""}.`)
      + ip("Enter — отправить, Shift + Enter — новая строка."), "Подсказка: как ответить");
    composer = `<div class="wk-composer">
      ${textual ? `<textarea id="in-${q.id}" rows="2" placeholder="Ваш ответ">${esc(v ?? "")}</textarea>`
        : `<textarea id="wk-cmd" rows="1" placeholder="Комментарий к ответу"></textarea>`}
      <input type="hidden" id="note-${q.id}" value="">
      ${ci.btn}<button class="wk-send" data-send="${q.id}" title="Отправить (Enter)">↑</button><span class="small muted" id="busy-${q.id}"></span></div>${ci.pop}`;
  } else {
    current = msgSenior(`Все вопросы шага закрыты. Итог шага и документ — ниже, или переходите к следующему шагу маршрута.`);
  }
  return summaryStrip(s, ss) + stepHeadNew(s, ss, phase, openId) + `<div class="wk-thread">${t}${current}</div>${composer}
    <details class="wk-more"><summary>Итог шага, документ и отраслевые примеры</summary>${domainHtml(s)}<div id="stepPanel"></div><div id="debrief"></div></details>`;
}
function bindWork(s, m) {
  m.querySelectorAll("[data-drawer-open]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); openDrawer(b.dataset.drawerOpen); }));
  const send = m.querySelector("[data-send]"); if (!send) return;
  const q = s.questions.find((x) => x.id === send.dataset.send);
  const box = $(`#in-${q.id}`)?.tagName === "TEXTAREA" ? $(`#in-${q.id}`) : $("#wk-cmd");
  const go = () => {
    const it = intentOf(box?.value);
    if (it) {
      if (box.id === `in-${q.id}`) box.value = "";
      toast(`Понял как: ${it.label}`);
      return it.act === "options" ? askOptions(s, q) : submitAnswer(s, q, it.act);
    }
    if (box && box.id === "wk-cmd") $(`#note-${q.id}`).value = box.value;
    submitAnswer(s, q, "answered");
  };
  send.onclick = go;
  if (box) {
    box.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); } };
    box.focus({ preventScroll: true });
  }
  const th = m.querySelector(".wk-current"); if (th) th.scrollIntoView({ block: "nearest" });
  // окно ввода не липкое: показываем его вместе с вопросом, если оба помещаются на экран, — верх вопроса не уезжает
  const cp = m.querySelector(".wk-composer");
  if (th && cp && cp.getBoundingClientRect().bottom - th.getBoundingClientRect().top < innerHeight - $(".topbar").offsetHeight - 24)
    cp.scrollIntoView({ block: "nearest" });
}

/* ---------- вопросы шага в видах «лента» и «карточки» ---------- */
const Q_WORD = { answered: "отвечен", needs_detail: "дополнить", unknown: "не знаю", skipped: "опущен" };
function firstOpenQ(s) {
  const open = (q) => { const r = answerOf(s.id, q.id); return !r || r.status === "needs_detail"; };
  return (s.questions.find((q) => q.required !== false && open(q)) || s.questions.find(open) || {}).id || null;
}
function qRing(st, size = 16) {
  const c = size / 2, r = c - 1.5, C = 2 * Math.PI * r;
  const base = `<circle class="qr-b" cx="${c}" cy="${c}" r="${r}"${st === "skip" ? ' stroke-dasharray="2 2"' : ""}/>`;
  const inner = st === "done" ? `<circle class="qr-a" cx="${c}" cy="${c}" r="${r}"/><path class="qr-k" d="M${c - 3.2} ${c + .2}l2.2 2.2 4-4.4"/>`
    : st === "active" ? `<circle class="qr-a" cx="${c}" cy="${c}" r="${r}" stroke-dasharray="${C}" stroke-dashoffset="${C * .65}" transform="rotate(-90 ${c} ${c})"/>`
    : st === "unknown" ? `<text class="qr-q" x="${c}" y="${c + 3.2}" text-anchor="middle">?</text>`
    : st === "skip" ? `<line class="qr-d" x1="${c - 3}" y1="${c}" x2="${c + 3}" y2="${c}"/>` : "";
  return `<svg class="qr" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">${base}${inner}</svg>`;
}
function ringState(rec, open) {
  if (open) return "active";
  return { answered: "done", needs_detail: "active", unknown: "unknown", skipped: "skip" }[rec?.status] || "idle";
}
function shortValue(q, v) {
  if (q.type === "table") return `${(v || []).length} строк(и) в таблице`;
  const t = Array.isArray(v) ? v.join("; ") : String(v ?? "");
  return t.length > 160 ? t.slice(0, 157) + "…" : t;
}
/* Прогресс вопросов шага без мелких значков: «Вопрос 3 из 7» и тонкая шкала из сегментов (рабочее место). */
function qScale(s, openId) {
  const n = s.questions.length, i = openId ? s.questions.findIndex((q) => q.id === openId) : n - 1;
  const seg = s.questions.map((q, k) => {
    const r = answerOf(s.id, q.id), st = k === i && openId ? "now" : r ? (r.status === "answered" ? "done" : "open") : "todo";
    return `<i class="qs-seg ${st}" data-openq="${q.id}" title="${esc(q.text)}"></i>`;
  }).join("");
  return `<div class="qscale"><span class="qs-txt">${openId ? `Вопрос <b>${i + 1}</b> из ${n}` : `Все ${n} вопросов закрыты`}</span><span class="qs-bar">${seg}</span></div>`;
}
function miniRoute(s, openId) {
  if (S.ui === "work") return qScale(s, openId);
  const n = s.questions.length, gap = 28, w = (n - 1) * gap + 20;
  let svg = `<svg class="mini" width="${w}" height="20" viewBox="0 0 ${w} 20"><line class="mini-t" x1="10" y1="10" x2="${w - 10}" y2="10"/>`;
  s.questions.forEach((q, i) => {
    const x = 10 + i * gap, st = ringState(answerOf(s.id, q.id), q.id === openId), C = 2 * Math.PI * 6;
    svg += `<g class="mini-p" data-openq="${q.id}"><title>${esc(q.text)}</title><circle class="mini-b" cx="${x}" cy="10" r="6"/>`
      + (st === "done" ? `<circle class="mini-a" cx="${x}" cy="10" r="6"/><circle class="mini-dot" cx="${x}" cy="10" r="2.2"/>` : "")
      + (st === "active" ? `<circle class="mini-a" cx="${x}" cy="10" r="6" stroke-dasharray="${C}" stroke-dashoffset="${C * .65}" transform="rotate(-90 ${x} 10)"/>` : "")
      + (st === "unknown" ? `<text class="mini-q" x="${x}" y="13" text-anchor="middle">?</text>` : "")
      + (st === "skip" ? `<line class="mini-d" x1="${x - 3}" y1="10" x2="${x + 3}" y2="10"/>` : "") + "</g>";
  });
  return svg + "</svg>";
}
function stepAbout(s) { return info(`<p><b>Цель шага:</b> ${esc(s.goal)}</p>${s.lead ? `<p><b>Ведущий вопрос:</b> ${esc(s.lead)}</p>` : ""}`, "Подсказка: цель шага"); }
function stepHeadNew(s, ss, phase, openId) {
  const a = stepAbout(s);
  if (S.ui === "work") return `<div class="sx-head wk-head"><div class="wk-titlerow"><h1 class="sx-title">${esc(s.title)}${a.btn}</h1>${qScale(s, openId)}</div>${a.pop}</div>`;
  return `<div class="sx-head"><div class="sx-meta"><span class="sx-code">${s.code} · ${esc(phase.title)}</span><span>закрыто ${ss.closed} из ${ss.required}</span></div>
    <h1 class="sx-title">${esc(s.title)}${a.btn}</h1>${a.pop}${miniRoute(s, openId)}</div>`;
}
function qNew(s, q, open) {
  const rec = answerOf(s.id, q.id), st = rec ? rec.status : "missing";
  const code = `${s.id}.${s.questions.indexOf(q) + 1}`;
  const word = Q_WORD[st] || (q.required === false ? "по желанию" : "не начат");
  if (!open) {
    const short = rec && (st === "answered" || st === "needs_detail") ? shortValue(q, rec.value) : rec?.note || "";
    return `<div class="qx" id="q-${q.id}" data-openq="${q.id}" tabindex="0"><div class="qx-row">${qRing(ringState(rec, false))}<span class="qx-code">${code}</span>
      <div class="qx-body"><div class="qx-text">${esc(q.text)}</div>${short ? `<div class="qx-ans">${esc(short)}</div>` : ""}</div><span class="qx-word">${word}</span></div></div>`;
  }
  const rv0 = S.reviews[`${s.id}.${q.id}`] || (rec && rec.senior ? { review: rec.senior } : rec && rec.senior_error ? { error: rec.senior_error } : null);
  const rv = rv0 ? { ...rv0, qid: q.id, synthesis: rec?.synthesis } : null;
  const qi = info(qExplain(q, rec), "Подсказка к вопросу");
  return `<div class="qx open" id="q-${q.id}"><div class="qx-row">${qRing("active")}<span class="qx-code">${code}</span><div class="qx-body">
    <div class="qx-text">${esc(q.text)}${qi.btn}</div>${qi.pop}
    ${prefillBtn(q, "b-link")}
    <div class="qx-input">${inputHtml(s.id, q, rec)}</div>
    <details class="qx-more"${rec?.note || rec?.status === "needs_detail" ? " open" : ""}><summary>Комментарий к ответу</summary><textarea id="note-${q.id}" style="min-height:48px">${esc(rec?.note || "")}</textarea></details>
    <div class="qx-acts"><button class="b-main" data-act="answered" data-q="${q.id}" title="Ctrl + Enter">Ответить<span class="kbd">Ctrl ↵</span></button>
      <button class="b-quiet" data-act="unknown" data-q="${q.id}" title="Вопрос закроется, в рекомендациях появится строка с владельцем">Не знаю</button>
      <button class="b-quiet" data-act="skipped" data-q="${q.id}" title="Осознанно откладываю — вернусь позже">Опускаю сейчас</button>
      <span class="small muted" id="busy-${q.id}"></span><button class="b-link qx-opts" data-opts="${q.id}">Варианты ›</button></div>
    ${reviewHtml(rv)}<div id="opts-${q.id}">${optionsHtml(q, S.options[`${s.id}.${q.id}`])}</div>
  </div></div></div>`;
}

/* ---------- рекомендации Senior Pilot ---------- */
const REC_STATUS = { new: "новая", in_progress: "в работе", done: "сделано", deferred: "отложено", after_launch: "после запуска", dismissed: "не актуально" };
const STAGES = ["тень", "человек в контуре", "самостоятельно"];
// на последнем шаге откладывать некуда — «Отложить» ведёт в план после запуска
function onLastStep() { return S.view && Number(S.view.project.current_step) >= S.meta.steps.length; }
// «важно до запуска» на последнем шаге не откладывается — закрыть или принять риском, путь в «Путь по шагам»
function deferLabel(r) { return r.nav ? null : !onLastStep() ? "Отложить" : r.level === "warning" ? null : "В план после запуска"; }
// путь по шагам в copilot — у каждой рекомендации: где делать, что сделать, как понять, что готово
function pathHtml(r, open = false) {
  const p = r.path || r.how || [];
  return p.length ? `<details class="qx-more rec-path"${open ? " open" : ""}><summary>Путь по шагам</summary><ol>${p.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></details>` : "";
}
/* Заголовок рекомендации; зачем она, путь по шагам и пример — под «i». */
function recTitle(r) {
  const en = r.enriched, p = r.path || r.how || [];
  const i = info(ip(en?.why_here || r.why) + (p.length ? `<p class="ip-h">Путь по шагам</p><ol>${p.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : "")
    + (!en && r.example ? ip(r.example) : ""), "Подсказка к рекомендации", true);
  return `<div class="rec-title">${esc(r.title)}${i.btn}</div>${i.pop}`;
}
async function loadRecs() {
  if (!S.view) return null;
  try { S.recs = await api("GET", `/api/projects/${S.pid}/recs`); } catch (e) { S.recs = null; }
  return S.recs;
}
function recHtml(r, full = false) {
  if (S.ui !== "classic") return recHtmlQuiet(r, full);
  const en = r.enriched;
  return `<div class="rec st-${r.status}" data-rec="${esc(r.id)}">
    <div><span class="pill grey mono">${esc(r.code)}</span> ${full ? `<span class="pill ${r.status === "done" ? "ok" : r.status === "in_progress" ? "" : "grey"}">${REC_STATUS[r.status] || r.status}</span>` : ""}</div>
    ${recTitle(r)}
    ${en ? `<ol>${(en.plan || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>${en.example ? `<div class="senior-box" style="margin-top:6px"><b>Образец:</b> ${esc(en.example)}</div>` : ""}${en.praise ? `<div class="small muted">${esc(en.praise)}</div>` : ""}` : ""}
    <div class="btnrow">
      ${r.status !== "in_progress" && r.status !== "done" ? `<button class="btn" data-ra="start">В работу</button>` : ""}
      ${r.status !== "done" ? `<button class="btn btn-primary" data-ra="done">Сделано</button>` : `<button class="btn" data-ra="reopen">Вернуть</button>`}
      ${r.status !== "done" ? `${deferLabel(r) && r.status !== "after_launch" ? `<button class="btn btn-ghost" data-ra="defer">${deferLabel(r)}</button>` : ""}<button class="btn btn-ghost" data-ra="dismiss">Не актуально</button>` : ""}
      ${!en && r.status !== "done" ? `<button class="btn" data-ra="enrich" title="Один вызов модели">План от Senior Pilot</button>` : ""}
    </div></div>`;
}
/* Рекомендация в новых видах: одно действие и меню «⋯» — не больше полутора кнопок на блок. */
function recHtmlQuiet(r, full, open = false) {
  const en = r.enriched;
  const main = r.status === "in_progress" ? ["done", "Сделано"] : r.status === "done" ? null : ["start", "В работу"];
  const dl = deferLabel(r);
  const menu = [r.status !== "done" && r.status !== "in_progress" ? ["done", "Сделано"] : null, r.status !== "done" && r.status !== "after_launch" && dl ? ["defer", dl] : null,
    r.status !== "done" ? ["dismiss", "Не актуально"] : ["reopen", "Вернуть"], !en && r.status !== "done" ? ["enrich", "План от Senior Pilot"] : null].filter(Boolean);
  return `<div class="rec rq st-${r.status}" data-rec="${esc(r.id)}">
    <div class="rq-top"><span class="rq-code">${esc(r.code)}</span>${full ? `<span class="rq-st">${REC_STATUS[r.status] || r.status}</span>` : ""}</div>
    ${open ? `<div class="rec-title">${esc(r.title)}</div><div class="rec-why">${esc(en?.why_here || r.why)}</div>${pathHtml(r)}` : recTitle(r)}
    ${en ? `<ol>${(en.plan || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : ""}
    <div class="rq-acts">${main ? `<button class="b-main" data-ra="${main[0]}">${main[1]}</button>` : ""}
      <details class="rq-menu"><summary title="Другие действия">⋯</summary><div class="rq-pop">${menu.map(([a, t]) => `<button class="b-quiet" data-ra="${a}">${t}</button>`).join("")}</div></details></div></div>`;
}
/* Рекомендация строкой чек-листа: «заголовок …… статус», по клику — зачем, как сделать, одно действие и «⋯». */
function recRow(r) {
  const inner = recHtmlQuiet(r, false, true);  // строку уже раскрыли — зачем и путь видны сразу
  return `<details class="pg-item" data-rec="${esc(r.id)}"><summary class="pg-row"><span class="pg-t">${esc(r.title)}</span><span class="wk-lead"></span><span class="pg-st st-${r.status}">${REC_STATUS[r.status] || r.status}</span></summary>
    <div class="pg-body">${inner.replace(/^<div class="rec rq[^>]*>/, "<div>")}</div></details>`;
}
function bindRecs(root) {
  root.querySelectorAll("[data-rec]").forEach((card) => card.querySelectorAll("[data-ra]").forEach((b) => (b.onclick = async () => {
    const rid = card.dataset.rec, action = b.dataset.ra;
    try {
      if (action === "enrich") {
        b.innerHTML = spin; const r = await api("POST", `/api/projects/${S.pid}/recs/${rid}/enrich`);
        if (r.error) toast("Senior Pilot недоступен: " + r.error, 5000);
      } else {
        let reason = "", owner = "";
        if (action === "dismiss") { reason = await askReason(); if (!reason) return; }
        if (action === "defer" && onLastStep()) { const a = await askAfterLaunch(); if (!a) return; reason = a.stage; owner = a.owner; }
        const r = await api("POST", `/api/projects/${S.pid}/recs/${rid}`, { action, reason, owner });
        toast(r.message, 4500);
      }
    } catch (e) { toast(e.message, 5000); }
    await renderRecs(); if (S.screen === "plan") renderMain();
  })));
}
function askAfterLaunch() {
  return new Promise((resolve) => {
    const root = $("#modalRoot");
    root.innerHTML = `<div class="modal-bg"><div class="modal">${headInfo("h2", "В план после запуска", ip("Пункт уйдёт из «Следующего шага» в «План развития» → «После запуска» и попадёт в каркас защиты и паспорт проекта."), "Подсказка: план после запуска")}
      <label class="small" style="display:block;margin-top:10px">Закрыть до перехода на стадию</label>
      <select id="alStage">${STAGES.map((s) => `<option>${s}</option>`).join("")}</select>
      <label class="small" style="display:block;margin-top:8px">Владелец (роль)</label>
      <input id="alOwner" placeholder="например, инженер сопровождения">
      <div class="small err" id="alErr"></div>
      <div class="btnrow"><button class="btn btn-primary" id="alOk">Сохранить</button><button class="btn" id="alCancel">Отмена</button></div></div></div>`;
    $("#alOk").onclick = () => {
      const owner = $("#alOwner").value.trim();
      if (!owner) { $("#alErr").textContent = "Нужен владелец — роль, не «все» и не «система»."; return; }
      const stage = $("#alStage").value; root.innerHTML = ""; resolve({ stage, owner });
    };
    $("#alCancel").onclick = () => { root.innerHTML = ""; resolve(null); };
  });
}
function askReason() {
  return new Promise((resolve) => {
    const root = $("#modalRoot");
    root.innerHTML = `<div class="modal-bg"><div class="modal">${headInfo("h2", "Почему не актуально?", ip("Причина сохранится — рекомендация не вернётся без нового повода."), "Подсказка: причина")}
      <textarea id="dismissReason" style="margin-top:8px"></textarea>
      <div class="btnrow"><button class="btn btn-primary" id="drOk">Сохранить</button><button class="btn" id="drCancel">Отмена</button></div></div></div>`;
    $("#drOk").onclick = () => { const v = $("#dismissReason").value.trim(); root.innerHTML = ""; resolve(v); };
    $("#drCancel").onclick = () => { root.innerHTML = ""; resolve(""); };
  });
}
async function renderRecs() {
  const box = $("#recNext"); if (!box) return;
  if (!S.view) { box.innerHTML = '<div class="muted small">Проект не выбран.</div>'; $("#recCount").textContent = ""; return; }
  const v = await loadRecs(); if (!v) return;
  const open = v.counts.new + v.counts.in_progress;
  $("#recCount").textContent = open ? `· открыто ${open}, сделано ${v.counts.done}` : `· сделано ${v.counts.done}`;
  const list = S.ui === "classic" ? v.next : v.next.slice(0, 1);  // в новых видах — одна, самая полезная
  box.innerHTML = list.length ? list.map((r) => recHtml(r)).join("") : '<div class="muted small">Открытых рекомендаций нет.</div>';
  bindRecs(box);
}
async function viewPlan(m) {
  const v = await loadRecs();
  if (!v) { m.innerHTML = '<div class="card">Проект не выбран.</div>'; return; }
  const codes = Object.keys(v.plan).sort((a, b) => (stepByCode(a)?.id || 0) - (stepByCode(b)?.id || 0));
  if (S.ui === "work") {  // как на «Маршруте»: заголовок, счётчики текстом без нулей, рекомендации строками чек-листа
    const counts = Object.entries(v.counts).filter(([, n]) => n).map(([k, n]) => `${REC_STATUS[k] || k} <b>${n}</b>`).join(" · ");
    m.innerHTML = `<div class="sx-head wk-head"><div class="wk-titlerow"><h1 class="sx-title">План развития</h1><span class="pg-meta">${counts}</span></div></div>
      ${codes.map((c) => `<section class="pg-sec"><div class="pg-sec-h"><span class="pg-code">${esc(c)}</span>${esc(stepByCode(c)?.title || "")}</div>
        ${v.plan[c].map((r) => recRow(r)).join("")}</section>`).join("") || '<div class="muted">Рекомендаций пока нет.</div>'}
      ${(v.after_launch || []).length ? `<section class="pg-sec"><div class="pg-sec-h">После запуска</div>${v.after_launch.map((r) => recRow(r)).join("")}</section>` : ""}
      ${(v.dismissed_feed || []).length ? `<section class="pg-sec"><div class="pg-sec-h">Не актуально</div>${v.dismissed_feed.map((d) => `<div class="pg-row"><span class="pg-t">${esc(d.title)}</span><span class="wk-lead"></span><span class="pg-st">${esc(d.reason || "без причины")}</span></div>`).join("")}</section>` : ""}
      ${v.done_feed.length ? `<section class="pg-sec"><div class="pg-sec-h">Закрыто</div>${v.done_feed.map((d) => `<div class="pg-row pg-done"><span class="pg-t">${esc(d.title)}</span><span class="wk-lead"></span><span class="pg-st">${esc(d.praise || "закрыто")}</span></div>`).join("")}</section>` : ""}`;
    bindRecs(m);
    return;
  }
  m.innerHTML = `<div class="card">${headInfo("h1", "План развития проекта", ip("Рекомендации Senior Pilot по шагам. Три самых полезных сейчас — в панели «Следующий шаг от Senior Pilot» на маршруте.") + ip("«Сделано» проверяется: если условие ещё на месте, рекомендация останется в работе с подсказкой."), "Подсказка: план развития")}
    <div class="step-meta">${Object.entries(v.counts).map(([k, n]) => `<span class="pill grey">${REC_STATUS[k] || k}: ${n}</span>`).join("")}</div></div>
    ${codes.map((c) => `<div class="card-output"><h2>${esc(c)} · ${esc(stepByCode(c)?.title || "")}</h2>${v.plan[c].map((r) => recHtml(r, true)).join("")}</div>`).join("") || '<div class="card-output muted">Рекомендаций пока нет.</div>'}
    ${(v.after_launch || []).length ? `<div class="card-output"><h2>После запуска</h2>${v.after_launch.map((r) => recHtml(r, true)).join("")}</div>` : ""}
    ${(v.dismissed_feed || []).length ? `<div class="card-output"><h2>Не актуально</h2>${v.dismissed_feed.map((d) => `<div class="small">${esc(d.title)} — ${esc(d.reason || "без причины")}</div>`).join("")}</div>` : ""}
    ${v.done_feed.length ? `<div class="card-output"><h2>Закрыто</h2>${v.done_feed.map((d) => `<div class="small">${esc(d.title)} — ${esc(d.praise || "закрыто")}</div>`).join("")}</div>` : ""}`;
  bindRecs(m);
}

function renderRisks() {
  const ul = $("#risklist");
  if (!S.view) { ul.innerHTML = '<li class="muted">Проект не выбран.</li>'; $("#riskCount").textContent = ""; return; }
  const rs = [...S.view.risks].sort((a, b) => (a.level === b.level ? a.step - b.step : a.level === "warning" ? -1 : 1));
  $("#riskCount").textContent = rs.length ? `· ${rs.length}` : "";
  if (!rs.length) { ul.innerHTML = '<li class="muted">Открытых рекомендаций нет.</li>'; return; }
  const showAll = S.showAllRisks;
  ul.innerHTML = rs.slice(0, showAll ? rs.length : 8).map((r) => `<li>
      <span class="lvl ${r.level}">${r.code}</span><b>${esc(r.reason)}</b>${infoInline(ip(r.text), "Подсказка к рекомендации", true)}
      <input class="owner-input" data-rid="${esc(r.id)}" placeholder="Кто займётся (роль)" value="${esc(r.owner)}">
    </li>`).join("") + (rs.length > 8 ? `<li><button class="btn" id="riskMore">${showAll ? "Свернуть" : "Показать все (" + rs.length + ")"}</button></li>` : "");
  ul.querySelectorAll(".owner-input").forEach((i) => (i.onchange = async () => {
    S.view = await api("POST", `/api/projects/${S.pid}/risk`, { id: i.dataset.rid, owner: i.value }); toast("Ответственный сохранён");
  }));
  const more = $("#riskMore"); if (more) more.onclick = () => { S.showAllRisks = !S.showAllRisks; renderRisks(); };
}

function renderHints() {
  const dom = (S.meta.domains || []).find((d) => d.id === S.view?.project.domain);
  const pool = [...(dom ? (dom.hints || []).map((h) => ({ ...h, title: h.title + " · " + dom.title })) : []), ...S.meta.hints];
  const hs = pool.sort((a, b) => (a.step === S.step ? -1 : 0) - (b.step === S.step ? -1 : 0)).slice(0, 3);
  // находка — заголовком; история и вывод из неё — под «i»
  $("#hints").innerHTML = hs.map((h) => { const i = info(`<p>${esc(h.text)}</p><p><i>${esc(h.lesson)}</i></p>`, "Подсказка: " + h.title, true); return `<div class="hintcard"><b>${esc(h.title)}${i.btn}</b>${i.pop}</div>`; }).join("");
}

/* ---------- Senior Pilot: чат ---------- */
function renderChat() {
  const msgs = S.chat[S.step] || [];
  $("#chat").innerHTML = msgs.length ? msgs.map((m) => `<div class="msg ${m.who}">${esc(m.text)}</div>`).join("") : "";  // пустой разговор — без подписи; о чём спросить, сказано под «i» у заголовка
  $("#chat").scrollTop = 1e6;
}
async function sendChat() {
  const text = $("#chatInput").value.trim();
  if (!text) return;
  if (!S.view) return toast("Сначала выберите проект");
  (S.chat[S.step] = S.chat[S.step] || []).push({ who: "me", text });
  $("#chatInput").value = ""; renderChat(); $("#chatStatus").innerHTML = spin + " Senior Pilot думает";
  try {
    const r = await api("POST", `/api/projects/${S.pid}/ask`, { step: S.step, message: text });
    S.chat[S.step].push({ who: "senior", text: r.error ? "Senior Pilot недоступен: " + r.error : r.answer });
  } catch (e) { S.chat[S.step].push({ who: "senior", text: "Ошибка: " + e.message }); }
  $("#chatStatus").textContent = ""; renderChat();
}

/* ---------- основной экран ---------- */
function saveDrafts() {
  if (!S.meta || !S.renderedStep || !document.querySelector("#main .q")) return;
  const s = stepMeta(S.renderedStep); if (!s) return;
  S.drafts = S.drafts || {};
  for (const q of s.questions) {
    if (!$(`#in-${q.id}`)) continue;
    const v = readInput(q), note = $(`#note-${q.id}`)?.value ?? "";
    S.drafts[`${s.id}.${q.id}`] = { v: JSON.stringify(v), note };
  }
}
function restoreDrafts(s) {
  if (!S.drafts) return;
  for (const q of s.questions) {
    const d = S.drafts[`${s.id}.${q.id}`]; if (!d || !$(`#in-${q.id}`)) continue;  // свёрнутый вопрос — черновик ждёт раскрытия
    const rec = answerOf(s.id, q.id);
    const saved = rec && (rec.status === "answered" || rec.status === "needs_detail") ? JSON.stringify(rec.value) : JSON.stringify(q.type === "table" || q.type === "multi" ? [] : "");
    if (d.v !== saved && d.v !== JSON.stringify(q.type === "table" ? emptyTable(q) : "")) {
      const v = JSON.parse(d.v);
      fillInput(q, q.type === "table" ? v.map((r) => r.join(" · ")).join("\n") : Array.isArray(v) ? v.join("; ") : v);
      const card = $(`#q-${q.id}`); if (card) card.classList.add("draft");
    }
    const n = $(`#note-${q.id}`); if (n && d.note && !n.value) n.value = d.note;
  }
}

function renderMain() {
  saveDrafts();
  S.renderedStep = null;
  const m = $("#main");
  const views = { projects: viewProjects, step: viewStep, plan: viewPlan, dev: viewDev, defense: viewDefense, docs: viewDocs, models: viewModels };
  // найдено 12.10.2026: экран, чей запрос к серверу вернулся позже, перерисовывал уже открытый другой экран —
  // меню показывало «Проекты», а на странице были «Модели». Запоздавшая отрисовка теперь ничего не пишет.
  const seq = (S.renderSeq = (S.renderSeq || 0) + 1);
  const guard = new Proxy(m, {
    get: (t, k) => { const v = t[k]; return typeof v === "function" ? v.bind(t) : v; },
    set: (t, k, v) => { if (seq === S.renderSeq) t[k] = v; return true; },
  });
  m.innerHTML = "";
  // запоздавшая отрисовка падает на привязке кнопок, которых уже нет, — это не ошибка; ошибка текущего экрана видна
  Promise.resolve((views[S.screen] || viewProjects)(guard)).catch((e) => { if (seq === S.renderSeq) throw e; });
}

/* проекты */
async function viewProjects(m) {
  const list = await api("GET", "/api/projects");
  const opts = S.meta.steps.map((s) => `<option value="${s.id}">${s.code} · ${esc(s.title)}</option>`).join("");
  const npi = info(ip(`Copilot ведёт проект от идеи до сдачи заказчику по маршруту из ${S.meta.steps.length} шагов: ${S.meta.steps.map((s) => s.code + " " + s.title).join(", ")}.`)
    + ip("Начать можно с любого — то, что уже сделано, вносится ответами шагов, а пропущенное подсвечивается рисками.")
    + ip("Пример названия: протокол совещания из записи. Отрасль нужна для примеров."), "Подсказка: новый проект");
  if (S.ui === "work") {  // как на «Маршруте»: список строками, проект открывается по клику; новый проект — ниже
    m.innerHTML = `<div class="sx-head wk-head"><div class="wk-titlerow"><h1 class="sx-title">Проекты</h1><span class="pg-meta">${list.length ? `всего <b>${list.length}</b>` : ""}</span></div></div>
      <section class="pg-sec">${list.length ? list.map((p) => `<div class="pg-row pg-link" data-open="${esc(p.id)}" tabindex="0"><span class="pg-code">${p.route_done ? "✓" : esc(stepMeta(p.current_step)?.code || "")}</span><span class="pg-t">${esc(p.name)}</span><span class="wk-lead"></span><span class="pg-st">${p.broken ? "файл повреждён" : esc(fmtDT(p.updated))}</span></div>`).join("") : '<div class="muted">Пока нет проектов.</div>'}</section>
      <section class="pg-sec"><div class="pg-sec-h">Новый проект${npi.btn}</div>${npi.pop}
        <input type="text" id="npName" placeholder="Название проекта">
        <div class="two" style="margin-top:8px"><select id="npStart">${opts}</select><select id="npDomain"><option value="">Отрасль — другая</option>${(S.meta.domains || []).map((d) => `<option value="${esc(d.id)}">${esc(d.title)}</option>`).join("")}</select></div>
        <textarea id="npDesc" placeholder="Коротко о задаче — по желанию" style="margin-top:8px;min-height:56px"></textarea>
        <div class="btnrow"><button class="b-main" id="npCreate">Создать и начать</button></div></section>`;
  } else m.innerHTML = `<div class="card"><h1>Новый проект${npi.btn}</h1>${npi.pop}
      <div class="two"><div><label class="small muted">Название проекта</label><input type="text" id="npName"></div>
      <div><label class="small muted">С какого шага начинаем</label><select id="npStart">${opts}</select></div>
      <div><label class="small muted">Отрасль</label><select id="npDomain"><option value="">Другая отрасль</option>${(S.meta.domains || []).map((d) => `<option value="${esc(d.id)}">${esc(d.title)}</option>`).join("")}</select></div></div>
      <label class="small muted" style="display:block;margin-top:8px">Коротко о задаче (по желанию)</label><textarea id="npDesc"></textarea>
      <div class="btnrow"><button class="btn btn-primary" id="npCreate">Создать и начать</button></div></div>
    <div class="card-output"><h2>Проекты</h2>${list.length ? list.map((p) => `<div class="hintcard"><b>${esc(p.name)}</b>
      <span class="muted small">${p.broken ? "" : "обновлён " + esc(fmtDT(p.updated))}${p.route_done ? " · маршрут пройден" : " · шаг " + esc(stepMeta(p.current_step)?.code || "")}</span>
      <div class="btnrow"><button class="btn" data-open="${esc(p.id)}">Открыть</button><button class="btn btn-ghost" data-arch="${esc(p.id)}">В архив</button></div></div>`).join("") : '<div class="muted">Пока нет проектов.</div>'}</div>`;
  $("#npCreate").onclick = async () => {
    try {
      S.view = await api("POST", "/api/projects", { name: $("#npName").value, start_step: Number($("#npStart").value), description: $("#npDesc").value, domain: $("#npDomain").value });
      S.pid = S.view.project.id; store("pid", S.pid); S.step = S.view.project.start_step; go("step");
    } catch (e) { toast(e.message); }
  };
  m.querySelectorAll("[data-open]").forEach((b) => {
    b.onclick = async () => { await loadProject(b.dataset.open); go("step"); };
    b.onkeydown = (e) => { if (e.key === "Enter") b.onclick(); };
  });
  m.querySelectorAll("[data-arch]").forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    const p = list.find((x) => x.id === b.dataset.arch); if (!p) return;
    if (!confirm(`Убрать проект «${p.name}» в архив? Папка проекта сохранится в data/_archive_projects.`)) return;
    try {
      await api("DELETE", `/api/projects/${encodeURIComponent(p.id)}`);
      toast("Проект в архиве"); viewProjects(m);
    } catch (err) { toast(err.message, 5000); }
  }));
}

/* шаг маршрута */
function statusPill(q, rec) {
  const st = rec ? rec.status : "missing";
  const map = { answered: ["ok", "Ответ принят"], needs_detail: ["warn", "Senior Pilot подсказывает, что добавить"], unknown: ["warn", "Не знаю — в рекомендациях"],
    skipped: ["warn", "Опущено сейчас — в рекомендациях"], missing: q.required ? ["grey", "Нет ответа"] : ["grey", "По желанию"] };
  const [cls, txt] = map[st];
  return `<span class="pill ${cls}">${txt}</span>`;
}

function inputHtml(step, q, rec) {
  const v = rec && (rec.status === "answered" || rec.status === "needs_detail") ? rec.value : null;
  const id = `in-${q.id}`;
  const extra = q.extra_text ? `<input type="text" class="extra-text" id="extra-${q.id}" placeholder="${esc(q.extra_text)}" title="${esc(q.extra_text)}" value="${esc(rec?.extra || "")}">` : "";
  if (q.type === "choice") return `<div class="opts" id="${id}">${q.options.map((o) => `<label><input type="radio" name="${id}" value="${esc(o)}" ${v === o ? "checked" : ""}> ${esc(o)}</label>`).join("")}</div>${extra}`;
  if (q.type === "multi") return `<div class="opts" id="${id}">${q.options.map((o) => `<label><input type="checkbox" value="${esc(o)}" ${(v || []).includes(o) ? "checked" : ""}> ${esc(o)}</label>`).join("")}</div>${extra}`;
  if (q.type === "number") return `<input type="number" id="${id}" value="${esc(v ?? "")}">`;
  if (q.type === "table") {
    const rows = v && v.length ? v : requiredRowsTemplate(q);  // пустая таблица — сразу с обязательными строками ядра
    const n = Math.max(rows.length, 1);
    const body = Array.from({ length: n }, (_, i) => rowHtml(q, rows[i] || [])).join("");
    return `<div class="table-scroll"><table class="grid" id="${id}"><thead><tr>${q.columns.map((c) => `<th>${esc(c)}</th>`).join("")}<th></th></tr></thead><tbody>${body}</tbody></table></div>
      <div class="btnrow"><button class="btn" data-addrow="${q.id}">+ строка</button>${infoInline(`<p>Нужно строк: не меньше ${q.min_rows || 1}${(q.required_rows || []).length ? "; обязательные: " + q.required_rows.map((x) => "«" + esc(x) + "»").join(", ") : ""}.</p><p>Можно вставить строки из Excel — в первую ячейку.</p>`, "Подсказка: как заполнить таблицу")}</div>`;
  }
  return `<textarea id="${id}">${esc(v ?? "")}</textarea>`;
}
function rowHtml(q, row) {
  return `<tr>${q.columns.map((_, j) => `<td><input type="text" value="${esc(row[j] || "")}"></td>`).join("")}<td><button class="rowdel" title="Удалить строку">✕</button></td></tr>`;
}
function readInput(q) {
  const el = $(`#in-${q.id}`);
  if (q.type === "choice") { const c = el.querySelector("input:checked"); return c ? c.value : ""; }
  if (q.type === "multi") return [...el.querySelectorAll("input:checked")].map((i) => i.value);
  if (q.type === "table") return [...el.querySelectorAll("tbody tr")].map((tr) => [...tr.querySelectorAll("input")].map((i) => i.value.trim()));
  return el.value;
}
function fillInput(q, text) {
  /* Возвращает, куда легло: "value" — в сам ответ, "note" — в комментарий (для выбора, если вариант
     не совпал), false — некуда. Раньше выбор молча не срабатывал, а сообщение говорило «добавлено». */
  const el = $(`#in-${q.id}`);
  if (q.type === "table") {
    const rows = String(text).split(/\n+/).filter((l) => l.trim()).map((l) => l.split(/\s*[·\t|]\s*/));
    el.querySelector("tbody").innerHTML = rows.map((r) => rowHtml(q, r)).join("") || rowHtml(q, []);
    bindTable(q);
    return "value";
  }
  if (q.type === "choice") {
    const t = String(text).trim(), low = t.toLowerCase();
    const opts = [...el.querySelectorAll("input")];
    // вариант ищется в обе стороны: ответ содержит вариант, вариант содержит ответ, или совпадает начало
    const match = opts.find((i) => t.includes(i.value))
      || opts.find((i) => i.value.toLowerCase().includes(low))
      || opts.find((i) => low.startsWith(i.value.toLowerCase().slice(0, 12)));
    if (match) { match.checked = true; match.dispatchEvent(new Event("change", { bubbles: true })); return "value"; }
    return putNote(q, t) ? "note" : false;   // выбранного варианта нет — текст уходит в комментарий к ответу
  }
  if (q.type === "multi") {
    const t = String(text), low = t.toLowerCase();
    const opts = [...el.querySelectorAll("input")];
    const hit = opts.filter((i) => t.includes(i.value) || i.value.toLowerCase().includes(low));
    hit.forEach((i) => { i.checked = true; i.dispatchEvent(new Event("change", { bubbles: true })); });
    if (hit.length) return "value";
    return putNote(q, t) ? "note" : false;
  }
  el.value = text;
  return "value";
}
function putNote(q, text) {
  const note = $(`#note-${q.id}`);
  if (!note) return false;
  const t = String(text).trim(); if (!t) return false;
  note.value = note.value.trim() ? `${note.value.trim()}\n${t}` : t;
  const d = note.closest("details"); if (d) d.open = true;  // спрятанный комментарий раскрывается: иначе не видно, что записали
  note.focus();
  return true;
}
function bindTable(q) {
  const el = $(`#in-${q.id}`); if (!el) return;
  el.querySelectorAll(".rowdel").forEach((b) => (b.onclick = () => { if (el.querySelectorAll("tbody tr").length > 1) b.closest("tr").remove(); }));
  const first = el.querySelector("tbody tr td input");
  el.querySelectorAll("tbody tr td:first-child input").forEach((inp) => (inp.onpaste = (e) => {
    const t = e.clipboardData.getData("text");
    if (t.includes("\t") || t.includes("\n")) { e.preventDefault(); fillInput(q, t.replace(/\r/g, "")); }
  }));
  return first;
}

async function viewStep(m) {
  const s = stepMeta(S.step), ss = stepStatus(S.step);
  S.renderedStep = s.id;
  const phase = S.meta.phases.find((p) => p.steps.includes(s.id));
  const pillCls = { ready: "ok", closed_with_risks: "warn", in_progress: "", not_started: "grey" }[ss.label];
  const pillTxt = { ready: "Готово", closed_with_risks: "Закрыто, есть рекомендации", in_progress: "В работе", not_started: "Не начат" }[ss.label];
  const about = stepAbout(s), chk = info(ip("«Не знаю» и «Опускаю сейчас» закрывают вопрос и уходят в рекомендации для развития."), "Подсказка: как закрывается вопрос");
  let html = `<div class="card"><div class="step-head"><div><div class="step-code">${s.code} · ${esc(phase.title)}</div><h1>${esc(s.title)}${about.btn}</h1>${about.pop}
      </div><div class="step-meta"><span class="pill" title="${esc((S.meta.levels || {})[s.level]?.expect || "")}">Уровень: ${esc((S.meta.levels || {})[s.level]?.title || "—")}</span><span class="pill ${pillCls}">${pillTxt}</span><span class="pill grey">Документ: ${esc(s.artifact)}</span></div></div>
      <div class="progress"><div style="width:${ss.closed_pct}%"></div></div>
      <div class="small muted" style="margin-top:6px">Предполётный чек-лист: закрыто ${ss.closed} из ${ss.required} обязательных, полных ответов ${ss.full}.${chk.btn}</div>${chk.pop}</div>`;
  const openId = S.ui !== "classic" ? (S.openQ[s.id] !== undefined ? S.openQ[s.id] : firstOpenQ(s)) : null;
  if (S.ui === "work") html = viewWorkHtml(s, ss, phase, openId);
  else if (S.ui !== "classic") html = stepHeadNew(s, ss, phase, openId) + `<div class="qx-list">` + s.questions.map((q) => (segFirst(s, q) ? segDivider(s, segOf(s, q.id)) : "") + qNew(s, q, q.id === openId)).join("") + `</div>`;
  for (const q of S.ui === "classic" ? s.questions : []) {
    if (segFirst(s, q)) html += segDivider(s, segOf(s, q.id));
    const rec = answerOf(s.id, q.id);
    const st = rec ? rec.status : "missing";
    const rv0 = S.reviews[`${s.id}.${q.id}`] || (rec && rec.senior ? { review: rec.senior } : rec && rec.senior_error ? { error: rec.senior_error } : null);
    const rv = rv0 ? { ...rv0, qid: q.id, synthesis: rec?.synthesis } : null;
    const qi = info(qExplain(q, rec), "Подсказка к вопросу");
    html += `<div class="q st-${st} ${q.required ? "required" : ""}" id="q-${q.id}">
      <div class="q-top"><div class="q-text">${esc(q.text)}${qi.btn}</div>${statusPill(q, rec)}</div>${qi.pop}
      ${prefillBtn(q) ? `<div class="btnrow">${prefillBtn(q, "btn btn-ghost")}</div>` : ""}
      ${inputHtml(s.id, q, rec)}
      <details class="hint"${rec?.note || rec?.status === "needs_detail" ? " open" : ""}><summary>Комментарий к ответу</summary><textarea id="note-${q.id}" style="min-height:48px">${esc(rec?.note || "")}</textarea></details>
      <div class="btnrow">
        <button class="btn btn-primary" data-act="answered" data-q="${q.id}">Ответить</button>
        <button class="btn btn-ghost" data-act="unknown" data-q="${q.id}" title="Вопрос закроется, а в рекомендациях появится строка с владельцем">Не знаю</button>
        <button class="btn btn-ghost" data-act="skipped" data-q="${q.id}" title="Осознанно откладываю — вернусь позже, рекомендация фиксируется">Сознательно опускаю сейчас</button>
        <button class="btn" data-opts="${q.id}">Варианты от Senior Pilot</button>
        <span class="small muted" id="busy-${q.id}"></span>
      </div>
      ${reviewHtml(rv)}
      <div id="opts-${q.id}">${optionsHtml(q, S.options[`${s.id}.${q.id}`])}</div>
    </div>`;
  }
  if (S.ui !== "work") html += domainHtml(s) + `<div id="stepPanel"></div><div id="debrief"></div>`;
  m.innerHTML = html;
  if (S.ui === "work") bindWork(s, m);
  for (const q of s.questions) if (q.type === "table") bindTable(q);
  m.querySelectorAll("[data-addrow]").forEach((b) => (b.onclick = () => {
    const q = s.questions.find((x) => x.id === b.dataset.addrow);
    $(`#in-${q.id} tbody`).insertAdjacentHTML("beforeend", rowHtml(q, [])); bindTable(q);
  }));
  m.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => submitAnswer(s, s.questions.find((x) => x.id === b.dataset.q), b.dataset.act)));
  m.querySelectorAll("[data-opts]").forEach((b) => (b.onclick = () => askOptions(s, s.questions.find((x) => x.id === b.dataset.opts))));
  bindMentor(s);
  bindOptionButtons(s);
  restoreDrafts(s);
  // лента и карточки: свёрнутый вопрос или точка мини-маршрута раскрывает вопрос; Ctrl+Enter — «Ответить»
  m.querySelectorAll("[data-openq]").forEach((el) => {
    const open = () => { saveDrafts(); S.openQ[s.id] = el.dataset.openq; renderMain(); const c = $(`#q-${el.dataset.openq}`); if (c) c.scrollIntoView({ block: "center" }); };
    el.onclick = open; el.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
  const openBox = m.querySelector(".qx.open");
  if (openBox) openBox.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); openBox.querySelector('[data-act="answered"]').click(); } };
  const dsel = $("#domainSel"), dbox = $("#domainBox");
  if (dsel) dsel.onchange = () => { S.domainView = dsel.value; S.domainOpen = true; renderMain(); };
  if (dbox) dbox.ontoggle = () => { S.domainOpen = dbox.open; };
  renderStepPanel(s);
  renderDebrief(s);
}

function domainHtml(s) {
  const doms = S.meta.domains || [];
  if (!doms.length) return "";
  const cur = S.domainView || S.view.project.domain || doms[0].id;
  const d = doms.find((x) => x.id === cur) || doms[0];
  const sketch = s.level === "sketch";
  const ex = sketch ? [] : (d.steps && (d.steps[s.id] || d.steps[String(s.id)])) || [];
  const di = info(ip("Обезличенные примеры из похожих проектов.") + ip(d.summary)
    + (sketch ? ip(`На этом шаге проект только рождается — примеры с числами появятся ${numbersFrom()}.`) : "")
    + ip("Числа в примерах — ориентир по конкретике из чужих проектов, а не факты вашего. Свои числа берите из своих следов."), "Подсказка: отраслевые примеры");
  return `<details class="card-output" id="domainBox" ${S.domainOpen ? "open" : ""}><summary class="disclosure"><b>Как это выглядит в отрасли</b></summary>
    <div class="btnrow"><select id="domainSel">${doms.map((x) => `<option value="${esc(x.id)}" ${x.id === d.id ? "selected" : ""}>${esc(x.title)}${x.id === S.view.project.domain ? " — отрасль проекта" : ""}</option>`).join("")}</select>${di.btn}${di.pop}</div>
    ${ex.map((x) => `<div class="senior-box" style="background:var(--accent-soft)">${esc(x)}</div>`).join("") || '<div class="muted small">Для этого шага примеров нет.</div>'}</details>`;
}

function reviewHtml(rv) {
  if (!rv) return "";
  if (rv.error) return `<div class="senior-box need">Senior Pilot не проверял ответ: ${esc(rv.error)}. Ответ сохранён.</div>`;
  const r = rv.review; if (!r) return "";
  const need = r.verdict === "needs_detail";
  const qid = rv.qid || "";
  const syn = rv.synthesis;
  return `<div class="senior-box ${need ? "need" : ""}"><b>Senior Pilot:</b> ${esc(r.comment)}
    ${need && r.follow_up ? `<div style="margin-top:6px"><b>Вопрос:</b> ${esc(r.follow_up)}</div>` : ""}
    ${need && (r.follow_options || []).length ? `<div class="btnrow">${r.follow_options.map((o, i) => `<button class="btn btn-ghost" data-fo="${esc(qid)}" data-i="${i}">${esc(o)}</button>`).join("")}${infoInline(ip("Вариант добавится в ответ — поправьте его или напишите свой."), "Подсказка: варианты дополнения")}</div>` : ""}
    ${need && r.sample ? `<div style="margin-top:6px"><b>Образец — заполните пропуски:</b><div class="ex">${esc(r.sample)}</div><div class="btnrow"><button class="btn" data-sample="${esc(qid)}">Взять образец в ответ</button></div></div>` : ""}
    ${syn ? `<div style="margin-top:6px" class="small"><b>Записано:</b> ${esc(syn.text)}</div>`
      : !need && r.synthesis ? `<div style="margin-top:6px"><b>Записываю так:</b> <span id="syn-${esc(qid)}">${esc(r.synthesis)}</span> Верно?
        <div class="btnrow"><button class="btn btn-primary" data-syn-ok="${esc(qid)}">Верно</button><button class="btn btn-ghost" data-syn-edit="${esc(qid)}">Поправить</button></div></div>` : ""}</div>`;
}
function bindMentor(s) {
  const qOf = (id) => s.questions.find((x) => x.id === id);
  const revOf = (q) => { const rec = answerOf(s.id, q.id); return (S.reviews[`${s.id}.${q.id}`] || {}).review || rec?.senior || {}; };
  document.querySelectorAll("[data-fo]").forEach((b) => (b.onclick = () => {
    const q = qOf(b.dataset.fo); const opt = revOf(q).follow_options[Number(b.dataset.i)];
    const cur = q.type === "text" ? $(`#in-${q.id}`).value.trim() : "";
    const res = fillInput(q, cur ? `${cur} ${opt}` : opt);
    if (res === "value") toast("Вариант добавлен в ответ — поправьте и нажмите «Ответить»");
    else if (res === "note") toast("Вариантов-кнопок у этого выбора нет — текст записан в комментарий к ответу, поправьте и нажмите «Ответить»", 6000);
    else toast("Не удалось подставить вариант: у этого вопроса нет выбора и нет поля комментария — запишите ответ вручную", 6000);
  }));
  document.querySelectorAll("[data-sample]").forEach((b) => (b.onclick = () => {
    const q = qOf(b.dataset.sample); const res = fillInput(q, revOf(q).sample);
    toast(res === "value" ? "Образец в ответе — заполните пропуски […]"
      : res === "note" ? "Образец записан в комментарий к ответу — поправьте его" : "Не удалось подставить образец — запишите ответ вручную", 6000);
  }));
  document.querySelectorAll("[data-syn-ok]").forEach((b) => (b.onclick = () => saveSynthesis(s, qOf(b.dataset.synOk), $(`#syn-${b.dataset.synOk}`).textContent)));
  document.querySelectorAll("[data-syn-edit]").forEach((b) => (b.onclick = () => {
    const id = b.dataset.synEdit, span = $(`#syn-${id}`);
    span.outerHTML = `<textarea id="syn-${id}" style="min-height:48px">${esc(span.textContent)}</textarea>`;
    b.textContent = "Сохранить"; b.onclick = () => saveSynthesis(s, qOf(id), $(`#syn-${id}`).value);
  }));
  document.querySelectorAll("[data-prefill]").forEach((b) => (b.onclick = async () => {
    const q = qOf(b.dataset.prefill);
    const r = await api("GET", `/api/projects/${S.pid}/prefill?step=${s.id}&qid=${encodeURIComponent(q.id)}`);
    if (q.prefill_pattern) {
      if (!r.text) { toast("Сначала выберите паттерн в этом шаге."); return; }
      fillInput(q, r.text); toast("Черновик из карточки паттерна — поправьте под свой случай и нажмите «Ответить»"); return;
    }
    if (!r.rows.length) { toast("Прошлых ответов для этой таблицы пока нет."); return; }
    fillInput(q, r.rows.map((x) => x.join(" · ")).join("\n")); toast("Черновик из прошлых ответов — поправьте и нажмите «Ответить»");
  }));
}
async function saveSynthesis(s, q, text) {
  try {
    await api("POST", `/api/projects/${S.pid}/answer-synthesis`, { step: s.id, qid: q.id, text });
    await loadProject(S.pid); S.step = s.id; toast("Принято."); renderAll();
  } catch (e) { toast(e.message, 5000); }
}

function optionsHtml(q, o) {
  if (!o) return "";
  if (o.loading) return `<div class="senior-box">${spin} Senior Pilot готовит варианты…</div>`;
  if (o.error) return `<div class="senior-box need">Senior Pilot недоступен: ${esc(o.error)}</div>`;
  return o.options.map((x, i) => `<div class="senior-box"><b>${esc(x.title)}</b><div style="white-space:pre-wrap;margin:4px 0">${esc(x.answer)}</div>
    <div class="small">Плюсы: ${esc(x.pros)} · Риски: ${esc(x.cons)}${x.when ? " · Когда: " + esc(x.when) : ""}</div>
    <div class="btnrow"><button class="btn" data-take="${q.id}" data-i="${i}">Взять за основу</button></div></div>`).join("");
}
function bindOptionButtons(s) {
  document.querySelectorAll("[data-take]").forEach((b) => (b.onclick = () => {
    const q = s.questions.find((x) => x.id === b.dataset.take);
    const res = fillInput(q, S.options[`${s.id}.${q.id}`].options[Number(b.dataset.i)].answer);
    toast(res === "value" ? "Вариант перенесён в ответ — поправьте и нажмите «Ответить»"
      : res === "note" ? "Вариант записан в комментарий к ответу — выберите пункт на экране и поправьте текст"
      : "Не удалось перенести вариант — запишите ответ вручную", 6000);
  }));
}

async function submitAnswer(s, q, status) {
  if (answerInFlight) { toast("Дождитесь конца проверки — ответы уходят по одному", 4000); return; }
  const value = status === "answered" ? readInput(q) : null;
  const note = $(`#note-${q.id}`).value;
  const ex = $(`#extra-${q.id}`), extra = ex ? { extra: ex.value } : {};
  answerInFlight = true;
  document.body.classList.add("answer-busy");
  $(`#busy-${q.id}`).innerHTML = status === "answered" ? spin + " Senior Pilot проверяет ответ" : spin;
  try {
    const r = await api("POST", `/api/projects/${S.pid}/answer`, { step: s.id, qid: q.id, status, value, note, ...extra });
    saveDrafts();
    if (S.drafts) delete S.drafts[`${s.id}.${q.id}`];
    S.view = r;
    S.reviews[`${s.id}.${q.id}`] = r.review ? { review: r.review } : r.review_error ? { error: r.review_error } : null;
    // в ленте и карточках после ответа раскрывается следующий открытый вопрос; если Senior Pilot просит дополнить — остаёмся на этом
    if (r.review && r.review.verdict === "needs_detail") S.openQ[s.id] = q.id; else delete S.openQ[s.id];
    renderAll();
    if (r.review) toast(r.review.verdict === "accepted" ? "Принято." : "Senior Pilot подсказал, что добавить, — образец ниже.");
    else if (status !== "answered") toast("Записано. К вопросу можно вернуться позже.");
    const card = $(`#q-${q.id}`); if (card) card.scrollIntoView({ block: "center" });
  } catch (e) { $(`#busy-${q.id}`).textContent = ""; toast(e.message, 5000); }
  finally { answerInFlight = false; document.body.classList.remove("answer-busy"); }
}

async function askOptions(s, q) {
  const key = `${s.id}.${q.id}`;
  S.options[key] = { loading: true }; $(`#opts-${q.id}`).innerHTML = optionsHtml(q, S.options[key]);
  try {
    const r = await api("POST", `/api/projects/${S.pid}/options`, { step: s.id, qid: q.id });
    S.options[key] = r.error ? { error: r.error } : r;
  } catch (e) { S.options[key] = { error: e.message }; }
  $(`#opts-${q.id}`).innerHTML = optionsHtml(q, S.options[key]); bindOptionButtons(s);
}

async function renderStepSynthesis(s) {
  const box = $("#stepSynthesis"); if (!box) return;
  const d = await api("GET", `/api/projects/${S.pid}/synthesis/${s.id}`);
  if (!box.isConnected) return;  // пока шёл запрос, открыли другой шаг — этот блок уже снят с экрана
  if (!d.draft && !d.saved) { box.innerHTML = ""; return; }
  const text = d.saved ? d.saved.text : d.draft;
  const gi = info((d.gate ? ip(`Мягкие ворота ${d.gate.n}: ${d.gate.title} — показывают зрелость, переход не блокируют.${d.gate.question ? " " + d.gate.question : ""}`) : "")
    + (d.saved ? ip("Вывод можно поправить и сохранить.") : ip("Собрано из ваших ответов. Пустые места «[…]» — что ещё не известно. Поправьте и подтвердите.")), "Подсказка: вывод шага");
  box.innerHTML = `<div class="senior-box" style="margin-top:8px"><b>Вывод шага${d.title ? " — " + esc(d.title) : ""}</b>${gi.btn}${gi.pop}
    ${d.saved ? `<div class="small muted">${d.saved.needs_reconfirm ? "Перенесено с прежнего маршрута — прочитайте и подтвердите заново." : "Подтверждено " + esc(fmtDT(d.saved.confirmed)) + "."}</div>` : ""}
    <textarea id="synText" style="min-height:90px;margin-top:6px">${esc(text)}</textarea>
    <div class="btnrow"><button class="btn btn-primary" id="synSave">${d.saved && !d.saved.needs_reconfirm ? "Сохранить правку" : "Подтвердить вывод"}</button>
    ${d.saved ? `<button class="btn btn-ghost" id="synRedo" title="Собрать заново из нынешних ответов">Собрать заново</button>` : ""}</div></div>`;
  $("#synSave").onclick = async () => {
    try { await api("POST", `/api/projects/${S.pid}/synthesis/${s.id}`, { text: $("#synText").value }); toast("Принято."); renderStepSynthesis(s); }
    catch (e) { toast(e.message, 5000); }
  };
  if ($("#synRedo")) $("#synRedo").onclick = () => { $("#synText").value = d.draft; };
}

async function renderDebrief(s) {
  const d = await api("GET", `/api/projects/${S.pid}/debrief/${s.id}`);
  const ss = d.status;
  const prev = s.id > 1 ? `<button class="btn" id="prevStep">← ${stepMeta(s.id - 1).code}</button>` : "";
  const next = s.id < S.meta.steps.length ? `<button class="btn btn-primary" id="nextStep">Следующая точка: ${stepMeta(s.id + 1).code} ${esc(stepMeta(s.id + 1).title)} →</button>` : `<button class="btn btn-primary" id="toDefense">К защите перед ЛПР →</button>`;
  const art = ss.artifact ? `<span class="pill ${ss.artifact_stale ? "warn" : "ok"}">${ss.artifact_stale ? "Документ устарел — ответы менялись" : "Документ собран"}</span>` : "";
  const ri = info(`<p class="ip-h">Критерии готовности шага</p><ul>${s.ready.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`, "Подсказка: критерии готовности шага");
  const bi = ss.can_build ? { btn: "", pop: "" } : info(ip(d.build_block || "Нужен ответ, «Не знаю» или «Опускаю» на каждый обязательный вопрос."), "Подсказка: почему документ не собирается");
  // в строке итога — факты («Сделано…», «Рекомендации…», «Шаг не начат»); что из них следует — под «i»
  const sents = String(d.message || "").match(/[^.]+(\.|$)\s*/g) || [];
  const isFact = (x) => /^\s*(Сделано|Рекомендации|Шаг не начат)/.test(x);
  const mi = info(ip(sents.filter((x) => !isFact(x)).join("").trim()), "Подсказка: что дальше");
  $("#debrief").innerHTML = `<div class="card-output debrief ${d.risks.length ? "has-risks" : ""}"><h2>Итог шага ${s.code}${ri.btn}</h2>${ri.pop}
    <div>${esc(sents.filter(isFact).join("").trim())}${mi.btn}</div>${mi.pop}
    ${d.risks.length ? `<ul class="risklist" style="margin-top:8px">${d.risks.map((r) => `<li><span class="lvl ${r.level}">${r.level === "warning" ? "важно" : "на развитие"}</span><b>${esc(r.reason)}:</b> ${esc(r.text)}${r.question ? infoInline(`<p><b>Вопрос:</b> ${esc(r.question)}</p>`, "Подсказка: к какому вопросу", true) : ""}</li>`).join("")}</ul>` : ""}
    <div id="stepSynthesis"></div>
    <div id="debriefSenior"></div>
    <div class="btnrow"><button class="btn" id="debriefAsk">Комментарий Senior Pilot к шагу</button>
      <button class="btn" id="buildDoc" ${ss.can_build ? "" : "disabled"}>Собрать документ шага</button>${bi.btn}${art}${bi.pop}</div>
    <div class="small muted" id="buildWhy"></div>
    <div class="btnrow" style="justify-content:space-between">${prev}${next}</div></div>`;
  renderStepSynthesis(s);
  $("#debriefAsk").onclick = async () => {
    $("#debriefSenior").innerHTML = `<div class="senior-box">${spin} Senior Pilot смотрит шаг…</div>`;
    const r = await api("GET", `/api/projects/${S.pid}/debrief/${s.id}?comment=true`);
    $("#debriefSenior").innerHTML = r.senior ? `<div class="senior-box"><b>Senior Pilot:</b> ${esc(r.senior.comment)}</div>` : `<div class="senior-box need">Senior Pilot недоступен: ${esc(r.senior_error)}</div>`;
  };
  $("#buildDoc").onclick = async () => {
    try { const r = await api("POST", `/api/projects/${S.pid}/build/${s.id}`); await loadProject(S.pid); S.step = s.id; toast("Собран: " + r.path); renderAll(); }
    catch (e) { $("#buildWhy").textContent = e.message; toast("Документ шага не собран — причины под кнопкой", 6000); }  // причин бывает много: текст целиком остаётся на экране, а не в исчезающем окне
  };
  if ($("#prevStep")) $("#prevStep").onclick = () => { S.step = s.id - 1; go("step"); };
  if ($("#nextStep")) $("#nextStep").onclick = () => { S.step = s.id + 1; go("step"); };
  if ($("#toDefense")) $("#toDefense").onclick = () => go("defense");
}

/* ---------- панели отдельных шагов ---------- */
async function renderMermaid(el, code) {
  if (!window.mermaid) { el.innerHTML = `<pre class="code">${esc(code)}</pre><div class="small muted">Библиотека схем не загрузилась (нет сети) — показан текст схемы.</div>`; return; }
  const dark = (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "neutral", securityLevel: "strict", flowchart: { htmlLabels: false } });
  try { const { svg } = await mermaid.render("m" + Math.random().toString(36).slice(2), code); el.innerHTML = svg; }
  catch (e) { el.innerHTML = `<pre class="code">${esc(code)}</pre><div class="err">Схема не отрисовалась: ${esc(e.message || e)}</div>`; }
}

async function renderStepPanel(s) {
  const p = $("#stepPanel");
  // панели привязаны к slug шага из ядра, а не к его номеру
  if (s.slug === "architecture") {
    const d = await api("GET", `/api/projects/${S.pid}/diagram`);
    if (!p.isConnected) return;  // пока шёл запрос, открыли другой шаг
    const from = stepBySlug("solution"), to = stepBySlug("prototype");
    const pi = info(`<div class="kv"><span class="muted">Кто решает</span><span>${esc(d.card.who_decides)}</span><span class="muted">Где предел</span><span>${esc(d.card.limit)}</span>
      <span class="muted">Что после</span><span>${esc(d.card.after)}</span><span class="muted">Когда брать</span><span>${esc(d.card.when)}</span><span class="muted">Когда не нужен</span><span>${esc(d.card.not_when)}</span></div>`
      + (d.pattern_chosen ? "" : ip("Паттерн ещё не выбран — показан базовый.")), "Подсказка: карточка паттерна");
    const mi = info(ip(`Пустые места заполнятся из ответов шагов ${from ? from.code : ""}–${to ? to.code : ""}.`), "Подсказка: пустые места на схеме");
    p.innerHTML = `<div class="card-output"><h2>Как работает агент — схема по паттерну</h2>
      <div class="kv"><span class="muted">Паттерн</span><span>${esc(d.card.title)}${pi.btn}</span></div>${pi.pop}
      <div class="mermaid-wrap" id="diag" style="margin-top:10px"></div>
      ${d.missing.length ? `<div class="small gap" style="margin-top:6px">На схеме пустые места: ${esc(d.missing.join(", "))}${mi.btn}</div>${mi.pop}` : ""}</div>`;
    renderMermaid($("#diag"), d.mermaid);
  } else if (s.slug === "prototype") {
    p.innerHTML = `<div class="card-output">${headInfo("h2", "Прототип", ip("Схема агента, скелет сценария n8n для наглядности и каркас агента на Python с потолками, очередью и журналом — из ответов проекта."), "Подсказка: что входит в прототип")}
      <div class="btnrow"><button class="btn btn-primary" id="buildProto">Собрать прототип</button><button class="btn" id="toDev">Подключить разработку и адрес агента</button></div><div id="protoOut"></div></div>`;
    $("#toDev").onclick = () => go("dev");
    $("#buildProto").onclick = async () => {
      $("#protoOut").innerHTML = spin;
      const r = await api("POST", `/api/projects/${S.pid}/prototype`);
      const blob = URL.createObjectURL(new Blob([JSON.stringify(r.n8n, null, 2)], { type: "application/json" }));
      $("#protoOut").innerHTML = `<div class="mermaid-wrap" id="protoDiag" style="margin-top:10px"></div>
        <div class="btnrow"><a class="btn" href="${blob}" download="прототип n8n.json">Скачать сценарий n8n</a>${infoInline(ip("Импорт в n8n: Workflows → Import from File. Адреса систем и модель задаются при импорте."), "Подсказка: импорт в n8n")}</div>
        <div class="small">Каркас на Python: <span class="mono">${esc(r.folder)}\\python_agent</span> — ${r.python_files.length} файлов (${esc(r.python_files.join(", "))}).</div>`;
      renderMermaid($("#protoDiag"), r.diagram.mermaid);
    };
  } else if (s.slug === "trials") {
    // две панели одного шага: прогон набора проверок и запуск атак (атаки — по-прежнему двумя ключами)
    const c = await api("GET", `/api/projects/${S.pid}/cost-of-success`);
    if (!p.isConnected) return;
    const last = (S.view.project.eval_runs || []).slice(-1)[0];
    p.innerHTML = `<div class="card-output">${headInfo("h2", "Стоимость успешного результата и прогон набора", ip("Считается из серии прогонов: сумма цен ÷ число успешных. Это число пойдёт на слайд ЛПР без изменений."), "Подсказка: как считается стоимость")}
      <div class="readouts" style="grid-template-columns:repeat(4,1fr)">${[["прогонов", c.runs], ["успешных", c.successful], ["за успешный, $", c.per_success_usd ?? "—"], ["доля неудачных", c.failed_share ?? "—"]].map(([a, b]) => `<div class="readout" style="background:var(--accent-soft)"><b>${b}</b><span class="muted">${a}</span></div>`).join("")}</div>
      <div class="btnrow"><button class="btn btn-primary" id="evalRun">Прогнать набор проверок по адресу агента</button>${infoInline(ip("Адрес агента задаётся на экране «Разработка»."), "Подсказка: адрес агента")}</div>
      <div id="evalOut">${S.lastEval ? evalHtml(S.lastEval, S.lastEval.regression) : last ? evalHtml(last, null) : ""}</div></div>
      <div class="card-output">${headInfo("h2", "Испытания — запуск двумя ключами", ip("Атаки идут реальными запросами по адресу агента. Ключ 1 снимает предохранитель и показывает план. Ключ 2 — пуск после ручного ввода хоста стенда и отметки «это не прод»."), "Подсказка: два ключа")}
      <div class="opts" id="atkSel" style="margin-top:8px">${S.meta.attacks.map((a) => { const ai = info(ip(a.why), "Подсказка: " + a.title, true); return `<div class="atk-row"><label><input type="checkbox" value="${a.id}" ${atkChecked(a) ? "checked" : ""}> <span><b>${esc(a.title)}</b>${a.manual && !a.title.includes("вручную") ? " (вручную)" : ""}</span></label>${ai.btn}</div>${ai.pop}`; }).join("")}</div>
      <div class="btnrow"><button class="btn btn-danger" id="armBtn">Ключ 1 — снять предохранитель</button></div>
      <div id="atkOut">${S.lastAttack ? "" : (S.view.project.attack_runs || []).slice(-3).reverse().map((r) => `<div class="small">Прогон ${esc(fmtDT(r.ts))}: ${Object.entries(r.summary).map(([k, v]) => `${esc(attackTitle(k))} — ${VERDICT[v] || v}`).join(", ")}</div>`).join("")}</div></div>`;
    if (!S.lastEval && last && (S.view.project.eval_runs || []).length > 1) {
      api("GET", `/api/projects/${S.pid}/eval/regression`).then((reg) => {
        const o = $("#evalOut"); if (o && reg && reg.before) o.innerHTML = evalHtml(last, reg);
      }).catch(() => {});
    }
    $("#evalRun").onclick = async () => {
      if (!confirm("Прогон набора — реальные запросы к агенту по адресу стенда. Запустить?")) return;
      $("#evalOut").innerHTML = spin;
      try { const r = await api("POST", `/api/projects/${S.pid}/eval/run`, { confirm: true }); S.lastEval = r; await loadProject(S.pid); S.step = s.id; renderAll(); }
      catch (e) { const o = $("#evalOut"); if (o) o.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    };
    $("#armBtn").onclick = armAttacks;
    renderAttackResult();
  } else if (s.slug === "launch") {
    p.innerHTML = `<div class="card-output">${headInfo("h2", "Сдача и защита", ip("Когда реестр рисков, стадии и просьба к ЛПР заполнены, соберите каркас защиты на 3–4 слайда со сверкой чисел."), "Подсказка: когда собирать защиту")}
      <div class="btnrow"><button class="btn btn-primary" id="goDef">Собрать каркас защиты</button><button class="btn" id="goPass">Собрать паспорт проекта</button></div></div>`;
    $("#goDef").onclick = () => go("defense");
    $("#goPass").onclick = async () => { const r = await api("POST", `/api/projects/${S.pid}/passport`); toast("Собран: " + r.path); };
  } else p.innerHTML = "";
}

function evalHtml(r, reg) {
  const s = r.summary;
  let h = `<div style="margin-top:10px"><b>Прогон ${esc(fmtDT(s.ts))}:</b> пройдено ${s.passed} из ${s.total}, не пройдено ${s.failed}, на отметку человеку ${s.manual}; стоимость прогона ${s.cost_usd} $.</div>
    <div class="table-scroll"><table class="grid"><thead><tr><th>№</th><th>тип</th><th>вход</th><th>признак</th><th>ответ агента</th><th>итог</th></tr></thead><tbody>
    ${r.results.map((x) => `<tr><td>${x.n}</td><td>${esc(x.type)}</td><td>${esc(x.input)}</td><td>${esc(x.criterion)}</td><td>${esc(x.error || x.answer)}</td><td>${x.verdict === "pass" ? '<span class="pill ok">пройден</span>' : x.verdict === "fail" ? '<span class="pill crit">не пройден</span>' : '<span class="pill grey">человек</span>'}</td></tr>`).join("")}</tbody></table></div>`;
  if (reg) h += `<div class="small" style="margin-top:6px">Регрессия: до — ${reg.before.passed}/${reg.before.total}, после — ${reg.after.passed}/${reg.after.total}${reg.same_set ? "" : " (наборы различаются — сравнение неполное)"}; изменилось кейсов: ${reg.changes.length}.</div>`;
  return h;
}

function attackTitle(id) { const a = S.meta.attacks.find((x) => x.id === id); return a ? a.title : id; }

/* испытания: два ключа */
function atkChecked(a) {
  const rec = answerOf(qStep("attack_selection"), "attack_selection");
  if (rec && rec.status === "answered" && Array.isArray(rec.value) && rec.value.length) return rec.value.includes(a.choice);
  return !a.manual;
}
function renderAttackResult() {
  const r = S.lastAttack, out = $("#atkOut");
  if (!r || !out) return;
  out.innerHTML = r.results.map((x) => `<div class="senior-box ${x.verdict === "failed" || x.verdict === "inconclusive" ? "need" : ""}"><b>${esc(x.title)}:</b> ${
    x.verdict === "passed" ? "пройдено" : x.verdict === "manual" ? "провести вручную по инструкции в протоколе" : esc(VERDICT[x.verdict]) + " — " + esc(x.problems.join("; "))}</div>`).join("")
    + `<div class="small muted">Протокол сохранён в документах проекта.</div>`;
}

async function armAttacks() {
  const sel = [...document.querySelectorAll("#atkSel input:checked")].map((i) => i.value);
  if (!sel.length) return toast("Выберите хотя бы одно испытание");
  let plan;
  try { plan = await api("POST", `/api/projects/${S.pid}/attacks/arm`, { attacks: sel }); }
  catch (e) { return toast(e.message + " — задайте адрес агента на экране «Разработка»", 6000); }
  const root = $("#modalRoot");
  root.innerHTML = `<div class="modal-bg"><div class="modal"><h2>Пуск испытаний</h2>
    <div class="keys"><div class="key on">КЛЮЧ 1 · предохранитель снят</div><div class="key" id="key2">КЛЮЧ 2 · пуск</div></div>
    <div class="err" style="margin-bottom:8px">${esc(plan.warning)}</div>
    <div class="kv"><span class="muted">Адрес</span><span class="mono">${esc(plan.target)}</span><span class="muted">Запросов</span><span>${plan.requests_total}</span>
      <span class="muted">Испытания</span><span>${plan.attacks.map((a) => esc(a.title) + (a.manual ? "" : ` — ${a.requests}`)).join("<br>")}</span></div>
    <label class="small muted" style="display:block;margin-top:10px">${esc(plan.second_confirmation)}</label><input type="text" id="typedHost" autocomplete="off" placeholder="хост:порт">
    <label style="display:flex;gap:8px;margin-top:8px"><input type="checkbox" id="notProd"> Это стенд, а не прод: копия данных, тестовые ключи, потолки боевые</label>
    <div class="btnrow"><button class="btn btn-danger" id="launchBtn" disabled>Ключ 2 — пуск</button><button class="btn" id="cancelAtk">Отмена</button>${infoInline(ip("Ключ пуска действует 10 минут."), "Подсказка: срок ключа пуска")}</div>
    <div id="launchOut"></div></div></div>`;
  const check = () => {
    const typed = $("#typedHost").value.trim(), hostOk = typed === plan.host, ok = hostOk && $("#notProd").checked;
    $("#launchBtn").disabled = !ok; $("#key2").classList.toggle("on", ok);
    $("#launchOut").innerHTML = typed && !hostOk ? '<div class="err">Хост не совпадает с адресом стенда — введите его вручную точно как в плане.</div>'
      : typed && !$("#notProd").checked ? '<div class="small muted">Отметьте, что это стенд, а не прод.</div>' : "";
  };
  $("#typedHost").oninput = check; $("#notProd").onchange = check;
  $("#cancelAtk").onclick = () => (root.innerHTML = "");
  $("#launchBtn").onclick = async () => {
    $("#launchOut").innerHTML = spin + " испытания идут…"; $("#launchBtn").disabled = true;
    try {
      const r = await api("POST", `/api/projects/${S.pid}/attacks/launch`, { token: plan.token, typed_host: $("#typedHost").value.trim(), not_prod: $("#notProd").checked });
      root.innerHTML = ""; await loadProject(S.pid); S.step = qStep("attack_selection"); renderAll();
      S.lastAttack = r; renderAttackResult();
    } catch (e) { $("#launchOut").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
}

/* ---------- разработка Junior Pilot ---------- */
async function viewDev(m) {
  const p = S.view.project, ws = p.workspace || {}, t = p.target || {};
  const scan = ws.last_scan;
  const cli = info(ip("Секреты маскируются. Без флажка материалы видит только локальная модель."), "Подсказка: облачная модель");
  m.innerHTML = `<div class="card">${headInfo("h1", "Разработка", ip("Copilot забирает материалы на проверку из рабочей папки или git-репозитория — только на чтение.") + ip("Файлы .env, ключи, сертификаты и бинарные файлы не читаются никогда."), "Подсказка: что читает Copilot")}</div>
    <div class="card-output"><h2>Рабочая папка или репозиторий</h2>
      <label class="small muted">Путь к папке проекта</label><input type="text" id="wsPath" value="${esc(ws.path || "")}" placeholder="D:\\work\\my-agent">
      <label style="display:flex;gap:8px;margin-top:8px"><input type="checkbox" id="wsCloud" ${ws.cloud_allowed ? "checked" : ""}> Материалы можно показывать облачной модели${cli.btn}</label>${cli.pop}
      <div class="btnrow"><button class="btn btn-primary" id="wsSave">Подключить</button><button class="btn" id="wsScan" ${ws.path ? "" : "disabled"}>Проверить кодом</button><button class="btn" id="wsPreview" ${ws.path ? "" : "disabled"}>Разбор моделью…</button></div>
      <div id="wsOut">${scan ? scanHtml(scan) : ""}</div>
      ${ws.last_review ? `<div class="senior-box"><b>Последний разбор (${esc(ws.last_review.model)}):</b> ${esc(ws.last_review.summary)}</div>` : ""}</div>
    <div class="card-output"><h2>Адрес агента для проверок и испытаний</h2>
      <div class="two"><div><label class="small muted">Адрес (стенд)</label><input type="url" id="tUrl" value="${esc(t.url || "")}" placeholder="http://127.0.0.1:8100/run"></div>
      <div><label class="small muted">Метод</label><select id="tMethod"><option ${t.method !== "GET" ? "selected" : ""}>POST</option><option ${t.method === "GET" ? "selected" : ""}>GET</option></select></div>
      <div><label class="small muted">Поле входа</label><input type="text" id="tIn" value="${esc(t.input_field || "query")}"></div>
      <div><label class="small muted">Поле ответа</label><input type="text" id="tOut" value="${esc(t.output_field || "answer")}"></div>
      <div><label class="small muted">Поле статуса</label><input type="text" id="tStatus" value="${esc(t.status_field || "status")}"></div>
      <div><label class="small muted">Переменная с токеном доступа (если нужен)</label><input type="text" id="tHeaders" value="${esc(t.headers_env || "")}" placeholder="AGENT_TOKEN"></div></div>
      <div class="btnrow"><button class="btn btn-primary" id="tSave">Сохранить адрес</button>${infoInline(ip('Контракт каркаса на Python: POST /run {"query"} → {"answer","status","cost_usd"}.'), "Подсказка: контракт агента")}</div></div>`;
  $("#wsSave").onclick = async () => {
    try { await api("PUT", `/api/projects/${S.pid}/workspace`, { path: $("#wsPath").value, cloud_allowed: $("#wsCloud").checked }); await loadProject(S.pid); toast("Папка подключена"); renderAll(); }
    catch (e) { toast(e.message); }
  };
  $("#wsScan").onclick = async () => {
    $("#wsOut").innerHTML = spin;
    try { const r = await api("POST", `/api/projects/${S.pid}/workspace/scan`); await loadProject(S.pid); renderAll(); $("#wsOut").innerHTML = scanHtml(r); }
    catch (e) { $("#wsOut").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  $("#wsPreview").onclick = previewReview;
  $("#tSave").onclick = async () => {
    try {
      await api("PUT", `/api/projects/${S.pid}/target`, { url: $("#tUrl").value.trim(), method: $("#tMethod").value, input_field: $("#tIn").value, output_field: $("#tOut").value, status_field: $("#tStatus").value, headers_env: $("#tHeaders").value });
      await loadProject(S.pid); toast("Адрес агента сохранён");
    } catch (e) { toast(e.message); }
  };
}
function scanHtml(r) {
  const lv = { warning: '<span class="lvl warning">критично</span>', caution: '<span class="lvl caution">внимание</span>', ok: '<span class="pill ok">есть</span>' };
  const g = r.git && r.git.is_git ? `git: ветка ${esc(r.git.branch)}, коммит ${esc(r.git.commit)}, незакоммиченных изменений ${r.git.dirty.length}` : "не git-репозиторий";
  const d = r.diff ? `С прошлой проверки: добавлено ${r.diff.added.length}, изменено ${r.diff.changed.length}, удалено ${r.diff.removed.length}.` : "Первая проверка.";
  return `<div class="small" style="margin-top:10px">Файлов ${r.files_total}, прочитано ${r.files_read}. ${g}. ${d}</div>
    <ul class="risklist" style="margin-top:6px">${r.findings.map((f) => `<li>${lv[f.level] || ""} <span class="mono small">${esc(stepMeta(f.step)?.code || "")}</span> ${esc(f.text)}${f.file ? ` <span class="muted small mono">${esc(f.file)}${f.line ? ":" + f.line : ""}</span>` : ""}</li>`).join("")}</ul>`;
}
async function previewReview() {
  let pv;
  try { pv = await api("GET", `/api/projects/${S.pid}/workspace/preview`); } catch (e) { return toast(e.message); }
  const root = $("#modalRoot");
  root.innerHTML = `<div class="modal-bg"><div class="modal"><h2>Что уйдёт модели</h2>
    <div class="kv"><span class="muted">Куда</span><span>${pv.endpoints.length ? esc(pv.endpoints.join(", ")) : '<span class="err">нет подходящей точки</span>'} ${pv.cloud_allowed ? "(облако разрешено)" : "(только локальные точки)"}</span>
    <span class="muted">Объём</span><span>${pv.chars} знаков, файлов ${pv.files.length}</span><span class="muted">Секретов скрыто</span><span>${pv.masked}</span></div>
    <pre class="code" style="max-height:180px">${esc(pv.files.join("\n"))}</pre>
    <div class="btnrow"><button class="btn btn-primary" id="rvGo" ${pv.endpoints.length ? "" : "disabled"}>Отправить на разбор</button><button class="btn" id="rvCancel">Отмена</button></div><div id="rvOut"></div></div></div>`;
  $("#rvCancel").onclick = () => (root.innerHTML = "");
  $("#rvGo").onclick = async () => {
    $("#rvOut").innerHTML = spin + " модель читает материалы…";
    const r = await api("POST", `/api/projects/${S.pid}/workspace/review`, { confirm: true });
    if (r.error) { $("#rvOut").innerHTML = `<div class="err">${esc(r.error)}</div>`; return; }
    root.innerHTML = ""; await loadProject(S.pid); renderAll();
  };
}

/* ---------- защита ---------- */
async function viewDefense(m) {
  m.innerHTML = `<div class="card">${headInfo("h1", "Защита перед ЛПР", ip("Каркас на 3–4 слайда: заголовок-вывод с числом и не больше четырёх строк. Каждое число сверяется с источником.") + ip("Слайды никто не любит смотреть — поэтому их мало, а на каждый вопрос руководителя есть ответ числом."), "Подсказка: каркас защиты")}
    <div class="btnrow"><button class="btn btn-primary" id="defBuild">Собрать каркас по проекту</button><button class="btn" id="defDocsToggle">По документам без проекта…</button></div>
    <div id="defDocs" class="hidden" style="margin-top:10px"><textarea id="defText" style="min-height:160px" placeholder="Текст документов проекта"></textarea>${infoInline(ip("Вставьте текст документов проекта: паспорт, расчёты, реестр рисков."), "Подсказка: какие документы")}
      <label style="display:flex;gap:8px;margin-top:6px"><input type="checkbox" id="defCloud"> Документы можно показывать облачной модели</label>
      <div class="btnrow"><button class="btn btn-primary" id="defFromDocs">Собрать по документам</button></div></div></div><div id="defOut"></div>`;
  $("#defDocsToggle").onclick = () => $("#defDocs").classList.toggle("hidden");
  $("#defBuild").onclick = async () => { $("#defOut").innerHTML = spin; renderDefense(await api("POST", `/api/projects/${S.pid}/defense`)); };
  $("#defFromDocs").onclick = async () => {
    $("#defOut").innerHTML = spin;
    try { const r = await api("POST", "/api/defense/documents", { text: $("#defText").value, title: S.view?.project.name, cloud_allowed: $("#defCloud").checked }); if (r.error) throw new Error(r.error); renderDefense(r); }
    catch (e) { $("#defOut").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
}
function renderDefense(d) {
  const j = d.jtbd, g = (t) => (String(t).startsWith("[ПУСТО") ? `<span class="gap">${esc(t)}</span>` : esc(t));
  $("#defOut").innerHTML = `<div class="go-board"><div class="go-light ${d.go.verdict}"></div><div><b>${d.go.verdict === "go" ? "Готово к защите" : d.go.verdict === "caution" ? "Можно готовиться к защите" : (d.slides && d.slides.length ? "Каркас собран — есть что закрыть" : "Пока рано на защиту")}</b><div class="small">${esc(d.go.text)}</div></div></div>
    <div class="card-output"><h2>Работа руководителя</h2><div class="kv">
      <span class="muted">Кто решает</span><span>${g(j.who)}</span><span class="muted">Когда</span><span>${g(j.when)}</span>
      <span class="muted">Хочет, по какому числу</span><span>${g(j.wants)}</span><span class="muted">Чтобы</span><span>${g(j.so_that)}</span>
      <span class="muted">Чем решает сейчас</span><span>${g(j.hired_now)}</span><span class="muted">Что удерживает</span><span>${g(j.holds_back)}</span><span class="muted">Что снимает</span><span>${g(j.relieves)}</span></div></div>
    <div class="card-output"><h2>Слайды</h2>${d.slides.map((s) => `<div class="slide"><div class="blk">Слайд ${s.n} · ${esc(s.block)}</div><div class="ttl">${g(s.title)}</div><ul>${s.lines.map((l) => `<li>${g(l)}</li>`).join("")}</ul></div>`).join("")}</div>
    <div class="card-output"><h2>Сверка чисел</h2><div class="table-scroll"><table class="grid"><thead><tr><th>число</th><th>слайд</th><th>источник</th><th>цитата</th><th>совпадает</th></tr></thead><tbody>
      ${d.reconcile.map((r) => `<tr><td class="mono">${esc(r.number)}</td><td>${r.slide}</td><td>${esc(r.source)}</td><td>${esc(r.quote)}</td><td>${r.match ? '<span class="pill ok">да</span>' : '<span class="pill crit">нет</span>'}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">Чисел на слайдах пока нет.</td></tr>'}</tbody></table></div></div>
    <div class="card-output"><h2>Пять вопросов ЛПР — ответ числом</h2><ul class="risklist">${d.lpr.map((x) => `<li><b>${esc(x.question)}</b><div>${g(x.answer)}</div></li>`).join("")}</ul></div>
    <div class="card-output"><h2>Пустые места</h2>${d.gaps.length ? `<ul>${d.gaps.map((x) => `<li class="gap">${esc(x)}</li>`).join("")}</ul>` : '<div class="muted">Нет.</div>'}
      ${d.path ? `<div class="small muted">Файл: ${esc(d.path)} — на экране «Документы».</div>` : ""}</div>`;
}

/* ---------- документы ---------- */
function md(text) {
  const lines = String(text).split("\n"); let out = "", inTable = false, inList = false;
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\s)_(.+?)_(?=\s|$|[.,;])/g, "$1<i>$2</i>").replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const l of lines) {
    if (/^\|/.test(l)) {
      if (/^\|[-| ]+\|$/.test(l)) continue;
      const cells = l.split("|").slice(1, -1);
      if (!inTable) { out += "<table><tr>" + cells.map((c) => `<th>${inline(c.trim())}</th>`).join("") + "</tr>"; inTable = true; }
      else out += "<tr>" + cells.map((c) => `<td>${inline(c.trim())}</td>`).join("") + "</tr>";
      continue;
    } else if (inTable) { out += "</table>"; inTable = false; }
    if (/^- /.test(l)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(l.slice(2))}</li>`; continue; }
    else if (inList) { out += "</ul>"; inList = false; }
    const h = l.match(/^(#{1,4}) (.*)/);
    if (h) out += `<h${Math.min(h[1].length + 1, 4)}>${inline(h[2])}</h${Math.min(h[1].length + 1, 4)}>`;
    else if (l.trim()) out += `<p>${inline(l)}</p>`;
  }
  if (inTable) out += "</table>"; if (inList) out += "</ul>";
  return out;
}
async function viewDocs(m) {
  const docs = await api("GET", `/api/projects/${S.pid}/docs`);
  const vers = await api("GET", `/api/projects/${S.pid}/versions`);
  m.innerHTML = `<div class="card">${headInfo("h1", "Документы проекта", ip("Документы шагов, паспорт, протоколы испытаний и каркас защиты. Каждое сохранение проекта пишется отдельной версией."), "Подсказка: документы проекта")}
    <div class="btnrow"><button class="btn btn-primary" id="passBtn">Собрать паспорт проекта</button></div></div>
    <div class="two"><div class="card-output"><h2>Файлы</h2>${docs.length ? docs.map((d) => `<div class="hintcard"><a href="#" data-doc="${esc(d.name)}">${esc(d.name)}</a> <a href="/api/projects/${encodeURIComponent(S.pid)}/docs/${encodeURIComponent(d.name)}/download" download class="small">Скачать</a></div>`).join("") : `<div class="muted">Документов пока нет.${infoInline(ip("Документы собираются в итоге каждого шага."), "Подсказка: когда появятся документы")}</div>`}</div>
    <div class="card-output"><h2>Версии</h2><div style="max-height:260px;overflow:auto">${vers.slice(0, 40).map((v) => `<div class="small hintcard"><span class="mono">${esc(v.label || v.version)}</span> ${esc(v.what)} <a href="#" data-ver="${esc(v.version)}">вернуть</a></div>`).join("")}</div></div></div>
    <div id="docView"></div>`;
  $("#passBtn").onclick = async () => { const r = await api("POST", `/api/projects/${S.pid}/passport`); toast("Собран: " + r.path); viewDocs(m); };
  m.querySelectorAll("[data-doc]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault(); const r = await api("GET", `/api/projects/${S.pid}/docs/${encodeURIComponent(a.dataset.doc)}`);
    $("#docView").innerHTML = `<div class="card-output"><h2>${esc(r.name)}</h2><div class="doc-content">${md(r.markdown)}</div></div>`; $("#docView").scrollIntoView();
  }));
  m.querySelectorAll("[data-ver]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault(); if (!confirm("Вернуть проект к этой версии? Текущее состояние тоже останется в версиях.")) return;
    S.view = await api("POST", `/api/projects/${S.pid}/restore`, { version: a.dataset.ver }); toast("Версия восстановлена"); renderAll();
  }));
}

/* ---------- модели ---------- */
async function viewModels(m) {
  const d = await api("GET", "/api/models");
  if (d.error) {
    m.innerHTML = `<div class="card"><h1>Модели — роутер</h1><div class="err">${esc(d.error)}</div>
      <div class="muted small" style="margin:8px 0">Файл <span class="mono">config/${esc(d.file)}</span> не читается. Исправьте его в редакторе по тексту ниже и откройте экран заново — до этого Senior Pilot и проверки работать не будут, ответы сохраняются.</div>
      <pre class="code">${esc(d.raw)}</pre></div>`;
    return;
  }
  const cfg = d.config, b = d.budget;
  const eps = Object.entries(cfg.endpoints);
  m.innerHTML = `<div class="card">${headInfo("h1", "Модели — роутер", `<p>Точки вызова моделей видны и правятся здесь, файл — <span class="mono">${esc(d.file)}</span>.</p>` + ip("Любой сервер, совместимый с OpenAI (OpenRouter, Ollama, LM Studio, vLLM), или прямой API Anthropic.") + ip("Ключи хранятся только в .env.local — здесь видно лишь, задан ли ключ."), "Подсказка: роутер моделей")}
    <div class="readouts" style="grid-template-columns:repeat(3,1fr);margin-top:10px">
      <div class="readout" style="background:var(--accent-soft)"><b>${b.spent_usd} $</b><span class="muted">потрачено за месяц</span></div>
      <div class="readout" style="background:var(--accent-soft)"><b>${b.budget_usd} $</b><span class="muted">потолок</span></div>
      <div class="readout" style="background:${b.warn ? "var(--warning-soft)" : "var(--accent-soft)"}"><b>${Math.round(b.share * 100)} %</b><span class="muted">${b.stopped ? "вызовы остановлены" : b.warn ? "больше 90 % — внимание" : "в пределах"}</span></div></div></div>
    <div class="card-output"><h2>Точки</h2><div class="table-scroll"><table class="grid" id="epTable"><thead><tr><th>имя</th><th>вид</th><th>адрес</th><th>модель</th><th>переменная ключа</th>${S.ui === "classic" ? "<th>$ вход / 1М</th><th>$ выход / 1М</th><th>цены сверены</th><th>семейство</th>" : ""}<th>ключ</th><th></th></tr></thead><tbody>
      ${eps.map(([n, e]) => epRow(n, e)).join("")}</tbody></table></div>
      <div class="btnrow"><button class="btn" id="epAdd">+ точка</button></div></div>
    <div class="card-output">${headInfo("h2", "Роли", ip("Первая точка — основная, остальные — запасные по порядку.") + ip("Проверяющий и судья — другого семейства, чем Senior Pilot.") + ip("Разбор материалов разработки без флажка «можно в облако» идёт только на локальные точки."), "Подсказка: роли моделей")}
      ${Object.entries(cfg.roles).map(([r, list]) => `<div class="two" style="margin-top:6px"><div class="mono">${esc(r)}</div><input type="text" data-role="${esc(r)}" value="${esc(list.join(", "))}"></div>`).join("")}
      <div class="two" style="margin-top:10px"><div>Потолок расходов в месяц, $</div><input type="number" id="budget" value="${esc(cfg.budget_usd)}" step="0.5"></div>
      <div class="btnrow"><button class="btn btn-primary" id="mdlSave">Сохранить</button><span id="mdlMsg" class="small"></span></div></div>
    <${S.ui === "classic" ? "div" : "details"} class="card-output calls"><${S.ui === "classic" ? "h2" : "summary"}>Последние вызовы${S.ui === "classic" ? "" : ` <span class="pg-meta">· ${d.calls.length}</span>`}</${S.ui === "classic" ? "h2" : "summary"}><div class="table-scroll"><table class="grid"><thead><tr><th>время</th><th>роль</th><th>зачем</th><th>точка</th><th>токены</th><th>$</th><th>с</th></tr></thead><tbody>
      ${d.calls.map((c) => `<tr><td class="mono small">${esc(fmtDT(c.ts))}</td><td>${esc(c.role)}</td><td>${esc(c.purpose)}</td><td>${esc(c.endpoint || "—")}${c.ok ? "" : ' <span class="pill crit">сбой</span>'}</td><td>${c.tokens_in ?? ""}/${c.tokens_out ?? ""}</td><td>${c.cost_usd ?? 0}</td><td>${c.seconds ?? ""}</td></tr>`).join("") || '<tr><td colspan="7" class="muted">Вызовов ещё не было.</td></tr>'}</tbody></table></div></${S.ui === "classic" ? "div" : "details"}>`;
  bindEp();
  $("#epAdd").onclick = () => { $("#epTable tbody").insertAdjacentHTML("beforeend", epRow("new-endpoint", { kind: "openai", base_url: "http://localhost:11434/v1", model: "", key_env: "", price_in: 0, price_out: 0, family: "" })); bindEp(); };
  $("#mdlSave").onclick = async () => {
    const endpoints = {};
    document.querySelectorAll("#epTable tbody tr").forEach((tr) => {
      const v = (k) => tr.querySelector(`[data-f="${k}"]`).value.trim();
      const ep = { kind: v("kind"), base_url: v("base_url"), model: v("model"), key_env: v("key_env"), price_in: Number(v("price_in") || 0), price_out: Number(v("price_out") || 0), family: v("family") };
      if (v("price_checked")) ep.price_checked = v("price_checked");
      endpoints[v("name")] = ep;
    });
    const roles = {}; document.querySelectorAll("[data-role]").forEach((i) => (roles[i.dataset.role] = i.value.split(",").map((x) => x.trim()).filter(Boolean)));
    const conf = { budget_usd: Number($("#budget").value), timeout_sec: cfg.timeout_sec, idle_timeout_sec: cfg.idle_timeout_sec, endpoints, roles };
    try {
      const saved = await api("PUT", "/api/models", { config: conf });
      const warn = (saved && saved.warnings) || [];
      toast(warn.length ? "Роутер сохранён, но: " + warn[0] : "Роутер сохранён", warn.length ? 8000 : 4000);
      if (warn.length) $("#mdlMsg").innerHTML = `<span class="small" style="color:var(--warning)">${warn.map(esc).join("<br>")}</span>`;
      viewModels(m);
    }
    catch (e) { $("#mdlMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
}
function epRow(n, e) {
  const f = (k, v, w) => `<td><input type="text" data-f="${k}" value="${esc(v ?? "")}" style="min-width:${w}px"${k === "price_checked" ? ' placeholder="дд.мм.гггг"' : ""}></td>`;
  // в новых дизайнах цены, дата сверки и семейство не показываются, но сохраняются: по ним считаются расходы и выбирается проверяющая модель
  const hid = (k, v) => `<input type="hidden" data-f="${k}" value="${esc(v ?? "")}">`;
  const extra = S.ui === "classic" ? `${f("price_in", e.price_in, 60)}${f("price_out", e.price_out, 60)}${f("price_checked", e.price_checked, 90)}${f("family", e.family, 70)}`
    : `<td hidden>${hid("price_in", e.price_in)}${hid("price_out", e.price_out)}${hid("price_checked", e.price_checked)}${hid("family", e.family)}</td>`;
  return `<tr>${f("name", n, 110)}<td><select data-f="kind"><option ${e.kind === "openai" ? "selected" : ""}>openai</option><option ${e.kind === "anthropic" ? "selected" : ""}>anthropic</option></select></td>
    ${f("base_url", e.base_url, 200)}${f("model", e.model, 160)}${f("key_env", e.key_env, 130)}${extra}
    <td>${e.key_needed ? (e.key_present ? '<span class="pill ok">задан</span>' : '<span class="pill crit">нет</span>') : '<span class="pill grey">не нужен</span>'}</td>
    <td><button class="btn" data-check="${esc(n)}">Проверить</button> <button class="rowdel" title="Удалить точку">✕</button></td></tr>`;
}
function bindEp() {
  document.querySelectorAll("#epTable .rowdel").forEach((b) => (b.onclick = () => b.closest("tr").remove()));
  document.querySelectorAll("[data-check]").forEach((b) => (b.onclick = async () => {
    b.innerHTML = spin; const r = await api("POST", `/api/models/check/${encodeURIComponent(b.dataset.check)}`);
    b.textContent = "Проверить"; toast(r.ok ? `Отвечает: ${r.model}, ${r.seconds} с` : `Не отвечает: ${r.error}`, 6000);
  }));
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="err" style="padding:12px">Не удалось загрузить: ${esc(e.message)}</div>`); });
