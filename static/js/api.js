/* fetch 封装 — 携带 JWT，处理 409/401 */
function _token() { return localStorage.getItem('hr_token') || ''; }
function _authHeader() {
  const t = _token();
  return t ? { 'Authorization': 'Bearer ' + t } : {};
}

async function api(path, options = {}) {
  const opts = { headers: { ..._authHeader(), ...(options.headers || {}) }, ...options };
  // 合并 headers 时保留 Authorization
  opts.headers = { ..._authHeader(), ...(options.headers || {}) };
  if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  if (opts.body && !opts.noJson) opts.headers['Content-Type'] = 'application/json';
  const res = await fetch('/api/v1' + path, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401) {
      // 外部 API 未认证或 Token 过期
      if (path !== '/auth/login' && path !== '/auth/me') {
        toast('登录已过期，请重新登录', 'error');
        if (window.Auth) Auth.showLogin();
      }
    }
    if (res.status === 429) {
      const msg = (data && data.detail) || '请求过于频繁，请稍后重试';
      toast(msg, 'error');
    }
    if (res.status === 409) {
      const msg = (data && (data.detail || data.message)) || '数据已被他人修改，请刷新后重试';
      throw Object.assign(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)), { status: 409 });
    }
    const msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}
const get = (p) => api(p);
const post = (p, b) => api(p, { method: 'POST', body: b });
const put = (p, b) => api(p, { method: 'PUT', body: b });
const patch = (p, b) => api(p, { method: 'PATCH', body: b });
const del = (p) => api(p, { method: 'DELETE' });

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/* 状态 → 徽标 */
const STATUS_LABEL = {
  planned: '编制规划', open: '招聘中', offered: '已录用',
  filled: '在职', vacant: '空缺', frozen: '冻结', closed: '关闭',
};
const SCOPE_LABEL = { family: 'Family', global: 'Global', regional: 'Regional', country: 'Country' };
const LEGAL_LABEL = {
  '法律强制·内部全职不可外包': 'mandatory',
  '可选（集团内控推荐）': 'optional',
  '纯后勤可选': 'logistics',
};

function statusBadge(s) { return `<span class="badge ${s || ''}">${STATUS_LABEL[s] || s}</span>`; }
function legalBadge(l) { return l ? `<span class="badge ${LEGAL_LABEL[l] || ''}">${esc(l)}</span>` : '—'; }

function fmtDate(d) { return d ? String(d).slice(0, 10) : '—'; }

/* Modal 工具 */
function openModal(html, opts = {}) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `
    <div class="modal-overlay">
      <div class="modal ${opts.wide ? 'wide' : ''}">
        ${html}
      </div>
    </div>`;
  root.querySelector('.modal-overlay').addEventListener('click', (e) => {
    if (e.target === root.querySelector('.modal-overlay')) closeModal();
  });
  return root.querySelector('.modal');
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; }

function toast(msg, type = 'error') {
  const el = document.createElement('div');
  el.style.cssText = `position:fixed;top:70px;right:24px;z-index:200;padding:10px 16px;border-radius:8px;background:${type==='error'?'#d64545':'#2e9e5b'};color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.2);font-size:13px;`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

/* 乐观锁冲突统一文案（后端 409 detail 已含版本信息时优先展示后端消息） */
const VERSION_CONFLICT_MSG = '数据已被他人修改，请刷新后重试';
function handleApiError(e, fallback) {
  if (e && e.status === 409) toast(VERSION_CONFLICT_MSG, 'error');
  else toast((e && e.message) || fallback || '操作失败');
}
