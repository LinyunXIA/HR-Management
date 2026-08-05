/* 数据清洗页：解析原始文件 → 预览/审核 → 确认导入 */
const DataClean = {
  parsed: null,
  csvText: null,

  async render() {
    const el = document.getElementById('tab-data_clean');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar"><b>数据清洗</b>
          <span class="hint">解析 testingdata/原始文件/ 下的 Org-Chart.md + Position.md，清洗后生成标准 CSV</span>
        </div>
        <div class="wf-body">
          <div id="dc-actions" style="display:flex;gap:12px;align-items:center;margin-bottom:16px">
            <button class="btn primary" id="dc-parse">▶ 解析原始文件</button>
            <button class="btn" id="dc-import" disabled>✓ 确认导入</button>
            <span id="dc-status" class="hint"></span>
          </div>
          <div id="dc-report"></div>
          <div id="dc-preview"></div>
          <div id="dc-csv" style="display:none;margin-top:16px">
            <div class="toolbar"><b>生成的 CSV</b>
              <span class="grow"></span>
              <button class="btn" id="dc-copy">复制 CSV</button>
              <button class="btn" id="dc-download">下载 .csv</button>
            </div>
            <textarea id="dc-csv-text" readonly style="width:100%;height:200px;font-family:ui-monospace,Menlo,monospace;font-size:12px;border:1px solid var(--border);border-radius:8px;padding:8px"></textarea>
          </div>
        </div>
      </div>`;

    document.getElementById('dc-parse').onclick = () => this.parse();
    document.getElementById('dc-import').onclick = () => this.importData();
    document.getElementById('dc-copy').onclick = () => {
      navigator.clipboard.writeText(document.getElementById('dc-csv-text').value);
      toast('已复制到剪贴板', 'ok');
    };
    document.getElementById('dc-download').onclick = () => this.downloadCsv();
  },

  async parse() {
    document.getElementById('dc-status').textContent = '解析中…';
    document.getElementById('dc-import').disabled = true;
    try {
      const r = await post('/data-clean/parse', {});
      this.parsed = r;
      this.csvText = r.csv_text;
      this.renderReport(r.report);
      this.renderPreview(r.cleaned);
      document.getElementById('dc-csv-text').value = r.csv_text;
      document.getElementById('dc-csv').style.display = '';
      document.getElementById('dc-import').disabled = false;
      document.getElementById('dc-status').textContent = `✅ 解析完成：${r.total_positions} 个岗位`;
    } catch (e) {
      document.getElementById('dc-status').textContent = '❌ ' + e.message;
      document.getElementById('dc-report').innerHTML = `<div class="empty">解析失败：${esc(e.message)}</div>`;
    }
  },

  renderReport(report) {
    const el = document.getElementById('dc-report');
    el.innerHTML = `
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <div class="report-card"><div class="n">${report.total_positions}</div><div class="t">总岗位数</div></div>
        <div class="report-card"><div class="n" style="color:var(--ok)">${report.valid}</div><div class="t">✅ 直接通过</div></div>
        <div class="report-card"><div class="n" style="color:#2f6fed">${report.fixed}</div><div class="t">🔧 已自动修复</div></div>
        <div class="report-card"><div class="n" style="color:var(--warn)">${report.warnings.length}</div><div class="t">⚠️ 警告</div></div>
        <div class="report-card"><div class="n" style="color:${report.errors.length?'var(--danger)':'var(--ok)'}">${report.errors.length}</div><div class="t">❌ 错误</div></div>
      </div>
      ${report.warnings.length ? '<div class="section-title">⚠️ 修复记录</div><ul style="font-size:13px;padding-left:18px;margin:0 0 12px">' + report.warnings.map((w) => `<li><b>${esc(w.position || '')}</b>：${esc(w.warning)}</li>`).join('') + '</ul>' : ''}
      ${report.errors.length ? '<div class="section-title" style="color:var(--danger)">❌ 错误</div><ul style="font-size:13px;padding-left:18px;margin:0 0 12px;color:var(--danger)">' + report.errors.map((e) => `<li><b>${esc(e.position)}</b>：${esc(e.errors.join('；'))}</li>`).join('') + '</ul>' : ''}`;
  },

  renderPreview(cleaned) {
    const el = document.getElementById('dc-preview');
    if (!cleaned.length) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <div class="section-title">数据预览（${cleaned.length} 个岗位）</div>
      <div style="overflow-x:auto;border:1px solid var(--border);border-radius:8px">
        <table>
          <thead><tr>
            <th>岗位编号</th><th>职位</th><th>职位类型</th><th>隶属公司</th>
            <th>直线经理</th><th>法律强制</th><th>开启日</th>
          </tr></thead>
          <tbody>${cleaned.map((p) => `<tr>
            <td class="num">${esc(p.number)}</td>
            <td>${esc(p.position_name || p.name_en || '')}</td>
            <td>${esc((p.type || '').split(' - ')[0])}</td>
            <td>${esc(p.company || '')}</td>
            <td>${esc(p.line_manager || '—')}</td>
            <td>${esc(p.legal_category || '—')}</td>
            <td>${esc(p.opening_year || '—')}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
  },

  async importData() {
    if (!confirm('确认导入清洗后的数据？这将覆盖现有岗位数据（幂等 upsert）。')) return;
    document.getElementById('dc-status').textContent = '导入中…';
    document.getElementById('dc-import').disabled = true;
    try {
      const r = await post('/data-clean/import', {});
      const imp = r.import_report || {};
      document.getElementById('dc-status').textContent =
        `✅ 导入完成：导入 ${imp.imported || 0}，更新 ${imp.updated || 0}，错误 ${imp.errors?.length || 0}`;
      toast('数据导入完成', 'ok');
      App.loadStats();
    } catch (e) {
      document.getElementById('dc-status').textContent = '❌ 导入失败：' + e.message;
    }
    document.getElementById('dc-import').disabled = false;
  },

  downloadCsv() {
    const blob = new Blob([this.csvText], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'cleaned_positions.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
};
