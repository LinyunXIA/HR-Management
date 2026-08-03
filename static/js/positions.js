/* 岗位管理：列表 / 新建 / 详情 / 生命周期流转 */
const Positions = {
  filters: { company_id: '', scope: '', status: '', search: '' },
  page: 1,
  pageSize: 20,
  result: null,

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
            ${Object.entries(SCOPE_LABEL).map(([k, v]) => `<option value="${k}" ${this.filters.scope === k ? 'selected' : ''}>${v}</option>`).join('')}
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
          <thead><tr><th>岗位编号</th><th>职位</th><th>隶属公司</th><th>级别</th><th>范围</th><th>状态</th><th>占用员工</th><th>法律强制</th><th></th></tr></thead>
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
    if (!this.result.items.length) return '<tr><td colspan="9" class="empty">暂无岗位</td></tr>';
    return this.result.items.map((p) => `
      <tr>
        <td class="num">${esc(p.number)}</td>
        <td>${esc(p.position_name)}</td>
        <td>${esc(p.company_name)}</td>
        <td>${esc(p.level || '—')}</td>
        <td>${esc(SCOPE_LABEL[p.scope] || p.scope)}${p.country_name ? '·' + esc(p.country_name) : ''}</td>
        <td>${statusBadge(p.status)}</td>
        <td>${esc(p.incumbent_name || '—')}</td>
        <td>${legalBadge(p.legal_category)}</td>
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

  async openCreate() {
    this._all = await this.allPositions();
    const modal = openModal(`
      <header><h2>新建岗位</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>职位（职能）</label>
            <input type="text" id="pc-posname" list="posfn-list" placeholder="输入或选择职位名">
            <datalist id="posfn-list">${App.functions.map((f) => `<option value="${esc(f.name)}">`).join('')}</datalist>
          </div>
          <div class="field"><label>隶属公司</label>
            <select id="pc-company">${App.companies.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>级别</label><input type="text" id="pc-level" placeholder="如 M8a / B7b"></div>
          <div class="field"><label>工作范围</label>
            <select id="pc-scope">${Object.entries(SCOPE_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
          </div>
          <div class="field" id="pc-country-wrap" style="display:none"><label>国家/地区</label>
            <select id="pc-country">${App.countries.map((c) => `<option value="${c.id}">${esc(c.name)} (${esc(c.code)})</option>`).join('')}</select>
          </div>
          <div class="field"><label>职位开启日</label><input type="date" id="pc-opening"></div>
          <div class="field"><label>职位关闭日（关闭时填）</label><input type="date" id="pc-closing"></div>
          <div class="field"><label>工作地点</label><input type="text" id="pc-wloc" placeholder="如 比利时布鲁塞尔"></div>
          <div class="field full"><label>工作职责描述</label><textarea id="pc-desc" rows="2"></textarea></div>
          <div class="field"><label>法律强制/可选</label>
            <select id="pc-legal"><option value="">—</option><option>法律强制·内部全职不可外包</option><option>可选（集团内控推荐）</option><option>纯后勤可选</option></select>
          </div>
          <div class="field"><label>直线经理</label>
            <select id="pc-solid"><option value="">无</option>${this.managerOptions()}</select>
          </div>
          <div class="field"><label>虚线经理（可多选）</label>
            <select id="pc-dotted" multiple size="3">${this.managerOptions()}</select>
          </div>
          <div class="field"><label>Org-Chart 显示名</label><input type="text" id="pc-display"></div>
          <div class="field"><label>岗位编号（留空自动生成）</label><input type="text" id="pc-number" placeholder="P###-{范围}"></div>
          <div class="field full"><label>备注</label><textarea id="pc-remark" rows="2"></textarea></div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="pc-save">保存</button></footer>`);
    modal.querySelector('#pc-scope').onchange = (e) => {
      modal.querySelector('#pc-country-wrap').style.display = e.target.value === 'country' ? '' : 'none';
    };
    modal.querySelector('#pc-save').onclick = async () => {
      const body = {
        position_name: val('#pc-posname'),
        company_id: +val('#pc-company'),
        level: val('#pc-level') || null,
        scope: val('#pc-scope'),
        country_id: val('#pc-scope') === 'country' ? +val('#pc-country') : null,
        opening_date: val('#pc-opening') || null,
        closing_date: val('#pc-closing') || null,
        work_location: val('#pc-wloc') || null,
        job_responsibility: val('#pc-desc') || null,
        legal_category: val('#pc-legal') || null,
        solid_line_manager_id: val('#pc-solid') ? +val('#pc-solid') : null,
        dotted_manager_ids: [...modal.querySelector('#pc-dotted').selectedOptions].map((o) => +o.value),
        org_chart_display: val('#pc-display') || null,
        remark: val('#pc-remark') || null,
        number: val('#pc-number') || null,
      };
      try {
        await post('/positions', body);
        closeModal(); toast('岗位已创建', 'ok'); this.page = 1; this.render(); App.loadStats();
      } catch (e) { toast(e.message); }
    };
  },

  async allPositions() {
    if (!this._all) { const r = await get('/positions?page_size=500'); this._all = r.items; }
    return this._all;
  },

  managerOptions() {
    const list = this._all || (this.result && this.result.items) || [];
    return list.length
      ? list.map((p) => `<option value="${p.id}">${esc(p.number)} ${esc(p.position_name)}</option>`).join('')
      : '';
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
          ${ditem('级别', p.level)} ${ditem('工作范围', SCOPE_LABEL[p.scope] + (p.country_name ? '·' + p.country_name : ''))}
          ${ditem('开启日', fmtDate(p.opening_date))} ${ditem('关闭日', fmtDate(p.closing_date))}
          ${ditem('工作地点', p.work_location)} ${ditem('占用员工', p.incumbent_name || '—')}
          ${ditem('直线经理', p.solid_line_number ? `${p.solid_line_number} ${p.solid_line_manager_name || ''}` : '—')}
          ${ditem('虚线经理', (p.dotted_manager_numbers || []).join('、') || '—')}
          ${ditem('法律强制', p.legal_category ? legalBadge(p.legal_category) : '—')}
          ${ditem('Org-Chart显示', p.org_chart_display)}
          ${ditem('之前的职位', p.prev_position_number || '—')}
          ${ditem('之前的公司', p.prev_company_name || '—')}
        </div>
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
        await post(`/positions/${id}/transition`, { to_status: b.dataset.to, note: '手动流转' });
        closeModal(); toast('流转成功', 'ok'); this.render(); App.loadStats();
      } catch (e) { toast(e.message); }
    });
    modal.querySelector('#pd-edit').onclick = () => this.openEdit(p);
  },

  async openEdit(p) {
    this._all = await this.allPositions();
    const modal = openModal(`
      <header><h2>编辑岗位 ${esc(p.number)}</h2><button class="btn small" onclick="closeModal()">✕</button></header>
      <div class="body">
        <div class="form-grid">
          <div class="field"><label>职位（职能）</label>
            <input type="text" id="pe-posname" list="posfn-list" value="${esc(p.position_name)}">
            <datalist id="posfn-list">${App.functions.map((f) => `<option value="${esc(f.name)}">`).join('')}</datalist>
          </div>
          <div class="field"><label>隶属公司</label>
            <select id="pe-company">${App.companies.map((c) => `<option value="${c.id}" ${c.id === p.company_id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>级别</label><input type="text" id="pe-level" value="${esc(p.level || '')}"></div>
          <div class="field"><label>工作范围</label>
            <select id="pe-scope">${Object.entries(SCOPE_LABEL).map(([k, v]) => `<option value="${k}" ${k === p.scope ? 'selected' : ''}>${v}</option>`).join('')}</select>
          </div>
          <div class="field" id="pe-country-wrap" style="display:${p.scope === 'country' ? '' : 'none'}"><label>国家/地区</label>
            <select id="pe-country">${App.countries.map((c) => `<option value="${c.id}" ${c.id === p.country_id ? 'selected' : ''}>${esc(c.name)} (${esc(c.code)})</option>`).join('')}</select>
          </div>
          <div class="field"><label>开启日</label><input type="date" id="pe-opening" value="${fmtDate(p.opening_date)}"></div>
          <div class="field"><label>关闭日</label><input type="date" id="pe-closing" value="${fmtDate(p.closing_date)}"></div>
          <div class="field"><label>工作地点</label><input type="text" id="pe-wloc" value="${esc(p.work_location || '')}"></div>
          <div class="field full"><label>职责描述</label><textarea id="pe-desc" rows="2">${esc(p.job_responsibility || '')}</textarea></div>
          <div class="field"><label>法律强制/可选</label>
            <select id="pe-legal"><option value="">—</option>${['法律强制·内部全职不可外包', '可选（集团内控推荐）', '纯后勤可选'].map((o) => `<option ${o === p.legal_category ? 'selected' : ''}>${o}</option>`).join('')}</select>
          </div>
          <div class="field"><label>直线经理</label>
            <select id="pe-solid"><option value="">无</option>${(this._all || []).map((o) => `<option value="${o.id}" ${o.id === p.solid_line_manager_id ? 'selected' : ''}>${esc(o.number)} ${esc(o.position_name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>虚线经理（可多选）</label>
            <select id="pe-dotted" multiple size="3">${(this._all || []).map((o) => `<option value="${o.id}" ${(p.dotted_manager_ids || []).includes(o.id) ? 'selected' : ''}>${esc(o.number)} ${esc(o.position_name)}</option>`).join('')}</select>
          </div>
          <div class="field"><label>Org-Chart 显示名</label><input type="text" id="pe-display" value="${esc(p.org_chart_display || '')}"></div>
          <div class="field full"><label>备注</label><textarea id="pe-remark" rows="2">${esc(p.remark || '')}</textarea></div>
        </div>
      </div>
      <footer><button class="btn" onclick="closeModal()">取消</button><button class="btn primary" id="pe-save">保存</button></footer>`);
    modal.querySelector('#pe-scope').onchange = (e) => {
      modal.querySelector('#pe-country-wrap').style.display = e.target.value === 'country' ? '' : 'none';
    };
    modal.querySelector('#pe-save').onclick = async () => {
      const body = {
        position_id: App.functions.find((f) => f.name === val('#pe-posname'))?.id || null,
        position_name: val('#pe-posname'),
        company_id: +val('#pe-company'),
        level: val('#pe-level') || null,
        scope: val('#pe-scope'),
        country_id: val('#pe-scope') === 'country' ? +val('#pe-country') : null,
        opening_date: val('#pe-opening') || null,
        closing_date: val('#pe-closing') || null,
        work_location: val('#pe-wloc') || null,
        job_responsibility: val('#pe-desc') || null,
        legal_category: val('#pe-legal') || null,
        solid_line_manager_id: val('#pe-solid') ? +val('#pe-solid') : null,
        dotted_manager_ids: [...modal.querySelector('#pe-dotted').selectedOptions].map((o) => +o.value),
        org_chart_display: val('#pe-display') || null,
        remark: val('#pe-remark') || null,
      };
      try {
        await put('/positions/' + p.id, body);
        closeModal(); toast('已保存', 'ok'); this.render();
      } catch (e) { toast(e.message); }
    };
  },
};

function val(id) { return document.getElementById(id).value.trim(); }
function ditem(k, v) {
  return `<div class="detail-item"><div class="k">${k}</div><div class="v">${v === undefined || v === null || v === '' ? '—' : v}</div></div>`;
}
