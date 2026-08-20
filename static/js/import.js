/* 数据导入页 */
const Import = {
  render() {
    const el = document.getElementById('tab-import');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar"><b>导入 Position.csv</b><span class="hint">16 列 91 行 · 幂等（按编号 upsert）· 数据权威为 CSV</span></div>
        <div style="padding:20px">
          <div class="import-drop" id="imp-drop">
            <div style="font-size:18px;margin-bottom:8px">📄 点击选择或拖入 Position.csv</div>
            <div class="hint">也可在命令行：<code>python -m scripts.import_csv data/Position.csv</code></div>
            <input type="file" id="imp-file" accept=".csv" style="display:none">
          </div>
          <div id="imp-result"></div>
        </div>
      </div>`;
    const drop = el.querySelector('#imp-drop');
    const input = el.querySelector('#imp-file');
    drop.onclick = () => input.click();
    input.onchange = () => this.upload(input.files[0]);
    drop.ondragover = (e) => { e.preventDefault(); drop.style.borderColor = '#2f6fed'; };
    drop.ondragleave = () => { drop.style.borderColor = ''; };
    drop.ondrop = (e) => { e.preventDefault(); drop.style.borderColor = ''; const f = e.dataTransfer.files[0]; if (f) this.upload(f); };
  },

  async upload(file) {
    if (!file) return;
    const resBox = document.getElementById('imp-result');
    resBox.innerHTML = '<div class="hint">导入中…</div>';
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch('/api/v1/imports', { method: 'POST', body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || '导入失败');
      const issues = [...(data.errors || []), ...(data.warnings || [])];
      resBox.innerHTML = `
        <div class="report-row">
          <div class="report-card"><div class="n">${data.total}</div><div class="t">共解析</div></div>
          <div class="report-card"><div class="n" style="color:#2e9e5b">${data.imported}</div><div class="t">新增</div></div>
          <div class="report-card"><div class="n" style="color:#2f6fed">${data.updated}</div><div class="t">更新</div></div>
          <div class="report-card"><div class="n" style="color:${data.errors.length ? '#d64545' : '#2e9e5b'}">${data.errors.length}</div><div class="t">错误</div></div>
          <div class="report-card"><div class="n" style="color:${data.warnings.length ? '#d98a1f' : '#2e9e5b'}">${data.warnings.length}</div><div class="t">警告</div></div>
        </div>
        ${issues.length ? `<div class="section-title">明细（错误 / 警告）</div><pre class="error-list">${issues.map((i) => '⚠ ' + esc(i)).join('\n')}</pre>` : ''}
        ${data.imported + data.updated ? '<div style="margin-top:14px"><button class="btn primary" id="imp-refresh">刷新数据并查看</button></div>' : ''}`;
      const btn = resBox.querySelector('#imp-refresh');
      if (btn) btn.onclick = () => { App.loadDicts().then(() => { App.show('positions'); }); };
    } catch (e) {
      resBox.innerHTML = `<pre class="error-list">导入失败：${esc(e.message)}</pre>`;
    }
  },
};
