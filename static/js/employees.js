/* 员工管理：列表 / 入职 / 调岗 / 离职 */
const Employees = {
  filters: { company_id: '', employee_type: '', employment_status: '', search: '' },
  page: 1,
  pageSize: 20,
  result: null,

  async render() {
    const qs = new URLSearchParams({ page: this.page, page_size: this.pageSize });
    Object.entries(this.filters).forEach(([k, v]) => { if (v) qs.set(k, v); });
    this.result = await get('/employees?' + qs.toString());
    const el = document.getElementById('tab-employees');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar">
          <select id="ef-company"><option value="">全部公司</option>
            ${App.companies.map((c) => `<option value="${c.id}" ${+this.filters.company_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
          </select>
          <select id="ef-type"><option value="">全部类型</option>
            ${['正式', '实习', '外包', '劳务'].map((t) => `<option value="${t}" ${this.filters.employee_type === t ? 'selected' : ''}>${t}</option>`).join('')}
          </select>
          <select id="ef-status"><option value="">全部在职状态</option>
            ${['试用期', '在职', '休假', '离职'].map((t) => `<option value="${t}" ${this.filters.employment_status === t ? 'selected' : ''}>${t}</option>`).join('')}
          </select>
          <input type="search" id="ef-search" placeholder="搜索姓名 / 工号" value="${esc(this.filters.search)}" style="width:200px">
          <button class="btn" id="ef-reset">重置</button>
          <span class="grow"></span>
          <button class="btn primary" id="ef-new">＋ 新增员工</button>
        </div>
        <table>
          <thead><tr><th>工号</th><th>姓名</th><th>性别</th><th>类型</th><th>在职状态</th><th>当前岗位</th><th>隶属公司</th><th>直线经理</th><th></th></tr></thead>
          <tbody>${this.rows()}</tbody>
        </table>
        ${this.pager()}
      </div>`;

    document.getElementById('ef-company').onchange = (e) => { this.filters.company_id = e.target.value; this.page = 1; this.render(); };
    document.getElementById('ef-type').onchange = (e) => { this.filters.employee_type = e.target.value; this.page = 1; this.render(); };
    document.getElementById('ef-status').onchange = (e) => { this.filters.employment_status = e.target.value; this.page = 1; this.render(); };
    document.getElementById('ef-search').onkeydown = (e) => { if (e.key === 'Enter') { this.filters.search = e.target.value; this.page = 1; this.render(); } };
    document.getElementById('ef-reset').onclick = () => { this.filters = { company_id: '', employee_type: '', employment_status: '', search: '' }; this.page = 1; this.render(); };
    document.getElementById('ef-new').onclick = () => this.openCreate();
    document.querySelectorAll('[data-emp]').forEach((b) => b.onclick = () => this.openDetail(+b.dataset.emp));
    document.querySelectorAll('[data-pg]').forEach((b) => b.onclick = () => { this.page = +b.dataset.pg; this.render(); });
  },

  rows() {
    if (!this.result.items.length) return '<tr><td colspan="9" class="empty">暂无员工</td></tr>';
    return this.result.items.map((e) => `
      <tr>
        <td>${esc(e.employee_no)}</td>
        <td>${esc(e.name)}</td>
        <td>${esc(e.gender || '—')}</td>
        <td>${esc(e.employee_type || '—')}</td>
        <td>${esc(e.employment_status || '—')}</td>
        <td>${e.position_number ? `<span class="num">${esc(e.position_number)}</span> ${esc(e.position_name || '')}` : '<span class="hint">已解绑</span>'}</td>
        <td>${esc(e.company_name || '—')}</td>
        <td>${e.solid_line_number ? `${esc(e.solid_line_number)} ${esc(e.solid_line_manager_name || '')}` : '—'}</td>
        <td><button class="btn small" data-emp="${e.id}">详情</button></td>
      </tr>`).join('');
  },

  pager() {
    const t = this.result.total, pages = Math.max(1, Math.ceil(t / this.pageSize));
    return `<div class="pager">
      <button class="btn small" data-pg="${this.page - 1}" ${this.page <= 1 ? 'disabled' : ''}>‹ 上一页</button>
      <span>第 ${this.page}/${pages} 页 · 共 ${t} 条</span>
      <button class="btn small" data-pg="${this.page + 1}" ${this.page >= pages ? 'disabled' : ''}>下一页 ›</button>
    </div>`;
  },

  async attachablePositions() {
    const r = await get('/positions?page_size=500');
    return r.items.filter((p) => ['open', 'vacant', 'offered'].includes(p.status));
  },

  posOptions() {
    const list = this._attachable || [];
    return list.length
      ? list.map((p) => `<option value="${p.id}">${esc(p.number)} ${esc(p.position_name)} · ${STATUS_LABEL[p.status]}${p.incumbent_name ? '（占用）' : ''}</option>`).join('')
      : '<option value="">（无 Open/Vacant 岗位）</option>';
  },

  async openCreate() {
    this._attachable = await this.attachablePositions();
    const modal = openModal(`
      <header><h2>新增员工（必须挂岗）</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>工号 *</label><input type="text" id="ec-no"></div>
          <div class="field"><label>姓名 *</label><input type="text" id="ec-name"></div>
          <div class="field"><label>性别 *</label><select id="ec-gender"><option value="男">男</option><option value="女">女</option><option value="其他">其他</option></select></div>
          <div class="field"><label>员工类型 *</label><select id="ec-type">${['正式', '实习', '外包', '劳务'].map((t) => `<option>${t}</option>`).join('')}</select></div>
          <div class="field"><label>在职状态</label><select id="ec-estatus">${['试用期', '在职', '休假'].map((t) => `<option>${t}</option>`).join('')}<option>离职</option></select></div>
          <div class="field"><label>入职日期</label><input type="date" id="ec-hire"></div>
          <div class="field full"><label>挂编岗位 *（仅 Open/Vacant/Offered）</label>
            <select id="ec-pos">${this.posOptions()}</select>
          </div>
          <div class="field"><label>出生日期</label><input type="date" id="ec-birth"></div>
          <div class="field"><label>手机</label><input type="text" id="ec-phone"></div>
          <div class="field"><label>邮箱</label><input type="text" id="ec-email"></div>
          <div class="field full"><label>备注</label><textarea id="ec-remark" rows="2"></textarea></div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="ec-save">保存（入职挂编）</button></footer>`);
    modal.querySelector('#ec-save').onclick = async () => {
      if (!val('#ec-no') || !val('#ec-name') || !val('#ec-pos')) { toast('请填写工号、姓名并选择岗位'); return; }
      const body = {
        employee_no: val('#ec-no'),
        name: val('#ec-name'),
        gender: val('#ec-gender'),
        employee_type: val('#ec-type'),
        employment_status: val('#ec-estatus'),
        position_number_id: +val('#ec-pos'),
        hire_date: val('#ec-hire') || null,
        birth_date: val('#ec-birth') || null,
        phone: val('#ec-phone') || null,
        email: val('#ec-email') || null,
        remark: val('#ec-remark') || null,
      };
      try {
        const emp = await post('/employees', body);
        closeModal(); toast(`员工已入职，岗位 ${emp.position_number} → 在职`, 'ok');
        this.page = 1; this.render(); App.loadStats();
      } catch (e) { toast(e.message); }
    };
  },

  async openDetail(id) {
    const e = await get('/employees/' + id);
    const modal = openModal(`
      <header><h2>${esc(e.name)} · ${esc(e.employee_no)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="detail-grid">
          ${ditem('在职状态', e.employment_status)} ${ditem('员工类型', e.employee_type)}
          ${ditem('性别', e.gender)} ${ditem('入职日期', fmtDate(e.hire_date))}
          ${ditem('出生日期', fmtDate(e.birth_date))} ${ditem('手机', e.phone)}
          ${ditem('邮箱', e.email)} ${ditem('隶属公司', e.company_name)}
          ${ditem('当前岗位', e.position_number ? `${e.position_number} ${e.position_name || ''}` : '已解绑')}
          ${ditem('直线经理', e.solid_line_number ? `${e.solid_line_number} ${e.solid_line_manager_name || ''}` : '—')}
          ${ditem('虚线经理', (e.dotted_manager_numbers || []).join('、') || '—')}
        </div>
        ${e.remark ? `<div class="section-title">备注</div><div style="font-size:13px">${esc(e.remark)}</div>` : ''}
        ${e.employment_status !== '离职' ? `
          <div class="section-title">操作</div>
          <div class="transition-bar">
            <button class="btn" id="ed-transfer">⇄ 调岗</button>
            <button class="btn danger" id="ed-offboard">离职</button>
          </div>` : ''}
      </div>`);
    if (e.employment_status !== '离职') {
      modal.querySelector('#ed-transfer').onclick = () => this.openTransfer(e);
      modal.querySelector('#ed-offboard').onclick = async () => {
        if (!confirm(`确认员工 ${e.name} 离职？岗位将转空缺。`)) return;
        try {
          await patch(`/employees/${id}`, { employment_status: '离职' });
          closeModal(); toast('已办理离职，岗位转空缺', 'ok'); this.render(); App.loadStats();
        } catch (err) { toast(err.message); }
      };
    }
  },

  async openTransfer(e) {
    this._attachable = (await this.attachablePositions()).filter((p) => p.id !== e.position_number_id);
    const modal = openModal(`
      <header><h2>调岗 · ${esc(e.name)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="field"><label>目标岗位（仅 Open/Vacant/Offered，且未占用）</label>
          <select id="tr-pos">${this.posOptions()}</select>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="tr-save">确认调岗</button></footer>`);
    modal.querySelector('#tr-save').onclick = async () => {
      if (!val('#tr-pos')) { toast('请选择目标岗位'); return; }
      try {
        const emp = await post(`/employees/${e.id}/transfers`, { to_position_id: +val('#tr-pos') });
        closeModal(); toast(`已调岗至 ${emp.position_number}`, 'ok'); this.render(); App.loadStats();
      } catch (err) { toast(err.message); }
    };
  },
};
