/* 应用外壳：Tab 路由 + 顶部统计 */
const App = {
  companies: [],
  countries: [],
  functions: [],
  levels: [],
  workLocations: [],
  scopes: [],
  legalCategories: [],
  positionTypes: [],
  externalCompanies: [],

  async loadDicts() {
    const [companies, countries, functions, levels, workLocations, scopes, legalCategories, positionTypes, externalCompanies] =
      await Promise.all([
        get('/companies'), get('/countries'), get('/position-functions'),
        get('/levels'), get('/work-locations'), get('/scopes'), get('/legal-categories'),
        get('/position-types'), get('/external-companies'),
      ]);
    this.companies = companies;
    this.countries = countries;
    this.functions = functions;
    this.levels = levels;
    this.workLocations = workLocations;
    this.scopes = scopes;
    this.legalCategories = legalCategories;
    this.positionTypes = positionTypes;
    this.externalCompanies = externalCompanies || [];
  },

  async loadStats() {
    try {
      const [p, e] = await Promise.all([
        get('/positions?page_size=1'),
        get('/employees?page_size=1'),
      ]);
      const el = document.getElementById('stats');
      el.innerHTML = `岗位 <b>${p.total}</b> · 员工 <b>${e.total}</b>`;
    } catch (_) { /* ignore */ }
  },

  init() {
    // issue #162：幂等守卫——登录成功会再次调用 init()，此前 nav/hashchange
    // 监听器随登录次数叠加（一次点击触发 k 次 render）
    if (this._initialized) return;
    this._initialized = true;
    document.querySelectorAll('nav button').forEach((btn) => {
      btn.addEventListener('click', () => this.show(btn.dataset.tab));
    });
    // issue #150：监听 hashchange——浏览器前进/后退、手改 URL hash 时同步切 Tab
    window.addEventListener('hashchange', () => {
      const tab = (location.hash || '').replace('#', '') || 'positions';
      const cur = document.querySelector('nav button.active');
      if (!cur || cur.dataset.tab !== tab) this.show(tab);
    });
    const tab = (location.hash || '').replace('#', '') || 'positions';
    this.show(tab);
  },

  async show(tab) {
    history.replaceState(null, '', '#' + tab);
    document.querySelectorAll('nav button').forEach((b) =>
      b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.tab').forEach((s) =>
      s.classList.toggle('active', s.id === `tab-${tab}`));
    this.loadStats();
    try {
      if (tab === 'data_clean') await DataClean.render();
      else if (tab === 'master') await MasterData.render();
      else if (tab === 'positions') await Positions.render();
      else if (tab === 'employees') await Employees.render();
      else if (tab === 'orgchart') await OrgChart.render();
      else if (tab === 'import') Import.render();
      else if (tab === 'users') await Users.render();
    } catch (e) {
      document.getElementById(`tab-${tab}`).innerHTML =
        `<div class="panel"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    }
  },

  renderEnvBadge() {
    // 同步渲染环境徽章：读取后端注入的 window.APP_ENV / window.APP_DB，无网络请求
    const el = document.getElementById('env-badge');
    if (!el) return;
    const env = (window.APP_ENV || '').toLowerCase();
    const db = window.APP_DB || '';
    const labels = { dev: '开发 DEV', test: '测试 TEST', prod: '生产 PROD' };
    el.className = `env-badge ${env}`;
    el.textContent = labels[env] || (env ? env.toUpperCase() : '未知环境');
    if (db) {
      el.title = `连接数据库：${db}`;
      const t = document.createElement('span');
      t.style.cssText = 'font-weight:400;font-size:11px;opacity:.85';
      t.textContent = db;
      el.appendChild(t);
    }
  },

  async initWithAuth() {
    this.renderEnvBadge();
    // 先检查登录态
    if (window.Auth) {
      await Auth.fetchMe();
      Auth.renderBadge();
      if (!Auth.user) {
        // 未登录，显示登录弹窗，等待登录成功后再初始化
        return;
      }
    }
    // 已登录，继续初始化
    if (window.Auth) Auth.syncNavByRole();
    try { await this.loadDicts(); } catch (e) { toast('初始化字典失败：' + e.message); }
    this.init();
    this.loadStats();
  },
};

document.addEventListener('DOMContentLoaded', async () => {
  await App.initWithAuth();
});

/* 经典脚本顶层 const 不挂 window，显式暴露供 auth.js 的 window.App 判断使用 */
window.App = App;
