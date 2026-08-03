/* 组织架构图：SVG 汇报线树渲染
   实线 = 直线汇报（父→子），虚线 = 虚线汇报（跨树虚线）。
   支持：虚拟根开关、含已关闭岗位开关、公司/范围/状态筛选高亮、折叠、缩放平移、悬浮提示。
*/
const NS = 'http://www.w3.org/2000/svg';
const H_SPACE = 230;   // 叶子横向间距
const V_SPACE = 132;   // 层高

const OrgChart = {
  data: null,
  nodeMap: {},          // number -> node
  solidManager: {},     // number -> 直线经理number
  dottedManagers: {},   // number -> [虚线经理number]
  vis: new Set(),       // 可见节点 number 集合
  collapsed: {},        // number -> true
  toggles: { virtualRoot: true, showClosed: false, company: '', scope: '', status: '' },
  scale: 1, tx: 0, ty: 0,
  world: null,

  async render() {
    this.data = await get('/orgchart');
    const el = document.getElementById('tab-orgchart');
    el.innerHTML = `
      <div class="org-wrap">
        <div class="org-toolbar">
          <label><input type="checkbox" id="og-virtual" ${this.toggles.virtualRoot ? 'checked' : ''}> 虚拟根「家族自然人」</label>
          <label><input type="checkbox" id="og-closed" ${this.toggles.showClosed ? 'checked' : ''}> 含已关闭岗位</label>
          <select id="og-company"><option value="">全部公司</option>${App.companies.map((c) => `<option value="${esc(c.name)}" ${this.toggles.company === c.name ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
          <select id="og-scope"><option value="">全部范围</option>${Object.entries(SCOPE_LABEL).map(([k, v]) => `<option value="${k}" ${this.toggles.scope === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
          <select id="og-status"><option value="">全部状态</option>${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}" ${this.toggles.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
          <button class="btn small" id="og-fit">适应窗口</button>
          <span class="hint">滚轮缩放 · 拖拽平移 · 点击节点折叠</span>
        </div>
        <svg class="org-svg" id="org-svg"></svg>
      </div>`;
    this.buildIndex();
    this.bind();
    this.draw();
  },

  buildIndex() {
    const nodeMap = {};
    this.data.nodes.forEach((n) => {
      nodeMap[n.number] = { ...n, children: [], parent: null };
    });
    this.nodeMap = nodeMap;
    this.data.solid_edges.forEach((e) => {
      const p = nodeMap[e.from], c = nodeMap[e.to];
      if (p && c) { p.children.push(c); c.parent = p; }
    });
    this.solidManager = {};
    this.data.solid_edges.forEach((e) => { this.solidManager[e.to] = e.from; });
    this.dottedManagers = {};
    this.data.dotted_edges.forEach((e) => { (this.dottedManagers[e.to] = this.dottedManagers[e.to] || []).push(e.from); });
  },

  isVisible(num) { return this.vis.has(num); },

  computeLayout() {
    // 可见节点集合
    this.vis = new Set();
    for (const num in this.nodeMap) {
      const n = this.nodeMap[num];
      if (this.toggles.showClosed || !n.closed) this.vis.add(num);
    }
    // 可见根
    const roots = [];
    for (const num of this.vis) {
      const n = this.nodeMap[num];
      const mgr = this.solidManager[num];
      if (!mgr || !this.vis.has(mgr)) roots.push(n);
    }
    // 虚拟根
    let tops = roots;
    if (this.toggles.virtualRoot && roots.length > 1) {
      const vroot = {
        number: '__virtual', display: '家族自然人股东', company: '', level: '',
        status: '', closed: false, incumbent: '', children: roots, isVirtual: true, parent: null,
      };
      tops = [vroot];
    }
    // 布局：全局叶子计数从左到右
    const counter = { x: 0 };
    const assign = (node, depth) => {
      node._depth = depth;
      node._children = (node.children || []).filter((c) => this.vis.has(c.number));
      if (node.collapsed || node._children.length === 0) {
        node._x = counter.x * H_SPACE;
        counter.x++;
      } else {
        node._children.forEach((c) => assign(c, depth + 1));
        node._x = (node._children[0]._x + node._children[node._children.length - 1]._x) / 2;
      }
    };
    tops.forEach((t) => assign(t, 0));
    // 边界
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const visit = (n) => {
      if (n.number !== '__virtual') {
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

  draw() {
    const svg = document.getElementById('org-svg');
    this.computeLayout();
    svg.innerHTML = '';
    const { minX, maxX, minY, maxY } = this.bounds;
    const pad = 70;
    svg.setAttribute('viewBox', `${minX - pad} ${minY - pad} ${maxX - minX + pad * 2} ${maxY - minY + pad * 2}`);
    this.world = document.createElementNS(NS, 'g');
    this.applyTransform();
    this.drawDottedEdges(svg);
    this.drawSolidEdges(svg);
    this.drawNodes(svg);
    svg.appendChild(this.world);
  },

  applyTransform() {
    if (this.world) this.world.setAttribute('transform', `translate(${this.tx} ${this.ty}) scale(${this.scale})`);
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
      const n = this.nodeMap[num];
      const p = this.nodePos(num);
      const mgrNum = this.solidManager[num];
      if (!mgrNum || !p || !this.vis.has(num) || !this.vis.has(mgrNum)) return;
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
      const from = e.from, to = e.to;
      if (!this.vis.has(from) || !this.vis.has(to)) return;
      const key = from + '>' + to;
      if (seen.has(key)) return;
      seen.add(key);
      const p1 = this.nodePos(from), p2 = this.nodePos(to);
      const y1 = p1.y + 38, y2 = p2.y + 38;
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('class', 'dotted-line');
      path.setAttribute('d', `M${p1.x},${y1} C${p1.x},${y1 + 70} ${p2.x},${y2 + 70} ${p2.x},${y2}`);
      g.appendChild(path);
      const lbl = document.createElementNS(NS, 'text');
      lbl.setAttribute('class', 'dotted-label');
      lbl.setAttribute('x', (p1.x + p2.x) / 2);
      lbl.setAttribute('y', Math.max(y1, y2) + 60);
      lbl.textContent = '虚线汇报';
      g.appendChild(lbl);
    });
    svg.appendChild(g);
  },

  drawNodes(svg) {
    const g = document.createElementNS(NS, 'g');
    const filterActive = this.toggles.company || this.toggles.scope || this.toggles.status;
    const matchFilter = (n) => {
      if (n.isVirtual) return true;
      if (this.toggles.company && n.company !== this.toggles.company) return false;
      if (this.toggles.scope && n.scope !== this.toggles.scope) return false;
      if (this.toggles.status && n.status !== this.toggles.status) return false;
      return true;
    };
    const drawNode = (n) => {
      const group = document.createElementNS(NS, 'g');
      const { w, h } = this.cardSize(n);
      const x = n._x, y = n._depth * V_SPACE;
      const cls = ['org-node', n.status, ...(n.isVirtual ? ['virtual'] : []), ...(filterActive && !matchFilter(n) ? ['dimmed'] : [])];
      group.setAttribute('class', cls.join(' '));
      group.setAttribute('transform', `translate(${x - w / 2} ${y - h / 2})`);

      const card = document.createElementNS(NS, 'rect');
      card.setAttribute('class', 'card');
      card.setAttribute('width', w);
      card.setAttribute('height', h);
      card.setAttribute('rx', 8);
      group.appendChild(card);

      if (!n.isVirtual) {
        const l1 = document.createElementNS(NS, 'text');
        l1.setAttribute('class', 'label');
        l1.setAttribute('x', 10); l1.setAttribute('y', 18);
        l1.textContent = truncate(n.display, w - 20);
        group.appendChild(l1);

        const l2 = document.createElementNS(NS, 'text');
        l2.setAttribute('class', 'sub');
        l2.setAttribute('x', 10); l2.setAttribute('y', 34);
        l2.textContent = (n.number || '') + (n.level ? ' · ' + n.level : '');
        group.appendChild(l2);

        const l3 = document.createElementNS(NS, 'text');
        l3.setAttribute('class', 'sub');
        l3.setAttribute('x', 10); l3.setAttribute('y', 50);
        l3.textContent = truncate(n.company || '', w - 20);
        group.appendChild(l3);

        const l4 = document.createElementNS(NS, 'text');
        if (n.incumbent) {
          l4.setAttribute('class', 'inc');
          l4.textContent = '👤 ' + n.incumbent;
        } else {
          l4.setAttribute('class', 'status-line');
          l4.textContent = STATUS_LABEL[n.status] || n.status;
        }
        l4.setAttribute('x', 10); l4.setAttribute('y', 66);
        group.appendChild(l4);

        if ((n.children || []).length) {
          const icon = document.createElementNS(NS, 'text');
          icon.setAttribute('class', 'collapse-icon');
          icon.setAttribute('x', w - 18); icon.setAttribute('y', 18);
          icon.textContent = n.collapsed ? '▸' : '▾';
          group.appendChild(icon);
        }
        // 悬浮提示
        group.addEventListener('mouseenter', (e) => this.showTip(e, n));
        group.addEventListener('mousemove', (e) => this.moveTip(e));
        group.addEventListener('mouseleave', () => this.hideTip());
        group.addEventListener('click', () => this.toggle(n.number));
      }
      // 虚拟根样式（虚线卡片 + 文本居中）
      if (n.isVirtual) {
        const t = document.createElementNS(NS, 'text');
        t.setAttribute('class', 'label');
        t.setAttribute('x', w / 2); t.setAttribute('y', 21);
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
    this.collapsed[num] = !this.collapsed[num];
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
      范围：${esc(SCOPE_LABEL[n.scope] || n.scope)}${n.country ? '·' + esc(n.country) : ''} · 状态：${esc(STATUS_LABEL[n.status] || n.status)}<br>
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

  bind() {
    const svg = document.getElementById('org-svg');
    document.getElementById('og-virtual').onchange = (e) => { this.toggles.virtualRoot = e.target.checked; this.tx = 0; this.ty = 0; this.draw(); };
    document.getElementById('og-closed').onchange = (e) => { this.toggles.showClosed = e.target.checked; this.tx = 0; this.ty = 0; this.draw(); };
    document.getElementById('og-company').onchange = (e) => { this.toggles.company = e.target.value; this.draw(); };
    document.getElementById('og-scope').onchange = (e) => { this.toggles.scope = e.target.value; this.draw(); };
    document.getElementById('og-status').onchange = (e) => { this.toggles.status = e.target.value; this.draw(); };
    document.getElementById('og-fit').onclick = () => { this.scale = 1; this.tx = 0; this.ty = 0; this.draw(); };

    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      this.scale = Math.min(4, Math.max(0.2, this.scale * factor));
      this.applyTransform();
    }, { passive: false });

    let dragging = false, sx = 0, sy = 0, stx = 0, sty = 0;
    svg.addEventListener('mousedown', (e) => { dragging = true; sx = e.clientX; sy = e.clientY; stx = this.tx; sty = this.ty; });
    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      this.tx = stx + (e.clientX - sx);
      this.ty = sty + (e.clientY - sy);
      this.applyTransform();
    });
    window.addEventListener('mouseup', () => { dragging = false; });
  },
};

function textWidth(s, size) {
  let w = 0;
  for (const ch of String(s || '')) w += (ch.charCodeAt(0) > 255 ? size : size * 0.6);
  return w;
}
function truncate(s, max) {
  s = String(s || '');
  return s.length > max / 6 ? s.slice(0, Math.floor(max / 6)) + '…' : s;
}
