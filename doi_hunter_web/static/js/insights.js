/* Insight maps. Local, deterministic SVG layouts with verifiable evidence and portable exports. */
(() => {
  'use strict';
  // Layout and visual parameters. No network libraries or build step are required.
  const WIDTH = 1000, HEIGHT = 650, TICKS = 300, PAGE_SIZE = 18;
  const COLORS = ['#0891b2', '#8b5cf6', '#d97706', '#10b981', '#e05d8b', '#527de3', '#a48544'];
  const STAGES = ['subjects', 'design', 'manipulation', 'measurement', 'analysis'];
  const KINDS = { concepts: 'Concepts', words: 'Words', goals: 'Goals', methods: 'Methodology' };
  const TYPES = { test: 'Test hypothesis', effect: 'Measure effect', mechanism: 'Mechanism', compare: 'Compare', develop: 'Develop method', review: 'Review', describe: 'Describe', unknown: 'No aim detected' };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const clip = (v, n = 28) => String(v || '').length > n ? String(v).slice(0, n - 1) + '…' : String(v || '');
  const color = i => COLORS[((Number(i) || 0) % COLORS.length + COLORS.length) % COLORS.length];
  const stageColor = s => color(STAGES.indexOf(s));
  const text = (x, y, value, size = 13, attrs = '') => `<text x="${x}" y="${y}" font-size="${size}" ${attrs}>${esc(value)}</text>`;
  const title = value => `<title>${esc(value)}</title>`;
  const csvCell = value => { const v = String(value ?? ''); return '"' + (typeof value === 'string' && /^[=+@\-\t\r]/.test(v) ? "'" : '') + v.replace(/"/g, '""') + '"'; };

  /** Deterministic velocity-Verlet integration with collision repulsion, springs and centering. */
  function forceLayout(raw, edges) {
    const nodes = raw.map((n, i) => {
      const angle = i * 2.39996323, radius = 35 * Math.sqrt(i + 1);
      return { ...n, x: WIDTH / 2 + Math.cos(angle) * radius, y: HEIGHT / 2 + Math.sin(angle) * radius,
        vx: 0, vy: 0, ax: 0, ay: 0, r: 7 + Math.sqrt(n.count || 1) * 3 };
    });
    const byId = new Map(nodes.map(n => [n.id, n]));
    const springs = edges.map(e => ({ ...e, a: byId.get(e.source), b: byId.get(e.target) })).filter(e => e.a && e.b);
    for (let tick = 0; tick < TICKS; tick++) {
      const cooling = 0.3 + 0.7 * (1 - tick / TICKS);
      for (const n of nodes) { n.x += n.vx + n.ax * .5; n.y += n.vy + n.ay * .5; n.vx += n.ax * .5; n.vy += n.ay * .5; n.ax = (WIDTH / 2 - n.x) * .0018; n.ay = (HEIGHT / 2 - n.y) * .0018; }
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j], dx = a.x - b.x || .1, dy = a.y - b.y || .1;
        const d2 = Math.max(25, dx * dx + dy * dy), d = Math.sqrt(d2);
        const repel = 650 / d2 + Math.max(0, a.r + b.r + 32 - d) * .03;
        a.ax += dx / d * repel; a.ay += dy / d * repel; b.ax -= dx / d * repel; b.ay -= dy / d * repel;
      }
      for (const e of springs) {
        const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.max(1, Math.hypot(dx, dy));
        const rest = e.a.cluster === e.b.cluster ? 92 : 185;
        const f = (d - rest) * .0008 * Math.min(3, Math.sqrt(e.weight || 1));
        e.a.ax += dx / d * f; e.a.ay += dy / d * f; e.b.ax -= dx / d * f; e.b.ay -= dy / d * f;
      }
      for (const n of nodes) {
        n.vx = (n.vx + n.ax * .5) * .78 * cooling; n.vy = (n.vy + n.ay * .5) * .78 * cooling;
        n.x = Math.max(75, Math.min(WIDTH - 75, n.x)); n.y = Math.max(40, Math.min(HEIGHT - 45, n.y));
      }
    }
    // Relax label rectangles after the physics settles. Labels must remain readable at 80 nodes.
    for (let pass = 0; pass < 55; pass++) {
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j], dx = b.x - a.x || .1, dy = b.y - a.y || .1;
        const requiredX = Math.min(145, (Math.min(24, a.label.length) + Math.min(24, b.label.length)) * 3 + 12);
        const overlapX = requiredX - Math.abs(dx), overlapY = 43 - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        if (overlapX / requiredX < overlapY / 43) { const shift = Math.sign(dx) * overlapX * .52; a.x -= shift; b.x += shift; }
        else { const shift = Math.sign(dy) * overlapY * .52; a.y -= shift; b.y += shift; }
      }
      for (const n of nodes) { n.x = Math.max(78, Math.min(WIDTH - 78, n.x)); n.y = Math.max(30, Math.min(HEIGHT - 50, n.y)); }
    }
    if (nodes.length > 1) {
      const minX = Math.min(...nodes.map(n => n.x)), maxX = Math.max(...nodes.map(n => n.x));
      const minY = Math.min(...nodes.map(n => n.y)), maxY = Math.max(...nodes.map(n => n.y));
      const scale = Math.min(2.3, (WIDTH - 180) / Math.max(1, maxX - minX), (HEIGHT - 130) / Math.max(1, maxY - minY));
      for (const n of nodes) { n.x = WIDTH / 2 + (n.x - (minX + maxX) / 2) * scale; n.y = HEIGHT / 2 - 10 + (n.y - (minY + maxY) / 2) * scale; }
    }
    return nodes;
  }

  /** Collision-tested rectangular word cloud, using measured local fonts and a spiral. */
  function wordLayout(words, metric) {
    const ctx = document.createElement('canvas').getContext('2d');
    const score = w => metric === 'distinct' ? Math.max(0, w.distinct || 0) : w.count;
    const sorted = [...words].filter(w => score(w) > 0).sort((a, b) => score(b) - score(a) || a.word.localeCompare(b.word));
    const max = Math.max(1, ...sorted.map(score)), placed = [];
    for (const [i, w] of sorted.slice(0, 90).entries()) {
      const size = 13 + 45 * Math.sqrt(score(w) / max);
      ctx.font = `600 ${size}px Georgia`;
      const width = ctx.measureText(w.word).width + 12, height = size * 1.3;
      if (width > WIDTH - 30) continue;
      for (let step = 0; step < 3200; step++) {
        const a = step * .24 + i * .17, r = step * .18;
        const x = WIDTH / 2 + Math.cos(a) * r * 1.35, y = HEIGHT / 2 + Math.sin(a) * r;
        const rect = { l: x - width / 2, r: x + width / 2, t: y - height / 2, b: y + height / 2 };
        if (rect.l < 15 || rect.r > WIDTH - 15 || rect.t < 15 || rect.b > HEIGHT - 15) continue;
        if (placed.some(p => rect.l < p.rect.r && rect.r > p.rect.l && rect.t < p.rect.b && rect.b > p.rect.t)) continue;
        placed.push({ ...w, x, y, size, rect }); break;
      }
    }
    return placed;
  }

  /** Trigger a browser download, preserving Unicode and revoking the temporary URL. */
  function download(blob, name) {
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  window.insightsPart = function insightsPart() {
    return {
      insight: { kind: 'concepts', loading: false, data: {}, scope: 'all', includeSub: true, source: 'abstract',
        metric: 'count', view: 'cloud', methodView: 'pipeline', focus: '', cluster: null, selected: null,
        svg: '', height: HEIGHT, width: WIDTH, actions: [], nodes: [], page: 0, error: '', request: 0, zoom: 1 },
      insightKinds: Object.entries(KINDS).map(([id, label]) => ({ id, label })),
      insightTitle() { return KINDS[this.insight.kind]; },
      insightDescription() { return ({ concepts: 'Themes that connect your literature', words: 'The language of this body of work', goals: 'Factors, outcomes and the questions between them', methods: 'How the science was done, with evidence for every step' })[this.insight.kind]; },
      insightScopeOptions() {
        const options = [{ value: 'all', label: 'Whole library' }, ...this.collections.map(c => ({ value: 'collection:' + c.id, label: 'Collection · ' + c.name }))];
        const authors = new Set([this.selectedAuthor, ...(this.facets?.authors || []).map(a => a.name)].filter(Boolean));
        for (const a of authors) options.push({ value: 'author:' + a, label: 'Author · ' + a });
        for (const t of this.allTags || []) options.push({ value: 'tag:' + t.name, label: 'Tag · ' + t.name });
        if (!options.some(o => o.value === this.insight.scope)) options.push({ value: this.insight.scope, label: this.insight.scope.replace(':', ' · ') });
        return options;
      },
      /** Capture navigation context before entering the map; switching map kinds keeps that scope. */
      async openInsight(kind = 'concepts', explicit = null) {
        if (!KINDS[kind]) kind = 'concepts';
        if (explicit !== null) {
          this.insight.scope = explicit.collection_id ? 'collection:' + explicit.collection_id : explicit.author ? 'author:' + explicit.author : explicit.tag || explicit.subject ? 'tag:' + (explicit.tag || explicit.subject) : 'all';
        } else if (this.virtual !== 'graph') {
          this.insight.scope = this.virtual === 'collection' && this.selectedCollectionId ? 'collection:' + this.selectedCollectionId : this.virtual === 'author' && this.selectedAuthor ? 'author:' + this.selectedAuthor : this.virtual === 'subject' && this.selectedSubject ? 'tag:' + this.selectedSubject : this.tagFilter?.length === 1 ? 'tag:' + this.tagFilter[0] : 'all';
          this.insight.includeSub = this.includeSub !== false;
        }
        this.virtual = 'graph'; this.clearItem(); this.closeContextMenu();
        this.insight.kind = kind; this.insight.focus = explicit?.focus || ''; this.insight.cluster = null;
        return this.loadInsight();
      },
      // Public compatibility routes used by existing collection/author context menus.
      openGraph(scope) { return this.openInsight('concepts', scope ?? null); },
      openGraphFromCurrent() { return this.openInsight('concepts'); },
      loadSubjectGraph(extra = {}) { if ('focus' in extra) this.insight.focus = extra.focus; return this.loadInsight(); },
      resetGraphFocus() { this.insight.focus = ''; this.insight.cluster = null; return this.loadInsight(); },
      async resetInsightScope() { this.insight.scope = 'all'; this.insight.focus = ''; this.insight.cluster = null; return this.loadInsight(); },
      async loadInsight() {
        const s = this.insight, request = ++s.request, params = { include_sub: s.includeSub ? 'true' : 'false', source: s.source, limit: s.kind === 'concepts' ? 80 : 150, focus: s.focus };
        const colon = s.scope.indexOf(':'), type = s.scope.slice(0, colon), value = s.scope.slice(colon + 1);
        if (type === 'collection') params.collection_id = Number(value);
        if (type === 'author') params.author = value;
        if (type === 'tag') params.tag = value;
        s.loading = true; s.error = ''; s.selected = null; s.page = 0; s.cluster = null;
        try {
          const data = await api.get('/api/insight/' + s.kind, params);
          if (s.request !== request) return;
          s.data = data; if (s.kind === 'words' && !data.scope?.comparison_available) s.metric = 'count';
          s.nodes = forceLayout(data.nodes || [], data.edges || []);
          this.renderInsight();
        } catch (e) { if (s.request === request) { s.error = e.message || 'Could not load this map'; s.data = {}; s.svg = ''; } }
        finally { if (s.request === request) s.loading = false; }
      },
      insightLimits() {
        const labels = { no_abstract: 'without an abstract', no_fulltext: 'without indexed full text', no_methods_section: 'without a detected Methods section', truncated: 'excluded by the scope limit' };
        const limits = this.insight.data.limits || {};
        const notes = Object.entries(limits).filter(([k, v]) => labels[k] && typeof v === 'number' && v > 0).map(([k, v]) => `${v} ${labels[k]}`);
        if (limits.scope_capped) notes.push(`Scope reached the ${limits.max_items || 2000}-paper analysis limit; narrow the scope for complete coverage.`);
        return notes.join(' · ');
      },
      insightHasData() { const d = this.insight.data; return !!(d.nodes?.length || d.words?.length || d.papers?.length); },
      insightPageCount() { return Math.max(1, Math.ceil((this.insight.data.papers || []).length / PAGE_SIZE)); },
      insightPage(delta) { this.insight.page = Math.max(0, Math.min(this.insightPageCount() - 1, this.insight.page + delta)); this.renderInsight(); },
      insightAction(detail) { const index = this.insight.actions.length; this.insight.actions.push(detail); return `data-index="${index}" tabindex="0" role="button"`; },
      insightActivate(event) {
        const element = event.target.closest('[data-index]');
        if (!element) return;
        const detail = this.insight.actions[Number(element.getAttribute('data-index'))];
        if (detail) { this.insight.selected = detail; this.clearItem(); }
      },
      insightHover(event, clear = false) {
        if (this.insight.kind !== 'concepts') return;
        const id = clear ? null : event.target.closest('[data-node]')?.getAttribute('data-node');
        const svg = document.getElementById('insight-svg'); if (!svg) return;
        const neighbours = new Set([id]);
        for (const e of this.insight.data.edges || []) { if (e.source === id) neighbours.add(e.target); if (e.target === id) neighbours.add(e.source); }
        for (const n of svg.querySelectorAll('[data-node]')) n.style.opacity = !id || neighbours.has(n.getAttribute('data-node')) ? 1 : .2;
        for (const e of svg.querySelectorAll('[data-edge]')) e.style.opacity = !id || e.getAttribute('data-a') === id || e.getAttribute('data-b') === id ? 1 : .1;
      },
      async focusInsight() { if (!this.insight.selected) return; this.insight.focus = this.insight.selected.label; return this.loadInsight(); },
      async openInsightPaper(paper) { this.insight.selected = null; return this.openItem(paper.id); },
      insightEvidence(detail) { return (detail?.evidence || []).map(e => typeof e === 'string' ? { sentence: e } : e); },
      methodConfidence(method) { return method.confidence === 'low' ? 'Author-stated' : method.confidence === 'high' ? 'High · Methods section' : 'Medium · repeated body mentions'; },
      goalType(kind) { return TYPES[kind] || kind; },
      /** Construct the active map once; Alpine does not rerun expensive layouts on hover. */
      renderInsight() {
        const s = this.insight; s.actions = []; s.height = HEIGHT; s.width = WIDTH;
        if (s.kind === 'concepts') s.svg = this.conceptSVG();
        if (s.kind === 'words') s.svg = this.wordSVG();
        if (s.kind === 'goals') s.svg = this.goalSVG();
        if (s.kind === 'methods') s.svg = s.methodView === 'matrix' ? this.matrixSVG() : this.pipelineSVG();
      },
      conceptSVG() {
        const s = this.insight, nodes = s.nodes.filter(n => s.cluster === null || n.cluster === s.cluster), byId = new Map(nodes.map(n => [n.id, n]));
        let svg = '';
        for (const e of s.data.edges || []) {
          const a = byId.get(e.source), b = byId.get(e.target); if (!a || !b) continue;
          svg += `<g data-edge="" data-a="${esc(e.source)}" data-b="${esc(e.target)}"><line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${color(a.cluster)}" stroke-width="${Math.min(6, .7 + Math.sqrt(e.weight))}" opacity="${Math.min(.4, .07 + e.weight * .045)}"/></g>`;
        }
        for (const n of nodes) {
          const action = this.insightAction({ label: n.label, items: n.items || [], evidence: n.evidence || [], related: n.related || [], count: n.count });
          svg += `<g class="insight-node" data-node="${esc(n.id)}" ${action}>${title(n.label + ' · ' + n.count + ' papers')}<circle cx="${n.x}" cy="${n.y}" r="${n.r + 6}" fill="${color(n.cluster)}" opacity=".1"/><circle cx="${n.x}" cy="${n.y}" r="${n.r}" fill="${color(n.cluster)}"/>${text(n.x, n.y + n.r + 18, clip(n.label, nodes.length > 45 ? 21 : 32), nodes.length > 45 ? 11 : 13, 'text-anchor="middle" class="insight-label"')}${text(n.x, n.y + 4, n.count, 10, 'text-anchor="middle" fill="white"')}</g>`;
        }
        return svg;
      },
      wordSVG() {
        const s = this.insight, words = s.data.words || [], detail = w => ({ label: w.word, count: w.n_items, items: (w.items || []).map(id => s.data.items?.[id]).filter(Boolean), evidence: w.evidence || [] });
        if (s.view === 'bars') {
          const sorted = [...words].sort((a, b) => s.metric === 'distinct' ? b.distinct - a.distinct : b.count - a.count).slice(0, 50);
          s.height = Math.max(HEIGHT, sorted.length * 34 + 40);
          const max = Math.max(1, ...sorted.map(w => s.metric === 'distinct' ? Math.abs(w.distinct) : w.count));
          return sorted.map((w, i) => {
            const v = s.metric === 'distinct' ? w.distinct : w.count, y = 20 + i * 34, negative = v < 0;
            return `<g class="insight-node" ${this.insightAction(detail(w))}>${title(`${w.word}: ${w.count} occurrences, log-odds ${w.distinct}`)}${text(20, y + 19, clip(w.word, 33))}<rect x="300" y="${y + 3}" width="${Math.max(2, Math.abs(v) / max * 540)}" height="20" rx="4" fill="${negative ? '#a78bfa' : color(i % 4)}" opacity=".7"/>${text(865, y + 19, s.metric === 'distinct' ? v.toFixed(2) : v)}</g>`;
          }).join('');
        }
        const placed = wordLayout(words, s.metric);
        return placed.map((w, i) => `<g class="insight-node" ${this.insightAction(detail(w))}>${title(`${w.count} occurrences · ${w.n_items} papers · log-odds ${w.distinct}`)}${text(w.x, w.y, w.word, w.size, `text-anchor="middle" dominant-baseline="central" font-family="Georgia,serif" font-weight="600" fill="${color(i % 6)}"`)}</g>`).join('') + text(20, HEIGHT - 6, `${placed.length} of ${words.length} terms placed. Ranked bars show signed distinctiveness.`, 11, 'opacity=".65"');
      },
      goalSVG() {
        const s = this.insight, d = s.data, factors = d.factors || [], outcomes = d.outcomes || [];
        s.height = Math.max(HEIGHT, Math.max(factors.length, outcomes.length) * 60 + 75);
        const fy = new Map(factors.map((n, i) => [n.id, 75 + i * 60])), oy = new Map(outcomes.map((n, i) => [n.id, 75 + i * 60]));
        let svg = text(20, 30, 'FACTORS', 12, 'letter-spacing="2" opacity=".65"') + text(725, 30, 'OUTCOMES', 12, 'letter-spacing="2" opacity=".65"');
        for (const l of d.links || []) {
          const y1 = fy.get(l.factor), y2 = oy.get(l.outcome); if (y1 === undefined || y2 === undefined) continue;
          const action = this.insightAction({ label: l.factor + ' → ' + l.outcome, items: l.items, evidence: l.items.map(p => ({ id: p.id, sentence: p.evidence })) });
          const path = `M 270 ${y1} C 435 ${y1},565 ${y2},715 ${y2}`;
          svg += `<g class="insight-goal-link" ${action}>${title(`${l.factor} → ${l.outcome}: ${l.items.length} papers`)}<path d="${path}" fill="none" stroke="transparent" stroke-width="18"/><path d="${path}" fill="none" stroke="${color(0)}" opacity=".45" stroke-width="${2 + Math.sqrt(l.items.length) * 2}"/></g>`;
        }
        for (const [nodes, positions, x] of [[factors, fy, 20], [outcomes, oy, 735]]) for (const n of nodes) {
          const papers = (d.papers || []).filter(p => n.items.includes(p.id));
          const action = this.insightAction({ label: n.label, items: papers, evidence: papers.flatMap(p => p.aims.map(a => ({ id: p.id, sentence: a.sentence }))) });
          svg += `<g class="insight-node" ${action}>${title(n.label)}<rect x="${x - 5}" y="${positions.get(n.id) - 23}" width="245" height="46" rx="9" class="insight-svg-card"/>${text(x + 5, positions.get(n.id) - 2, clip(n.label, 29), 12)}${text(x + 5, positions.get(n.id) + 14, n.count + ' papers', 10, 'opacity=".6"')}</g>`;
        }
        if (!factors.length) svg += text(35, 115, 'No factor-to-outcome pairs detected. Review the aim sentences below.', 16);
        return svg;
      },
      pipelineSVG() {
        const s = this.insight, papers = (s.data.papers || []).slice(s.page * PAGE_SIZE, (s.page + 1) * PAGE_SIZE), col = 174, left = 250;
        s.width = 1150; let y = 75, svg = text(20, 30, 'PAPER / EVIDENCE COVERAGE', 11, 'letter-spacing="1" opacity=".6"');
        for (const [i, stage] of (s.data.stages || []).entries()) svg += text(left + i * col, 30, stage.label, 12, `fill="${stageColor(stage.id)}" font-weight="600"`);
        for (const p of papers) {
          const lines = Math.max(2, ...(p.pipeline || []).map(stage => stage.methods.length)), height = lines * 31 + 35;
          svg += `<rect x="6" y="${y - 22}" width="1138" height="${height}" rx="10" class="insight-svg-card"/>`;
          const paperAction = this.insightAction({ label: p.title, items: [p], evidence: p.detected.map(m => ({ sentence: m.evidence, source: m.source || m.confidence })) });
          svg += `<g class="insight-node" ${paperAction}>${title(p.title)}${text(20, y, clip(p.title, 30), 13, 'font-weight="600"')}${text(20, y + 19, clip(p.title.slice(29), 30), 12)}${text(20, y + 42, `${p.year || 'Year unknown'} · ${p.source}`, 10, 'opacity=".65"')}</g>`;
          for (const [i, stage] of (p.pipeline || []).entries()) {
            if (!stage.methods.length) svg += text(left + i * col, y + 3, 'Not detected', 11, 'opacity=".4"');
            for (const [j, m] of stage.methods.entries()) {
              const x = left + i * col, cy = y - 14 + j * 31, confidence = this.methodConfidence(m);
              const action = this.insightAction({ label: m.label, items: [p], evidence: [{ id: p.id, sentence: m.evidence, source: confidence }], confidence });
              svg += `<g class="insight-method-chip" ${action}>${title(m.label + '\n' + confidence + '\n' + m.evidence)}<rect x="${x}" y="${cy}" width="164" height="25" rx="6" fill="${stageColor(stage.stage)}" opacity=".14"/><circle cx="${x + 8}" cy="${cy + 12}" r="3" fill="${stageColor(stage.stage)}" opacity="${m.confidence === 'high' ? 1 : .45}"/>${text(x + 16, cy + 16, clip(m.label, 23), 10.5)}</g>`;
            }
          }
          y += height + 10;
        }
        s.height = Math.max(230, y); return svg;
      },
      matrixSVG() {
        const s = this.insight, methods = s.data.matrix || [], papers = (s.data.papers || []).slice(s.page * PAGE_SIZE, (s.page + 1) * PAGE_SIZE);
        const cell = 30, left = 260, top = 180; s.width = Math.max(WIDTH, left + methods.length * cell + 30); s.height = Math.max(270, top + papers.length * 38 + 35);
        let svg = text(18, 25, 'Papers × methods · columns ordered by prevalence', 14);
        methods.forEach((m, i) => { svg += `<g transform="translate(${left + i * cell + 15},${top - 16}) rotate(-55)">${title(m.label)}${text(0, 0, clip(m.label, 27), 11, `fill="${stageColor(m.stage)}"`)}</g>`; });
        papers.forEach((p, row) => {
          const y = top + row * 38;
          svg += `<g class="insight-node" ${this.insightAction({ label: p.title, items: [p], evidence: [] })}>${title(p.title)}${text(16, y + 18, clip(p.title, 34), 12)}</g>`;
          methods.forEach((m, col) => {
            const found = p.detected.find(d => d.id === m.id), x = left + col * cell;
            const detail = { label: m.label, items: [p], evidence: found ? [{ sentence: found.evidence, source: this.methodConfidence(found) }] : [{ sentence: 'Not detected in the available text. This does not establish that the method was not used.' }] };
            svg += `<g class="insight-matrix-cell" ${this.insightAction(detail)}>${title(found ? found.label + ': ' + found.evidence : m.label + ': not detected')}<rect x="${x}" y="${y}" width="25" height="25" rx="4" fill="${stageColor(m.stage)}" opacity="${found ? found.confidence === 'high' ? .9 : .5 : .07}"/></g>`;
          });
        });
        return svg;
      },
      /** Reporting drill-down keeps affirmative and absent statements inspectable per paper. */
      inspectReporting(check) {
        const papers = (this.insight.data.papers || []).filter(p => p.checks.some(c => c.id === check.id && c.evidence));
        this.insight.selected = { label: check.label, items: papers, evidence: papers.map(p => { const c = p.checks.find(c => c.id === check.id); return { id: p.id, sentence: c.evidence, source: c.status || (c.found ? 'detected' : 'not detected') }; }) }; this.clearItem();
      },
      inspectGoalPaper(paper) { this.insight.selected = { label: paper.title, items: [paper], evidence: paper.aims.map(a => ({ sentence: a.sentence, source: this.goalType(a.kind) })) }; this.clearItem(); },
      insightCSV() {
        const d = this.insight.data, kind = this.insight.kind;
        if (kind === 'methods') return [['paper_id', 'title', 'year', 'text_source', ...(d.matrix || []).map(m => m.label)], ...(d.papers || []).map(p => [p.id, p.title, p.year, p.source, ...(d.matrix || []).map(m => p.detected.some(x => x.id === m.id) ? 1 : 0)])];
        if (kind === 'words') return [['word', 'occurrences', 'paper_count', 'log_odds', 'paper_ids'], ...(d.words || []).map(w => [w.word, w.count, w.n_items, w.distinct, w.items.join(';')])];
        if (kind === 'goals') return [['factor', 'outcome', 'goal_type', 'paper_id', 'paper_title', 'evidence'], ...(d.links || []).flatMap(l => l.items.map(p => [l.factor, l.outcome, l.kind, p.id, p.title, p.evidence]))];
        return [['record_type', 'id_or_source', 'label_or_target', 'paper_count_or_weight', 'cluster', 'paper_ids'], ...(d.nodes || []).map(n => ['node', n.id, n.label, n.count, n.cluster, n.items.map(p => p.id).join(';')]), ...(d.edges || []).map(e => ['edge', e.source, e.target, e.weight, '', ''])];
      },
      async exportInsight(format) {
        const s = this.insight, name = `ReviewHunter-${s.kind}-${(s.data.scope?.label || 'library').replace(/[^a-z0-9-]/gi, '_')}`;
        try {
          if (format === 'json') return download(new Blob([JSON.stringify(s.data, null, 2)], { type: 'application/json' }), name + '.json');
          if (format === 'csv') return download(new Blob(['\ufeff' + this.insightCSV().map(row => row.map(csvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }), name + '.csv');
          const background = this.dark ? '#0f111c' : '#f8f9fb', foreground = this.dark ? '#eef0f4' : '#272b3c';
          const notes = s.kind === 'methods' ? [this.insightLimits(), ...(s.data.checks || []).map(c => `${c.label}: ${c.count}/${s.data.scope?.items || 0} papers`), ...(s.data.insights?.gaps || [])] : [this.insightLimits()];
          const footer = notes.filter(Boolean).flatMap(line => String(line).match(/.{1,115}(?:\s|$)|.{1,115}/g) || []);
          const height = s.height + 90 + footer.length * 22;
          const markup = `<svg xmlns="http://www.w3.org/2000/svg" width="${s.width}" height="${height}" viewBox="0 0 ${s.width} ${height}"><style>text{font-family:Arial,sans-serif}text:not([fill]){fill:${foreground}}.insight-svg-card{fill:${this.dark ? '#181b29' : '#fff'};stroke:${this.dark ? '#272b3c' : '#dde1ea'}}.insight-label{paint-order:stroke;stroke:${background};stroke-width:3px;stroke-linejoin:round}</style><rect width="100%" height="100%" fill="${background}"/>${text(20, 27, this.insightTitle() + ' · ' + (s.data.scope?.label || ''), 20)}${text(20, 49, `${s.data.scope?.items || 0} papers · ${s.kind === 'methods' ? 'Displayed page ' + (s.page + 1) + '; CSV/JSON include every paper. 0 = not detected.' : 'Evidence and complete data in the accompanying JSON.'}`, 11)}<g transform="translate(0,65)">${s.svg}</g>${footer.map((line, i) => text(20, s.height + 90 + i * 22, line, 12)).join('')}</svg>`;
          const url = URL.createObjectURL(new Blob([markup], { type: 'image/svg+xml' }));
          try {
            const img = new Image(); await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = url; });
            const scale = Math.min(2, 12000 / height, 12000 / s.width), canvas = document.createElement('canvas'); canvas.width = s.width * scale; canvas.height = height * scale;
            canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
            const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png')); if (!blob) throw new Error('PNG export failed'); download(blob, name + '.png');
          } finally { URL.revokeObjectURL(url); }
        } catch (e) { this.toast('Export failed: ' + (e.message || 'Could not render image'), 'error'); }
      },
    };
  };
  // Small pure entry points for the browser harness and deterministic layout regression checks.
  window.InsightLayouts = { forceLayout, wordLayout, csvCell };
})();
