/* 认证：登录态管理（JWT，PRD §7B） */
const Auth = {
  user: null,

  token() { return localStorage.getItem('hr_token') || ''; },
  setToken(t) { if (t) localStorage.setItem('hr_token', t); },
  clear() { localStorage.removeItem('hr_token'); this.user = null; this.renderBadge(); },
  isLoggedIn() { return !!this.token(); },

  async fetchMe() {
    if (!this.token()) { this.user = null; return null; }
    try {
      const me = await get('/auth/me');
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
    const modal = openModal(`
      <header><h2>登录</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
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
        const r = await post('/auth/login', { username: u, password: p });
        this.setToken(r.access_token);
        this.user = { username: r.username, role: r.role };
        closeModal(); toast('登录成功', 'ok'); this.renderBadge();
        // 登录成功后初始化应用
        if (window.App) {
          const navUsers = document.getElementById('nav-users');
          if (navUsers && this.user && this.user.role === 'admin') {
            navUsers.style.display = '';
          }
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
