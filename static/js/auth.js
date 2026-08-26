/* 认证：登录态管理（JWT，PRD §7B） */
const Auth = {
  user: null,

  token() { return localStorage.getItem('hr_token') || ''; },
  setToken(t) { if (t) localStorage.setItem('hr_token', t); },
  clear() {
    localStorage.removeItem('hr_token');
    this.user = null;
    // issue #150：登出同步隐藏用户管理入口（此前同页 admin→hr 切换导航泄漏）
    const nu = document.getElementById('nav-users');
    if (nu) nu.style.display = 'none';
    this.renderBadge();
  },
  isLoggedIn() { return !!this.token(); },

  /* issue #144/#150：按角色切换「用户管理」导航显隐（admin 显示、其余隐藏） */
  syncNavByRole() {
    const nu = document.getElementById('nav-users');
    if (nu) nu.style.display = (this.user && this.user.role === 'admin') ? '' : 'none';
  },

  async fetchMe() {
    if (!this.token()) { this.user = null; return null; }
    try {
      const me = await get('/auth/me');
      // API 类型账号不允许进入 Web 界面（与后端 /auth/ui-login 拦截一致，兜底已持有 JWT 的会话）
      if ((me.user_type || '').toLowerCase() === 'api') {
        this.clear();
        toast('API 账号不支持网页界面登录');
        return null;
      }
      this.user = me;
      return me;
    } catch (_) {
      this.clear();
      return null;
    }
  },

  renderBadge() {
    const el = document.getElementById('auth-badge');
    if (!el) return;
    if (this.user) {
      el.innerHTML = `👤 ${esc(this.user.username)} · ${esc(this.user.role)} <button class="btn small" id="btn-logout">退出</button>`;
      const b = document.getElementById('btn-logout');
      if (b) b.onclick = () => { this.clear(); toast('已退出登录', 'ok'); this.renderBadge(); };
    } else if (this.isLoggedIn()) {
      el.innerHTML = `🔑 已登录 <button class="btn small" id="btn-logout">退出</button>`;
      document.getElementById('btn-logout').onclick = () => { this.clear(); toast('已退出登录', 'ok'); this.renderBadge(); };
    } else {
      el.innerHTML = `<button class="btn small" id="btn-login">登录</button>`;
      document.getElementById('btn-login').onclick = () => this.showLogin();
    }
  },

  showLogin() {
    // 环境标识：与顶部徽章同源（后端注入 window.APP_ENV / window.APP_DB），防呆提示防误登环境
    const envLabels = { dev: '开发 DEV', test: '测试 TEST', prod: '生产 PROD' };
    const envColors = { dev: '#1e7e34', test: '#8a6d00', prod: '#d64545' };
    const _env = (window.APP_ENV || '').toLowerCase();
    const envLabel = envLabels[_env] || (_env ? _env.toUpperCase() : '未知环境');
    const envColor = envColors[_env] || '#666';
    const dbTxt = window.APP_DB ? ` · ${window.APP_DB}` : '';
    const modal = openModal(`
      <header><h2>登录</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="hint" style="margin-bottom:10px">当前环境：<b style="color:${esc(envColor)}">${esc(envLabel)}</b>${esc(dbTxt)}</div>
        <div class="hint" style="margin-bottom:10px">默认账号：<code>admin / admin123</code>（首次启动自动创建）</div>
        <div class="form-grid">
          <div class="field"><label>用户名 *</label><input type="text" id="lg-user" value="admin"></div>
          <div class="field"><label>密码 *</label><input type="password" id="lg-pwd" value="admin123"></div>
        </div>
        <div class="hint" id="lg-msg" style="margin-top:8px"></div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="lg-submit">登录</button></footer>`);
    const submit = async () => {
      const u = document.getElementById('lg-user').value.trim();
      const p = document.getElementById('lg-pwd').value;
      if (!u || !p) { document.getElementById('lg-msg').textContent = '请填写用户名和密码'; return; }
      try {
        // UI 专用登录端点：API 类型账号被后端拒绝（v2.5 登录入口拆分）
        const r = await post('/auth/ui-login', { username: u, password: p });
        this.setToken(r.access_token);
        this.user = { username: r.username, role: r.role };
        closeModal(); toast('登录成功', 'ok'); this.renderBadge();
        // 登录成功后初始化应用
        if (window.App) {
          this.syncNavByRole();
          await App.loadDicts();
          App.init();
          App.loadStats();
        }
      } catch (e) {
        document.getElementById('lg-msg').textContent = e.message;
        document.getElementById('lg-msg').style.color = 'var(--danger)';
      }
    };
    modal.querySelector('#lg-submit').onclick = submit;
    modal.querySelector('#lg-pwd').onkeydown = (e) => { if (e.key === 'Enter') submit(); };
  },
};

/* 经典脚本顶层 const 不挂 window，显式暴露供 api.js/app.js 的 window.Auth 判断使用 */
window.Auth = Auth;
