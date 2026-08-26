/* 组织架构图：SVG 汇报线树渲染
   实线 = 直线汇报（父→子），虚线 = 虚线汇报（跨树虚线）。
   支持：虚拟根、含已关闭岗位、公司聚焦视图、范围/状态筛选、折叠、居中缩放、拖拽平移、导出 MD 3 格式。
*/
const NS = 'http://www.w3.org/2000/svg';
const H_SPACE = 230;
const V_SPACE = 132;

const OrgChart = {
  data: null,
  nodeMap: {},
  solidManager: {},
  dottedManagers: {},
  vis: new Set(),
  collapsed: {},
  toggles: { virtualRoot: true, showClosed: false, companyFocus: '', scope: '', status: '' },
  vb: null,            // viewBox {x,y,w,h}
  tops: [],
  bounds: { minX: 0, maxX: 1, minY: 0, maxY: 1 },

  async render() {
    this.data = await get('/org-charts');
    this.vb = null;   // 重进页重置视图
    const companies = [...new Set(this.data.nodes.map((n) => n.company).filter(Boolean))].sort();
    const el = document.getElementById('tab-orgchart');
    el.innerHTML = `
      <div class="org-wrap">
        <div class="org-toolbar">
          <label><input type="checkbox" id="og-virtual" ${this.toggles.virtualRoot ? 'checked' : ''}> 虚拟根</label>
          <label><input type="checkbox" id="og-closed" ${this.toggles.showClosed ? 'checked' : ''}> 含已关闭岗位</label>
          <select id="og-focus" title="聚焦公司">
            <option value="">🎯 聚焦公司：全部</option>
            ${companies.map((c) => `<option value="${esc(c)}" ${this.toggles.companyFocus === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}
          </select>
          <select id="og-scope"><option value="">全部范围</option>
            ${App.scopes.map((s) => `<option value="${s.code}" ${this.toggles.scope === s.code ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select>
          <select id="og-status"><option value="">全部状态</option>
            ${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}" ${this.toggles.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
          <button class="btn small" id="og-fit">适应窗口</button>
          <span class="grow"></span>
          <span class="hint">⬇ 导出 MD：</span>
          <button class="btn small" data-exp="org">公司+岗位</button>
          <button class="btn small" data-exp="solid">直线汇报线</button>
          <button class="btn small" data-exp="dotted">虚线汇报线</button>
        </div>
        <svg class="org-svg" id="org-svg"></svg>
        <div id="og-anomalies"></div>
      </div>`;
    this.buildIndex();
    this.bind();
    this.draw();
    this.renderAnomalies();
  },

  // 孤立/成环异常岗位底部列表（#124，PRD F3.3；数据由后端 build_orgchart.anomalies 提供）
  renderAnomalies() {
    const box = document.getElementById('og-anomalies');
    if (!box) return;
    const an = this.data.anomalies || [];
    box.innerHTML = an.length ? `
      <div class="panel" style="border-left:4px solid var(--danger);margin-top:8px">
        <div class="section-title">⚠️ 异常岗位（孤立 / 成环，${an.length} 个）</div>
        <table style="max-width:760px">
          <thead><tr><th>岗位编号</th><th>显示名</th><th>隶属公司</th><th>状态</th><th>原因</th></tr></thead>
          <tbody>${an.map((a) => `
            <tr>
              <td class="num">${esc(a.number)}</td>
              <td>${esc(a.display || '—')}</td>
              <td>${esc(a.company || '—')}</td>
              <td>${statusBadge(a.status)}</td>
              <td class="hint">${esc(a.reason)}</td>
            </tr>`).join('')}</tbody>
        </table>
      </div>` : '';
  },

  buildIndex() {
    const nodeMap = {};
    this.data.nodes.forEach((n) => { nodeMap[n.number] = { ...n, children: [], parent: null }; });
    this.nodeMap = nodeMap;
    this.data.solid_edges.forEach((e) => {
      const p = nodeMap[e.from], c = nodeMap[e.to];
      if (p && c) { p.children.push(c); c.parent = p; }
    });
    // issue #157：折叠态跨 Tab 持久化——nodeMap 每次 render 重建，
    // 把 this.collapsed 字典（toggle 写入）回填到节点属性，供布局/图标消费
    Object.keys(this.collapsed).forEach((num) => {
      if (nodeMap[num]) nodeMap[num].collapsed = !!this.collapsed[num];
    });
    this.solidManager = {};
    this.data.solid_edges.forEach((e) => { this.solidManager[e.to] = e.from; });
    this.dottedManagers = {};
    this.data.dotted_edges.forEach((e) => { (this.dottedManagers[e.to] = this.dottedManagers[e.to] || []).push(e.from); });
  },

  computeLayout() {
    this.vis = new Set();
    for (const num in this.nodeMap) {
      const n = this.nodeMap[num];
      if (this.toggles.showClosed || !n.closed) this.vis.add(num);
    }
    // 公司聚焦：仅保留该公司岗位及其子树
    if (this.toggles.companyFocus) {
      const newVis = new Set();
      const collect = (num) => {
        if (newVis.has(num)) return;
        newVis.add(num);
        (this.nodeMap[num].children || []).forEach((c) => { if (this.vis.has(c.number)) collect(c.number); });
      };
      for (const num of this.vis) {
        if (this.nodeMap[num].company === this.toggles.companyFocus) collect(num);
      }
      this.vis = newVis;
    }
    const roots = [];
    for (const num of this.vis) {
      const n = this.nodeMap[num];
      const mgr = this.solidManager[num];
      if (!mgr || !this.vis.has(mgr)) roots.push(n);
    }
    let tops = roots;
    if (this.toggles.virtualRoot && roots.length > 1) {
      tops = [{ number: '__virtual', display: '家族自然人股东', company: '', level: '',
                status: '', closed: false, incumbent: '', children: roots, isVirtual: true, parent: null }];
    }
    const counter = { x: 0 };
    // issue #135：laidOut 记录本次布局实际分配了坐标的节点——折叠节点的子树
    // 不参与坐标分配，渲染/边界/连线据此跳过（此前折叠时子树沿用旧坐标继续
    // 渲染；切 Tab 回来后坐标为 undefined → translate(NaN) + viewBox 回退）
    this.laidOut = new Set();
    const assign = (node, depth) => {
      node._depth = depth;
      this.laidOut.add(node.number);
      if (node.collapsed) {
        // 折叠：自身占一个叶子位，子树整体不布局不渲染
        node._children = [];
        node._x = counter.x * H_SPACE;
        counter.x++;
        return;
      }
      node._children = (node.children || []).filter((c) => this.vis.has(c.number));
      if (node._children.length === 0) {
        node._x = counter.x * H_SPACE;
        counter.x++;
      } else {
        node._children.forEach((c) => assign(c, depth + 1));
        node._x = (node._children[0]._x + node._children[node._children.length - 1]._x) / 2;
      }
    };
    tops.forEach((t) => assign(t, 0));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const visit = (n) => {
      if (n.number !== '__virtual' && typeof n._x === 'number' && isFinite(n._x)) {
        minX = Math.min(minX, n._x); maxX = Math.max(maxX, n._x);
        minY = Math.min(minY, n._depth * V_SPACE); maxY = Math.max(maxY, n._depth * V_SPACE);
      }
      (n._children || []).forEach(visit);
    };
    tops.forEach(visit);
    if (!isFinite(minX)) { minX = 0; maxX = 1; minY = 0; maxY = 1; }
    this.tops = tops;
    this.bounds = { minX, maxX, minY, maxY };
  },

  fit() {
    this.computeLayout();
    const { minX, maxX, minY, maxY } = this.bounds;
    const pad = 70;
    this.vb = { x: minX - pad, y: minY - pad, w: maxX - minX + pad * 2, h: maxY - minY + pad * 2 };
  },

  applyViewBox() {
    const svg = document.getElementById('org-svg');
    if (svg && this.vb) svg.setAttribute('viewBox', `${this.vb.x} ${this.vb.y} ${this.vb.w} ${this.vb.h}`);
  },

  draw() {
    const svg = document.getElementById('org-svg');
    this.computeLayout();
    svg.innerHTML = '';
    if (!this.vb) this.fit();
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    this.applyViewBox();
    this.drawDottedEdges(svg);
    this.drawSolidEdges(svg);
    this.drawNodes(svg);
  },

  nodePos(num) {
    const n = this.nodeMap[num];
    return n ? { x: n._x, y: n._depth * V_SPACE } : null;
  },

  cardSize(n) {
    const wd = Math.max(textWidth(n.display, 12), textWidth((n.number || '') + (n.company ? ' ' + n.company : ''), 11));
    return { w: Math.min(Math.max(wd + 24, 150), 300), h: n.isVirtual ? 34 : 76 };
  },

  drawSolidEdges(svg) {
    const g = document.createElementNS(NS, 'g');
    const drawEdge = (num) => {
      const p = this.nodePos(num);
      const mgrNum = this.solidManager[num];
      if (!mgrNum || !p || !this.vis.has(num) || !this.vis.has(mgrNum)) return;
      if (!this.laidOut || !this.laidOut.has(num) || !this.laidOut.has(mgrNum)) return; // issue #135：折叠子树的连线不绘制
      const mp = this.nodePos(mgrNum);
      const from = { x: mp.x, y: mp.y + (this.nodeMap[mgrNum].isVirtual ? 17 : 38) };
      const to = { x: p.x, y: p.y - 38 };
      const midY = from.y + (to.y - from.y) / 2;
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('class', 'solid-line');
      path.setAttribute('d', `M${from.x},${from.y} L${from.x},${midY} L${to.x},${midY} L${to.x},${to.y}`);
      g.appendChild(path);
    };
    Object.keys(this.solidManager).forEach(drawEdge);
    svg.appendChild(g);
  },

  drawDottedEdges(svg) {
    const g = document.createElementNS(NS, 'g');
    const seen = new Set();
    this.data.dotted_edges.forEach((e) => {
      if (!this.vis.has(e.from) || !this.vis.has(e.to)) return;
      if (!this.laidOut || !this.laidOut.has(e.from) || !this.laidOut.has(e.to)) return; // issue #135
      const key = e.from + '>' + e.to;
      if (seen.has(key)) return;
      seen.add(key);
      const p1 = this.nodePos(e.from), p2 = this.nodePos(e.to);
      const y1 = p1.y + 38, y2 = p2.y + 38;
      // 每条虚线独立 <g>：「虚线汇报」标签默认隐藏，悬浮该边时显示（PRD F3.2 悬浮标注，#124）
      const eg = document.createElementNS(NS, 'g');
      eg.setAttribute('class', 'dotted-edge');
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('class', 'dotted-line');
      path.setAttribute('d', `M${p1.x},${y1} C${p1.x},${y1 + 70} ${p2.x},${y2 + 70} ${p2.x},${y2}`);
      eg.appendChild(path);
      const lbl = document.createElementNS(NS, 'text');
      lbl.setAttribute('class', 'dotted-label');
      lbl.setAttribute('x', (p1.x + p2.x) / 2);
      lbl.setAttribute('y', Math.max(y1, y2) + 60);
      lbl.textContent = e.label || '虚线汇报';
      eg.appendChild(lbl);
      g.appendChild(eg);
    });
    svg.appendChild(g);
  },

  drawNodes(svg) {
    const g = document.createElementNS(NS, 'g');
    const filterActive = this.toggles.scope || this.toggles.status;
    const matchFilter = (n) => {
      if (n.isVirtual) return true;
      if (this.toggles.scope && n.scope !== this.toggles.scope) return false;
      if (this.toggles.status && n.status !== this.toggles.status) return false;
      return true;
    };
    const drawNode = (n) => {
      if (this.laidOut && !this.laidOut.has(n.number)) return; // issue #135：折叠子树不渲染
      const group = document.createElementNS(NS, 'g');
      const { w, h } = this.cardSize(n);
      const x = n._x, y = n._depth * V_SPACE;
      const cls = ['org-node', n.status, ...(n.isVirtual ? ['virtual'] : []), ...(filterActive && !matchFilter(n) ? ['dimmed'] : [])];
      group.setAttribute('class', cls.join(' '));
      group.setAttribute('transform', `translate(${x - w / 2} ${y - h / 2})`);

      const card = document.createElementNS(NS, 'rect');
      card.setAttribute('class', 'card');
      card.setAttribute('width', w); card.setAttribute('height', h); card.setAttribute('rx', 8);
      group.appendChild(card);

      if (!n.isVirtual) {
        const l1 = document.createElementNS(NS, 'text');
        l1.setAttribute('class', 'label'); l1.setAttribute('x', 10); l1.setAttribute('y', 18);
        l1.textContent = truncate(n.display, w - 20);
        group.appendChild(l1);

        const l2 = document.createElementNS(NS, 'text');
        l2.setAttribute('class', 'sub'); l2.setAttribute('x', 10); l2.setAttribute('y', 34);
        l2.textContent = (n.number || '') + (n.level ? ' · ' + n.level : '');
        group.appendChild(l2);

        const l3 = document.createElementNS(NS, 'text');
        l3.setAttribute('class', 'sub'); l3.setAttribute('x', 10); l3.setAttribute('y', 50);
        l3.textContent = truncate(n.company || '', w - 20);
        group.appendChild(l3);

        const l4 = document.createElementNS(NS, 'text');
        l4.setAttribute('class', n.incumbent ? 'inc' : 'status-line');
        l4.setAttribute('x', 10); l4.setAttribute('y', 66);
        l4.textContent = n.incumbent ? '👤 ' + n.incumbent : (STATUS_LABEL[n.status] || n.status);
        group.appendChild(l4);

        if ((n.children || []).length) {
          const icon = document.createElementNS(NS, 'text');
          icon.setAttribute('class', 'collapse-icon'); icon.setAttribute('x', w - 18); icon.setAttribute('y', 18);
          icon.setAttribute('style', 'cursor:pointer');
          icon.textContent = n.collapsed ? '▸' : '▾';
          // 折叠热区独立拦截（#124）：卡片点击=跳岗位详情，图标点击=展开/折叠
          icon.addEventListener('click', (e) => { e.stopPropagation(); this.toggle(n.number); });
          group.appendChild(icon);
        }
        group.addEventListener('mouseenter', (e) => this.showTip(e, n));
        group.addEventListener('mousemove', (e) => this.moveTip(e));
        group.addEventListener('mouseleave', () => this.hideTip());
        // 点击节点 → 跳转岗位详情（PRD F3.3，#124）；折叠改由 ▾ 图标承担
        group.addEventListener('click', () => {
          this.hideTip();
          App.show('positions');
          Positions.openDetail(n.id);
        });
      } else {
        const t = document.createElementNS(NS, 'text');
        t.setAttribute('class', 'label'); t.setAttribute('x', w / 2); t.setAttribute('y', 21);
        t.setAttribute('text-anchor', 'middle');
        t.textContent = n.display;
        group.appendChild(t);
      }
      g.appendChild(group);
      (n._children || []).forEach(drawNode);
    };
    this.tops.forEach(drawNode);
    svg.appendChild(g);
  },

  toggle(num) {
    // issue #157：折叠态必须写在**节点属性**上（assign 分支与 ▸/▾ 图标读取的
    // 是 node.collapsed）——此前写 this.collapsed 字典无人消费，折叠自 v1 起失效；
    // 字典同步保留用于跨 Tab 持久化（buildIndex 回填）
    const n = this.nodeMap[num];
    if (!n) return;
    n.collapsed = !n.collapsed;
    this.collapsed[num] = n.collapsed;
    this.draw();
  },

  showTip(e, n) {
    this._tip = document.createElement('div');
    this._tip.className = 'org-tooltip';
    const mgrNum = this.solidManager[n.number];
    const dm = (this.dottedManagers[n.number] || []);
    const dmNames = dm.map((d) => { const x = this.nodeMap[d]; return x ? `${x.number} ${x.position_name || ''}` : d; }).join('、');
    this._tip.innerHTML = `
      <b>${esc(n.display)}</b><br>
      ${esc(n.number || '')} · ${esc(n.level || '')} · ${esc(n.company || '')}<br>
      范围：${esc(shortScopeLabel(n.scope))}${n.country ? '·' + esc(n.country) : ''} · 状态：${esc(STATUS_LABEL[n.status] || n.status)}<br>
      占用：${esc(n.incumbent || '—')}<br>
      直线经理：${mgrNum ? esc(mgrNum) : '—'}<br>
      虚线经理：${esc(dmNames || '—')}`;
    document.body.appendChild(this._tip);
    this.moveTip(e);
  },
  moveTip(e) {
    if (this._tip) { this._tip.style.left = (e.clientX + 14) + 'px'; this._tip.style.top = (e.clientY + 14) + 'px'; }
  },
  hideTip() { if (this._tip) { this._tip.remove(); this._tip = null; } },

  async exportMd(fmt) {
    const res = await fetch(`/api/v1/org-charts?report=${fmt}`, { headers: { 'Accept': 'text/markdown', ..._authHeader() } });
    // issue #150：原生 fetch 补齐全局错误处理（401 弹登录 / 429 toast），与 api() 口径一致
    if (res.status === 401) {
      toast('登录已过期，请重新登录', 'error');
      if (window.Auth) Auth.showLogin();
      throw new Error('未登录');
    }
    if (res.status === 429) {
      const d = await res.json().catch(() => null);
      const msg = (d && d.detail) || '请求过于频繁，请稍后重试';
      toast(msg, 'error');
      throw new Error(msg);
    }
    if (!res.ok) { const d = await res.json().catch(() => null); throw new Error(d?.detail || '导出失败'); }
    const text = await res.text();
    const names = { org: 'orgchart-公司岗位', solid: 'orgchart-直线汇报线', dotted: 'orgchart-虚线汇报线' };
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${names[fmt]}.md`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  },

  bind() {
    const svg = document.getElementById('org-svg');
    document.getElementById('og-virtual').onchange = (e) => { this.toggles.virtualRoot = e.target.checked; this.fit(); this.draw(); };
    document.getElementById('og-closed').onchange = (e) => { this.toggles.showClosed = e.target.checked; this.fit(); this.draw(); };
    document.getElementById('og-focus').onchange = (e) => { this.toggles.companyFocus = e.target.value; this.fit(); this.draw(); };
    document.getElementById('og-scope').onchange = (e) => { this.toggles.scope = e.target.value; this.draw(); };
    document.getElementById('og-status').onchange = (e) => { this.toggles.status = e.target.value; this.draw(); };
    document.getElementById('og-fit').onclick = () => { this.fit(); this.draw(); };
    document.querySelectorAll('[data-exp]').forEach((b) => b.onclick = async () => {
      try { await this.exportMd(b.dataset.exp); toast('已导出', 'ok'); }
      catch (e) { toast(e.message); }
    });

    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (!this.vb) return;
      // 以光标为中心缩放（PRD F3.3 / #124）：保持光标下的图坐标点在缩放前后位置不变
      const rect = svg.getBoundingClientRect();
      const mx = this.vb.x + ((e.clientX - rect.left) / (rect.width || 1)) * this.vb.w;
      const my = this.vb.y + ((e.clientY - rect.top) / (rect.height || 1)) * this.vb.h;
      const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
      const nw = Math.max(200, this.vb.w / factor), nh = Math.max(120, this.vb.h / factor);
      const kx = (mx - this.vb.x) / this.vb.w;
      const ky = (my - this.vb.y) / this.vb.h;
      this.vb = { x: mx - kx * nw, y: my - ky * nh, w: nw, h: nh };
      this.applyViewBox();
    }, { passive: false });

    let dragging = false, sx = 0, sy = 0, vb0 = null;
    svg.addEventListener('mousedown', (e) => {
      if (e.target.closest('.org-node')) return;   // 点击节点交给折叠
      dragging = true; sx = e.clientX; sy = e.clientY; vb0 = { ...this.vb };
      svg.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', (e) => {
      if (!dragging || !vb0) return;
      const vw = svg.clientWidth || 1, vh = svg.clientHeight || 1;
      const dx = (e.clientX - sx) * (vb0.w / vw);
      const dy = (e.clientY - sy) * (vb0.h / vh);
      this.vb = { x: vb0.x - dx, y: vb0.y - dy, w: vb0.w, h: vb0.h };
      this.applyViewBox();
    });
    window.addEventListener('mouseup', () => { dragging = false; svg.style.cursor = 'grab'; });
  },
};

function shortScopeLabel(code) {
  const s = (App.scopes || []).find((x) => x.code === code);
  return s ? String(s.label).split('（')[0] : code;
}
function textWidth(s, size) {
  let w = 0;
  for (const ch of String(s || '')) w += (ch.charCodeAt(0) > 255 ? size : size * 0.6);
  return w;
}
function truncate(s, max) {
  s = String(s || '');
  return s.length > max / 6 ? s.slice(0, Math.floor(max / 6)) + '…' : s;
}
