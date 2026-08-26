/* 员工管理：列表 / 入职 / 调岗 / 离职 */
const Employees = {
  filters: { company_id: '', employee_type: '', employment_status: '', search: '' },
  page: 1,
  pageSize: 20,
  result: null,

  async fetchCalc(empId, params) {
    const q = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => { if (v !== '' && v != null) q.set(k, v); });
    const qs = q.toString() ? `?${q.toString()}` : '';
    const c = await get(`/employees/${empId}/cost-calculation${qs}`);
    if (c.configured === false) throw new Error(c.message || '该税区未配置税率，无法自动计算');
    return c;
  },

  async saveCalc(empId, patchBody) {
    const fresh = await get('/employees/' + empId);
    return patch('/employees/' + empId, { ...patchBody, version: fresh.version });
  },

  // 成本六栏（v2.6 F1.6）：auto=税前可填，其余五栏由引擎计算置灰；
  // manual=六栏均可手填（#106：修复引用已删除的 #emp-share 导致详情抽屉崩溃）
  EMP_COST_FIELDS: ['#emp-salary', '#emp-mtax', '#emp-mfee', '#emp-fbonus', '#emp-vbonus', '#emp-labor'],

  applyCostMode(modal) {
    const isAuto = modal.querySelector('input[name="emp-costmode"]:checked').value === 'auto';
    const hint = modal.querySelector('#emp-costhint');
    const recalcBtn = modal.querySelector('#emp-calccost');
    // issue #142：auto 仅禁派生三栏（强制扣税/定额扣费/用工成本），
    // 税前与奖金两栏是自动模式的输入项，与岗位页（positions.js）口径一致
    this.EMP_COST_FIELDS.forEach((sel, i) => {
      const el = modal.querySelector(sel);
      if (el) el.disabled = isAuto && [1, 2, 5].includes(i);
    });
    hint.textContent = isAuto
      ? '自动模式：强制扣税 = 税前 × Σ(公司所绑税区 rate 科目税率%)；定额扣费 = Σ(fixed 科目)；奖金两栏仍可手填并计入用工成本；点「重算」按公司税区计算。未绑定税区将提示「未配置」。'
      : '手动模式：六栏均可手工填写。';
    recalcBtn.style.display = isAuto ? '' : 'none';
  },

  async render() {
    const qs = new URLSearchParams({ page: this.page, page_size: this.pageSize });
    Object.entries(this.filters).forEach(([k, v]) => { if (v) qs.set(k, v); });
    this.result = await get('/employees?' + qs.toString());
    let pending = { items: [] };
    try { pending = await get('/transfers/pending'); } catch (_e) { /* 403/网络异常时静默隐藏 */ }
    const el = document.getElementById('tab-employees');
    el.innerHTML = `
      ${this.pendingPanel(pending.items || [])}
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
    // 待认领池操作（#108：转调认领链路前端入口）
    document.querySelectorAll('[data-claim]').forEach((b) => b.onclick = () => this.openClaim(pending.items.find((t) => t.id === +b.dataset.claim)));
    document.querySelectorAll('[data-reject]').forEach((b) => b.onclick = async () => {
      const t = pending.items.find((x) => x.id === +b.dataset.reject);
      if (!t || !confirm(`确认退回 ${t.employee_name} 的转调？员工将留任原岗。`)) return;
      try {
        await post(`/transfers/${t.id}/reject`);
        toast('已退回，员工继续留任原岗', 'ok');
        this.render(); App.loadStats();
      } catch (err) { toast(err.message); }
    });
  },

  // 转调待认领池（#108）：仅目标公司可管 HR 可见（后端 /transfers/pending 已按实体过滤）
  pendingPanel(items) {
    if (!items.length) return '';
    return `
      <div class="panel" style="border-left:4px solid #f0ad4e">
        <div class="section-title">📥 待认领转调（${items.length}）</div>
        <table>
          <thead><tr><th>发起时间</th><th>员工</th><th>原岗位</th><th>转入目标公司</th><th>备注</th><th style="width:180px"></th></tr></thead>
          <tbody>
            ${items.map((t) => `
              <tr>
                <td>${fmtDate(t.created_at)}</td>
                <td>${esc(t.employee_name || '—')}</td>
                <td><span class="num">${esc(t.from_position_number || '—')}</span></td>
                <td>${esc(t.target_company_name || '—')}</td>
                <td>${esc(t.note || '—')}</td>
                <td>
                  <button class="btn small primary" data-claim="${t.id}">✓ 认领分配</button>
                  <button class="btn small danger" data-reject="${t.id}">✕ 拒绝退回</button>
                </td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  },

  async openClaim(t) {
    if (!t) return;
    // 空闲目标岗：Open/Vacant/Offered/Planned 且无占用（PRD F1.5b）
    const all = await this.allPositions((p) => p.company_id === t.target_company_id);
    const available = all.filter((p) =>
      ['open', 'vacant', 'offered', 'planned'].includes(p.status) && !p.incumbent_id);
    const modal = openModal(`
      <header><h2>认领转调 · ${esc(t.employee_name || '')}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="hint" style="margin-bottom:12px">认领即单事务生效：原岗位 → Vacant、所选岗位 → Filled、员工挂新岗并记来源（工龄不变）。</div>
        <div class="field"><label>目标公司</label><input type="text" value="${esc(t.target_company_name || '')}" disabled></div>
        <div class="field"><label>空闲目标岗位 *</label>
          <select id="tc-pos">${available.length
            ? available.map((p) => `<option value="${p.id}">${esc(p.number)} ${esc(p.position_name)} · ${STATUS_LABEL[p.status]}${p.company_name ? ' · ' + esc(p.company_name) : ''}</option>`).join('')
            : '<option value="">（该公司暂无空闲编制）</option>'}</select>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="tc-save">确认认领</button></footer>`);
    modal.querySelector('#tc-save').onclick = async () => {
      if (!val('#tc-pos')) { toast('请选择空闲目标岗位'); return; }
      try {
        await post(`/transfers/${t.id}/claim`, { to_position_id: +val('#tc-pos') });
        closeModal();
        toast(`已认领：${t.employee_name} 入编新岗，原岗转空缺`, 'ok');
        this.render(); App.loadStats();
      } catch (err) {
        if (err.status === 409) toast('员工转调状态已变化，请刷新后重试', 'error');
        else toast(err.message);
      }
    };
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
        <td>${e.position_number ? `<span class="num">${esc(e.position_number)}</span> ${esc(e.position_name || '')}` : (e.employee_type === '外包' ? '<span class="hint">虚拟建档</span>' : '<span class="hint">已解绑</span>')}</td>
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
    return this.allPositions((p) => ['open', 'vacant', 'offered'].includes(p.status));
  },

  /* issue #150：分页拉全量岗位（此前 page_size=500 硬编码，超 500 岗静默截断）；
     issue #162：触顶 10000 条时 toast 告警不再静默 */
  async allPositions(filterFn) {
    const out = [];
    let page = 1, total = Infinity;
    while ((page - 1) * 500 < total && page <= 20) {
      const r = await get(`/positions?page=${page}&page_size=500`);
      total = r.total;
      out.push(...r.items);
      if (r.items.length < 500) break;
      page++;
    }
    if (total > out.length) toast(`岗位共 ${total} 条，下拉仅加载前 ${out.length} 条（请先用筛选缩小范围）`, 'warn');
    return filterFn ? out.filter(filterFn) : out;
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
      <header><h2>新增员工</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>工号（自动生成）</label><input type="text" id="ec-no" disabled placeholder="保存后按类型生成"></div>
          <div class="field"><label>姓名 *</label><input type="text" id="ec-name"></div>
          <div class="field"><label>性别 *</label><select id="ec-gender"><option value="男">男</option><option value="女">女</option><option value="其他">其他</option></select></div>
          <div class="field"><label>员工类型 *</label><select id="ec-type">${['正式', '实习', '外包', '劳务'].map((t) => `<option>${t}</option>`).join('')}</select>
            <div class="hint" id="ec-nohint"></div></div>
          <div class="field"><label>在职状态</label><select id="ec-estatus">${['试用期', '在职', '休假'].map((t) => `<option>${t}</option>`).join('')}<option>离职</option></select></div>
          <div class="field"><label>入职日期</label><input type="date" id="ec-hire"></div>
          <div class="field full"><label>挂编岗位 *（仅 Open/Vacant/Offered；外包可不挂）</label>
            <select id="ec-pos"><option value="">— 不挂岗（仅外包可虚拟建档）—</option>${this.posOptions()}</select>
          </div>
          <div class="field"><label>出生日期</label><input type="date" id="ec-birth"></div>
          <div class="field"><label>手机</label><input type="text" id="ec-phone"></div>
          <div class="field"><label>邮箱</label><input type="text" id="ec-email"></div>
          <div class="field full"><label>备注</label><textarea id="ec-remark" rows="2"></textarea></div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="ec-save">保存</button></footer>`);

    // 工号系列提示：正式 G / 实习·劳务 V / 外包 O；外包提示可不挂岗
    const NO_HINT = { '正式': '工号将生成为 G00001 起', '实习': '工号将生成为 V00001 起', '劳务': '工号将生成为 V00001 起', '外包': '工号将生成为 O00001 起；外包人员由外包公司管理，可不挂岗' };
    const typeSel = modal.querySelector('#ec-type');
    const hint = modal.querySelector('#ec-nohint');
    const syncHint = () => { hint.textContent = NO_HINT[typeSel.value] || ''; };
    typeSel.onchange = syncHint;
    syncHint();

    modal.querySelector('#ec-save').onclick = async () => {
      const etype = val('#ec-type');
      if (!val('#ec-name')) { toast('请填写姓名'); return; }
      if (etype !== '外包' && !val('#ec-pos')) { toast('该员工类型必须选择挂编岗位（仅外包可不挂岗）'); return; }
      const body = {
        name: val('#ec-name'),
        gender: val('#ec-gender'),
        employee_type: etype,
        employment_status: val('#ec-estatus'),
        position_number_id: val('#ec-pos') ? +val('#ec-pos') : null,
        hire_date: val('#ec-hire') || null,
        birth_date: val('#ec-birth') || null,
        phone: val('#ec-phone') || null,
        email: val('#ec-email') || null,
        remark: val('#ec-remark') || null,
      };
      try {
        const emp = await post('/employees', body);
        closeModal();
        toast(emp.position_number ? `员工 ${emp.employee_no} 已入职，岗位 ${emp.position_number} → 在职` : `员工 ${emp.employee_no} 已建档（虚拟挂名单，未挂岗）`, 'ok');
        this.page = 1; this.render(); App.loadStats();
      } catch (e) { toast(e.message); }
    };
  },

  async openDetail(id) {
    const e = await get('/employees/' + id);
    const hasActual = [e.actual_salary_before_tax, e.actual_mandatory_tax, e.actual_mandatory_fixed_fee,
                       e.actual_fixed_bonus, e.actual_floating_bonus, e.actual_labor_cost]
                      .some((v) => v != null);
    const isAuto = e.actual_cost_mode === 'auto';
    const num = (v) => (v != null ? v : '');
    const modal = openModal(`
      <header><h2>${esc(e.name)} · ${esc(e.employee_no)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="section-title">基本信息 <button class="btn small" id="ed-edit" style="margin-left:8px">编辑</button></div>
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
          <div class="cost-field"><label>税前（年薪，实际）</label><input type="number" step="0.01" id="emp-salary" value="${num(e.actual_salary_before_tax)}"></div>
          <div class="cost-field"><label>强制扣税（实际）</label><input type="number" step="0.01" id="emp-mtax" value="${num(e.actual_mandatory_tax)}"></div>
          <div class="cost-field"><label>强制定额扣费（实际）</label><input type="number" step="0.01" id="emp-mfee" value="${num(e.actual_mandatory_fixed_fee)}"></div>
          <div class="cost-field"><label>固定奖金（实际）</label><input type="number" step="0.01" id="emp-fbonus" value="${num(e.actual_fixed_bonus)}"></div>
          <div class="cost-field"><label>浮动奖金（实际）</label><input type="number" step="0.01" id="emp-vbonus" value="${num(e.actual_floating_bonus)}"></div>
          <div class="cost-field"><label>用工成本（实际）</label><input type="number" step="0.01" id="emp-labor" value="${num(e.actual_labor_cost)}"></div>
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
    modal.querySelectorAll('input[name="emp-costmode"]').forEach((r) => r.onchange = () => this.applyCostMode(modal));
    this.applyCostMode(modal);
    modal.querySelector('#ed-edit').onclick = () => this.openEdit(e);
    modal.querySelector('#emp-calccost').onclick = async () => {
      try {
        if (!val('#emp-salary')) { toast('请先填写税前薪资'); return; }
        const c = await this.fetchCalc(id, {
          salary_before_tax: val('#emp-salary'),
          fixed_bonus: val('#emp-fbonus'),
          floating_bonus: val('#emp-vbonus'),
        });
        await this.saveCalc(id, {
          actual_cost_mode: 'auto', actual_salary_before_tax: +val('#emp-salary'),
          actual_mandatory_tax: c.mandatory_tax, actual_mandatory_fixed_fee: c.mandatory_fixed_fee,
          actual_fixed_bonus: val('#emp-fbonus') !== '' ? +val('#emp-fbonus') : null,
          actual_floating_bonus: val('#emp-vbonus') !== '' ? +val('#emp-vbonus') : null,
          actual_labor_cost: c.labor_cost,
        });
        modal.querySelector('#emp-mtax').value = c.mandatory_tax ?? '';
        modal.querySelector('#emp-mfee').value = c.mandatory_fixed_fee ?? '';
        modal.querySelector('#emp-labor').value = c.labor_cost ?? '';
        toast(`已重算并保存：强制扣税 ${fmtMoney(c.mandatory_tax)} · 定额 ${fmtMoney(c.mandatory_fixed_fee)} · 用工成本 ${fmtMoney(c.labor_cost)}（含奖金）`, 'ok');
      } catch (err) { toast(err.message); }
    };
    modal.querySelector('#ed-cost-edit').onclick = async () => {
      // 保存手动模式下的修改（六栏）
      const mode = modal.querySelector('input[name="emp-costmode"]:checked').value;
      const body = {
        version: e.version,
        actual_cost_mode: mode,
      };
      if (mode === 'manual') {
        for (const [f, id] of [
          ['actual_salary_before_tax', '#emp-salary'], ['actual_mandatory_tax', '#emp-mtax'],
          ['actual_mandatory_fixed_fee', '#emp-mfee'], ['actual_fixed_bonus', '#emp-fbonus'],
          ['actual_floating_bonus', '#emp-vbonus'], ['actual_labor_cost', '#emp-labor'],
        ]) {
          body[f] = val(id) !== '' ? +val(id) : null;
        }
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

  async openEdit(e) {
    const modal = openModal(`
      <header><h2>编辑员工 · ${esc(e.employee_no)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>姓名 *</label><input type="text" id="ee-name" value="${esc(e.name)}"></div>
          <div class="field"><label>性别</label><select id="ee-gender">${['男', '女', '其他'].map((g) => `<option ${g === e.gender ? 'selected' : ''}>${g}</option>`).join('')}</select></div>
          <div class="field"><label>员工类型</label><select id="ee-type">${['正式', '实习', '外包', '劳务'].map((t) => `<option ${t === e.employee_type ? 'selected' : ''}>${t}</option>`).join('')}</select>
            <div class="hint">挂岗员工的类型须与岗位编制匹配（正式/实习/劳务→Employee，外包→External Employee）</div></div>
          <div class="field"><label>在职状态 *</label><select id="ee-status">${(e.employment_status === '转调中'
            ? ['转调中', '离职']  /* issue #162：转调中仅可认领/退回或离职，其余选项提交必 400 */
            : ['试用期', '在职', '休假', '离职'])
            .map((s) => `<option ${s === e.employment_status ? 'selected' : ''}>${s}</option>`).join('')}</select>
            <div class="hint">选「离职」将解绑岗位并转空缺${e.employment_status === '转调中' ? '；「转调中」仅可认领/退回或办理离职' : ''}</div></div>
          <div class="field"><label>入职日期</label><input type="date" id="ee-hire" value="${fmtDate(e.hire_date)}"></div>
          <div class="field"><label>出生日期</label><input type="date" id="ee-birth" value="${fmtDate(e.birth_date)}"></div>
          <div class="field"><label>手机</label><input type="text" id="ee-phone" value="${esc(e.phone || '')}"></div>
          <div class="field"><label>邮箱</label><input type="text" id="ee-email" value="${esc(e.email || '')}"></div>
          <div class="field full"><label>备注</label><textarea id="ee-remark" rows="2">${esc(e.remark || '')}</textarea></div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="ee-save">保存</button></footer>`);

    modal.querySelector('#ee-save').onclick = async () => {
      if (!val('#ee-name')) { toast('请填写姓名'); return; }
      const body = {
        version: e.version,
        name: val('#ee-name'),
        gender: val('#ee-gender'),
        employee_type: val('#ee-type'),
        employment_status: val('#ee-status'),
        hire_date: val('#ee-hire') || null,
        birth_date: val('#ee-birth') || null,
        phone: val('#ee-phone') || null,
        email: val('#ee-email') || null,
        remark: val('#ee-remark') || null,
      };
      try {
        const emp = await patch('/employees/' + e.id, body);
        closeModal();
        const offboarded = e.employment_status !== '离职' && emp.employment_status === '离职';
        toast(offboarded ? `已办理离职，岗位转空缺` : '已保存', 'ok');
        this.render(); App.loadStats();
      } catch (err) {
        if (err.status === 409) toast('数据已被他人修改，请刷新后重试', 'error');
        else toast(err.message);
      }
    };
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

  /* 普通调岗（issue #134：此前按钮绑定未定义的 openTransfer 抛 TypeError，
     POST /transfers 在前端完全无入口）。虚拟建档员工经此首次挂编。 */
  async openTransfer(e) {
    const all = await this.allPositions();
    const available = all.filter(p =>
      ['open', 'vacant', 'offered'].includes(p.status) &&
      p.id !== e.position_number_id &&
      !p.incumbent_id
    );
    if (!available.length) { toast('无可用的空闲岗位用于调岗'); return; }
    const modal = openModal(`
      <header><h2>调岗 · ${esc(e.name)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="hint" style="margin-bottom:12px">${e.position_number ? '原岗位将转 Vacant，新岗位转 Filled。' : '该员工为虚拟建档（未挂岗），本次调岗即首次挂编。'}</div>
        <div class="field"><label>目标岗位 *（仅 Open/Vacant/Offered 空闲编制）</label>
          <select id="tr-pos">${available.map(p => `<option value="${p.id}">${esc(p.number)} ${esc(p.position_name)} · ${esc(p.company_name || '')} · ${STATUS_LABEL[p.status]}</option>`).join('')}</select>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="tr-save">确认调岗</button></footer>`);
    modal.querySelector('#tr-save').onclick = async () => {
      if (!val('#tr-pos')) { toast('请选择目标岗位'); return; }
      try {
        await post('/transfers', { employee_id: e.id, to_position_id: +val('#tr-pos') });
        const targetNo = available.find(p => p.id === +val('#tr-pos'));
        closeModal(); toast(`调岗完成：${e.name} 入编${targetNo ? ' ' + targetNo.number : ' 新岗'}`, 'ok');
        this.render(); App.loadStats();
      } catch (err) { toast(err.message); }
    };
  },

  async openPromote(e) {
    const positions = await this.allPositions();
    // 只显示 Open/Vacant/Offered/Planned 且非当前岗位的空闲编制
    const available = positions.filter(p =>
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
          <select id="pr-pos">${available.map(p => `<option value="${p.id}">${esc(p.number)} ${esc(p.position_name)} · ${esc(p.company_name || '')} · ${STATUS_LABEL[p.status]}</option>`).join('')}</select>
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
