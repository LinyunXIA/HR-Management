/* fetch 封装 */
async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  if (opts.body && !opts.noJson) opts.headers['Content-Type'] = 'application/json';
  const res = await fetch('/api' + path, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}
const get = (p) => api(p);
const post = (p, b) => api(p, { method: 'POST', body: b });
const put = (p, b) => api(p, { method: 'PUT', body: b });
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
