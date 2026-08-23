/* 用户管理（仅 admin）：建号 + 分配可管法人实体（v2.3 PRD §7B.2） */
const Users = {
  async render() {
    const el = document.getElementById('tab-users');
    if (!window.Auth || !Auth.user || Auth.user.role !== 'admin') {
      el.innerHTML = '<div class="panel"><div class="empty">仅管理员可访问</div></div>';
      return;
    }
    const [users, companies] = await Promise.all([get('/admin/users'), get('/companies')]);
    App._allCompanies = companies;
    el.innerHTML = `
      <div class="panel">
        <div class="section-title">用户管理（${users.total}）</div>
        <div style="margin-bottom:12px">
          <button class="btn" id="btn-add-user">＋ 建号</button>
          <span class="hint">hr 账号需分配可管实体后方可修改对应公司的员工/成本；admin 自带全司。</span>
        </div>
        <table>
          <thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>可管实体</th><th>操作</th></tr></thead>
          <tbody>${users.items.map((u) => `
            <tr>
              <td class="num">${u.id}</td>
              <td>${esc(u.username)}</td>
              <td>${esc(u.role)}</td>
              <td>${u.is_active ? '✅ 启用' : '⛔ 停用'}</td>
              <td>${(u.companies || []).map((c) => esc(c.name)).join('、') || (u.role === 'admin' ? '<i>全司</i>' : '<i>未分配</i>')}</td>
              <td>
                <button class="btn small" onclick="Users.assign(${u.id})">分配实体</button>
                <button class="btn small" onclick="Users.toggle(${u.id}, ${!u.is_active})">${u.is_active ? '停用' : '启用'}</button>
              </td>
            </tr>`).join('')}</tbody>
        </table>
      </div>`;
    document.getElementById('btn-add-user').onclick = () => this.create();
  },

  async create() {
    const companies = App._allCompanies || [];
    openModal(`
      <h3>建号</h3>
      <div class="field"><label>用户名 *</label><input id="nu-name"></div>
      <div class="field"><label>密码 *（≥6 位）</label><input id="nu-pwd" type="password"></div>
      <div class="field"><label>角色</label>
        <select id="nu-role"><option value="hr">hr（子公司 HR）</option><option value="admin">admin</option></select></div>
      <div class="field"><label>可管实体（Ctrl 多选）</label>
        <select id="nu-companies" multiple size="${Math.min(6, companies.length)}">
          ${companies.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}
        </select></div>
      <button class="btn" id="nu-save">创建</button>`);
    document.getElementById('nu-save').onclick = async () => {
      const sel = document.getElementById('nu-companies');
      const company_ids = [...sel.selectedOptions].map((o) => Number(o.value));
      try {
        await post('/admin/users', {
          username: document.getElementById('nu-name').value.trim(),
          password: document.getElementById('nu-pwd').value,
          role: document.getElementById('nu-role').value, company_ids,
        });
        toast('已建号'); closeModal(); this.render();
      } catch (e) { toast('建号失败：' + e.message); }
    };
  },

  async assign(id) {
    const [users, companies] = await Promise.all([get('/admin/users'), get('/companies')]);
    const u = users.items.find((x) => x.id === id);
    if (!u) return;
    const current = new Set((u.companies || []).map((c) => c.id));
    openModal(`
      <h3>分配可管实体 — ${esc(u.username)}</h3>
      <div class="field"><label>可管实体（Ctrl 多选，全不选=撤销全部）</label>
        <select id="as-companies" multiple size="${Math.min(8, companies.length)}">
          ${companies.map((c) => `<option value="${c.id}" ${current.has(c.id) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
        </select></div>
      <button class="btn" id="as-save">保存</button>`);
    document.getElementById('as-save').onclick = async () => {
      const sel = document.getElementById('as-companies');
      const company_ids = [...sel.selectedOptions].map((o) => Number(o.value));
      try {
        await post(`/admin/users/${id}/companies`, { company_ids });
        toast('已更新可管实体'); closeModal(); this.render();
      } catch (e) { toast('失败：' + e.message); }
    };
  },

  async toggle(id, active) {
    try {
      await patch(`/admin/users/${id}/active`, { is_active: active });
      toast(active ? '已启用' : '已停用'); this.render();
    } catch (e) { toast('失败：' + e.message); }
  },
};
