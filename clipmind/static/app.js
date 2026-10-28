/* ClipMind UI logic — vanilla JS, no build step, no CDN. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const list = $("#list");
const input = $("#q");

const state = {
  mode: "auto",          // auto | semantic | keyword
  query: "",
  kindFilter: null,
  clips: [],             // currently rendered
  selected: -1,
  live: false,
  kinds: {},
};

/* ------------------------------------------------------------------ api */
async function api(path, opts) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

/* ------------------------------------------------------------- rendering */
const KIND_LABEL = {
  otp: "验证码", url: "链接", email: "邮箱", phone: "电话", json: "JSON",
  command: "命令", color: "颜色", path: "路径", ip: "IP", uuid: "UUID",
  code: "代码", markdown: "Markdown", table: "表格", text: "文本",
};
const MONO_KINDS = new Set(["json", "command", "code", "uuid", "ip", "path"]);

function relTime(ts) {
  const diff = (Date.now() / 1000 - ts);
  if (diff < 45) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}

function esc(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function buildEntities(clip) {
  const out = [];
  const meta = clip.meta || {};
  if (meta.otp) out.push({ label: "验证码", value: meta.otp, cls: "otp" });
  for (const u of meta.urls || []) out.push({ label: "链接", value: u });
  for (const e of meta.emails || []) out.push({ label: "邮箱", value: e });
  for (const p of meta.phones || []) out.push({ label: "电话", value: p });
  if (meta.color) out.push({ label: "颜色", value: meta.color, cls: "swatchable" });
  for (const u of meta.uuids || []) out.push({ label: "UUID", value: u });
  for (const p of meta.paths || []) out.push({ label: "路径", value: p });
  return out.slice(0, 6);
}

const MATCH_LABEL = {
  keyword: "关键词", semantic: "语义", intent: "意图",
  "intent+kind": "意图识别", recent: "",
};

function clipNode(clip, opts = {}) {
  const el = document.createElement("div");
  el.className = "clip" + (MONO_KINDS.has(clip.kind) ? " mono" : "");
  el.dataset.id = clip.id;
  el.setAttribute("role", "option");
  if (opts.fresh) el.classList.add("fresh");
  if (clip.content.length > 220) el.classList.add("fade");

  const entities = buildEntities(clip);
  const label = KIND_LABEL[clip.kind] || clip.kind;
  const isColor = clip.kind === "color" && entities.some((e) => e.cls === "swatchable");
  const matched = clip.matched_by
    ? `<span class="matched">${MATCH_LABEL[clip.matched_by] || clip.matched_by}</span>`
    : "";
  const scoreTag = typeof clip.score === "number"
    ? `<span class="score-tag">${clip.score.toFixed(2)}</span>` : "";

  el.innerHTML = `
    <div class="clip-head">
      <span class="badge k-${esc(clip.kind)}">${label}</span>
      ${clip.copy_count > 1 ? `<span class="clip-usage">用过 ${clip.copy_count} 次</span>` : ""}
      <span class="clip-time">${relTime(clip.created_at)}</span>
      ${matched}
      ${scoreTag}
      <button class="pin-btn ${clip.pinned ? "pinned" : ""}" title="置顶">★</button>
      <button class="del-btn" title="删除">✕</button>
    </div>
    <div class="clip-body">${isColor
      ? `<span class="swatch" style="background:${esc(entities.find((e) => e.cls === "swatchable").value)}"></span>${esc(clip.content)}`
      : esc(clip.content)}</div>
    ${entities.length ? `<div class="entities"></div>` : ""}
  `;

  const entityBox = el.querySelector(".entities");
  if (entityBox) {
    for (const ent of entities) {
      const chip = document.createElement("span");
      chip.className = "entity" + (ent.cls === "otp" ? " otp" : "");
      chip.title = `点击复制：${ent.value}`;
      chip.innerHTML = `<span class="entity-label">${esc(ent.label)}</span>${esc(ent.value)}`;
      chip.addEventListener("click", (ev) => {
        ev.stopPropagation();
        copyText(ent.value, `已复制${ent.label}`);
      });
      entityBox.appendChild(chip);
    }
  }

  el.addEventListener("click", (ev) => {
    if (ev.target.closest(".pin-btn, .del-btn, .entity")) return;
    copyClip(clip.id, el);
  });
  el.addEventListener("dblclick", () => el.classList.toggle("expand"));
  el.querySelector(".pin-btn").addEventListener("click", async (ev) => {
    ev.stopPropagation();
    await api(`/api/clips/${clip.id}/pin`, { method: "POST" });
    await refresh();
  });
  el.querySelector(".del-btn").addEventListener("click", async (ev) => {
    ev.stopPropagation();
    el.style.opacity = "0.4";
    try {
      await api(`/api/clips/${clip.id}`, { method: "DELETE" });
      el.remove();
      state.clips = state.clips.filter((c) => c.id !== clip.id);
    } catch { el.style.opacity = ""; }
  });

  el._clip = clip;
  return el;
}

function renderClips(clips, opts = {}) {
  list.innerHTML = "";
  state.clips = clips;
  state.selected = -1;
  for (const c of clips) list.appendChild(clipNode(c, opts));
  $("#empty").classList.toggle("hidden", clips.length > 0);
  $("#nohits").classList.toggle(
    "hidden", !(opts.searched && clips.length === 0)
  );
}

function renderFilters(kinds) {
  const box = $("#filters");
  const entries = Object.entries(kinds).sort((a, b) => b[1] - a[1]).slice(0, 10);
  box.innerHTML = "";
  for (const [kind, n] of entries) {
    const chip = document.createElement("span");
    chip.className = "chip" + (state.kindFilter === kind ? " on" : "");
    chip.textContent = `${KIND_LABEL[kind] || kind} ${n}`;
    chip.addEventListener("click", () => {
      state.kindFilter = state.kindFilter === kind ? null : kind;
      renderFilters(state.kinds);
      refresh();
    });
    box.appendChild(chip);
  }
}

/* ----------------------------------------------------------------- copy */
async function copyText(text, msg) {
  try { await navigator.clipboard.writeText(text); }
  catch { /* http context fallback */ }
  toast(msg || "已复制", "ok");
}

async function copyClip(id, el) {
  try {
    await api(`/api/clips/${id}/copy`, { method: "POST" });
    el.classList.remove("copied-flash");
    void el.offsetWidth;  // restart animation
    el.classList.add("copied-flash");
    toast("已复制到剪贴板 ✓", "ok");
  } catch { toast("复制失败"); }
}

let toastTimer;
function toast(msg, cls) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + (cls || "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1600);
}

/* ---------------------------------------------------------------- search */
let searchTimer;
input.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refresh, 220);
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { input.value = ""; refresh(); input.blur(); }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); moveSelection(e.key === "ArrowDown" ? 1 : -1);
  }
  if (e.key === "Enter") {
    if (state.selected >= 0) {
      const node = list.children[state.selected];
      node?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== input) {
    e.preventDefault(); input.focus();
  }
});

function moveSelection(delta) {
  const n = list.children.length;
  if (!n) return;
  state.selected = Math.min(n - 1, Math.max(0, state.selected + delta));
  [...list.children].forEach((el, i) =>
    el.classList.toggle("selected", i === state.selected));
  list.children[state.selected].scrollIntoView({ block: "nearest" });
}

$("#mode").addEventListener("click", () => {
  const modes = ["auto", "semantic", "keyword"];
  const names = { auto: "智能", semantic: "语义", keyword: "精确" };
  state.mode = modes[(modes.indexOf(state.mode) + 1) % 3];
  const btn = $("#mode");
  btn.textContent = names[state.mode];
  btn.classList.toggle("on", state.mode !== "auto");
  refresh();
});

$("#btn-clear").addEventListener("click", async () => {
  if (!confirm("清空所有未置顶的剪贴板历史？")) return;
  await api("/api/clips?keep_pinned=true", { method: "DELETE" });
  refresh(true);
});

$("#btn-demo").addEventListener("click", async () => {
  const samples = [
    "https://github.com/anthropics/anthropic-sdk-python",
    "您的验证码是 483920，10分钟内有效。【星河银行】",
    "+86 138 0013 8000",
    "sales@example.com",
    "#3B82F6",
    'git push --force-with-lease origin feature/clip-history',
    '{"model": "gpt-4o-mini", "temperature": 0.7, "max_tokens": 1024}',
    "ssh -i ~/.ssh/id_ed25519 deploy@203.0.113.10",
    "pip install fastembed",
    "会议纪要：\n- 下周三发布 v2\n- 联系 UI 组要设计稿",
  ];
  for (const s of samples) {
    await api("/api/clips", { method: "POST", body: JSON.stringify({ content: s }) });
  }
  toast("已注入示例数据，试试搜索「验证码」「github」「颜色」", "ok");
  input.value = "";
  refresh(true);
});

/* ----------------------------------------------------------------- data */
async function refresh(full = false) {
  const q = input.value.trim();
  try {
    if (q) {
      const res = await api(
        `/api/search?q=${encodeURIComponent(q)}&mode=${state.mode}`
      );
      renderClips(res.hits, { searched: true });
      renderFilters(state.kinds || {});
    } else {
      const res = await api(
        `/api/clips?limit=60${state.kindFilter ? `&kind=${state.kindFilter}` : ""}`
      );
      renderClips(res.clips, {});
      if (full) { await loadStats(); }
    }
  } catch (err) {
    toast("服务未响应，请确认 clipmind 正在运行");
  }
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    state.kinds = s.kinds || {};
    renderFilters(state.kinds);
    $("#stat-total").textContent = `${s.total} 条历史`;
    const sem = s.backend?.semantic_search;
    $("#stat-semantic").textContent = sem
      ? `语义搜索 ON (${s.backend.selected})`
      : "语义搜索 OFF（关键词模式）";
    $("#stat-semantic").style.color = sem ? "#38d9c8" : "";
  } catch {}
}

/* ------------------------------------------------------------ websocket */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { state.live = true; $("#stat-live").textContent = "● 实时"; };
  ws.onclose = () => {
    state.live = false;
    $("#stat-live").textContent = "○ 离线，重连中…";
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "clip.new" && msg.clip) {
      if (!input.value.trim() && !state.kindFilter) {
        const node = clipNode(msg.clip, { fresh: true });
        list.prepend(node);
        state.clips.unshift(msg.clip);
        $("#empty").classList.add("hidden");
        updateCount();
      }
    } else if (msg.type === "clip.copied") {
      updateCount();
    } else if (msg.type === "indexed") {
      $("#stat-semantic").textContent = `语义索引 +${msg.count}`;
    }
  };
}

function updateCount() {
  const n = state.clips.length;
  const el = $("#stat-total");
  if (el && n) el.textContent = `${n}+ 条历史`;
}

/* ----------------------------------------------------------------- boot */
refresh(true);
loadStats();
connectWS();
