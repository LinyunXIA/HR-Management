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
      columns: [{ k: 'name', label: '名称' }, { k: 'sort_order', label: '排序' }],
      fields: [{ k: 'name', label: '名称', type: 'text', req: true }, { k: 'sort_order', label: '排序', type: 'number' }] },
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
  ],

  async render() {
    const el = document.getElementById('tab-master');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar"><b>主数据配置管理</b>
          <span class="hint">公司 / 级别 / 工作地点 / 工作范围 / 国家 / 员工用工税额</span>
        </div>
        <div style="display:flex;gap:16px;padding:14px">
          <div class="side" style="width:210px">
            ${this.KINDS.map((k) => `<div class="item" data-kind="${k.key}" ${k.key === this.current ? 'active' : ''}>${k.label}</div>`).join('')}
            <div class="item" data-kind="taxtax" ${this.current === 'taxtax' ? 'active' : ''}>员工用工税额（按国家）</div>
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

  /* ---------------- 员工用工税额 ---------------- */
  async renderTaxPanel(el) {
    const panel = el.querySelector('#md-panel');
    const countries = App.countries;
    if (!countries.length) { panel.innerHTML = '<div class="empty">请先在「国家/地区」维护国家</div>'; return; }
    if (!this.taxCountry) this.taxCountry = countries[0].id;
    const items = await get('/employment-tax-items?country_id=' + this.taxCountry);
    const cname = countries.find((c) => c.id === this.taxCountry)?.name || '';
    const totalRate = items.filter((i) => i.is_active).reduce((s, i) => s + i.tax_rate, 0);
    panel.innerHTML = `
      <div class="toolbar" style="border:1px solid var(--border);border-radius:8px">
        <b>员工用工税额 · ${esc(cname)}</b>
        <select id="tax-country">${countries.map((c) => `<option value="${c.id}" ${c.id === this.taxCountry ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
        <span class="hint">启用科目合计税率：${totalRate.toFixed(2)}%（公司份额 = 税前薪资 × ${totalRate.toFixed(2)}%）</span>
        <span class="grow"></span>
        <button class="btn primary" id="tax-new">＋ 新增科目</button>
      </div>
      <div style="margin-top:10px;border:1px solid var(--border);border-radius:8px;overflow:hidden">
        <table><thead><tr><th>科目</th><th>税率 %</th><th>启用</th><th></th></tr></thead>
        <tbody>${items.length ? items.map((it) => `
          <tr><td>${esc(it.item_name)}</td><td class="num">${it.tax_rate}</td><td>${it.is_active ? '✔' : '—'}</td>
          <td><button class="btn small" data-tedit="${it.id}">编辑</button>
              <button class="btn small danger" data-tdel="${it.id}">删除</button></td></tr>`).join('')
          : '<tr><td colspan="4" class="empty">该国家暂无税务科目配置</td></tr>'}</tbody></table>
      </div>`;
    panel.querySelector('#tax-country').onchange = (e) => { this.taxCountry = +e.target.value; this.render(); };
    panel.querySelector('#tax-new').onclick = () => this.openTaxForm(null);
    panel.querySelectorAll('[data-tedit]').forEach((b) => b.onclick = () => this.openTaxForm(items.find((i) => i.id === +b.dataset.tedit)));
    panel.querySelectorAll('[data-tdel]').forEach((b) => b.onclick = () => this.delTax(+b.dataset.tdel));
  },

  openTaxForm(item) {
    const isEdit = !!item;
    const modal = openModal(`
      <header><h2>${isEdit ? '编辑' : '新增'} 税务科目</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body"><div class="form-grid">
        <div class="field"><label>国家/地区</label>
          <select id="taxf-country">${App.countries.map((c) => `<option value="${c.id}" ${c.id === this.taxCountry ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div>
        <div class="field"><label>科目 *</label><input type="text" id="taxf-name" value="${esc(item?.item_name || '')}" required></div>
        <div class="field"><label>税率 % *</label><input type="number" step="0.01" id="taxf-rate" value="${item?.tax_rate ?? ''}" required></div>
        <div class="field"><label>启用</label><input type="checkbox" id="taxf-active" ${!item || item.is_active ? 'checked' : ''}></div>
      </div></div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="taxf-save">保存</button></footer>`);
    modal.querySelector('#taxf-save').onclick = async () => {
      if (!val('#taxf-name') || val('#taxf-rate') === '') { toast('请填写科目与税率'); return; }
      const body = {
        country_id: +val('#taxf-country'),
        item_name: val('#taxf-name'),
        tax_rate: parseFloat(val('#taxf-rate')),
        is_active: modal.querySelector('#taxf-active').checked,
      };
      try {
        if (isEdit) await patch('/employment-tax-items/' + item.id, body);
        else await post('/employment-tax-items', body);
        closeModal(); toast('已保存', 'ok'); this.render();
      } catch (e) { toast(e.message); }
    };
  },

  async delTax(id) {
    if (!confirm('确认删除该税务科目？')) return;
    try { await del('/employment-tax-items/' + id); toast('已删除', 'ok'); this.render(); }
    catch (e) { toast(e.message); }
  },
};
