// Cron Dashboard 前端逻辑
// 通过 AstrBot Plugin Views 提供的 window.AstrBotPluginView bridge 调用后端 API。

const API_BASE = "/astrbot_plugin_cron_dashboard";
const COLORS = {
  scheduled: "#2f7cf6",
  running: "#f59e0b",
  completed: "#22c55e",
  failed: "#ef4444",
  disabled: "#94a3b8",
};
const STATUS_TEXT = {
  scheduled: "已调度",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

const $ = (id) => document.getElementById(id);

let bridge = window.AstrBotPluginView;
let jobs = [];
let stats = null;
let logs = [];
let handlers = {};
let editingJob = null;

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

async function init() {
  if (!bridge || typeof bridge.apiGet !== "function") {
    $("toast").textContent = "当前环境未提供 AstrBotPluginView bridge，视图不可用。";
    $("toast").hidden = false;
    return;
  }
  bindEvents();
  await refresh();
}

function bindEvents() {
  $("btn-new").addEventListener("click", () => openModal(null));
  $("btn-refresh").addEventListener("click", () => refresh());
  $("search").addEventListener("input", renderJobs);
  $("filter-type").addEventListener("change", renderJobs);
  $("filter-status").addEventListener("change", renderJobs);
  $("f-type").addEventListener("change", onTypeChange);
  $("f-handler").addEventListener("change", renderHandlerParams);
  $("job-form").addEventListener("submit", onSubmit);
  document.querySelectorAll("[data-action='close']").forEach((el) =>
    el.addEventListener("click", closeModal)
  );
  $("modal").addEventListener("click", (e) => {
    if (e.target === $("modal")) closeModal();
  });
  $("btn-clear-logs").addEventListener("click", () => {
    logs = [];
    renderLogs();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
}

// ---------------------------------------------------------------------------
// 数据加载与刷新
// ---------------------------------------------------------------------------

async function refresh() {
  const btn = $("btn-refresh");
  btn.disabled = true;
  try {
    const [s, j, l, h] = await Promise.all([
      bridge.apiGet(`${API_BASE}/stats`),
      bridge.apiGet(`${API_BASE}/jobs`),
      bridge.apiGet(`${API_BASE}/logs`, { limit: 50 }),
      bridge.apiGet(`${API_BASE}/handlers`),
    ]);
    stats = s;
    jobs = (j && j.jobs) || [];
    logs = (l && l.logs) || [];
    handlers = (h && h.handlers) || {};
    renderAll();
  } catch (err) {
    toast("加载失败: " + (err && err.message ? err.message : err));
  } finally {
    btn.disabled = false;
  }
}

function renderAll() {
  renderStats();
  renderDonut();
  renderTodayBars();
  renderJobs();
  renderLogs();
}

// ---------------------------------------------------------------------------
// 统计卡片
// ---------------------------------------------------------------------------

function renderStats() {
  const s = stats || {};
  const cards = [
    ["任务总数", s.total ?? 0],
    ["启用中", s.enabled ?? 0],
    ["basic / AI", `${s.basic ?? 0} / ${s.active_agent ?? 0}`],
    ["今日执行", s.today_runs ?? 0],
    ["运行中", s.running ?? 0],
    ["失败任务", s.failed ?? 0],
  ];
  $("stats").innerHTML = cards
    .map(([label, num]) => `<div class="stat"><div class="num">${num}</div><div class="label">${label}</div></div>`)
    .join("");
}

// ---------------------------------------------------------------------------
// 环形图
// ---------------------------------------------------------------------------

function renderDonut() {
  const svg = $("donut");
  const keys = ["scheduled", "running", "completed", "failed"];
  const values = keys.map((k) => (stats ? stats[k] || 0 : 0));
  const total = values.reduce((a, b) => a + b, 0);
  const cx = 100;
  const cy = 100;
  const r = 70;
  const C = 2 * Math.PI * r;
  let offset = 0;
  let segs = "";

  keys.forEach((k, i) => {
    const v = values[i];
    if (!v) return;
    const frac = v / (total || 1);
    const len = frac * C;
    segs += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${COLORS[k]}" stroke-width="22" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})"><title>${STATUS_TEXT[k]} ${v}</title></circle>`;
    offset += len;
  });

  svg.innerHTML =
    segs +
    `<text x="${cx}" y="${cy - 4}" text-anchor="middle" font-size="30" font-weight="700" fill="currentColor">${total}</text>` +
    `<text x="${cx}" y="${cy + 20}" text-anchor="middle" font-size="11" fill="currentColor" opacity="0.6">任务</text>`;

  $("donut-legend").innerHTML = keys
    .map((k, i) => {
      const v = values[i];
      return `<div class="legend-item"><span class="legend-dot" style="background:${COLORS[k]}"></span>${STATUS_TEXT[k]}<span class="val">${v}</span></div>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// 今日执行条形图
// ---------------------------------------------------------------------------

function renderTodayBars() {
  const byHour = new Array(24).fill(0);
  const failedByHour = new Array(24).fill(0);
  const now = new Date();
  const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  logs.forEach((rec) => {
    const t = new Date(rec.time);
    if (Number.isNaN(t.getTime())) return;
    const key = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
    if (key !== todayKey) return;
    byHour[t.getHours()] += 1;
    if (rec.status === "failed") failedByHour[t.getHours()] += 1;
  });
  const max = Math.max(1, ...byHour);
  $("today-total").textContent = `${byHour.reduce((a, b) => a + b, 0)} 次`;
  $("today-bars").innerHTML = byHour
    .map((v, h) => {
      const height = v ? Math.max(6, (v / max) * 100) : 2;
      const cls = failedByHour[h] ? "failed" : "";
      return `<div class="bar-col" title="${h}:00 - ${v} 次"><div class="bar ${cls}" style="height:${height}px"></div><span class="bar-label">${h}</span></div>`;
    })
    .join("");
  $("today-hint").textContent =
    byHour.reduce((a, b) => a + b, 0) === 0 ? "今日暂无执行记录（日志来自 basic 任务执行）" : "按小时统计今日 basic 任务执行情况（执行日志）";
}

// ---------------------------------------------------------------------------
// 任务表格
// ---------------------------------------------------------------------------

function describeCron(expr) {
  if (!expr) return "一次性";
  const p = expr.split(/\s+/);
  if (p.length !== 5) return expr;
  const names = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  const dow =
    p[4] === "*"
      ? "每天"
      : p[4].includes(",")
        ? p[4]
        : /^\d+$/.test(p[4])
          ? `每周${names[Number(p[4]) % 7]}`
          : p[4];
  const hm = p[1] === "*" ? "每小时" : `${p[1].padStart(2, "0")}:${p[0].padStart(2, "0")}`;
  return `${hm} ${dow}`;
}

function renderJobs() {
  const kw = $("search").value.trim().toLowerCase();
  const ft = $("filter-type").value;
  const fs = $("filter-status").value;
  const list = jobs.filter((j) => {
    if (ft && j.job_type !== ft) return false;
    if (fs && j.status !== fs) return false;
    if (kw) {
      const hay = `${j.name} ${j.description} ${j.job_id}`.toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    return true;
  });
  $("job-count").textContent = `${list.length} / ${jobs.length}`;
  $("empty").hidden = list.length !== 0;
  $("job-tbody").innerHTML = list.map(renderJobRow).join("");
}

function renderJobRow(j) {
  const enabled = j.enabled ? "" : "disabled";
  const statusKey = enabled ? j.status || "scheduled" : "disabled";
  const typeTag =
    j.job_type === "basic"
      ? `<span class="tag tag-basic">basic</span>`
      : `<span class="tag tag-agent">active_agent</span>`;
  const statusText = enabled ? STATUS_TEXT[j.status] || j.status : "停用";
  const shortId = j.job_id ? j.job_id.slice(0, 8) : "";
  const btnToggle = enabled
    ? `<button class="btn btn-sm" data-action="toggle" data-id="${j.job_id}">停用</button>`
    : `<button class="btn btn-sm" data-action="toggle" data-id="${j.job_id}">启用</button>`;
  return `<tr>
    <td><strong>${escapeHtml(j.name)}</strong><br /><span class="hint">${escapeHtml(j.description || shortId)}</span></td>
    <td>${typeTag}</td>
    <td>${escapeHtml(describeCron(j.cron_expression))}</td>
    <td><span class="status"><span class="dot dot-${statusKey}"></span>${statusText}</span></td>
    <td>${escapeHtml(j.last_run_at || "—")}</td>
    <td>${escapeHtml(j.next_run_time || "—")}</td>
    <td class="col-ops"><div class="row-ops">
      <button class="btn btn-sm" data-action="run" data-id="${j.job_id}">运行</button>
      <button class="btn btn-sm" data-action="edit" data-id="${j.job_id}">编辑</button>
      ${btnToggle}
      <button class="btn btn-sm btn-danger" data-action="delete" data-id="${j.job_id}">删除</button>
    </div></td>
  </tr>`;
}

$("job-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.id;
  const action = btn.dataset.action;
  if (action === "run") return runJob(id);
  if (action === "edit") return openModal(id);
  if (action === "toggle") return toggleJob(id);
  if (action === "delete") return deleteJob(id);
});

async function runJob(id) {
  try {
    await bridge.apiPost(`${API_BASE}/jobs/${id}/run`, {});
    toast("已触发任务立即运行");
    refresh();
  } catch (err) {
    toast("运行失败: " + msg(err));
  }
}

async function toggleJob(id) {
  try {
    const res = await bridge.apiPost(`${API_BASE}/jobs/${id}/toggle`, {});
    toast(res && res.enabled ? "任务已启用" : "任务已停用");
    refresh();
  } catch (err) {
    toast("操作失败: " + msg(err));
  }
}

async function deleteJob(id) {
  if (!confirm("确定要删除该任务吗？此操作不可恢复。")) return;
  try {
    await bridge.apiPost(`${API_BASE}/jobs/${id}/delete`, {});
    toast("任务已删除");
    refresh();
  } catch (err) {
    toast("删除失败: " + msg(err));
  }
}

// ---------------------------------------------------------------------------
// 新建 / 编辑模态框
// ---------------------------------------------------------------------------

async function openModal(jobId) {
  editingJob = null;
  $("job-form").reset();
  $("modal-title").textContent = jobId ? "编辑任务" : "新建任务";
  $("f-enabled").value = "true";
  $("f-timezone").value = "Asia/Shanghai";
  $("agent-fields").hidden = true;
  $("basic-fields").hidden = false;
  $("f-payload").value = "";

  const handlerSelect = $("f-handler");
  const keys = Object.keys(handlers || {});
  handlerSelect.innerHTML = keys
    .map((k) => `<option value="${k}">${escapeHtml(handlers[k].name)}</option>`)
    .join("");
  if (keys.length) {
    renderHandlerParams();
  } else {
    $("handler-params").innerHTML = "<p class='hint'>未加载到执行器信息</p>";
  }

  if (jobId) {
    const job = jobs.find((x) => x.job_id === jobId);
    if (job) {
      editingJob = job;
      $("f-job-id").value = job.job_id;
      $("f-name").value = job.name;
      $("f-description").value = job.description || "";
      $("f-cron").value = job.cron_expression || "";
      $("f-timezone").value = job.timezone || "Asia/Shanghai";
      $("f-enabled").value = String(job.enabled);
      $("f-type").value = job.job_type;
      onTypeChange();
      if (job.job_type === "basic") {
        const h = (job.payload && job.payload.handler) || "";
        handlerSelect.value = h;
        renderHandlerParams();
        fillParams(job.payload || {});
      } else {
        $("f-payload").value = JSON.stringify(job.payload || {}, null, 2);
      }
    }
  }
  $("modal").hidden = false;
}

function closeModal() {
  $("modal").hidden = true;
}

function onTypeChange() {
  const isBasic = $("f-type").value === "basic";
  $("basic-fields").hidden = !isBasic;
  $("agent-fields").hidden = isBasic;
}

function renderHandlerParams() {
  const key = $("f-handler").value;
  const meta = (handlers || {})[key];
  const wrap = $("handler-params");
  if (!meta) {
    wrap.innerHTML = "";
    $("handler-desc").textContent = "";
    return;
  }
  $("handler-desc").textContent = meta.description || "";
  wrap.innerHTML = (meta.params || [])
    .map((p) => {
      if (p.type === "select") {
        const opts = (p.options || []).map((o) => `<option value="${o}">${o}</option>`).join("");
        return `<div class="field"><label for="p-${p.key}">${escapeHtml(p.name)}${p.required ? " <em>*</em>" : ""}</label><select id="p-${p.key}" data-param="${p.key}">${opts}</select></div>`;
      }
      const tag = p.type === "text" ? "textarea" : "input";
      const attrs = p.type === "text" ? 'rows="3"' : `type="${p.type === "string" ? "text" : p.type}"`;
      return `<div class="field"><label for="p-${p.key}">${escapeHtml(p.name)}${p.required ? " <em>*</em>" : ""}</label><${tag} id="p-${p.key}" data-param="${p.key}" ${attrs} placeholder="${escapeHtml(p.placeholder || "")}"></${tag}></div>`;
    })
    .join("");
}

function fillParams(payload) {
  const meta = (handlers || {})[$("f-handler").value];
  if (!meta) return;
  (meta.params || []).forEach((p) => {
    const el = $(`p-${p.key}`);
    if (el && payload[p.key] != null) el.value = payload[p.key];
  });
}

async function onSubmit(e) {
  e.preventDefault();
  const name = $("f-name").value.trim();
  const cron = $("f-cron").value.trim();
  const jobType = $("f-type").value;
  if (!name) return toast("任务名称不能为空");
  if (!cron) return toast("Cron 表达式不能为空");

  const body = {
    name,
    job_type: jobType,
    cron_expression: cron,
    timezone: $("f-timezone").value.trim() || "Asia/Shanghai",
    description: $("f-description").value.trim(),
    enabled: $("f-enabled").value === "true",
  };

  if (jobType === "basic") {
    const handler = $("f-handler").value;
    const meta = (handlers || {})[handler];
    const params = {};
    (meta.params || []).forEach((p) => {
      const el = $(`p-${p.key}`);
      if (el && el.value) params[p.key] = el.value;
    });
    body.handler = handler;
    body.params = params;
  } else {
    let payload = {};
    try {
      payload = $("f-payload").value.trim() ? JSON.parse($("f-payload").value) : {};
    } catch (err) {
      return toast("任务参数不是合法 JSON");
    }
    body.params = payload;
  }

  const id = $("f-job-id").value;
  try {
    if (id) {
      await bridge.apiPost(`${API_BASE}/jobs/${id}/update`, body);
      toast("任务已更新");
    } else {
      await bridge.apiPost(`${API_BASE}/jobs`, body);
      toast("任务已创建");
    }
    closeModal();
    refresh();
  } catch (err) {
    toast("保存失败: " + msg(err));
  }
}

// ---------------------------------------------------------------------------
// 执行日志
// ---------------------------------------------------------------------------

function renderLogs() {
  $("log-count").textContent = String(logs.length);
  $("logs").innerHTML = logs
    .map((rec) => {
      const ok = rec.status !== "failed";
      const cls = ok ? "log-ok" : "log-fail";
      const detail = rec.detail ? `<div class="log-detail">${escapeHtml(rec.detail)}</div>` : "";
      const time = formatTime(rec.time);
      return `<div class="log-item ${cls}"><div class="log-head"><span class="status"><span class="dot ${ok ? "dot-completed" : "dot-failed"}"></span>${ok ? "成功" : "失败"}</span><span>${escapeHtml(rec.name || "")}</span><span class="log-time">${time}</span><span class="hint">${rec.cost_ms ?? ""} ms</span></div>${detail}</div>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function msg(err) {
  return err && err.message ? err.message : String(err);
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, 2600);
}

init();
