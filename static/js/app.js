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

  async loadDicts() {
    const [companies, countries, functions, levels, workLocations, scopes, legalCategories, positionTypes] =
      await Promise.all([
        get('/companies'), get('/countries'), get('/position-functions'),
        get('/levels'), get('/work-locations'), get('/scopes'), get('/legal-categories'),
        get('/position-types'),
      ]);
    this.companies = companies;
    this.countries = countries;
    this.functions = functions;
    this.levels = levels;
    this.workLocations = workLocations;
    this.scopes = scopes;
    this.legalCategories = legalCategories;
    this.positionTypes = positionTypes;
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
    document.querySelectorAll('nav button').forEach((btn) => {
      btn.addEventListener('click', () => this.show(btn.dataset.tab));
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

  async initWithAuth() {
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
    const navUsers = document.getElementById('nav-users');
    if (navUsers && window.Auth && Auth.user && Auth.user.role === 'admin') {
      navUsers.style.display = '';
    }
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
