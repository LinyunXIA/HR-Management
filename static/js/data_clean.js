/* 数据清洗页：上传 Org-Chart.md → 解析清洗 → CSV 预览（Position.csv 模版）→ 导入 */
const DataClean = {
  parsed: null,
  csvText: null,
  jobId: null,

  async render() {
    const el = document.getElementById('tab-data_clean');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar"><b>数据清洗</b>
          <span class="hint">上传 Org-Chart.md → 解析清洗 → 输出 Position.csv 格式 CSV → 导入系统</span>
        </div>
        <div class="wf-body">

          <div class="section-title">① 上传 Org-Chart.md</div>
          <div class="import-drop" id="dc-drop" style="margin-bottom:16px">
            <div style="font-size:18px;margin-bottom:8px">📄 点击选择或拖入 Org-Chart.md</div>
            <div class="hint">系统将按 Position.csv 模版格式自动清洗并输出 CSV</div>
            <input type="file" id="dc-file" accept=".md,.txt" style="display:none">
          </div>

          <div id="dc-status" style="margin-bottom:12px"></div>
          <div id="dc-report"></div>
          <div id="dc-preview"></div>

          <div id="dc-csv" style="display:none;margin-top:16px">
            <div class="toolbar" style="border:1px solid var(--border);border-radius:8px">
              <b>② 清洗结果 CSV</b>
              <span class="hint">格式：Position.csv 模版（17 列）</span>
              <span class="grow"></span>
              <button class="btn" id="dc-copy">复制 CSV</button>
              <button class="btn" id="dc-download">下载 .csv</button>
              <button class="btn primary" id="dc-import">✓ 确认导入系统</button>
            </div>
            <textarea id="dc-csv-text" readonly
              style="width:100%;height:220px;font-family:ui-monospace,Menlo,monospace;font-size:12px;border:1px solid var(--border);border-radius:8px;padding:8px;margin-top:8px"></textarea>
          </div>

        </div>
      </div>`;

    const drop = document.getElementById('dc-drop');
    const fileInput = document.getElementById('dc-file');

    drop.onclick = () => fileInput.click();
    drop.ondragover = (e) => { e.preventDefault(); drop.style.borderColor = 'var(--primary)'; };
    drop.ondragleave = () => { drop.style.borderColor = ''; };
    drop.ondrop = (e) => {
      e.preventDefault(); drop.style.borderColor = '';
      if (e.dataTransfer.files[0]) this.uploadFile(e.dataTransfer.files[0]);
    };
    fileInput.onchange = () => { if (fileInput.files[0]) this.uploadFile(fileInput.files[0]); };

    document.getElementById('dc-copy').onclick = () => {
      navigator.clipboard.writeText(document.getElementById('dc-csv-text').value);
      toast('已复制到剪贴板', 'ok');
    };
    document.getElementById('dc-download').onclick = () => this.downloadCsv();
    document.getElementById('dc-import').onclick = () => this.importData();
  },

  async uploadFile(file) {
    document.getElementById('dc-status').innerHTML = '<span class="hint">⏳ 正在解析 ' + esc(file.name) + '…</span>';
    document.getElementById('dc-report').innerHTML = '';
    document.getElementById('dc-preview').innerHTML = '';
    document.getElementById('dc-csv').style.display = 'none';

    const fd = new FormData();
    fd.append('orgchart', file);
    try {
      const r = await fetch('/api/v1/data-clean-jobs', { method: 'POST', body: fd });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: '解析失败' }));
        throw new Error(err.detail || '解析失败');
      }
      const data = await r.json();
      this.parsed = data;
      this.csvText = data.csv_text;
      this.jobId = data.id;
      document.getElementById('dc-status').innerHTML =
        '<span style="color:var(--ok)">✅ 解析完成，共 ' + data.total_positions + ' 个岗位</span>' +
        '<span class="hint" style="margin-left:8px">输出格式：Position.csv 模版（17 列）</span>';
      this.renderReport(data.report);
      this.renderPreview(data.cleaned);
      document.getElementById('dc-csv-text').value = data.csv_text;
      document.getElementById('dc-csv').style.display = '';
    } catch (e) {
      document.getElementById('dc-status').innerHTML = '<span style="color:var(--danger)">❌ ' + esc(e.message) + '</span>';
    }
  },

  renderReport(report) {
    const el = document.getElementById('dc-report');
    el.innerHTML = `
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
        <div class="report-card"><div class="n">${report.total_positions}</div><div class="t">解析岗位数</div></div>
        <div class="report-card"><div class="n" style="color:var(--ok)">${report.valid}</div><div class="t">✅ 直接通过</div></div>
        <div class="report-card"><div class="n" style="color:#2f6fed">${report.fixed}</div><div class="t">🔧 已自动修复</div></div>
        <div class="report-card"><div class="n" style="color:${report.warnings.length?'var(--warn)':'var(--ok)'}">${report.warnings.length}</div><div class="t">⚠️ 警告</div></div>
        <div class="report-card"><div class="n" style="color:${report.errors.length?'var(--danger)':'var(--ok)'}">${report.errors.length}</div><div class="t">❌ 错误</div></div>
      </div>
      ${report.warnings.length ? '<div class="hint" style="margin-bottom:8px">🔧 自动修复项：' + report.warnings.map((w) => esc((w.position||'') + ' ' + w.warning)).join('；') + '</div>' : ''}
      ${report.errors.length ? '<div style="color:var(--danger);font-size:13px;margin-bottom:8px">❌ ' + report.errors.map((e) => esc(e.position + ': ' + e.errors.join('; '))).join('<br>') + '</div>' : ''}`;
  },

  renderPreview(cleaned) {
    const el = document.getElementById('dc-preview');
    if (!cleaned.length) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <div class="section-title">清洗结果预览（${cleaned.length} 个岗位）</div>
      <div style="overflow-x:auto;border:1px solid var(--border);border-radius:8px">
        <table>
          <thead><tr><th>岗位编号</th><th>职位</th><th>职位类型</th><th>隶属公司</th><th>直线经理</th><th>法律强制</th><th>开启日</th><th>备注</th></tr></thead>
          <tbody>${cleaned.map((p) => `<tr>
            <td class="num">${esc(p.number || '')}</td>
            <td>${esc(p.position_name || p.name_en || '')}</td>
            <td>${esc((p.type || '').split(' - ')[0])}</td>
            <td>${esc(p.company || '')}</td>
            <td>${esc(p.line_manager || '—')}</td>
            <td>${esc(p.legal_category || '—')}</td>
            <td>${esc(p.opening_year || '—')}</td>
            <td class="hint">${esc(p.remark || '')}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
  },

  async importData() {
    if (!this.csvText) { toast('请先解析 Org-Chart.md'); return; }
    if (!confirm('确认导入清洗后的岗位到系统？\n这将对现有岗位数据执行幂等 upsert（按编号新增或更新）。')) return;
    try {
      if (!this.jobId) { toast('请先重新解析'); return; }
      const r = await post(`/data-clean-jobs/${this.jobId}/imports`, {});
      const imp = r.import_report || {};
      toast('导入完成：新增 ' + (imp.imported||0) + '，更新 ' + (imp.updated||0) + '，错误 ' + (imp.errors?.length||0), 'ok');
      App.loadStats();
    } catch (e) { toast('导入失败：' + e.message); }
  },

  downloadCsv() {
    if (!this.csvText) return;
    const blob = new Blob([this.csvText], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'cleaned_positions.csv';
    document.body.appendChild(a); a.click(); a.remove();
  },
};
