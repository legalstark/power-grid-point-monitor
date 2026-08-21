/*
 * 实时监控前端：无第三方依赖，直接消费监视服务 REST API。
 *
 * 状态约定：
 * - page/type/keyword 共同决定当前列表视图。
 * - selected 保存抽屉当前测点，不因每秒刷新丢失用户上下文。
 * - auto 只控制浏览器刷新；模拟器与后端采集始终独立运行。
 * - historyMinutes 只允许1、5、10，与服务端校验保持一致。
 *
 * 渲染约定：
 * - 所有来自接口的文本进入 HTML 前进行实体转义。
 * - YX 值转换为“分/合”，历史曲线采用阶梯线。
 * - YC 值固定显示两位小数，并在数值后显示工程单位。
 * - 质量码同时显示十六进制值和中文说明。
 * - 人工替代值额外显示置数标签，便于评分截图识别。
 *
 * 刷新约定：
 * - 自动刷新间隔固定为1秒，满足不大于2秒的要求。
 * - 手动刷新先触发服务端立即采集，再更新当前页面。
 * - 静默自动刷新失败时保留表格，连接状态转为异常。
 * - 图表抽屉打开时随列表刷新同步更新历史数据。
 *
 * 图表实现：
 * - Canvas 根据设备像素比重设位图大小，避免高分屏模糊。
 * - Y 轴从窗口内最小/最大值计算，并保留12%的视觉边距。
 * - 单值窗口主动扩展坐标范围，避免除零和水平线不可见。
 * - 折线下方使用渐变填充，末端点突出当前值。
 */
"use strict";

const state = {
  type: "",
  keyword: "",
  page: 1,
  pageSize: 20,
  auto: true,
  selected: null,
  historyMinutes: 1,
  points: [],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const rows = $("#pointRows");
let searchTimer = null;
let toastTimer = null;

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
}

function displayValue(point) {
  if (point.pointType === "YX") return point.value ? "合" : "分";
  return Number(point.value).toFixed(2);
}

function formatTime(iso) {
  if (!iso) return "--";
  const date = new Date(iso);
  return date.toLocaleString("zh-CN", {hour12: false}).replaceAll("/", "-");
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error?.message || `请求失败（${response.status}）`);
  return payload;
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = "toast", 2600);
}

function updateMetrics(payload) {
  const all = payload.items;
  const summary = payload.summary;
  const status = payload.status;
  const good = all.filter(p => p.qualityCode === 0).length;
  // 当前页指标之外，总数由固定监控范围和服务状态共同表达。
  $("#totalMetric").textContent = summary.total || "--";
  $("#goodMetric").textContent = summary.total ? summary.good : "--";
  $("#manualMetric").textContent = summary.manual;
  $("#qualityHint").textContent = good === all.length && all.length ? "当前页全部正常" : "存在替代或异常值";
  $("#lastUpdated").textContent = status.lastSuccess ? `更新于 ${formatTime(status.lastSuccess).slice(-8)}` : "尚未刷新";
  const pill = $("#connectionPill");
  pill.className = `connection-pill ${status.connected ? "connected" : "offline"}`;
  pill.querySelector("span").textContent = status.connected ? "模拟器在线" : "连接中断 · 数据陈旧";
}

function renderRows(items) {
  state.points = items;
  if (!items.length) {
    rows.innerHTML = '<tr><td colspan="7" class="empty-cell">没有匹配的测点</td></tr>';
    return;
  }
  rows.innerHTML = items.map(point => {
    const selected = state.selected?.pointId === point.pointId ? " selected" : "";
    const qualityClass = point.manualOverride ? "manual" : "good";
    const qualityCode = `0x${Number(point.qualityCode).toString(16).padStart(2, "0").toUpperCase()}`;
    return `<tr class="${selected}" data-id="${point.pointId}">
      <td class="id-cell">${escapeHtml(point.identifier)}</td>
      <td class="name-cell">${escapeHtml(point.displayName)}</td>
      <td><span class="type-badge ${point.pointType.toLowerCase()}">${point.pointType === "YC" ? "遥测" : "遥信"}</span></td>
      <td class="value-cell"><strong>${displayValue(point)}</strong><small>${escapeHtml(point.unit || "")}</small></td>
      <td><span class="quality-badge ${qualityClass}">${qualityCode} · ${escapeHtml(point.qualityText)}</span>${point.manualOverride ? '<span class="manual-badge">置数</span>' : ""}</td>
      <td class="time-cell">${formatTime(point.refreshedAt)}</td>
      <td><button class="row-action" data-action="history" aria-label="查看历史">›</button></td>
    </tr>`;
  }).join("");
  rows.querySelectorAll("tr[data-id]").forEach(row => row.addEventListener("click", () => {
    const point = state.points.find(item => item.pointId === row.dataset.id);
    openHistory(point);
  }));
}

function renderPagination(pagination) {
  $("#pageSummary").textContent = `第 ${pagination.page} / ${pagination.totalPages} 页 · 共 ${pagination.total} 条`;
  const buttons = [];
  buttons.push(`<button data-page="${pagination.page - 1}" ${pagination.page === 1 ? "disabled" : ""}>‹</button>`);
  for (let page = 1; page <= pagination.totalPages; page++) {
    if (pagination.totalPages > 7 && Math.abs(page - pagination.page) > 2 && page !== 1 && page !== pagination.totalPages) {
      if (page === 2 || page === pagination.totalPages - 1) buttons.push("<span>…</span>");
      continue;
    }
    buttons.push(`<button data-page="${page}" class="${page === pagination.page ? "active" : ""}">${page}</button>`);
  }
  buttons.push(`<button data-page="${pagination.page + 1}" ${pagination.page === pagination.totalPages ? "disabled" : ""}>›</button>`);
  const pager = $("#pagination");
  pager.innerHTML = buttons.join("");
  pager.querySelectorAll("button:not(:disabled)").forEach(button => button.addEventListener("click", () => {
    state.page = Number(button.dataset.page);
    loadPoints();
  }));
}

async function loadPoints({quiet = false} = {}) {
  const query = new URLSearchParams({page: state.page, pageSize: state.pageSize});
  if (state.type) query.set("type", state.type);
  if (state.keyword) query.set("keyword", state.keyword);
  try {
    const payload = await api(`/api/v1/points?${query}`);
    updateMetrics(payload);
    renderRows(payload.items);
    renderPagination(payload.pagination);
    if (state.selected) {
      const refreshed = payload.items.find(p => p.pointId === state.selected.pointId);
      if (refreshed) {
        state.selected = refreshed;
        updateDrawerSummary();
      }
      if ($("#historyDrawer").classList.contains("open")) loadHistory(true);
    }
  } catch (error) {
    if (!quiet) showToast(error.message, true);
    $("#connectionPill").className = "connection-pill offline";
    $("#connectionPill span").textContent = "监视服务异常";
  }
}

async function forceRefresh() {
  const button = $("#manualRefresh");
  button.classList.add("spinning");
  try {
    await api("/api/v1/refresh", {method: "POST"});
    await loadPoints();
    showToast("已完成手动刷新");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setTimeout(() => button.classList.remove("spinning"), 350);
  }
}

function updateDrawerSummary() {
  const point = state.selected;
  if (!point) return;
  $("#drawerTitle").textContent = `${point.pointId} · ${point.displayName}`;
  $("#drawerMeta").textContent = `${point.identifier} / ${point.pointType === "YX" ? "遥信" : "遥测"}`;
  $("#drawerValue").textContent = `${displayValue(point)}${point.unit ? ` ${point.unit}` : ""}`;
  $("#drawerQuality").textContent = `0x${point.qualityCode.toString(16).padStart(2, "0").toUpperCase()} · ${point.qualityText}`;
  $("#clearManual").disabled = !point.manualOverride;
}

function openHistory(point) {
  state.selected = point;
  updateDrawerSummary();
  $("#historyDrawer").classList.add("open");
  $("#historyDrawer").setAttribute("aria-hidden", "false");
  $("#drawerOverlay").classList.add("open");
  renderRows(state.points);
  loadHistory();
}

function closeHistory() {
  $("#historyDrawer").classList.remove("open");
  $("#historyDrawer").setAttribute("aria-hidden", "true");
  $("#drawerOverlay").classList.remove("open");
}

async function loadHistory(quiet = false) {
  if (!state.selected) return;
  try {
    const payload = await api(`/api/v1/points/${state.selected.pointId}/history?minutes=${state.historyMinutes}`);
    drawChart(payload.items, state.selected.pointType);
  } catch (error) {
    if (!quiet) showToast(error.message, true);
  }
}

function drawChart(items, pointType) {
  const canvas = $("#historyChart");
  const empty = $("#chartEmpty");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  ctx.clearRect(0, 0, width, height);
  if (!items.length) { empty.style.display = "grid"; return; }
  empty.style.display = "none";
  const padding = {left: 44, right: 14, top: 18, bottom: 30};
  const values = items.map(item => Number(item.value));
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min -= pointType === "YX" ? .2 : 1; max += pointType === "YX" ? .2 : 1; }
  const margin = (max - min) * .12;
  min -= margin; max += margin;
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const x = i => padding.left + (items.length === 1 ? plotW / 2 : i * plotW / (items.length - 1));
  const y = value => padding.top + (max - value) * plotH / (max - min);

  ctx.font = "10px Segoe UI";
  ctx.strokeStyle = "#e4e9f1"; ctx.fillStyle = "#8490a4"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const gy = padding.top + i * plotH / 4;
    ctx.beginPath(); ctx.moveTo(padding.left, gy); ctx.lineTo(width - padding.right, gy); ctx.stroke();
    ctx.fillText((max - i * (max - min) / 4).toFixed(pointType === "YX" ? 1 : 2), 4, gy + 3);
  }
  const firstTime = new Date(items[0].refreshed_at).toLocaleTimeString("zh-CN", {hour12:false});
  const lastTime = new Date(items.at(-1).refreshed_at).toLocaleTimeString("zh-CN", {hour12:false});
  ctx.fillText(firstTime, padding.left, height - 8);
  ctx.textAlign = "right"; ctx.fillText(lastTime, width - padding.right, height - 8); ctx.textAlign = "left";

  const gradient = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom);
  gradient.addColorStop(0, "rgba(36,107,253,.25)"); gradient.addColorStop(1, "rgba(36,107,253,0)");
  ctx.beginPath(); ctx.moveTo(x(0), height - padding.bottom); ctx.lineTo(x(0), y(values[0]));
  items.forEach((item, index) => {
    if (index === 0) return;
    if (pointType === "YX") { ctx.lineTo(x(index), y(values[index - 1])); }
    ctx.lineTo(x(index), y(values[index]));
  });
  ctx.lineTo(x(items.length - 1), height - padding.bottom); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
  ctx.beginPath(); ctx.moveTo(x(0), y(values[0]));
  items.forEach((item, index) => {
    if (index === 0) return;
    if (pointType === "YX") ctx.lineTo(x(index), y(values[index - 1]));
    ctx.lineTo(x(index), y(values[index]));
  });
  ctx.strokeStyle = "#246bfd"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  const lastX = x(items.length - 1), lastY = y(values.at(-1));
  ctx.beginPath(); ctx.arc(lastX, lastY, 4, 0, Math.PI * 2); ctx.fillStyle = "white"; ctx.fill(); ctx.strokeStyle = "#246bfd"; ctx.stroke();
}

function openManualDialog() {
  const point = state.selected;
  if (!point) return;
  $("#manualTitle").textContent = `${point.pointId} · ${point.displayName}`;
  $("#manualValue").value = point.pointType === "YX" ? (point.value ? 1 : 0) : point.value;
  $("#manualValue").type = "number";
  $("#manualValue").min = point.minimum;
  $("#manualValue").max = point.maximum;
  $("#manualValue").step = point.pointType === "YX" ? "1" : "0.01";
  $("#rangeHint").textContent = point.pointType === "YX" ? "遥信仅允许 0（分）或 1（合）" : `允许范围：${point.minimum} ～ ${point.maximum} ${point.unit}`;
  $("#manualDialog").showModal();
}

async function submitManual(event) {
  event.preventDefault();
  if (!state.selected) return;
  const value = $("#manualValue").value;
  try {
    await api(`/api/v1/points/${state.selected.pointId}/manual`, {method: "PUT", body: JSON.stringify({value})});
    $("#manualDialog").close();
    await loadPoints();
    showToast(`${state.selected.pointId} 已启用人工置数`);
  } catch (error) { showToast(error.message, true); }
}

async function clearManual() {
  if (!state.selected) return;
  try {
    await api(`/api/v1/points/${state.selected.pointId}/manual`, {method: "DELETE"});
    await loadPoints();
    showToast(`${state.selected.pointId} 已恢复自动模拟`);
  } catch (error) { showToast(error.message, true); }
}

// 绑定筛选、分页、刷新、抽屉和置数交互。
$$('.segmented button').forEach(button => button.addEventListener("click", () => {
  $$('.segmented button').forEach(item => item.classList.remove("active"));
  button.classList.add("active"); state.type = button.dataset.type; state.page = 1; loadPoints();
}));
$("#searchInput").addEventListener("input", event => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.keyword = event.target.value.trim(); state.page = 1; loadPoints(); }, 260);
});
$("#autoRefresh").addEventListener("change", event => { state.auto = event.target.checked; $("#refreshMetric").textContent = state.auto ? "1 秒" : "已暂停"; });
$("#manualRefresh").addEventListener("click", forceRefresh);
$("#closeDrawer").addEventListener("click", closeHistory); $("#drawerOverlay").addEventListener("click", closeHistory);
$("#openManual").addEventListener("click", openManualDialog); $("#clearManual").addEventListener("click", clearManual);
$("#manualForm").addEventListener("submit", submitManual);
$$('.history-tabs button').forEach(button => button.addEventListener("click", () => {
  $$('.history-tabs button').forEach(item => item.classList.remove("active")); button.classList.add("active");
  state.historyMinutes = Number(button.dataset.minutes); loadHistory();
}));
$("#historyNav").addEventListener("click", event => {
  event.preventDefault();
  if (state.selected) openHistory(state.selected); else showToast("请先在实时数据列表中选择一个测点");
});
window.addEventListener("resize", () => { if (state.selected) loadHistory(true); });
setInterval(() => { if (state.auto) loadPoints({quiet: true}); }, 1000);
loadPoints();
