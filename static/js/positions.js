/* 岗位管理：列表 / 新建 / 详情 / 生命周期流转 / 成本字段 */
const Positions = {
  filters: { company_id: '', scope: '', status: '', search: '' },
  page: 1,
  pageSize: 20,
  result: null,
  _managers: [],       // 管理岗（直线/虚线经理下拉，仅管理岗）
  _all: [],

  /* 隶属公司下拉（v2.4.1）：已关闭公司不可挂新岗；编辑时保留当前所属公司以维持原值 */
  companyOptions(selectedId) {
    return App.companies
      .filter((c) => c.is_active || c.id === selectedId)
      .map((c) => `<option value="${c.id}" ${c.id === selectedId ? 'selected' : ''}>${esc(c.name)}${c.is_active ? '' : '（已关闭）'}</option>`)
      .join('');
  },

  /* 手动流转白名单（与后端 ALLOWED_MANUAL 一致；filled/vacant 由员工动作触发） */
  TRANSITIONS: {
    planned: ['open', 'closed', 'frozen'],
    open: ['offered', 'closed'],
    offered: ['open'],
    vacant: ['open', 'closed', 'frozen'],
    frozen: ['planned', 'open'],
    closed: [],
  },

  async render() {
    const qs = new URLSearchParams({ page: this.page, page_size: this.pageSize });
    Object.entries(this.filters).forEach(([k, v]) => { if (v) qs.set(k, v); });
    this.result = await get('/positions?' + qs.toString());
    const el = document.getElementById('tab-positions');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar">
          <select id="pf-company">
            <option value="">全部公司</option>
            ${App.companies.map((c) => `<option value="${c.id}" ${+this.filters.company_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
          </select>
          <select id="pf-scope">
            <option value="">全部范围</option>
            ${App.scopes.map((s) => `<option value="${s.code}" ${this.filters.scope === s.code ? 'selected' : ''}>${esc(shortScope(s.label))}</option>`).join('')}
          </select>
          <select id="pf-status">
            <option value="">全部状态</option>
            ${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}" ${this.filters.status === k ? 'selected' : ''}>${v}</option>`).join('')}
          </select>
          <input type="search" id="pf-search" placeholder="搜索编号 / 职位 / 显示名" value="${esc(this.filters.search)}" style="width:220px">
          <button class="btn" id="pf-reset">重置</button>
          <span class="grow"></span>
          <button class="btn primary" id="pf-new">＋ 新建岗位</button>
        </div>
        <table>
          <thead><tr><th>岗位编号</th><th>职位</th><th>职位类型</th><th>隶属公司</th><th>级别</th><th>范围</th><th>状态</th><th>占用员工</th><th>直线经理</th><th>虚线经理</th><th></th></tr></thead>
          <tbody>${this.rows()}</tbody>
        </table>
        ${this.pager()}
      </div>`;

    document.getElementById('pf-company').onchange = (e) => { this.filters.company_id = e.target.value; this.page = 1; this.render(); };
    document.getElementById('pf-scope').onchange = (e) => { this.filters.scope = e.target.value; this.page = 1; this.render(); };
    document.getElementById('pf-status').onchange = (e) => { this.filters.status = e.target.value; this.page = 1; this.render(); };
    document.getElementById('pf-search').onkeydown = (e) => { if (e.key === 'Enter') { this.filters.search = e.target.value; this.page = 1; this.render(); } };
    document.getElementById('pf-reset').onclick = () => { this.filters = { company_id: '', scope: '', status: '', search: '' }; this.page = 1; this.render(); };
    document.getElementById('pf-new').onclick = () => this.openCreate();
    document.querySelectorAll('[data-open]').forEach((b) => b.onclick = () => this.openDetail(+b.dataset.open));
    document.querySelectorAll('[data-pg]').forEach((b) => b.onclick = () => { this.page = +b.dataset.pg; this.render(); });
  },

  rows() {
    if (!this.result.items.length) return '<tr><td colspan="11" class="empty">暂无岗位</td></tr>';
    return this.result.items.map((p) => `
      <tr>
        <td class="num">${esc(p.number)}</td>
        <td>${esc(p.position_name)}</td>
        <td>${esc(p.position_type || '—')}</td>
        <td>${esc(p.company_name)}</td>
        <td>${esc(p.level || '—')}</td>
        <td>${esc(scopeDisplay(p))}</td>
        <td>${statusBadge(p.status)}</td>
        <td>${esc(p.incumbent_name || '—')}</td>
        <td>${esc(p.solid_line_number ? `${p.solid_line_number} ${p.solid_line_manager_name || ''}` : '—')}</td>
        <td>${esc((p.dotted_manager_numbers || []).join('、') || '—')}</td>
        <td><button class="btn small" data-open="${p.id}">详情</button></td>
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

  async managerOptions(force = false) {
    if (force || !this._managers.length) {
      const r = await get('/positions?role=manager&page_size=500');
      this._managers = r.items.map((p) => ({ id: p.id, number: p.number, position_name: p.position_name, level: p.level }));
    }
    return this._managers;
  },

  mgrOptions() {
    return this._managers.length
      ? this._managers.map((m) => `<option value="${m.id}">${esc(m.number)} ${esc(m.position_name || '')}（${esc(m.level || '')}）</option>`).join('')
      : '';
  },

  /* ---- 岗位预算成本六栏（issue #107，PRD F1.6）----
     auto/manual 互斥：auto 下强制扣税/定额扣费/用工成本由引擎计算置灰，
     税前与奖金两栏仍可编辑；manual 六栏均可手填 */
  COST_FIELDS: [
    ['salary_before_tax', '税前（年薪）'],
    ['mandatory_tax', '强制扣税'],
    ['mandatory_fixed_fee', '强制定额扣费'],
    ['fixed_bonus', '固定奖金'],
    ['floating_bonus', '浮动奖金'],
    ['labor_cost', '用工成本'],
  ],

  costModeRadios(prefix, isAuto) {
    return `
      <label class="cost-mode"><input type="radio" name="${prefix}-costmode" value="manual" ${!isAuto ? 'checked' : ''}> 手动输入</label>
      <label class="cost-mode"><input type="radio" name="${prefix}-costmode" value="auto" ${isAuto ? 'checked' : ''}> 自动计算（按公司绑定税区）</label>
      <button type="button" class="btn" id="${prefix}-calccost" style="display:none">重算</button>`;
  },

  costInputs(prefix, src) {
    const num = (v) => (v != null ? v : '');
    return this.COST_FIELDS.map(([f, label]) => `
      <div class="cost-field"><label>${label}</label>
        <input type="number" step="0.01" id="${prefix}-${f}" value="${num(src ? src[f] : '')}">
      </div>`).join('');
  },

  bindCostMode(modal, prefix) {
    const sync = (initial = false) => {
      const isAuto = modal.querySelector(`input[name="${prefix}-costmode"]:checked`).value === 'auto';
      ['mandatory_tax', 'mandatory_fixed_fee', 'labor_cost'].forEach((f) => {
        const el = modal.querySelector(`#${prefix}-${f}`);
        if (!el) return;
        const wasDisabled = el.disabled;
        el.disabled = isAuto;
        // issue #162：仅用户主动切到 auto 时清空派生三栏显示值；弹窗初始 sync
        // 不清——否则打开 auto 岗位编辑弹窗即看到空盒子（引擎值被误读为从未算过）
        if (isAuto && !wasDisabled && !initial) el.value = '';
      });
      const btn = modal.querySelector(`#${prefix}-calccost`);
      if (btn && btn.dataset.ready === '1') btn.style.display = isAuto ? '' : 'none';
      const hint = modal.querySelector(`#${prefix}-costhint`);
      if (hint) {
        hint.textContent = isAuto
          ? (btn && btn.dataset.ready === '1'
            ? '自动模式：填税前与奖金两栏后点「重算」（按隶属公司绑定税区计算）；保存时服务端按引擎覆写派生三栏。未绑税区提示「未配置」。'
            : '自动模式：创建后可在「编辑」中按公司税区一键重算。')
          : '手动模式：六栏均可手工填写。';
      }
    };
    modal.querySelectorAll(`input[name="${prefix}-costmode"]`).forEach((r) => r.onchange = () => sync(false));
    sync(true);
  },

  collectCost(modal, prefix) {
    const body = { cost_mode: modal.querySelector(`input[name="${prefix}-costmode"]:checked`).value };
    for (const [f] of this.COST_FIELDS) {
      const el = modal.querySelector(`#${prefix}-${f}`);
      // issue #150/#141：auto 模式下被置灰的派生三栏提交 null（disabled input 的
      // .value 仍可读，此前残留旧值会以 cost_mode=auto 落库）
      if (el && el.disabled) { body[f] = null; continue; }
      body[f] = val(`#${prefix}-${f}`) !== '' ? +val(`#${prefix}-${f}`) : null;
    }
    return body;
  },

  async recalcCost(modal, id, prefix) {
    if (!val(`#${prefix}-salary_before_tax`)) { toast('请先填写税前薪资'); return; }
    try {
      const q = new URLSearchParams({
        scope: 'budget',
        salary_before_tax: val(`#${prefix}-salary_before_tax`),
        fixed_bonus: val(`#${prefix}-fixed_bonus`) || '0',
        floating_bonus: val(`#${prefix}-floating_bonus`) || '0',
      });
      const c = await get(`/positions/${id}/cost-calculation?${q.toString()}`);
      if (c.configured === false) throw new Error(c.message || '该公司未绑定税区，成本显示「未配置」，不猜测');
      modal.querySelector(`#${prefix}-mandatory_tax`).value = c.mandatory_tax ?? '';
      modal.querySelector(`#${prefix}-mandatory_fixed_fee`).value = c.mandatory_fixed_fee ?? '';
      modal.querySelector(`#${prefix}-labor_cost`).value = c.labor_cost ?? '';
      toast(`已按公司税区试算：强制扣税 ${fmtMoney(c.mandatory_tax)} · 定额 ${fmtMoney(c.mandatory_fixed_fee)} · 用工成本 ${fmtMoney(c.labor_cost)}（点「保存」落库）`, 'ok');
    } catch (e) { toast(e.message); }
  },

  async openCreate() {
    this._managers = await this.managerOptions(true);  // 强制刷新，避免缓存导致新管理岗缺失
    const modal = openModal(`
      <header><h2>新建岗位（编号自动生成）</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>职位（职能）*</label>
            <input type="text" id="pc-posname" list="posfn-list" placeholder="输入或选择职位名">
            <datalist id="posfn-list">${App.functions.map((f) => `<option value="${esc(f.name)}">`).join('')}</datalist>
          </div>
          <div class="field"><label>隶属公司 *（已关闭公司不可选）</label>
            <select id="pc-company">${this.companyOptions()}</select>
          </div>
          <div class="field"><label>级别</label>
            <select id="pc-level"><option value="">—</option>${App.levels.map((l) => `<option value="${esc(l.code)}">${esc(l.code)}${l.label ? ' · ' + esc(l.label) : ''}${l.is_management ? '（管理岗）' : ''}</option>`).join('')}</select>
          </div>
          <div class="field"><label>职位类型</label>
            <select id="pc-ptype"><option value="">—</option>${App.positionTypes.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>工作范围 *</label>
            <select id="pc-scope">${App.scopes.map((s) => `<option value="${s.code}">${esc(s.label)}</option>`).join('')}</select>
          </div>
          <div class="field" id="pc-country-wrap" style="display:none"><label>国家/地区</label>
            <select id="pc-country">${App.countries.map((c) => `<option value="${c.id}">${esc(c.name)} (${esc(c.code)})</option>`).join('')}</select>
          </div>
          <div class="field"><label>职位开启日 *</label><input type="date" id="pc-opening"></div>
          <div class="field"><label>职位关闭日（关闭时填）</label><input type="date" id="pc-closing"></div>
          <div class="field"><label>工作地点</label>
            <select id="pc-wloc"><option value="">—</option>${App.workLocations.map((w) => `<option value="${esc(w.name)}">${esc(w.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>直线经理（仅管理岗）</label>
            <select id="pc-solid"><option value="">无</option>${this.mgrOptions()}</select>
          </div>
<div class="field"><label>虚线经理（仅管理岗，可多选）</label>
             <select id="pc-dotted" multiple size="3">${this.mgrOptions()}</select>
             <div class="hint">每行一个标签，与上方选择顺序对应（如 AML、IT、合规）</div>
             <textarea id="pc-dotted-labels" rows="3" placeholder="虚线标签，每行一个，顺序对应上方选择&#10;示例：&#10;AML 虚线&#10;IT 虚线"></textarea>
           </div>
          <div class="field"><label>法律强制/可选（可留空）</label>
            <select id="pc-legal"><option value="">—</option>${App.legalCategories.map((l) => `<option value="${esc(l.name)}">${esc(l.name)}</option>`).join('')}</select>
          </div>
          <div class="field full"><label>工作职责描述（可留空）</label><textarea id="pc-desc" rows="2"></textarea></div>
          <div class="field full"><label>Org-Chart 显示名</label><input type="text" id="pc-display"></div>
          <div class="field full"><label>备注（可留空）</label><textarea id="pc-remark" rows="2"></textarea></div>
          <div class="field full">
            <label>预算成本六栏（可留空；v2.6 F1.6）</label>
            <div class="cost-toggle" style="margin:4px 0 8px">${this.costModeRadios('pc', false)}</div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">${this.costInputs('pc', null)}</div>
            <div class="hint" id="pc-costhint" style="margin-top:6px"></div>
          </div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="pc-save">保存</button></footer>`);

    this.bindScopeToggle(modal, 'pc');
    this.bindCostMode(modal, 'pc');
    modal.querySelector('#pc-save').onclick = async () => {
      if (!val('#pc-posname')) { toast('请填写职位'); return; }
      if (!val('#pc-opening')) { toast('请填写开启日（必填，作为在岗判定与幂等键依据）'); return; }
      const dottedIds = [...modal.querySelector('#pc-dotted').selectedOptions].map((o) => +o.value);
      const dottedLabels = val('#pc-dotted-labels').split('\n').map(s => s.trim()).filter(s => s);
      const body = {
        position_name: val('#pc-posname'),
        company_id: +val('#pc-company'),
        level: val('#pc-level') || null,
        scope: val('#pc-scope'),
        country_id: val('#pc-scope') === 'country' ? +val('#pc-country') : null,
        position_type: val('#pc-ptype') || null,
        opening_date: val('#pc-opening') || null,
        closing_date: val('#pc-closing') || null,
        work_location: val('#pc-wloc') || null,
        job_responsibility: val('#pc-desc') || null,
        legal_category: val('#pc-legal') || null,
        solid_line_manager_id: val('#pc-solid') ? +val('#pc-solid') : null,
        dotted_manager_ids: dottedIds,
        dotted_manager_labels: dottedLabels,
        org_chart_display: val('#pc-display') || null,
        remark: val('#pc-remark') || null,
        ...this.collectCost(modal, 'pc'),
      };
      try {
        await post('/positions', body);
        closeModal(); toast('岗位已创建，编号自动生成', 'ok'); this.page = 1; this.render(); App.loadStats();
      } catch (e) { toast(e.message); }
    };
  },

  bindScopeToggle(modal, prefix) {
    modal.querySelector(`#${prefix}-scope`).onchange = (e) => {
      modal.querySelector(`#${prefix}-country-wrap`).style.display = e.target.value === 'country' ? '' : 'none';
    };
  },

  async openDetail(id) {
    const p = await get('/positions/' + id);
    const modal = openModal(`
      <header><h2>${esc(p.number)} · ${esc(p.position_name)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="section-title">基本信息 <button class="btn small" id="pd-edit" style="margin-left:8px">编辑</button></div>
        <div class="detail-grid">
          ${ditem('岗位编号', p.number)} ${ditem('状态', statusBadge(p.status))}
          ${ditem('职位', p.position_name)} ${ditem('隶属公司', p.company_name)}
          ${ditem('级别', p.level)} ${ditem('职位类型', p.position_type || '—')}
          ${ditem('工作范围', scopeDisplay(p))}
          ${ditem('开启日', fmtDate(p.opening_date))} ${ditem('关闭日', fmtDate(p.closing_date))}
          ${ditem('工作地点', p.work_location)} ${ditem('占用员工', p.incumbent_name || '—')}
          ${ditem('直线经理', p.solid_line_number ? `${p.solid_line_number} ${p.solid_line_manager_name || ''}` : '—')}
          ${ditem('虚线经理', (p.dotted_manager_numbers || []).join('、') || '—')}
          ${ditem('法律强制/可选', p.legal_category || '—')}
          ${ditem('Org-Chart显示', p.org_chart_display)}
          ${ditem('之前的职位', p.prev_position_number || '—')}
          ${ditem('之前的公司', p.prev_company_name || '—')}
        </div>
        <div class="section-title">预算成本（岗位口径，不随人走）</div>
        <div class="detail-grid">
          ${ditem('输入模式', p.cost_mode === 'auto' ? '自动计算（公司绑定税区）' : '手动输入')}
          ${this.COST_FIELDS.map(([f, label]) => ditem(label, fmtMoney(p[f]))).join('')}
        </div>
        ${p.incumbent_id ? `
        <div class="section-title">实际成本（${esc(p.incumbent_name || '')} · 跟人走）</div>
        <div class="detail-grid">
          ${ditem('输入模式', p.actual_cost_mode === 'auto' ? '自动计算（公司绑定税区）' : '手动输入')}
          ${[['actual_salary_before_tax', '税前（年薪）'], ['actual_mandatory_tax', '强制扣税'],
             ['actual_mandatory_fixed_fee', '强制定额扣费'], ['actual_fixed_bonus', '固定奖金'],
             ['actual_floating_bonus', '浮动奖金'], ['actual_labor_cost', '用工成本']]
            .map(([f, label]) => ditem(label, fmtMoney(p[f]))).join('')}
        </div>` : '<div class="hint" style="margin:4px 0 8px">当前无在职员工，仅显示预算口径；入职后此处并置实际成本对照。</div>'}
        ${p.remark ? `<div class="section-title">备注</div><div style="font-size:13px">${esc(p.remark)}</div>` : ''}

        <div class="section-title">生命周期流转</div>
        <div class="transition-bar" id="pd-trans"></div>

        <div class="section-title">生命周期时间线</div>
        <div class="timeline">
          ${(p.events || []).map((e) => `
            <div class="event">
              <div class="m">${e.from_status ? STATUS_LABEL[e.from_status] + ' → ' : ''}<b>${STATUS_LABEL[e.to_status] || e.to_status}</b></div>
              <div class="n">${esc(e.note || '')} · ${fmtDate(e.changed_at)}</div>
            </div>`).join('') || '<div class="empty">暂无事件</div>'}
        </div>
      </div>`);

    const trans = Positions.TRANSITIONS[p.status] || [];
    modal.querySelector('#pd-trans').innerHTML =
      trans.length
        ? trans.map((t) => `<button class="btn" data-to="${t}">→ ${STATUS_LABEL[t]}</button>`).join('')
        : '<span class="hint">当前状态无手动流转</span>';
    modal.querySelectorAll('[data-to]').forEach((b) => b.onclick = async () => {
      if (!confirm(`确认将岗位流转为「${STATUS_LABEL[b.dataset.to]}」？`)) return;
      try {
        await post(`/positions/${id}/transitions`, { to_status: b.dataset.to, note: '手动流转' });
        closeModal(); toast('流转成功', 'ok'); this.render(); App.loadStats();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#pd-edit').onclick = () => this.openEdit(p);
  },

  async openEdit(p) {
    this._managers = await this.managerOptions(true);  // 强制刷新，避免缓存导致新管理岗缺失
    // 预填虚线标签
    const dottedLabels = (p.dotted_manager_labels || []).join('\n');
    const modal = openModal(`
      <header><h2>编辑岗位 ${esc(p.number)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>职位（职能）</label>
            <input type="text" id="pe-posname" list="posfn-list" value="${esc(p.position_name)}">
            <datalist id="posfn-list">${App.functions.map((f) => `<option value="${esc(f.name)}">`).join('')}</datalist>
          </div>
          <div class="field"><label>隶属公司（已关闭公司不可改选）</label>
            <select id="pe-company">${this.companyOptions(p.company_id)}</select>
          </div>
          <div class="field"><label>级别</label>
            <select id="pe-level"><option value="">—</option>${App.levels.map((l) => `<option value="${esc(l.code)}" ${l.code === p.level ? 'selected' : ''}>${esc(l.code)}${l.label ? ' · ' + esc(l.label) : ''}</option>`).join('')}</select>
          </div>
          <div class="field"><label>职位类型</label>
            <select id="pe-ptype"><option value="">—</option>${App.positionTypes.map((t) => `<option value="${esc(t.name)}" ${t.name === p.position_type ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>工作范围</label>
            <select id="pe-scope">${App.scopes.map((s) => `<option value="${s.code}" ${s.code === p.scope ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select>
          </div>
          <div class="field" id="pe-country-wrap" style="display:${p.scope === 'country' ? '' : 'none'}"><label>国家/地区</label>
            <select id="pe-country">${App.countries.map((c) => `<option value="${c.id}" ${c.id === p.country_id ? 'selected' : ''}>${esc(c.name)} (${esc(c.code)})</option>`).join('')}</select>
          </div>
          <div class="field"><label>开启日 *</label><input type="date" id="pe-opening" value="${fmtDate(p.opening_date)}"></div>
          <div class="field"><label>关闭日</label><input type="date" id="pe-closing" value="${fmtDate(p.closing_date)}"></div>
          <div class="field"><label>工作地点</label>
            <select id="pe-wloc"><option value="">—</option>${App.workLocations.map((w) => `<option value="${esc(w.name)}" ${w.name === p.work_location ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>法律强制/可选（可留空）</label>
            <select id="pe-legal"><option value="">—</option>${App.legalCategories.map((l) => `<option value="${esc(l.name)}" ${l.name === p.legal_category ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>直线经理（仅管理岗）</label>
            <select id="pe-solid"><option value="">无</option>${this._managers.map((m) => `<option value="${m.id}" ${m.id === p.solid_line_manager_id ? 'selected' : ''}>${esc(m.number)} ${esc(m.position_name || '')}（${esc(m.level || '')}）</option>`).join('')}</select>
          </div>
<div class="field"><label>虚线经理（仅管理岗，可多选）</label>
             <select id="pe-dotted" multiple size="3">${this._managers.map((m) => `<option value="${m.id}" ${(p.dotted_manager_ids || []).includes(m.id) ? 'selected' : ''}>${esc(m.number)} ${esc(m.position_name || '')}（${esc(m.level || '')}）</option>`).join('')}</select>
             <div class="hint">每行一个标签，与上方选择顺序对应（如 AML、IT、合规）</div>
             <textarea id="pe-dotted-labels" rows="3" placeholder="虚线标签，每行一个，顺序对应上方选择&#10;示例：&#10;AML 虚线&#10;IT 虚线">${esc(dottedLabels)}</textarea>
           </div>
          <div class="field full"><label>工作职责描述</label><textarea id="pe-desc" rows="2">${esc(p.job_responsibility || '')}</textarea></div>
          <div class="field full"><label>Org-Chart 显示名</label><input type="text" id="pe-display" value="${esc(p.org_chart_display || '')}"></div>
          <div class="field full"><label>备注</label><textarea id="pe-remark" rows="2">${esc(p.remark || '')}</textarea></div>
          <div class="field full">
            <label>预算成本六栏（v2.6 F1.6）</label>
            <div class="cost-toggle" style="margin:4px 0 8px">${this.costModeRadios('pe', p.cost_mode === 'auto')}</div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">${this.costInputs('pe', p)}</div>
            <div class="hint" id="pe-costhint" style="margin-top:6px"></div>
          </div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="pe-save">保存</button></footer>`);

    this.bindScopeToggle(modal, 'pe');
    this.bindCostMode(modal, 'pe');
    const recalcBtn = modal.querySelector('#pe-calccost');
    recalcBtn.dataset.ready = '1';
    if (modal.querySelector('input[name="pe-costmode"]:checked').value === 'auto') recalcBtn.style.display = '';
    recalcBtn.onclick = () => this.recalcCost(modal, p.id, 'pe');
    modal.querySelector('#pe-save').onclick = async () => {
      if (!val('#pe-opening')) { toast('请填写开启日（必填，作为在岗判定与幂等键依据）'); return; }
      const dottedIds = [...modal.querySelector('#pe-dotted').selectedOptions].map((o) => +o.value);
      const dottedLabels = val('#pe-dotted-labels').split('\n').map(s => s.trim()).filter(s => s);
      const body = {
        version: p.version,
        // 职能改名（issue #143）：后端 Update 已支持 position_name（匹配现有或自动新建），
        // 不再发送 position_id——此前全新职能名解析为 null 被 PATCH 静默跳过
        position_name: val('#pe-posname'),
        company_id: +val('#pe-company'),
        level: val('#pe-level') || null,
        scope: val('#pe-scope'),
        country_id: val('#pe-scope') === 'country' ? +val('#pe-country') : null,
        position_type: val('#pe-ptype') || null,
        opening_date: val('#pe-opening') || null,
        closing_date: val('#pe-closing') || null,
        work_location: val('#pe-wloc') || null,
        job_responsibility: val('#pe-desc') || null,
        legal_category: val('#pe-legal') || null,
        solid_line_manager_id: val('#pe-solid') ? +val('#pe-solid') : null,
        dotted_manager_ids: dottedIds,
        dotted_manager_labels: dottedLabels,
        org_chart_display: val('#pe-display') || null,
        remark: val('#pe-remark') || null,
        ...this.collectCost(modal, 'pe'),
      };
      try {
        const updated = await patch('/positions/' + p.id, body);
        p.version = updated.version;
        closeModal(); toast('已保存', 'ok'); this.render();
      } catch (e) { handleApiError(e); }
    };
  },
};

function val(sel) {
  /* 兼容两种调用风格：'#pe-x'（querySelector）与 'pe-x'（getElementById）；
     元素缺失返回 '' 而非抛错（修复表单保存静默失败） */
  const el = sel && sel.startsWith('#') ? document.querySelector(sel) : document.getElementById(sel);
  return el ? el.value.trim() : '';
}

function ditem(k, v) {
  return `<div class="detail-item"><div class="k">${k}</div><div class="v">${v === undefined || v === null || v === '' ? '—' : v}</div></div>`;
}
function shortScope(label) { return String(label || '').split('（')[0]; }
function scopeDisplay(p) {
  const base = shortScope((App.scopes.find((s) => s.code === p.scope) || {}).label || SCOPE_LABEL[p.scope] || p.scope);
  return p.country_name ? base + '·' + p.country_name : base;
}
function fmtMoney(v) {
  return v == null ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}
