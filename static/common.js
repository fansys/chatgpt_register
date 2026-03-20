/* ─────────────────────────────────────────────────────────────────
   common.js  —  全局工具 & UI 系统
   • Toast
   • Confirm 模态窗
   • JSON 查看器模态窗
   • 日志模态窗（多 Tab）+ 右上角日志图标
   • Fetch 封装 / 任务轮询 / 排序工具
───────────────────────────────────────────────────────────────── */

/* ── DOM helpers ─────────────────────────────────────────────── */
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

/* ── HTML 转义 ────────────────────────────────────────────────── */
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

/* ── 时间格式化 ───────────────────────────────────────────────── */
function fmtTime(s) {
  if (!s) return '—';
  return s.replace('T', ' ').replace(/\+.*$/, '');
}
function isExpired(s) { return !!s && new Date(s) < new Date(); }

/* ── Fetch 封装 ───────────────────────────────────────────────── */
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) { const t = await r.text(); throw new Error(t); }
  return r.json();
}

/* ── 排序工具 ─────────────────────────────────────────────────── */
function _sortedBy(arr, field, dir) {
  if (!field) return arr;
  return [...arr].sort((a, b) => {
    const av = (a[field] ?? '').toString();
    const bv = (b[field] ?? '').toString();
    return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
  });
}
function _setSortIcon(idPrefix, field, dir) {
  $$(`[id^="${idPrefix}-"]`).forEach(el => { el.textContent = '⇅'; el.style.color = '#cbd5e1'; });
  const el = $(`#${idPrefix}-${field}`);
  if (el) { el.textContent = dir === 'asc' ? '↑' : '↓'; el.style.color = '#2563eb'; }
}

/* ─────────────────────────────────────────────────────────────────
   Toast
───────────────────────────────────────────────────────────────── */
function showToast(msg, type = 'ok') {
  const el = document.getElementById('_toast');
  if (!el) return;
  el.textContent = msg;
  el.style.background = type === 'error' ? '#ef4444' : type === 'warn' ? '#f59e0b' : '#22c55e';
  el.style.opacity = '1'; el.style.transform = 'translateY(0)';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(1rem)'; }, 3500);
}
function copyText(text) { navigator.clipboard.writeText(text).then(() => showToast('已复制')); }

function _errorText(err) {
  if (!err) return 'unknown error';
  if (typeof err === 'string') return err;
  if (err.message) return err.message;
  return String(err);
}

function showRequestError(err, prefix = '请求失败') {
  showToast(`${prefix}: ${_errorText(err)}`, 'error');
}

function showTaskStartError(err) {
  showRequestError(err, '任务启动失败');
}

function formatStats(stats, pairs) {
  return pairs.map(([label, key, fallback = 0]) => `${label} ${stats?.[key] ?? fallback}`).join(' | ');
}

/* ─────────────────────────────────────────────────────────────────
   Confirm 模态窗
───────────────────────────────────────────────────────────────── */
function showConfirm(msg, okLabel = '确认', danger = true) {
  return new Promise(resolve => {
    const modal = document.getElementById('_confirm-modal');
    document.getElementById('_cm-msg').textContent = msg;
    const okBtn = document.getElementById('_cm-ok');
    okBtn.textContent = okLabel;
    okBtn.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
    modal.style.display = 'flex';
    const finish = v => { modal.style.display = 'none'; okBtn.onclick = null; cancelBtn.onclick = null; resolve(v); };
    okBtn.onclick = () => finish(true);
    const cancelBtn = document.getElementById('_cm-cancel');
    cancelBtn.onclick = () => finish(false);
    modal.onclick = e => { if (e.target === modal) finish(false); };
  });
}

/* ─────────────────────────────────────────────────────────────────
   JSON / 文本 查看器
───────────────────────────────────────────────────────────────── */
let _viewerText = '';
function openModal(title, text) {
  _viewerText = text;
  document.getElementById('_viewer-title').textContent = title;
  document.getElementById('_viewer-body').textContent = text;
  document.getElementById('_viewer-modal').style.display = 'flex';
}
function closeModal() { document.getElementById('_viewer-modal').style.display = 'none'; }
function _copyViewer() { copyText(_viewerText); }

/* ─────────────────────────────────────────────────────────────────
   日志模态窗（多 Tab）
───────────────────────────────────────────────────────────────── */
const _logs = [];        // { id, title, lines[], status }
let _activeLogId = null;
let _logOpen = false;

const _DOT_COLOR = { running: '#fbbf24', done: '#a3e635', error: '#f87171', pending: '#94a3b8' };

/** 创建新日志 entry，自动打开窗口，返回 entry 对象 */
function openLog(title) {
  const entry = { id: (Date.now() + Math.random()).toString(36), title, lines: [], status: 'running' };
  _logs.push(entry);
  _activeLogId = entry.id;
  _logOpen = true;
  document.getElementById('_log-modal').style.display = 'flex';
  _renderLog();
  _updateLogIcon();
  return entry;
}

/** 更新 entry 状态并刷新 UI */
function setLogStatus(entry, status) {
  entry.status = status;
  _renderLogTabs();
  _renderLogDot();
  _updateLogIcon();
}

function _showLogModal() {
  if (!_logs.length) return;
  _logOpen = true;
  document.getElementById('_log-modal').style.display = 'flex';
  _renderLog();
}
function _hideLogModal() {
  _logOpen = false;
  document.getElementById('_log-modal').style.display = 'none';
  _updateLogIcon();
}

function _renderLog() {
  _renderLogTabs();
  const entry = _logs.find(e => e.id === _activeLogId);
  if (entry) {
    _renderLogBody(entry);
    _renderLogTitle(entry);
  }
  _renderLogDot();
}

function _deleteLog(id) {
  const idx = _logs.findIndex(e => e.id === id);
  if (idx === -1) return;
  _logs.splice(idx, 1);
  if (_activeLogId === id) {
    // 优先激活右侧，其次左侧，没有则关闭窗口
    const next = _logs[idx] || _logs[idx - 1];
    if (next) { _activeLogId = next.id; }
    else { _activeLogId = null; _hideLogModal(); return; }
  }
  _renderLog();
  _updateLogIcon();
}

function _renderLogTabs() {
  const bar = document.getElementById('_log-tab-bar');
  if (!bar) return;
  if (_logs.length <= 1) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  bar.innerHTML = '';
  _logs.forEach(entry => {
    const isActive = entry.id === _activeLogId;
    const wrap = document.createElement('div');
    wrap.className = 'log-tab-btn' + (isActive ? ' active' : '');

    const label = document.createElement('span');
    label.style.cssText = 'display:inline-flex;align-items:center;gap:5px;flex:1;min-width:0;cursor:pointer';
    label.innerHTML = `<span class="log-tab-dot" style="background:${_DOT_COLOR[entry.status] || '#94a3b8'};flex-shrink:0"></span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(entry.title)}</span>`;
    label.onclick = () => { _activeLogId = entry.id; _renderLog(); };

    const del = document.createElement('button');
    del.textContent = '×';
    del.title = '关闭';
    del.style.cssText = 'margin-left:4px;padding:0 2px;background:none;border:none;cursor:pointer;color:#64748b;font-size:14px;line-height:1;flex-shrink:0;border-radius:3px';
    del.onmouseover = () => { del.style.color = '#f87171'; del.style.background = '#1e293b'; };
    del.onmouseout  = () => { del.style.color = '#64748b'; del.style.background = 'none'; };
    del.onclick = e => { e.stopPropagation(); _deleteLog(entry.id); };

    wrap.appendChild(label);
    wrap.appendChild(del);
    bar.appendChild(wrap);
  });
}

function _renderLogBody(entry) {
  const el = document.getElementById('_log-body');
  if (!el) return;
  el.textContent = entry.lines.join('\n') || '(等待日志...)';
  el.scrollTop = el.scrollHeight;
}

function _renderLogTitle(entry) {
  const el = document.getElementById('_log-header-title');
  if (el) el.textContent = entry ? entry.title : '任务日志';
}

function _renderLogDot() {
  const dot = document.getElementById('_log-header-dot');
  if (!dot) return;
  const entry = _logs.find(e => e.id === _activeLogId);
  dot.style.background = entry ? (_DOT_COLOR[entry.status] || '#94a3b8') : '#94a3b8';
}

function _updateLogIcon() {
  const btn = document.getElementById('_log-icon-btn');
  if (!btn) return;
  const running = _logs.filter(e => e.status === 'running' || e.status === 'pending').length;
  const badge = document.getElementById('_log-icon-badge');
  if (badge) {
    const count = running > 0 ? running : _logs.length;
    badge.textContent = count;
    badge.style.display = _logs.length ? 'flex' : 'none';
    badge.style.background = running > 0 ? '#f59e0b' : '#64748b';
  }
  btn.style.background = running > 0 ? '#fef3c7' : '#f1f5f9';
  btn.style.borderColor = running > 0 ? '#fbbf24' : '#e2e8f0';
}

/* ─────────────────────────────────────────────────────────────────
   任务轮询（与日志 entry 绑定）
───────────────────────────────────────────────────────────────── */
const _pollers = {};

function startPolling(tid, logEntry, onDone) {
  if (_pollers[tid]) return;
  _pollers[tid] = setInterval(async () => {
    try {
      const t = await api('GET', `/api/tasks/${tid}`);
      logEntry.lines = t.logs;
      if (_activeLogId === logEntry.id && _logOpen) _renderLogBody(logEntry);
      if (t.status === 'done' || t.status === 'error') {
        setLogStatus(logEntry, t.status);
        clearInterval(_pollers[tid]); delete _pollers[tid];
        if (onDone) onDone(t);
      } else if (logEntry.status !== t.status) {
        logEntry.status = t.status;
        _renderLogDot(); _updateLogIcon();
      }
    } catch (_) {}
  }, 1000);
}

/* ─────────────────────────────────────────────────────────────────
   查看已有任务日志（任务历史表格用）
───────────────────────────────────────────────────────────────── */
async function viewTaskLog(tid, label) {
  try {
    const t = await api('GET', `/api/tasks/${tid}`);
    const entry = { id: tid + '_view', title: label || `任务 ${tid}`, lines: t.logs, status: t.status };
    // 如果已有相同 id 的 entry，直接激活
    const existing = _logs.find(e => e.id === entry.id);
    if (existing) {
      existing.lines = t.logs; existing.status = t.status;
      _activeLogId = existing.id;
    } else {
      _logs.push(entry); _activeLogId = entry.id;
    }
    _logOpen = true;
    document.getElementById('_log-modal').style.display = 'flex';
    _renderLog(); _updateLogIcon();
  } catch (e) { showToast('获取日志失败: ' + e.message, 'error'); }
}

/* ─────────────────────────────────────────────────────────────────
   页面初始化：注入全局 UI
───────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {

  /* ── 注入所有全局 DOM ─────────────────────────────────────── */
  document.body.insertAdjacentHTML('beforeend', `
<!-- Toast -->
<div id="_toast" style="position:fixed;bottom:24px;right:24px;padding:10px 16px;border-radius:12px;color:#fff;font-size:12px;font-weight:500;box-shadow:0 8px 24px #0000002a;z-index:500;opacity:0;transform:translateY(1rem);transition:all .2s;pointer-events:none;max-width:300px"></div>

<!-- Confirm Modal -->
<div id="_confirm-modal" style="display:none;position:fixed;inset:0;background:#00000060;z-index:600;align-items:center;justify-content:center;padding:20px">
  <div style="background:#fff;border-radius:14px;max-width:420px;width:100%;padding:28px;box-shadow:0 24px 64px #00000030">
    <p id="_cm-msg" style="font-size:14px;color:#1e293b;margin-bottom:22px;white-space:pre-line;line-height:1.65"></p>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button id="_cm-cancel" class="btn btn-ghost">取消</button>
      <button id="_cm-ok" class="btn btn-danger">确认</button>
    </div>
  </div>
</div>

<!-- JSON Viewer Modal -->
<div id="_viewer-modal" style="display:none;position:fixed;inset:0;background:#00000060;z-index:600;align-items:center;justify-content:center;padding:20px">
  <div style="background:#fff;border-radius:14px;max-width:700px;width:100%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 24px 64px #00000030">
    <div style="padding:14px 18px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between">
      <span id="_viewer-title" style="font-weight:600;color:#1e293b;font-size:13px"></span>
      <button onclick="closeModal()" style="color:#94a3b8;font-size:20px;background:none;border:none;cursor:pointer;line-height:1">&times;</button>
    </div>
    <div style="flex:1;overflow-y:auto">
      <pre id="_viewer-body" style="font-family:monospace;font-size:12px;color:#334155;padding:20px;white-space:pre-wrap;word-break:break-all;margin:0"></pre>
    </div>
    <div style="padding:10px 18px;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:8px">
      <button onclick="_copyViewer()" class="btn btn-ghost btn-sm">复制全部</button>
      <button onclick="closeModal()" class="btn btn-ghost btn-sm">关闭</button>
    </div>
  </div>
</div>

<!-- Log Modal -->
<div id="_log-modal" style="display:none;position:fixed;inset:0;background:#00000070;z-index:550;align-items:center;justify-content:center;padding:20px">
  <div style="background:#0f172a;border-radius:14px;max-width:800px;width:100%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 24px 64px #00000050;border:1px solid #1e293b">
    <!-- header -->
    <div style="padding:10px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1e293b;flex-shrink:0">
      <div style="display:flex;align-items:center;gap:8px">
        <span id="_log-header-dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#fbbf24"></span>
        <span id="_log-header-title" style="color:#e2e8f0;font-size:13px;font-weight:600">任务日志</span>
      </div>
      <div style="display:flex;align-items:center;gap:4px">
        <button onclick="_deleteLog(_activeLogId)" title="删除此日志" style="color:#64748b;font-size:11px;background:none;border:1px solid #334155;border-radius:4px;cursor:pointer;padding:2px 7px;transition:all .15s;line-height:1.4" onmouseover="this.style.color='#f87171';this.style.borderColor='#f87171'" onmouseout="this.style.color='#64748b';this.style.borderColor='#334155'">删除</button>
        <button onclick="_hideLogModal()" title="关闭窗口" style="color:#64748b;font-size:20px;background:none;border:none;cursor:pointer;line-height:1;transition:color .15s;padding:0 2px" onmouseover="this.style.color='#e2e8f0'" onmouseout="this.style.color='#64748b'">&times;</button>
      </div>
    </div>
    <!-- tab bar (hidden if ≤1 tab) -->
    <div id="_log-tab-bar" style="display:none;gap:4px;padding:8px 12px;border-bottom:1px solid #1e293b;flex-shrink:0;overflow-x:auto"></div>
    <!-- log body -->
    <div id="_log-body" style="font-family:monospace;font-size:12px;line-height:1.75;color:#34d399;padding:16px;flex:1;overflow-y:auto;white-space:pre-wrap;word-break:break-all;min-height:300px">等待日志...</div>
    <!-- footer -->
    <div style="padding:8px 16px;border-top:1px solid #1e293b;display:flex;justify-content:flex-end;flex-shrink:0">
      <button onclick="document.getElementById('_log-body').textContent=''" style="font-size:11px;color:#64748b;background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:6px;transition:all .15s" onmouseover="this.style.color='#e2e8f0'" onmouseout="this.style.color='#64748b'">清空</button>
    </div>
  </div>
</div>
  `);

  /* ── 注入日志图标到 #header-actions ─────────────────────── */
  const headerActions = document.getElementById('header-actions');
  if (headerActions) {
    const iconBtn = document.createElement('button');
    iconBtn.id = '_log-icon-btn';
    iconBtn.title = '任务日志';
    iconBtn.onclick = () => { if (_logOpen) _hideLogModal(); else _showLogModal(); };
    iconBtn.style.cssText = 'position:relative;width:34px;height:34px;border-radius:8px;border:1px solid #e2e8f0;background:#f1f5f9;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;flex-shrink:0';
    iconBtn.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="9 9 12 12 9 15"/>
        <line x1="15" y1="12" x2="15" y2="12.01"/>
      </svg>
      <span id="_log-icon-badge" style="display:none;position:absolute;top:-5px;right:-5px;min-width:16px;height:16px;border-radius:8px;background:#64748b;color:#fff;font-size:10px;font-weight:700;align-items:center;justify-content:center;padding:0 4px;border:2px solid #f1f5f9"></span>
    `;
    headerActions.insertBefore(iconBtn, headerActions.firstChild);
  }
});
