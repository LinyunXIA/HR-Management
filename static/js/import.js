/* 数据导入页 */
const Import = {
  render() {
    const el = document.getElementById('tab-import');
    el.innerHTML = `
      <div class="panel">
        <div class="toolbar"><b>导入 Position.csv</b><span class="hint">17 列（编号列由系统分配 P/PA）· 幂等键=职位+公司+国家+开启日 · 带「岗位ID」列按 ID 认老</span></div>
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
      // issue #150：改走 api() 封装（FormData 支持），401 弹登录 / 429 toast 与全局一致
      const data = await api('/imports', { method: 'POST', body: fd });
      // 编号分配明细（#125）：imported=新建分配 / updated_by_id=按岗位ID 认老 / updated_by_key=幂等键认老
      const ACTION_LABEL = { imported: '新建·系统分配', updated_by_id: '认老·按ID', updated_by_key: '认老·幂等键' };
      const assigned = data.assigned_numbers || [];
      resBox.innerHTML = `
        <div class="report-row">
          <div class="report-card"><div class="n">${data.total}</div><div class="t">共解析</div></div>
          <div class="report-card"><div class="n" style="color:#2e9e5b">${data.imported}</div><div class="t">新增</div></div>
          <div class="report-card"><div class="n" style="color:#2f6fed">${data.updated}</div><div class="t">更新</div></div>
          <div class="report-card"><div class="n" style="color:${data.errors.length ? '#d64545' : '#2e9e5b'}">${data.errors.length}</div><div class="t">错误</div></div>
          <div class="report-card"><div class="n" style="color:${data.warnings.length ? '#d98a1f' : '#2e9e5b'}">${data.warnings.length}</div><div class="t">警告</div></div>
        </div>
        ${(data.updated_by_id || data.updated_by_key) ? `
        <div class="hint" style="margin-top:8px">迭代识别：${data.updated_by_id} 行按「岗位ID」认老 · ${data.updated_by_key} 行按幂等键认老（编号/事件/员工关联保留不变）</div>` : ''}
        ${assigned.length ? `
        <div class="section-title">编号分配明细</div>
        <table style="max-width:640px">
          <thead><tr><th>职位</th><th>岗位编号</th><th>动作</th></tr></thead>
          <tbody>${assigned.map((a) => `
            <tr><td>${esc(a.label || '—')}</td><td><span class="num">${esc(a.number)}</span></td><td>${ACTION_LABEL[a.action] || esc(a.action || '—')}</td></tr>`).join('')}
          </tbody>
        </table>` : ''}
        ${/* issue #150：errors 与 warnings 分区展示（此前合并混排丢失分区） */''}
        ${data.errors.length ? `<div class="section-title" style="color:var(--danger)">❌ 错误（该行未导入）</div><pre class="error-list">${data.errors.map((i) => '✕ ' + esc(i)).join('\n')}</pre>` : ''}
        ${data.warnings.length ? `<div class="section-title" style="color:#d98a1f">⚠️ 警告</div><pre class="error-list">${data.warnings.map((i) => '⚠ ' + esc(i)).join('\n')}</pre>` : ''}
        ${data.imported + data.updated ? '<div style="margin-top:14px"><button class="btn primary" id="imp-refresh">刷新数据并查看</button></div>' : ''}`;
      const btn = resBox.querySelector('#imp-refresh');
      if (btn) btn.onclick = () => { App.loadDicts().then(() => { App.show('positions'); }); };
    } catch (e) {
      resBox.innerHTML = `<pre class="error-list">导入失败：${esc(e.message)}</pre>`;
    }
  },
};
