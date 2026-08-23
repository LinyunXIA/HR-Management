/* 主数据配置页（F0）：公司/级别/工作地点/工作范围/国家/法律强制/员工用工税额 */
const MasterData = {
  current: 'companies',
  taxCountry: '',

  KINDS: [
    { key: 'companies', label: '隶属公司', endpoint: '/companies',
      columns: [{ k: 'name', label: '名称' }],
      fields: [{ k: 'name', label: '名称', type: 'text', req: true }] },
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
            ${this.KINDS.map((k) => `<div class="item" data-kind="${k.key}" ${k.key === this.current ? 'active' : ''}>${k.label}</div>`).join('')}
            <div class="item" data-kind="taxtax" ${this.current === 'taxtax' ? 'active' : ''}>税区与用工税额</div>
          </div>
          <div style="flex:1" id="md-panel"></div>
        </div>
      </div>`;
    el.querySelectorAll('[data-kind]').forEach((item) => item.onclick = () => { this.current = item.dataset.kind; this.render(); });
    if (this.current === 'taxtax') await this.renderTaxPanel(el);
    else await this.renderKindPanel(el);
  },

  kind() { return this.KINDS.find((k) => k.key === this.current); },

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
