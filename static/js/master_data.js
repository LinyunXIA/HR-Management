/* 主数据配置页（F0）：公司/级别/工作地点/工作范围/国家/法律强制/员工用工税额 */
const MasterData = {
  current: 'companies',
  taxCountry: '',

  KINDS: [
    { key: 'levels', label: '级别', endpoint: '/levels',
      columns: [{ k: 'code', label: '编码' }, { k: 'label', label: '名称' }, { k: 'is_management', label: '管理岗', type: 'bool' }],
      fields: [
        { k: 'code', label: '编码（如 M8a / B7b）', type: 'text', req: true },
        { k: 'label', label: '名称', type: 'text' },
        { k: 'is_management', label: '管理岗（M 开头）', type: 'check' },
        { k: 'sort_order', label: '排序', type: 'number' }] },
    { key: 'worklocations', label: '工作地点', endpoint: '/work-locations',
      columns: [{ k: 'name', label: '名称' }, { k: 'country', label: '国家/地区' }, { k: 'city', label: '城市' }, { k: 'sort_order', label: '排序' }],
      fields: [{ k: 'name', label: '名称', type: 'text', req: true },
        { k: 'country', label: '国家/地区', type: 'text' },
        { k: 'city', label: '城市', type: 'text' },
        { k: 'sort_order', label: '排序', type: 'number' }] },
    { key: 'scopes', label: '工作范围', endpoint: '/scopes',
      columns: [{ k: 'code', label: '编码' }, { k: 'label', label: '名称' }, { k: 'suffix_code', label: '后缀' }],
      fields: [
        { k: 'code', label: '编码（family/global/regional/country）', type: 'text', req: true },
        { k: 'label', label: '名称', type: 'text', req: true },
        { k: 'suffix_code', label: '后缀编号（1/2/3/4）', type: 'text', req: true },
        { k: 'sort_order', label: '排序', type: 'number' }] },
    { key: 'countries', label: '国家/地区（Country 二级）', endpoint: '/countries',
      columns: [{ k: 'name', label: '名称' }, { k: 'code', label: '编号' }],
      fields: [
        { k: 'name', label: '名称', type: 'text', req: true },
        { k: 'code', label: '编号（如 4-5）', type: 'text', req: true }] },
    { key: 'legalcategories', label: '法律强制/可选', endpoint: '/legal-categories',
      columns: [{ k: 'name', label: '名称' }, { k: 'sort_order', label: '排序' }],
      fields: [{ k: 'name', label: '名称', type: 'text', req: true }, { k: 'sort_order', label: '排序', type: 'number' }] },
    { key: 'positiontypes', label: '职位类型', endpoint: '/position-types',
      columns: [{ k: 'name', label: '名称' }, { k: 'sort_order', label: '排序' }],
      fields: [{ k: 'name', label: '名称', type: 'text', req: true }, { k: 'sort_order', label: '排序', type: 'number' }] },
  ],

  async render() {
    const el = document.getElementById('tab-master');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar"><b>主数据配置管理</b>
          <span class="hint">公司 / 级别 / 工作地点 / 工作范围 / 国家 / 法律强制 / 员工用工税额</span>
        </div>
        <div style="display:flex;gap:16px;padding:14px">
          <div class="side" style="width:210px">
            <div class="item" data-kind="companies" ${this.current === 'companies' ? 'active' : ''}>隶属公司（含股权）</div>
            <div class="item" data-kind="extcompanies" ${this.current === 'extcompanies' ? 'active' : ''}>外部合作公司</div>
            ${this.KINDS.map((k) => `<div class="item" data-kind="${k.key}" ${k.key === this.current ? 'active' : ''}>${k.label}</div>`).join('')}
            <div class="item" data-kind="taxtax" ${this.current === 'taxtax' ? 'active' : ''}>税区与用工税额</div>
          </div>
          <div style="flex:1" id="md-panel"></div>
        </div>
      </div>`;
    el.querySelectorAll('[data-kind]').forEach((item) => item.onclick = () => { this.current = item.dataset.kind; this.render(); });
    if (this.current === 'companies') await this.renderCompanyPanel(el);
    else if (this.current === 'extcompanies') await this.renderExternalPanel(el);
    else if (this.current === 'taxtax') await this.renderTaxPanel(el);
    else await this.renderKindPanel(el);
  },

  kind() { return this.KINDS.find((k) => k.key === this.current); },

  /* ---------------- 隶属公司（v2.4：开业/关闭日期 + 股权结构三来源） ---------------- */
  _shSource(sh) {
    if (sh.internal_company_id) return { type: 'internal', label: sh.internal_company_name };
    if (sh.external_company_id) return { type: 'external', label: sh.external_company_name };
    return { type: 'person', label: sh.person_name };
  },

  _shSummary(shs) {
    if (!shs || !shs.length) return '—';
    return shs.map((sh) => {
      const s = this._shSource(sh);
      const pct = (sh.ownership_pct !== null && sh.ownership_pct !== undefined) ? ` ${sh.ownership_pct}%` : '';
      const tag = s.type === 'external' ? '外部·' : (s.type === 'person' ? '' : '');
      return esc((tag || '') + (s.label || '—') + pct);
    }).join('；');
  },

  async renderCompanyPanel(el) {
    const panel = el.querySelector('#md-panel');
    const items = await get('/companies');
    panel.innerHTML = `
      <div class="toolbar" style="border:1px solid var(--border);border-radius:8px">
        <b>隶属公司</b>
        <span class="hint">v2.4：开业/关闭日期 + 股权结构（内部公司 / 外部合作公司 / 自然人，比例合计≠100% 仅告警不拦截）</span>
        <span class="grow"></span>
        <button class="btn primary" id="co-new">＋ 新增</button>
      </div>
      <div style="margin-top:10px;border:1px solid var(--border);border-radius:8px;overflow:hidden">
        <table><thead><tr><th>名称</th><th>开业日期</th><th>关闭日期</th><th>股权结构</th><th>状态</th><th></th></tr></thead>
        <tbody>${items.length ? items.map((c) => `
          <tr>
            <td>${esc(c.name)}</td>
            <td>${fmtDate(c.opening_date)}</td>
            <td>${fmtDate(c.closing_date)}</td>
            <td>${this._shSummary(c.shareholders)}</td>
            <td>${c.is_active ? '<span class="badge open">启用</span>' : '<span class="badge closed">停用/关闭</span>'}</td>
            <td>
              <button class="btn small" data-edit="${c.id}">编辑</button>
              <button class="btn small danger" data-del="${c.id}">删除</button>
            </td>
          </tr>`).join('') : '<tr><td colspan="6" class="empty">暂无数据</td></tr>'}</tbody></table>
      </div>`;
    panel.querySelector('#co-new').onclick = () => this.openCompanyForm(null);
    panel.querySelectorAll('[data-edit]').forEach((b) => b.onclick = () =>
      this.openCompanyForm(items.find((i) => i.id === +b.dataset.edit)));
    panel.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => this.delCompany(+b.dataset.del, items));
  },

  async delCompany(id, items) {
    const c = items.find((i) => i.id === id);
    if (!confirm(`确认删除公司「${c.name}」？\n（物理删除；被岗位/股权结构/转调目标/HR 绑定引用时将被拒绝）`)) return;
    try { await del('/companies/' + id); toast('已删除', 'ok'); this.render(); }
    catch (e) { toast(e.message); }
  },

  async openCompanyForm(item) {
    const isEdit = !!item;
    // 下拉数据现拉现用：内部公司排除自身；外部合作公司独立主数据
    let allCompanies = [], externals = [];
    try {
      [allCompanies, externals] = await Promise.all([get('/companies'), get('/external-companies')]);
    } catch (e) { toast(e.message); return; }
    const internalOpts = allCompanies.filter((c) => !isEdit || c.id !== item.id);
    this._shRows = isEdit && item.shareholders
      ? item.shareholders.map((sh) => ({
          type: this._shSource(sh).type,
          internal_company_id: sh.internal_company_id,
          external_company_id: sh.external_company_id,
          person_name: sh.person_name,
          ownership_pct: sh.ownership_pct,
        }))
      : [];

    const modal = openModal(`
      <header><h2>${isEdit ? '编辑' : '新增'} · 隶属公司</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid" style="grid-template-columns:1fr 1fr;align-items:end">
          <div class="field"><label>公司名称 *</label><input type="text" id="co-name" value="${esc(item?.name || '')}"></div>
          <div class="field"></div>
          <div class="field"><label>开业日期（可空，年份按 YYYY-01-01 存）</label><input type="date" id="co-opening" value="${item?.opening_date || ''}"></div>
          <div class="field"><label>关闭日期（填了视为关闭；须名下全部岗位已「关闭」；清空即恢复启用）</label><input type="date" id="co-closing" value="${item?.closing_date || ''}"></div>
        </div>
        <div style="margin-top:12px">
          <b>股权结构（0..N 股东）</b>
          <table style="margin-top:6px"><thead><tr><th style="width:130px">来源类型</th><th>股东</th><th style="width:110px">持股比例 %</th><th style="width:60px"></th></tr></thead>
            <tbody id="sh-rows"></tbody>
          </table>
          <div id="sh-warn" style="display:none;margin-top:6px;padding:8px 10px;border-radius:6px;background:#fff3cd;color:#856404;font-size:12px"></div>
          <button class="btn small" id="sh-add" style="margin-top:6px">＋ 添加股东行</button>
          <div class="hint" style="margin-top:4px">内部公司=系统内法人实体（不含自身）；外部合作公司在左侧「外部合作公司」维护；自然人直接填写姓名。比例合计≠100% 时保存仍会成功。</div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="co-save">保存</button></footer>`);

    const tbody = modal.querySelector('#sh-rows');

    const refreshWarn = () => {
      const pcts = this._shRows.map((r) => parseFloat(r.ownership_pct)).filter((v) => !isNaN(v));
      const sum = pcts.reduce((a, b) => a + b, 0);
      const warn = modal.querySelector('#sh-warn');
      if (pcts.length && Math.abs(sum - 100) > 0.005) {
        warn.style.display = ''; warn.textContent = `持股比例合计 ${+sum.toFixed(2)}%（≠100%），请确认`;
      } else warn.style.display = 'none';
    };

    const rowInputHtml = (row) => {
      if (row.type === 'internal')
        return `<select data-sh-field="internal_company_id">${internalOpts.map((c) =>
          `<option value="${c.id}" ${row.internal_company_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>`;
      if (row.type === 'external')
        return `<select data-sh-field="external_company_id">${externals.map((c) =>
          `<option value="${c.id}" ${row.external_company_id === c.id ? 'selected' : ''}>${esc(c.name)}${c.is_active ? '' : '（停用）'}</option>`).join('')}</select>`;
      return `<input type="text" data-sh-field="person_name" placeholder="自然人姓名" value="${esc(row.person_name || '')}">`;
    };

    const renderRows = () => {
      tbody.innerHTML = this._shRows.map((row, idx) => `
        <tr data-sh-idx="${idx}">
          <td><select data-sh-type>
            <option value="internal" ${row.type === 'internal' ? 'selected' : ''}>内部公司</option>
            <option value="external" ${row.type === 'external' ? 'selected' : ''}>外部合作公司</option>
            <option value="person" ${row.type === 'person' ? 'selected' : ''}>自然人</option>
          </select></td>
          <td>${rowInputHtml(row)}</td>
          <td><input type="number" step="0.01" min="0" max="100" data-sh-pct value="${row.ownership_pct ?? ''}" placeholder="可选"></td>
          <td><button class="btn small danger" data-sh-del>删</button></td>
        </tr>`).join('') || '<tr><td colspan="4" class="empty">暂无股东行（如全资可由上级公司 100% 表达）</td></tr>';

      tbody.querySelectorAll('tr[data-sh-idx]').forEach((tr) => {
        const idx = +tr.dataset.shIdx;
        tr.querySelector('[data-sh-type]').onchange = (e) => {
          const t = e.target.value;
          this._shRows[idx] = { type: t, internal_company_id: null, external_company_id: null, person_name: '', ownership_pct: this._shRows[idx].ownership_pct };
          renderRows();
        };
        const inp = tr.querySelector('[data-sh-field]');
        // oninput 即时同步（避免文本输入后直接点保存时 change 未触发的时序问题）；select 同样兼容
        const syncField = () => {
          const row = this._shRows[idx];
          if (row.type === 'internal') row.internal_company_id = +inp.value || null;
          else if (row.type === 'external') row.external_company_id = +inp.value || null;
          else row.person_name = inp.value.trim();
        };
        inp.oninput = syncField;
        inp.onchange = syncField;
        // 渲染后立即以 DOM 当前值为准：下拉首选项默认选中不触发 change，
        // 用户不改选直接保存时状态仍为 null → 「未选择来源」假报错（v2.4.1）
        syncField();
        const pctEl = tr.querySelector('[data-sh-pct]');
        const syncPct = () => {
          this._shRows[idx].ownership_pct = pctEl.value === '' ? null : pctEl.value;
          refreshWarn();
        };
        pctEl.oninput = syncPct;
        pctEl.onchange = syncPct;
        tr.querySelector('[data-sh-del]').onclick = () => { this._shRows.splice(idx, 1); renderRows(); refreshWarn(); };
      });
    };

    modal.querySelector('#sh-add').onclick = () => {
      // 第一家公司场景：系统内尚无其他公司可选，顶层股东通常是家族自然人 → 默认「自然人」
      const defType = internalOpts.length ? 'internal' : 'person';
      this._shRows.push({ type: defType, internal_company_id: null, external_company_id: null, person_name: '', ownership_pct: null });
      renderRows();
    };
    renderRows();

    modal.querySelector('#co-save').onclick = async () => {
      const name = modal.querySelector('#co-name').value.trim();
      if (!name) { toast('请填写公司名称'); return; }
      const body = {
        name,
        opening_date: modal.querySelector('#co-opening').value || null,
        closing_date: modal.querySelector('#co-closing').value || null,
        shareholders: [],
      };
      for (let i = 0; i < this._shRows.length; i++) {
        const r = this._shRows[i];
        const hasSource = (r.type === 'internal' && r.internal_company_id)
          || (r.type === 'external' && r.external_company_id)
          || (r.type === 'person' && r.person_name);
        if (!hasSource) { toast(`第 ${i + 1} 行股东未选择/未填写来源`); return; }
        body.shareholders.push({
          internal_company_id: r.type === 'internal' ? r.internal_company_id : null,
          external_company_id: r.type === 'external' ? r.external_company_id : null,
          person_name: r.type === 'person' ? r.person_name : null,
          ownership_pct: r.ownership_pct === null || r.ownership_pct === '' ? null : +r.ownership_pct,
          sort_order: i,
        });
      }
      try {
        const res = isEdit
          ? await patch('/companies/' + item.id, body)
          : await post('/companies', body);
        closeModal();
        toast(res && res.warning ? '已保存（注意：' + res.warning + '）' : '已保存', res && res.warning ? 'warn' : 'ok');
        this.render();
      } catch (e) { toast(e.message); }
    };
  },


  /* ---------------- 外部合作公司（v2.4.1：关闭日期管理启停，不在系统内设岗） ---------------- */
  async renderExternalPanel(el) {
    const panel = el.querySelector('#md-panel');
    const items = await get('/external-companies');
    panel.innerHTML = `
      <div class="toolbar" style="border:1px solid var(--border);border-radius:8px">
        <b>外部合作公司</b>
        <span class="hint">不在系统内设岗、仅作股权等关系引用的外部法人实体；启停由「关闭日期」管理</span>
        <span class="grow"></span>
        <button class="btn primary" id="ext-new">＋ 新增</button>
      </div>
      <div style="margin-top:10px;border:1px solid var(--border);border-radius:8px;overflow:hidden">
        <table><thead><tr><th>名称</th><th>备注</th><th>开业日期</th><th>关闭日期</th><th>状态</th><th></th></tr></thead>
        <tbody>${items.length ? items.map((c) => `
          <tr>
            <td>${esc(c.name)}</td>
            <td>${esc(c.remark ?? '—')}</td>
            <td>${fmtDate(c.opening_date)}</td>
            <td>${fmtDate(c.closing_date)}</td>
            <td>${c.is_active ? '<span class="badge open">启用</span>' : '<span class="badge closed">停用/关闭</span>'}</td>
            <td>
              <button class="btn small" data-edit="${c.id}">编辑</button>
              <button class="btn small danger" data-del="${c.id}">删除</button>
            </td>
          </tr>`).join('') : '<tr><td colspan="6" class="empty">暂无数据</td></tr>'}</tbody></table>
      </div>`;
    panel.querySelector('#ext-new').onclick = () => this.openExternalForm(null);
    panel.querySelectorAll('[data-edit]').forEach((b) => b.onclick = () =>
      this.openExternalForm(items.find((i) => i.id === +b.dataset.edit)));
    panel.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => this.delExternal(+b.dataset.del, items));
  },

  async delExternal(id, items) {
    const c = items.find((i) => i.id === id);
    if (!confirm(`确认删除外部合作公司「${c.name}」？（被股权结构引用时将被拒绝）`)) return;
    try { await del('/external-companies/' + id); toast('已删除', 'ok'); this.render(); }
    catch (e) { toast(e.message); }
  },

  async openExternalForm(item) {
    const isEdit = !!item;
    const modal = openModal(`
      <header><h2>${isEdit ? '编辑' : '新增'} · 外部合作公司</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body"><div class="form-grid" style="grid-template-columns:1fr 1fr;align-items:end">
        <div class="field"><label>名称 *</label><input type="text" id="ext-name" value="${esc(item?.name || '')}"></div>
        <div class="field"><label>备注</label><input type="text" id="ext-remark" value="${esc(item?.remark || '')}"></div>
        <div class="field"><label>开业日期（可空）</label><input type="date" id="ext-opening" value="${item?.opening_date || ''}"></div>
        <div class="field"><label>关闭日期（填了视为关闭；清空即恢复启用）</label><input type="date" id="ext-closing" value="${item?.closing_date || ''}"></div>
      </div></div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="ext-save">保存</button></footer>`);
    modal.querySelector('#ext-save').onclick = async () => {
      const name = modal.querySelector('#ext-name').value.trim();
      if (!name) { toast('请填写名称'); return; }
      const body = {
        name,
        remark: modal.querySelector('#ext-remark').value.trim() || null,
        opening_date: modal.querySelector('#ext-opening').value || null,
        closing_date: modal.querySelector('#ext-closing').value || null,
      };
      try {
        if (isEdit) await patch('/external-companies/' + item.id, body);
        else await post('/external-companies', body);
        closeModal(); toast('已保存', 'ok'); this.render();
      } catch (e) { toast(e.message); }
    };
  },

  async renderKindPanel(el) {
    const kind = this.kind();
    const items = await get(kind.endpoint);
    const panel = el.querySelector('#md-panel');
    panel.innerHTML = `
      <div class="toolbar" style="border:1px solid var(--border);border-radius:8px">
        <b>${kind.label}</b><span class="grow"></span>
        <button class="btn primary" id="md-new">＋ 新增</button>
      </div>
      <div style="margin-top:10px;border:1px solid var(--border);border-radius:8px;overflow:hidden">
        <table><thead><tr>${kind.columns.map((c) => `<th>${c.label}</th>`).join('')}<th></th></tr></thead>
        <tbody>${items.length ? items.map((it) => this.row(it, kind)).join('') : '<tr><td colspan="99" class="empty">暂无数据</td></tr>'}</tbody></table>
      </div>`;
    panel.querySelector('#md-new').onclick = () => this.openForm(kind, null);
    panel.querySelectorAll('[data-edit]').forEach((b) => b.onclick = () => this.openForm(kind, items.find((i) => i.id === +b.dataset.edit)));
    panel.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => this.del(kind, +b.dataset.del));
  },

  row(it, kind) {
    return `<tr>
      ${kind.columns.map((c) => `<td>${c.type === 'bool' ? (it[c.k] ? '✔' : '—') : esc(it[c.k] ?? '—')}</td>`).join('')}
      <td><button class="btn small" data-edit="${it.id}">编辑</button>
          <button class="btn small danger" data-del="${it.id}">删除</button></td></tr>`;
  },

  openForm(kind, item) {
    const isEdit = !!item;
    const modal = openModal(`
      <header><h2>${isEdit ? '编辑' : '新增'} · ${kind.label}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body"><div class="form-grid">
        ${kind.fields.map((f) => `
          <div class="field ${f.type === 'check' ? '' : ''}"><label>${f.label}${f.req ? ' *' : ''}</label>
          ${f.type === 'check'
            ? `<input type="checkbox" id="mdf-${f.k}" ${item && item[f.k] ? 'checked' : ''}>`
            : `<input type="${f.type || 'text'}" id="mdf-${f.k}" value="${esc(item ? item[f.k] : '')}" ${f.req ? 'required' : ''}>`}
          </div>`).join('')}
      </div></div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="md-save">保存</button></footer>`);
    modal.querySelector('#md-save').onclick = async () => {
      const body = {};
      for (const f of kind.fields) {
        const el = modal.querySelector('#mdf-' + f.k);
        if (f.type === 'check') body[f.k] = el.checked;
        else body[f.k] = el.value.trim() || null;
      }
      try {
        if (isEdit) await patch(kind.endpoint + '/' + item.id, body);
        else await post(kind.endpoint, body);
        closeModal(); toast('已保存', 'ok'); this.render();
      } catch (e) { toast(e.message); }
    };
  },

  async del(kind, id) {
    if (!confirm(`确认删除「${kind.label}」记录？`)) return;
    try { await del(kind.endpoint + '/' + id); toast('已删除', 'ok'); this.render(); }
    catch (e) { toast(e.message); }
  },

  /* ---------------- 税区与用工税额（v2.3 F1.6：挂载点可配置，城市级分拆后无国家兜底） ---------------- */
  async renderTaxPanel(el) {
    const panel = el.querySelector('#md-panel');
    const countries = App.countries;
    if (!countries.length) { panel.innerHTML = '<div class="empty">请先在「国家/地区」维护国家</div>'; return; }
    const zones = await get('/tax-zones');
    panel.innerHTML = `
      <div class="toolbar" style="border:1px solid var(--border);border-radius:8px">
        <b>税区与用工税额</b>
        <span class="hint">税率挂载点：国家级 或 城市级；某国一旦按城市分拆，未配置的城市无兜底 → 成本显示「未配置」</span>
        <span class="grow"></span>
        <button class="btn primary" id="zone-new">＋ 新增税区</button>
      </div>
      <div style="margin-top:10px;border:1px solid var(--border);border-radius:8px;overflow:hidden">
        <table><thead><tr><th>挂载级别</th><th>国家/地区</th><th>城市</th><th>科目数</th><th>合计税率 %</th><th></th></tr></thead>
        <tbody>${zones.length ? zones.map((z) => `
          <tr>
            <td>${z.level === 'city' ? '🏙 城市级' : '🌍 国家级'}</td>
            <td>${esc(z.country_name || '—')}</td>
            <td>${esc(z.city || '—')}</td>
            <td class="num">${(z.items || []).length}</td>
            <td class="num">${(z.items || []).filter((i) => i.is_active).reduce((s, i) => s + i.tax_rate, 0).toFixed(2)}</td>
            <td>
              <button class="btn small" data-zitems="${z.id}">管理科目</button>
              <button class="btn small danger" data-zdel="${z.id}">删除</button>
            </td>
          </tr>`).join('') : '<tr><td colspan="6" class="empty">暂无税区。自动计算成本前请先配置税区与税务科目。</td></tr>'}</tbody></table>
      </div>`;
    panel.querySelector('#zone-new').onclick = () => this.openZoneForm(null);
    panel.querySelectorAll('[data-zitems]').forEach((b) => b.onclick = () =>
      this.openZoneItems(zones.find((z) => z.id === +b.dataset.zitems)));
    panel.querySelectorAll('[data-zdel]').forEach((b) => b.onclick = () => this.delZone(+b.dataset.zdel));
  },

  openZoneForm(item) {
    const isEdit = !!item;
    const modal = openModal(`
      <header><h2>${isEdit ? '编辑' : '新增'} 税区</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body"><div class="form-grid">
        <div class="field"><label>挂载级别 *</label>
          <select id="zf-level"><option value="country">国家级（该国统一税率）</option><option value="city">城市级（按城市分拆）</option></select></div>
        <div class="field"><label>国家/地区 *</label>
          <select id="zf-country">${App.countries.map((c) => `<option value="${c.id}" ${item && item.country_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div>
        <div class="field"><label>城市（城市级必填）</label><input type="text" id="zf-city" value="${esc(item?.city || '')}"></div>
      </div></div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="zf-save">保存</button></footer>`);
    modal.querySelector('#zf-save').onclick = async () => {
      const body = {
        level: modal.querySelector('#zf-level').value,
        country_id: +modal.querySelector('#zf-country').value,
        city: modal.querySelector('#zf-city').value.trim() || null,
      };
      try {
        if (isEdit) await patch('/tax-zones/' + item.id, { city: body.city });
        else await post('/tax-zones', body);
        closeModal(); toast('已保存', 'ok'); this.render();
      } catch (e) { toast(e.message); }
    };
  },

  async delZone(id) {
    if (!confirm('确认删除该税区？（其下不可有税务科目）')) return;
    try { await del('/tax-zones/' + id); toast('已删除', 'ok'); this.render(); }
    catch (e) { toast(e.message); }
  },

  /* 税区下的税务科目维护 */
  openZoneItems(zone) {
    const modal = openModal(`
      <header><h2>税务科目 — ${esc(zone.country_name)}${zone.level === 'city' && zone.city ? ' · ' + esc(zone.city) : ''}</h2>
        <button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <table><thead><tr><th>科目</th><th>税率 %</th><th>启用</th><th></th></tr></thead>
          <tbody id="zi-rows">${(zone.items || []).map((it) => `
            <tr data-item="${it.id}"><td>${esc(it.item_name)}</td><td class="num">${it.tax_rate}</td><td>${it.is_active ? '✔' : '—'}</td>
            <td><button class="btn small danger" data-idel="${it.id}">删</button></td></tr>`).join('')
            || '<tr><td colspan="4" class="empty">暂无科目</td></tr>'}</tbody></table>
        <div class="form-grid" style="margin-top:10px">
          <div class="field"><label>新科目 *</label><input type="text" id="zi-name"></div>
          <div class="field"><label>税率 % *</label><input type="number" step="0.01" id="zi-rate"></div>
          <div class="field" style="align-self:end"><button class="btn" id="zi-add">＋ 添加科目</button></div>
        </div>
      </div>`);
    modal.querySelectorAll('[data-idel]').forEach((b) => b.onclick = () =>
      this.delTax(+b.dataset.idel));
    modal.querySelector('#zi-add').onclick = async () => {
      const name = modal.querySelector('#zi-name').value.trim();
      const rate = parseFloat(modal.querySelector('#zi-rate').value);
      if (!name || isNaN(rate)) { toast('请填写科目名与税率'); return; }
      try {
        await post('/employment-tax-items', { tax_zone_id: zone.id, item_name: name, tax_rate: rate, is_active: true });
        closeModal(); toast('已添加', 'ok'); this.render();
      } catch (e) { toast(e.message); }
    };
  },

  async delTax(id) {
    if (!confirm('确认删除该税务科目？')) return;
    try { await del('/employment-tax-items/' + id); toast('已删除', 'ok'); closeModal(); this.render(); }
    catch (e) { toast(e.message); }
  },
};
