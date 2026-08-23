/* 员工管理：列表 / 入职 / 调岗 / 离职 */
const Employees = {
  filters: { company_id: '', employee_type: '', employment_status: '', search: '' },
  page: 1,
  pageSize: 20,
  result: null,

  async fetchCalc(empId, salary) {
    const q = salary != null ? `?salary_before_tax=${salary}` : '';
    const c = await get(`/employees/${empId}/cost-calculation${q}`);
    if (c.configured === false) throw new Error(c.message || '该税区未配置税率，无法自动计算');
    return c;
  },

  async saveCalc(empId, patchBody) {
    const fresh = await get('/employees/' + empId);
    return patch('/employees/' + empId, { ...patchBody, version: fresh.version });
  },

  applyCostMode(modal, emp) {
    const mode = modal.querySelector('input[name="emp-costmode"]:checked').value;
    const salary = modal.querySelector('#emp-salary');
    const share = modal.querySelector('#emp-share');
    const labor = modal.querySelector('#emp-labor');
    const hint = modal.querySelector('#emp-costhint');
    const recalcBtn = modal.querySelector('#emp-calccost');
    if (mode === 'auto') {
      salary.disabled = false; share.disabled = true; labor.disabled = true;
      hint.textContent = '自动模式：公司份额 = 税前薪资 × Σ(该员工归属税区全部有效科目税率)，用工成本 = 税前 + 份额；城市级分拆后无国家兜底，未配置税率将提示「未配置」。';
      recalcBtn.style.display = '';
    } else {
      salary.disabled = false; share.disabled = false; labor.disabled = false;
      hint.textContent = '手动模式：三个字段均可填写。';
      recalcBtn.style.display = 'none';
    }
  },

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
            ${['试用期', '在职', '休假', '转调中', '离职'].map((t) => `<option value="${t}" ${this.filters.employment_status === t ? 'selected' : ''}>${t}</option>`).join('')}
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
    const hasActual = e.actual_salary_before_tax != null || e.actual_company_share != null || e.actual_labor_cost != null;
    const isAuto = e.actual_cost_mode === 'auto';
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
        <div class="section-title">实际成本（跟人走） <button class="btn small" id="ed-cost-edit" style="margin-left:8px">编辑</button></div>
        <div class="cost-toggle" style="margin-bottom:8px">
          <label class="cost-mode"><input type="radio" name="emp-costmode" value="manual" ${!isAuto ? 'checked' : ''}> 手动输入</label>
          <label class="cost-mode"><input type="radio" name="emp-costmode" value="auto" ${isAuto ? 'checked' : ''}> 自动计算（按税区用工税额）</label>
          <button class="btn" id="emp-calccost" style="${isAuto && hasActual ? '' : 'display:none'}">重算</button>
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px" id="emp-costfields">
          <div class="cost-field"><label>税前薪资（实际）</label><input type="number" step="0.01" id="emp-salary" value="${e.actual_salary_before_tax != null ? e.actual_salary_before_tax : ''}" ${isAuto ? 'disabled' : ''}></div>
          <div class="cost-field"><label>公司份额（实际）</label><input type="number" step="0.01" id="emp-share" value="${e.actual_company_share != null ? e.actual_company_share : ''}" ${isAuto ? 'disabled' : ''}></div>
          <div class="cost-field"><label>用工成本（实际）</label><input type="number" step="0.01" id="emp-labor" value="${e.actual_labor_cost != null ? e.actual_labor_cost : ''}" ${isAuto ? 'disabled' : ''}></div>
        </div>
        <div class="hint" id="emp-costhint"></div>
        ${e.remark ? `<div class="section-title">备注</div><div style="font-size:13px">${esc(e.remark)}</div>` : ''}
        ${e.employment_status !== '离职' ? `
          <div class="section-title">操作</div>
          <div class="transition-bar">
            <button class="btn" id="ed-transfer">⇄ 调岗</button>
            <button class="btn" id="ed-transfer-initiate">🔄 转调发起</button>
            <button class="btn" id="ed-promote">⬆ 升职</button>
            <button class="btn danger" id="ed-offboard">离职</button>
          </div>` : ''}
      </div>`);
    // 实际成本模式切换
    modal.querySelectorAll('input[name="emp-costmode"]').forEach((r) => r.onchange = () => this.applyCostMode(modal, e));
    this.applyCostMode(modal, e);
    modal.querySelector('#emp-calccost').onclick = async () => {
      try {
        const salary = val('#emp-salary') ? +val('#emp-salary') : null;
        if (salary == null) { toast('请先填写税前薪资'); return; }
        const c = await this.fetchCalc(id, salary);
        await this.saveCalc(id, { actual_cost_mode: 'auto', actual_salary_before_tax: salary, actual_company_share: c.company_share, actual_labor_cost: c.labor_cost });
        modal.querySelector('#emp-salary').value = c.salary_before_tax ?? '';
        modal.querySelector('#emp-share').value = c.company_share ?? '';
        modal.querySelector('#emp-labor').value = c.labor_cost ?? '';
        toast(`已重算并保存：公司份额 ${fmtMoney(c.company_share)} · 用工成本 ${fmtMoney(c.labor_cost)}`, 'ok');
      } catch (err) { toast(err.message); }
    };
    modal.querySelector('#ed-cost-edit').onclick = async () => {
      // 保存手动模式下的修改
      const mode = modal.querySelector('input[name="emp-costmode"]:checked').value;
      const body = {
        version: e.version,
        actual_cost_mode: mode,
      };
      if (mode === 'manual') {
        body.actual_salary_before_tax = val('#emp-salary') ? +val('#emp-salary') : null;
        body.actual_company_share = val('#emp-share') ? +val('#emp-share') : null;
        body.actual_labor_cost = val('#emp-labor') ? +val('#emp-labor') : null;
      }
      try {
        await patch(`/employees/${id}`, body);
        toast('实际成本已保存', 'ok');
      } catch (err) {
        if (err.status === 409) toast('数据已被他人修改，请刷新后重试', 'error');
        else toast(err.message);
      }
    };
    if (e.employment_status !== '离职') {
      modal.querySelector('#ed-transfer').onclick = () => this.openTransfer(e);
      modal.querySelector('#ed-transfer-initiate').onclick = () => this.openTransferInitiate(e);
      modal.querySelector('#ed-promote').onclick = () => this.openPromote(e);
      modal.querySelector('#ed-offboard').onclick = async () => {
        if (!confirm(`确认员工 ${e.name} 离职？岗位将转空缺。`)) return;
        try {
          await patch(`/employees/${id}`, { version: e.version, employment_status: '离职' });
          closeModal(); toast('已办理离职，岗位转空缺', 'ok'); this.render(); App.loadStats();
        } catch (err) {
          if (err.status === 409) toast('数据已被他人修改，请刷新后重试', 'error');
          else toast(err.message);
        }
      };
    }
  },

  async openTransferInitiate(e) {
    const companies = await get('/companies');
    const modal = openModal(`
      <header><h2>转调发起 · ${esc(e.name)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="hint" style="margin-bottom:12px">员工将标记为「转调中」，原岗位保持 Filled 锁定，等待目标公司 HR 认领分配空闲岗位。</div>
        <div class="field"><label>目标公司 *</label>
          <select id="ti-company">${companies.filter(c => c.id !== e.company_id).map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select>
        </div>
        <div class="field"><label>备注</label><textarea id="ti-note" rows="2"></textarea></div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="ti-save">发起转调</button></footer>`);
    modal.querySelector('#ti-save').onclick = async () => {
      if (!val('#ti-company')) { toast('请选择目标公司'); return; }
      try {
        await post('/transfers/initiate', {
          employee_id: e.id,
          target_company_id: +val('#ti-company'),
          note: val('#ti-note') || null
        });
        closeModal(); toast('转调已发起，等待目标公司 HR 认领', 'ok');
        this.render(); App.loadStats();
      } catch (err) { toast(err.message); }
    };
  },

  async openPromote(e) {
    const positions = await get('/positions?page_size=500');
    // 只显示 Open/Vacant/Offered/Planned 且非当前岗位的空闲编制
    const available = positions.items.filter(p =>
      ['open', 'vacant', 'offered', 'planned'].includes(p.status) &&
      p.id !== e.position_number_id &&
      !p.incumbent_id
    );
    if (!available.length) {
      toast('无可用的空闲岗位用于升职'); return;
    }
    const modal = openModal(`
      <header><h2>升职 · ${esc(e.name)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="hint" style="margin-bottom:12px">升职后原岗位默认转 Vacant（可后续手动 Closed），工龄跟人不变，实际成本跟人走。</div>
        <div class="field"><label>目标岗位 *（仅空闲编制）</label>
          <select id="pr-pos">${available.map(p => `<option value="${p.id}">${esc(p.number)} ${esc(p.position_name)} · ${p.company_name} · ${STATUS_LABEL[p.status]}</option>`).join('')}</select>
        </div>
        <div class="field"><label>生效时节</label>
          <select id="pr-timing"><option value="immediate">即时升职</option><option value="month_end">月末升职（财务月边界归属）</option></select>
        </div>
        <div class="field"><label>备注</label><textarea id="pr-note" rows="2"></textarea></div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="pr-save">确认升职</button></footer>`);
    modal.querySelector('#pr-save').onclick = async () => {
      if (!val('#pr-pos')) { toast('请选择目标岗位'); return; }
      try {
        const emp = await post(`/employees/${e.id}/promote`, {
          to_position_id: +val('#pr-pos'),
          timing: val('#pr-timing'),
          note: val('#pr-note') || null
        });
        closeModal(); toast(`升职完成：${emp.employee.position_number}`, 'ok');
        this.render(); App.loadStats();
      } catch (err) { toast(err.message); }
    };
  },
};
