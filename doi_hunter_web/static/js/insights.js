/* Insight maps. Local, deterministic SVG layouts with verifiable evidence and portable exports. */
(() => {
  'use strict';
  // Layout and visual parameters. No network libraries or build step are required.
  const WIDTH = 1000, HEIGHT = 440, TICKS = 300, PAGE_SIZE = 18;
  const COLORS = ['#087d93', '#7650bd', '#a75e08', '#07815f', '#ba416e', '#476cbd', '#8a702e'];
  const DARK_COLORS = ['#51c6dc', '#b49aef', '#edb45d', '#5acead', '#e78caa', '#91aaf0', '#d9c385'];
  // Concept colours stay fixed across scopes. Gold and pink are reserved for rarity and outliers.
  const WORD_COLORS = {
    recording: ['#285cae', '#88b7ff'], drugs: ['#a44429', '#ef9b7e'],
    neural: ['#087987', '#51cede'], behavior: ['#7050b0', '#ba9cef'],
    biology: ['#267248', '#75cea0'], physiology: ['#766126', '#cab888'],
    disease: ['#8c435b', '#d9a4b5'], statistics: ['#506781', '#a7bbd4'],
    computing: ['#586b28', '#b3c978'], other: ['#586271', '#aeb8c7'],
    rare: ['#936000', '#ffd166'], unusual: ['#b32178', '#ff86ce'],
  };
  const METHOD_GROUPS = { drugs: 'Drugs & compounds', interventions: 'Interventions',
    signals: 'Recorded signals', techniques_tools: 'Techniques & tools' };
  const STAGES = ['subjects', 'design', 'manipulation', 'measurement', 'analysis'];
  const KINDS = { concepts: 'Concepts', words: 'Words', goals: 'Goals', methods: 'Methodology' };
  const TYPES = { test: 'Test hypothesis', effect: 'Measure effect', mechanism: 'Mechanism', compare: 'Compare', develop: 'Develop method', review: 'Review', describe: 'Describe', unknown: 'No aim detected' };
  const GOAL_PAGE_SIZE = 12;
  const GOAL_COLORS = { test: 1, effect: 0, mechanism: 3, compare: 2, develop: 5, review: 4, describe: 6, unknown: 5 };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const clip = (v, n = 28) => String(v || '').length > n ? String(v).slice(0, n - 1) + '…' : String(v || '');
  const color = i => (document.documentElement.classList.contains('dark') ? DARK_COLORS : COLORS)[((Number(i) || 0) % COLORS.length + COLORS.length) % COLORS.length];
  const stageColor = s => color(STAGES.indexOf(s));
  const text = (x, y, value, size = 13, attrs = '') => `<text x="${x}" y="${y}" font-size="${size}" ${attrs}>${esc(value)}</text>`;
  const title = value => `<title>${esc(value)}</title>`;
  const paperCount = n => `${n} paper${n === 1 ? '' : 's'}`;
  const wordColour = word => (WORD_COLORS[word.unusual ? 'unusual' : word.rare ? 'rare' : word.concept_id] || WORD_COLORS.other)[document.documentElement.classList.contains('dark') ? 1 : 0];
  const graphHeight = count => count > 45 ? Math.max(650, count * 12) : HEIGHT;
  const csvCell = value => { const v = String(value ?? ''); return '"' + (typeof value === 'string' && /^[=+@\-\t\r]/.test(v) ? "'" : '') + v.replace(/"/g, '""') + '"'; };

  /** Measure the same font used in SVG, wrapping at word boundaries without discarding names. */
  function wrapText(value, width, size = 13, weight = 400) {
    const context = document.createElement('canvas').getContext('2d');
    context.font = `${weight} ${size}px Arial`;
    const lines = [];
    let line = '';
    for (const word of String(value || '').split(/\s+/)) {
      const candidate = line ? line + ' ' + word : word;
      if (line && context.measureText(candidate).width > width) { lines.push(line); line = ''; }
      if (context.measureText(word).width > width) {
        // Exceptionally long identifiers can break at a character boundary, ordinary words cannot.
        for (const letter of word) {
          if (context.measureText(line + letter).width > width) { lines.push(line); line = ''; }
          line += letter;
        }
      } else line = line ? line + ' ' + word : word;
    }
    if (line) lines.push(line);
    return lines;
  }
  function textLines(x, y, lines, size = 13, lineHeight = 17, attrs = '') {
    return lines.map((line, index) => text(x, y + index * lineHeight, line, size, attrs)).join('');
  }

  /** Deterministic velocity-Verlet integration with collision repulsion, springs and centering. */
  function forceLayout(raw, edges) {
    const height = graphHeight(raw.length);
    const nodes = raw.map((n, i) => {
      const angle = i * 2.39996323, radius = 35 * Math.sqrt(i + 1);
      return { ...n, x: WIDTH / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius,
        vx: 0, vy: 0, ax: 0, ay: 0, r: 7 + Math.sqrt(n.count || 1) * 3 };
    });
    const byId = new Map(nodes.map(n => [n.id, n]));
    const springs = edges.map(e => ({ ...e, a: byId.get(e.source), b: byId.get(e.target) })).filter(e => e.a && e.b);
    for (let tick = 0; tick < TICKS; tick++) {
      const cooling = 0.3 + 0.7 * (1 - tick / TICKS);
      for (const n of nodes) { n.x += n.vx + n.ax * .5; n.y += n.vy + n.ay * .5; n.vx += n.ax * .5; n.vy += n.ay * .5; n.ax = (WIDTH / 2 - n.x) * .0018; n.ay = (height / 2 - n.y) * .0018; }
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
        n.x = Math.max(75, Math.min(WIDTH - 75, n.x)); n.y = Math.max(40, Math.min(height - 45, n.y));
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
      for (const n of nodes) { n.x = Math.max(78, Math.min(WIDTH - 78, n.x)); n.y = Math.max(30, Math.min(height - 50, n.y)); }
    }
    if (nodes.length > 1) {
      const minX = Math.min(...nodes.map(n => n.x)), maxX = Math.max(...nodes.map(n => n.x));
      const minY = Math.min(...nodes.map(n => n.y)), maxY = Math.max(...nodes.map(n => n.y));
      const scale = Math.min(2.3, (WIDTH - 180) / Math.max(1, maxX - minX), (height - 110) / Math.max(1, maxY - minY));
      for (const n of nodes) { n.x = WIDTH / 2 + (n.x - (minX + maxX) / 2) * scale; n.y = height / 2 - 10 + (n.y - (minY + maxY) / 2) * scale; }
    }
    // Place measured labels independently of the node centres. Crowded maps gain small leader lines.
    const context = document.createElement('canvas').getContext('2d');
    const size = nodes.length > 45 ? 13 : 16;
    context.font = `${size}px Arial`;
    const placed = [];
    for (const node of nodes) {
      const label = clip(node.label, nodes.length > 45 ? 21 : 32);
      const width = context.measureText(label).width + 10;
      const candidates = [{ x: node.x, y: node.y + node.r + 20 }, { x: node.x, y: node.y - node.r - 12 }];
      for (let step = 0; step < 1200; step++) {
        const angle = step * 2.39996323, radius = 12 + Math.sqrt(step) * 7;
        candidates.push({ x: node.x + Math.cos(angle) * radius, y: node.y + Math.sin(angle) * radius });
      }
      for (let y = 22; y < height - 14; y += size + 7) {
        for (let x = width / 2 + 8; x < WIDTH - width / 2 - 8; x += 14) candidates.push({ x, y });
      }
      for (const candidate of candidates) {
        const box = { left: candidate.x - width / 2, right: candidate.x + width / 2, top: candidate.y - size, bottom: candidate.y + 5 };
        if (box.left < 8 || box.right > WIDTH - 8 || box.top < 8 || box.bottom > height - 8) continue;
        if (placed.some(other => box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top)) continue;
        if (nodes.some(other => box.left < other.x + other.r + 3 && box.right > other.x - other.r - 3 && box.top < other.y + other.r + 3 && box.bottom > other.y - other.r - 3)) continue;
        node.labelX = candidate.x; node.labelY = candidate.y;
        placed.push(box); break;
      }
      if (node.labelX === undefined) { node.labelX = node.x; node.labelY = node.y + node.r + 20; }
    }
    return nodes;
  }

  /** Collision-tested rectangular word cloud, using measured local fonts and a spiral. */
  function wordLayout(words, metric) {
    const ctx = document.createElement('canvas').getContext('2d');
    const score = w => metric === 'distinct' ? Math.max(0, w.distinct || 0) : w.count;
    const sorted = [...words].filter(w => score(w) > 0).sort((a, b) => score(b) - score(a) || a.word.localeCompare(b.word));
    const max = Math.max(1, ...sorted.map(score)), min = Math.min(max, ...sorted.map(score)), placed = [];
    for (const [i, w] of sorted.slice(0, 90).entries()) {
      // Stretch the observed range for a clear hierarchy; counts remain available on click.
      // Equal counts receive equal sizes, including the single-occurrence view.
      const size = max === min ? 30 : 16 + 60 * Math.pow((score(w) - min) / (max - min), .85);
      ctx.font = `600 ${size}px Georgia`;
      const width = ctx.measureText(w.word).width + 12, height = size * 1.3;
      if (width > WIDTH - 30) continue;
      for (let step = 0; step < 3200; step++) {
        const a = step * .24 + i * .17, r = step * .18;
        const x = WIDTH / 2 + Math.cos(a) * r * 1.35, y = HEIGHT / 2 + Math.sin(a) * r;
        const rect = { l: x - width / 2, r: x + width / 2, t: y - height / 2, b: y + height / 2 };
        if (rect.l < 15 || rect.r > WIDTH - 15 || rect.t < 15 || rect.b > HEIGHT - 35) continue;
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
        collectionIds: [], collectionDraft: [], collectionQuery: '', collectionPicker: false,
        goalView: 'atlas', goalKind: '', goalTheme: '', goalQuery: '', goalPage: 0,
        resultDirection: '', resultMode: 'all', conceptView: 'network', conceptCount: 24, conceptSingles: false, conceptBroad: false, conceptEdges: [],
        metric: 'count', terms: 'all', wordPool: 'frequent', wordConcept: '', view: 'cloud', methodView: 'pipeline', focus: '', cluster: null, selected: null,
        svg: '', height: HEIGHT, width: WIDTH, actions: [], nodes: [], page: 0, error: '', request: 0, zoom: 1 },
      insightKinds: Object.entries(KINDS).map(([id, label]) => ({ id, label })),
      insightTitle() { return KINDS[this.insight.kind]; },
      insightDescription() { return ({ concepts: 'Themes that connect your literature', words: 'The language of this body of work', goals: 'Research questions, shared themes and evidence across every paper', methods: 'How the science was done, with evidence for every step' })[this.insight.kind]; },
      insightWords() {
        const state = this.insight;
        const pool = state.wordPool === 'rare' ? state.data.rare_words : state.wordPool === 'unusual' ? state.data.outlier_words : state.data.words;
        return (pool || []).filter(word => (!state.wordConcept || word.concept_id === state.wordConcept) &&
          (state.terms === 'phrases' ? word.bigram : state.terms === 'words' ? !word.bigram : true));
      },
      wordColour,
      paperCount,
      /** Legend entries filter the current pool; rare and unusual colours override concept colour. */
      insightWordLegend() {
        const state = this.insight;
        const pool = state.wordPool === 'rare' ? state.data.rare_words : state.wordPool === 'unusual' ? state.data.outlier_words : state.data.words;
        const ids = new Set((pool || []).map(word => word.concept_id));
        return (state.data.concept_categories || []).filter(category => ids.has(category.id));
      },
      insightWordPoolLabel() { return ({ frequent: 'frequent terms', rare: 'single-occurrence terms', unusual: 'unusual terms' })[this.insight.wordPool] || 'terms'; },
      insightRareSuggestions() {
        const state = this.insight;
        return (state.data.rare_words || []).filter(word => (!state.wordConcept || word.concept_id === state.wordConcept) &&
          (state.terms === 'phrases' ? word.bigram : state.terms === 'words' ? !word.bigram : true)).slice(0, 8);
      },
      setInsightWordPool(pool) {
        this.insight.wordPool = pool; this.insight.wordConcept = '';
        if (pool === 'rare') this.insight.metric = 'count';
        this.renderInsight();
      },
      /** Counts and comparison status travel with the exact text-source evidence. */
      inspectWord(word) {
        const data = this.insight.data;
        this.insight.selected = { label: word.word, count: word.n_items,
          items: (word.items || []).map(id => data.items?.[id]).filter(Boolean),
          evidence: word.evidence || [], description: this.wordDescription(word) };
        this.clearItem();
      },
      wordDescription(word) {
        return `${word.count} occurrence${word.count === 1 ? '' : 's'} in ${paperCount(word.n_items)}. ${word.concept_label || 'Other terms'}.` +
          (word.rare ? ' Very rare: occurs exactly once in this scope and text source.' : '') +
          (word.unusual ? ' Unusual here: not found in the available comparison text elsewhere in your library.' : '');
      },
      /** Plain-language orientation without inventing research conclusions from co-occurrence. */
      insightSummary() {
        const data = this.insight.data, total = data.scope?.items || 0;
        if (this.insight.kind === 'concepts') {
          const leading = [...(data.nodes || [])].sort((a, b) => b.count - a.count)[0];
          return [{ value: data.nodes?.length || 0, label: 'concepts retained' }, { value: data.clusters?.length || 0, label: data.clusters?.length === 1 ? 'theme' : 'themes' },
            { value: leading ? `${leading.count}/${total}` : '0', label: leading ? `papers share “${leading.label}”` : 'papers linked' }];
        }
        if (this.insight.kind === 'words') {
          const terms = this.insightWords(), leading = terms[0];
          return [{ value: terms.length, label: this.insightWordPoolLabel() }, { value: this.insight.source === 'abstract' ? 'Abstracts' : 'Full text', label: 'text source' },
            { value: leading?.count || 0, label: leading ? `occurrence${leading.count === 1 ? '' : 's'} of “${leading.word}”` : 'occurrences' }];
        }
        if (this.insight.kind === 'goals') return [
          { value: (data.papers || []).length + '/' + total, label: 'papers represented' },
          { value: (data.papers || []).filter(paper => paper.focus_status === 'explicit_aim' || paper.aims?.length).length, label: 'with explicit aims' },
          { value: (data.papers || []).filter(paper => paper.focus_status === 'title_focus').length, label: 'with title-based focuses' }];
        return [{ value: (data.papers || []).filter(paper => paper.source === 'methods section').length + '/' + total, label: 'Methods sections available' },
          { value: data.matrix?.length || 0, label: 'distinct methods detected' }, { value: data.checks?.length || 0, label: 'reporting checks', action: true }];
      },
      scrollInsightReporting() { document.querySelector('.insight-diagnostics')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); },
      insightChartStyle() {
        const state = this.insight;
        if (state.kind === 'concepts') return `width:${state.zoom * 100}%;min-width:${state.width}px;height:auto;`;
        if (state.kind === 'goals' && state.goalView === 'atlas') return `width:100%;min-width:${state.selected ? 580 : 850}px;`;
        const fitted = state.kind === 'concepts' || state.kind === 'words' && state.view === 'cloud';
        if (fitted) {
          const zoom = state.kind === 'concepts' ? state.zoom : 1;
          if (state.nodes.length > 45) return `width:${zoom * 100}%;min-width:1000px;height:auto;`;
          return `width:${zoom * 100}%;min-width:850px;height:calc(clamp(320px, 100vh - 370px, 500px) * ${zoom});`;
        }
        return `width:100%;min-width:${state.kind === 'methods' ? state.width : 850}px;`;
      },
      insightScopeOptions() {
        const options = this.collections.map(c => ({ value: 'collection:' + c.id, label: 'Collection · ' + c.name }));
        const authors = new Set([this.selectedAuthor, ...(this.facets?.authors || []).map(a => a.name)].filter(Boolean));
        for (const a of authors) options.push({ value: 'author:' + a, label: 'Author · ' + a });
        for (const t of this.allTags || []) options.push({ value: 'tag:' + t.name, label: 'Tag · ' + t.name });
        if (!['all', 'collections'].includes(this.insight.scope) && !options.some(o => o.value === this.insight.scope)) options.push({ value: this.insight.scope, label: this.insight.scope.replace(':', ' · ') });
        return options;
      },
      /** A draft selection applies as one union request, never as repeated additive counts. */
      insightCollectionIds() {
        return this.insight.scope === 'collections' ? this.insight.collectionIds :
          this.insight.scope.startsWith('collection:') ? [Number(this.insight.scope.split(':')[1])] : [];
      },
      insightSelectedCollections() {
        const selected = new Set(this.insightCollectionIds());
        return this.collections.filter(collection => selected.has(collection.id));
      },
      insightCollectionChoices() {
        const query = this.insight.collectionQuery.trim().toLowerCase();
        return this.collections.filter(collection => !query || collection.name.toLowerCase().includes(query));
      },
      openInsightCollections() {
        this.insight.collectionDraft = [...this.insightCollectionIds()];
        this.insight.collectionQuery = ''; this.insight.collectionPicker = true;
      },
      toggleInsightCollection(id) {
        const draft = this.insight.collectionDraft;
        this.insight.collectionDraft = draft.includes(id) ? draft.filter(value => value !== id) : [...draft, id];
      },
      async applyInsightCollections() {
        const ids = [...new Set(this.insight.collectionDraft.map(Number))];
        if (!ids.length) return;
        this.insight.collectionIds = ids;
        this.insight.scope = ids.length === 1 ? 'collection:' + ids[0] : 'collections';
        this.insight.collectionPicker = false; this.insight.focus = '';
        return this.loadInsight();
      },
      async changeInsightScope() {
        this.insight.collectionIds = this.insightCollectionIds();
        this.insight.collectionPicker = false; this.insight.focus = '';
        return this.loadInsight();
      },
      /** Capture navigation context before entering the map; switching map kinds keeps that scope. */
      async openInsight(kind = 'concepts', explicit = null) {
        if (!KINDS[kind]) kind = 'concepts';
        if (explicit !== null) {
          this.insight.scope = explicit.collection_ids?.length ? 'collections' : explicit.collection_id ? 'collection:' + explicit.collection_id : explicit.author ? 'author:' + explicit.author : explicit.tag || explicit.subject ? 'tag:' + (explicit.tag || explicit.subject) : 'all';
          this.insight.collectionIds = explicit.collection_ids?.length ? [...explicit.collection_ids] : this.insightCollectionIds();
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
      async resetInsightScope() { this.insight.scope = 'all'; this.insight.collectionIds = []; this.insight.collectionPicker = false; this.insight.focus = ''; this.insight.cluster = null; return this.loadInsight(); },
      async loadInsight() {
        const s = this.insight, request = ++s.request, params = { include_sub: s.includeSub ? 'true' : 'false', source: s.source, limit: s.kind === 'concepts' ? 80 : 150, focus: s.focus };
        const colon = s.scope.indexOf(':');
        const type = colon >= 0 ? s.scope.slice(0, colon) : s.scope;
        const value = colon >= 0 ? s.scope.slice(colon + 1) : '';
        if (type === 'collection') params.collection_id = Number(value);
        if (s.scope === 'collections') params.collection_ids = s.collectionIds;
        if (type === 'author') params.author = value;
        if (type === 'tag') params.tag = value;
        s.loading = true; s.error = ''; s.selected = null; s.page = 0; s.goalPage = 0; s.cluster = null;
        try {
          const data = await api.get('/api/insight/' + s.kind, params);
          if (s.request !== request) return;
          s.data = data;
          if (s.kind === 'words' && !data.scope?.comparison_available) {
            s.metric = 'count';
            if (s.wordPool === 'unusual') s.wordPool = 'frequent';
          }
          s.nodes = data.nodes || [];
          this.renderInsight();
        } catch (e) { if (s.request === request) { s.error = e.message || 'Could not load this map'; s.data = {}; s.svg = ''; } }
        finally { if (s.request === request) s.loading = false; }
      },
      insightLimits() {
        const labels = { no_abstract: 'without an abstract', no_fulltext: 'without indexed full text', no_methods_section: 'without a detected Methods section', truncated: 'excluded by the scope limit' };
        const limits = this.insight.data.limits || {};
        const notes = Object.entries(limits).filter(([k, v]) => labels[k] && typeof v === 'number' && v > 0).map(([k, v]) => `${v} ${labels[k]}`);
        if (this.insight.kind === 'words') {
          const data = this.insight.data, pool = this.insight.wordPool;
          const available = data.candidate_counts?.[pool];
          if (available > limits.list_limit) notes.push(`This ${this.insightWordPoolLabel()} list contains ${limits.list_limit} of ${available} candidates; concept and phrase filters apply to that list.`);
          if (data.scope?.comparison_available) notes.push(`Comparison text available for ${data.scope.comparison_items_with_text ?? 0}/${data.scope.comparison_items ?? 0} papers outside this scope.`);
          if (limits.comparison_capped) notes.push('Comparison reached the library analysis limit.');
        }
        if (limits.scope_capped) notes.push(`Scope reached the ${limits.max_items || 2000}-paper analysis limit; narrow the scope for complete coverage.`);
        if (limits.no_text) notes.push(`${limits.no_text} without assessable text`);
        if (limits.fulltext_scan_chars) notes.push(`Aim and result search reads up to ${Number(limits.fulltext_scan_chars).toLocaleString()} indexed characters per paper.`);
        if (limits.findings_capped) notes.push(`${limits.findings_capped} papers reached the ${limits.findings_per_paper}-finding display limit; capped papers are excluded from compatible comparison counts.`);
        return notes.join(' · ');
      },
      insightHasData() { const d = this.insight.data; return !!(d.nodes?.length || d.words?.length || d.rare_words?.length || d.papers?.length); },
      insightPageCount() { return Math.max(1, Math.ceil((this.insight.data.papers || []).length / PAGE_SIZE)); },
      insightPage(delta) { this.insight.page = Math.max(0, Math.min(this.insightPageCount() - 1, this.insight.page + delta)); this.renderInsight(); },
      insightAction(detail) { const index = this.insight.actions.length; this.insight.actions.push(detail); return `data-index="${index}" tabindex="0" role="button"`; },
      insightActivate(event) {
        const element = event.target.closest('[data-index]');
        if (!element) return;
        const detail = this.insight.actions[Number(element.getAttribute('data-index'))];
        if (detail) {
          this.insight.selected = detail; this.clearItem();
          if (this.insight.kind === 'goals') this.$nextTick(() => this.renderInsight());
        }
      },
      insightHover(event, clear = false) {
        if (this.insight.kind !== 'concepts') return;
        const id = clear ? null : event.target.closest('[data-node]')?.getAttribute('data-node');
        const svg = document.getElementById('insight-svg'); if (!svg) return;
        const neighbours = new Set([id]);
        for (const e of this.insight.conceptEdges || []) { if (e.source === id) neighbours.add(e.target); if (e.target === id) neighbours.add(e.source); }
        for (const n of svg.querySelectorAll('[data-node]')) n.style.opacity = !id || neighbours.has(n.getAttribute('data-node')) ? 1 : .2;
        for (const e of svg.querySelectorAll('[data-edge]')) e.style.opacity = !id || e.getAttribute('data-a') === id || e.getAttribute('data-b') === id ? 1 : .1;
      },
      async focusInsight() { if (!this.insight.selected) return; this.insight.focus = this.insight.selected.label; return this.loadInsight(); },
      async openInsightPaper(paper) { this.insight.selected = null; return this.openItem(paper.id); },
      insightEvidence(detail) { return (detail?.evidence || []).map(e => typeof e === 'string' ? { sentence: e } : e); },
      methodConfidence(method) { return method.confidence === 'low' ? 'Author-stated' : method.confidence === 'high' ? 'High · Methods section' : 'Medium · repeated body mentions'; },
      goalType(kind) { return TYPES[kind] || kind; },
      goalColour(kind) { return color(GOAL_COLORS[kind] ?? 5); },
      /** Keep every scoped paper eligible, including clearly labelled title-only focuses. */
      goalFilteredPapers() {
        const state = this.insight, query = state.goalQuery.trim().toLowerCase();
        return (state.data.papers || []).filter(paper => (!state.goalKind || paper.goal_type === state.goalKind) &&
          (!state.goalTheme || (paper.themes || []).some(theme => theme.id === state.goalTheme)) &&
          (!query || [paper.title, paper.focus, ...(paper.aims || []).map(aim => aim.sentence), ...(paper.findings || []).map(finding => finding.evidence)].join(' ').toLowerCase().includes(query)) &&
          (state.goalView !== 'outcomes' || !state.resultDirection || (paper.findings || []).some(finding => finding.direction === state.resultDirection)));
      },
      goalThemes() {
        const themes = new Map();
        for (const paper of this.insight.data.papers || []) for (const theme of paper.themes || []) {
          if (!themes.has(theme.id)) themes.set(theme.id, { ...theme, count: 0 });
          themes.get(theme.id).count++;
        }
        return [...themes.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
      },
      goalVisiblePapers() { return this.goalFilteredPapers().slice(this.insight.goalPage * GOAL_PAGE_SIZE, (this.insight.goalPage + 1) * GOAL_PAGE_SIZE); },
      goalPageCount() { return Math.max(1, Math.ceil(this.goalFilteredPapers().length / GOAL_PAGE_SIZE)); },
      filterGoals() { this.insight.goalPage = 0; this.renderInsight(); },
      goalPage(delta) { this.insight.goalPage = Math.max(0, Math.min(this.goalPageCount() - 1, this.insight.goalPage + delta)); this.renderInsight(); },
      clearGoalFilters() { this.insight.goalKind = ''; this.insight.goalTheme = ''; this.insight.goalQuery = ''; this.insight.resultDirection = ''; this.filterGoals(); },
      directionLabel(direction) { const [symbol, label] = window.InsightAnalysisViews.direction(direction); return symbol + ' ' + label; },
      directionColour(direction) { return color(window.InsightAnalysisViews.direction(direction)[2]); },
      findingContext(context) {
        return Object.entries(context || {}).filter(([key, value]) => Array.isArray(value) && !key.endsWith('_source'))
          .flatMap(([, value]) => value).filter(Boolean).join(' · ');
      },
      findingDetail(finding, paper) {
        return { label: this.directionLabel(finding.direction) + ': ' + finding.outcome, items: [paper],
          description: finding.factor + '. Context: ' + (this.findingContext(finding.context) || 'not resolved') + '. Direction describes this source statement, not a pooled effect.',
          evidence: [{ id: paper.id, sentence: finding.evidence, source: finding.source }] };
      },
      /** Compare only complete backend groups; a UI filter must not change who is in the minority. */
      resultComparisons() {
        const visible = new Set(this.goalFilteredPapers().map(paper => paper.id));
        return (this.insight.data.result_comparisons || []).filter(group =>
          (group.findings || []).some(finding => visible.has(finding.paper_id)) &&
          (this.insight.resultMode !== 'different' || Object.keys(group.direction_counts || {}).length > 1) &&
          (this.insight.resultMode !== 'similar' || Object.keys(group.direction_counts || {}).length === 1));
      },
      relatedOutcomes() {
        const visible = new Set(this.goalFilteredPapers().map(paper => paper.id));
        return (this.insight.data.related_outcomes || []).filter(group =>
          (group.findings || []).some(finding => visible.has(finding.paper_id)));
      },
      inspectResultComparison(group) {
        const ids = new Set((group.findings || []).map(finding => finding.paper_id));
        this.insight.selected = { label: group.factor ? group.factor + ' → ' + group.outcome : group.outcome,
          description: group.interpretation + (group.context ? ' Context: ' + this.findingContext(group.context) : ''),
          items: (this.insight.data.papers || []).filter(paper => ids.has(paper.id)),
          evidence: (group.findings || []).map(finding => ({ id: finding.paper_id, sentence: finding.evidence, source: this.directionLabel(finding.direction) + ' · ' + finding.source + ' · ' + this.findingContext(finding.context) })) };
        this.clearItem();
      },
      resultCoverage() {
        const papers = this.goalFilteredPapers();
        return `${papers.filter(paper => paper.findings?.length).length} of ${papers.length} papers have extracted findings. Missing direction is kept explicit.`;
      },
      goalPaperDetail(paper) {
        const evidence = (paper.aims || []).map(aim => ({ id: paper.id, sentence: aim.sentence, source: aim.source || paper.focus_source || 'Available text' }));
        if (!evidence.length && paper.focus) evidence.push({ id: paper.id, sentence: paper.focus, source: paper.focus_source === 'title' ? 'Title-derived focus, not an explicit aim' : paper.focus_source });
        for (const pair of paper.pairs || []) if (!evidence.some(entry => entry.sentence === pair.evidence)) evidence.push({ id: paper.id, sentence: pair.evidence, source: pair.source || 'Relation evidence' });
        for (const finding of paper.findings || []) evidence.push({ id: paper.id, sentence: finding.evidence, source: this.directionLabel(finding.direction) + ' · ' + finding.source });
        return { label: paper.title, items: [paper], evidence, goalPairs: paper.pairs || [],
          description: `${paper.focus_label || 'Research focus'} · ${this.goalType(paper.goal_type)}. ${(paper.pairs || []).length} supported factor–outcome ${(paper.pairs || []).length === 1 ? 'relation' : 'relations'}.` };
      },
      closeInsightEvidence() { this.insight.selected = null; if (this.insight.kind === 'goals') this.$nextTick(() => this.renderInsight()); },
      /** Construct the active map once; Alpine does not rerun expensive layouts on hover. */
      renderInsight() {
        const s = this.insight; s.actions = []; s.height = HEIGHT; s.width = WIDTH;
        if (s.kind === 'words' && s.wordPool === 'rare') s.metric = 'count';
        if (s.kind === 'concepts') s.svg = this.conceptSVG();
        if (s.kind === 'words') s.svg = this.wordSVG();
        if (s.kind === 'goals') s.svg = this.goalSVG();
        if (s.kind === 'methods') s.svg = s.methodView === 'matrix' ? this.matrixSVG() : this.pipelineSVG();
      },
      conceptSVG() {
        const state = this.insight, nodes = this.visibleConcepts();
        // Memberships are complete even when the API caps its visual edge list.
        const memberships = new Map(nodes.map(node => [node.id, new Set((node.items || []).map(paper => paper.id))]));
        const edges = [];
        nodes.forEach((a, i) => nodes.slice(i + 1).forEach(b => {
          const left = memberships.get(a.id), right = memberships.get(b.id);
          const shared = [...left].filter(id => right.has(id));
          if (shared.length) edges.push({ source: a.id, target: b.id, weight: shared.length, shared_count: shared.length,
            item_ids: shared, jaccard: shared.length / (left.size + right.size - shared.length) });
        }));
        state.conceptEdges = edges;
        // A sparse network keeps each node's two strongest overlaps; the matrix retains every edge.
        const backbone = new Set();
        for (const node of nodes) edges.filter(edge => edge.source === node.id || edge.target === node.id)
          .sort((a, b) => b.weight - a.weight || b.jaccard - a.jaccard).slice(0, 2).forEach(edge => backbone.add(edge));
        const draw = window.InsightAnalysisViews[state.conceptView === 'matrix' ? 'matrix' : 'network'];
        const rendered = draw(nodes, state.conceptView === 'matrix' ? edges : [...backbone], {
          text, textLines, wrapText, esc, color, action: detail => this.insightAction(detail),
          nodeDetail: node => this.conceptDetail(node), edgeDetail: edge => this.conceptEdgeDetail(edge) });
        state.height = rendered.height; state.width = rendered.width;
        return rendered.svg;
      },
      visibleConcepts() {
        const state = this.insight;
        return (state.data.nodes || []).filter(node => (state.cluster === null || node.cluster === state.cluster) &&
          (state.conceptBroad || !node.broad_metadata) && (state.conceptSingles || node.count > 1 || state.data.scope?.items === 1))
          .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)).slice(0, Number(state.conceptCount));
      },
      conceptDetail(node) { return { label: node.label, items: node.items || [], evidence: node.evidence || [], related: node.related || [], count: node.count,
        description: `${Math.round((node.prevalence || 0) * 100)}% of scoped papers contain this concept. ${node.broad_metadata ? 'Broad indexing metadata.' : 'Presence in title, abstract or curated metadata.'}` }; },
      conceptEdgeDetail(edge) {
        const nodes = this.insight.data.nodes || [], a = nodes.find(node => node.id === edge.source), b = nodes.find(node => node.id === edge.target);
        const shared = new Set(edge.item_ids || (a?.items || []).filter(item => (b?.items || []).some(other => item.id === other.id)).map(item => item.id));
        return { label: `${a?.label || edge.source} + ${b?.label || edge.target}`,
          items: (a?.items || []).filter(item => shared.has(item.id)),
          evidence: [...(a?.evidence || []), ...(b?.evidence || [])].filter(entry => shared.has(entry.id)),
          description: `${paperCount(edge.shared_count ?? edge.weight)} contain both concepts. ${Math.round((edge.jaccard || 0) * 100)}% Jaccard overlap: shared papers divided by papers containing either concept. Co-occurrence does not imply a causal relationship.` };
      },
      inspectConcept(node) { this.insight.selected = this.conceptDetail(node); this.clearItem(); },
      inspectConceptLink(edge) { this.insight.selected = this.conceptEdgeDetail(edge); this.clearItem(); },
      inspectConceptCombination(entry) {
        this.insight.selected = { label: entry.title || entry.paper?.title || 'Unusual concept combination', items: entry.items || (entry.paper ? [entry.paper] : []),
          evidence: entry.evidence || [], description: entry.interpretation || 'These concept pairs occur in only this paper in the selected scope. This is descriptive rarity, not evidence of scientific novelty.' };
        this.clearItem();
      },
      /** Signed values use a shared zero axis; frequency and distinctiveness never share an encoding. */
      wordSVG() {
        const state = this.insight;
        const words = this.insightWords();
        const detail = word => ({ label: word.word, count: word.n_items, description: this.wordDescription(word),
          items: (word.items || []).map(id => state.data.items?.[id]).filter(Boolean), evidence: word.evidence || [] });
        if (!words.length) return text(35, 100, state.wordPool === 'unusual'
          ? 'No terms meet the comparison criteria in this view.' : 'No matching terms. Try another concept, source or term group.', 16);
        if (state.view === 'bars') {
          const distinct = state.metric === 'distinct';
          const sorted = [...words].sort((a, b) => distinct ? b.distinct - a.distinct : b.count - a.count).slice(0, 50);
          const max = Math.max(1, ...sorted.map(word => distinct ? Math.abs(word.distinct) : word.count));
          const zero = distinct ? 615 : 320;
          const range = distinct ? 255 : 560;
          const rows = sorted.map(word => ({ word, lines: wrapText(word.word, 280, 14) }));
          state.height = Math.max(220, rows.reduce((height, row) => height + Math.max(36, row.lines.length * 17 + 12), 90));
          let svg = text(20, 27, 'TERM', 12, 'font-weight="600"') +
            text(935, 27, distinct ? 'LOG-ODDS' : 'COUNT', 12, 'text-anchor="end" font-weight="600"');
          if (distinct) svg += text(360, 48, 'Less distinctive', 12) + text(725, 48, 'More distinctive', 12);
          svg += `<line x1="${zero}" y1="55" x2="${zero}" y2="${state.height - 20}" stroke="currentColor" opacity=".3"/>`;
          let y = 68;
          rows.forEach(({ word, lines }) => {
            const value = distinct ? word.distinct : word.count;
            const length = Math.abs(value) / max * range;
            const x = value < 0 ? zero - length : zero;
            svg += `<g class="insight-node" data-score="${value}" data-concept="${esc(word.concept_id || 'other')}" data-rare="${!!word.rare}" data-unusual="${!!word.unusual}" ${this.insightAction(detail(word))}>` +
              title(this.wordDescription(word) + ` Log-odds ${word.distinct}.`) +
              textLines(20, y + 18, lines, 14, 17, word.rare || word.unusual ? `fill="${wordColour(word)}" font-weight="600"` : '') +
              `<rect class="insight-word-bar" x="${x}" y="${y}" width="${length}" height="24" rx="4" fill="${wordColour(word)}" opacity=".78"/>` +
              text(935, y + 18, distinct ? (value > 0 ? '+' : '') + value.toFixed(2) : value, 13, 'text-anchor="end"') + '</g>';
            y += Math.max(36, lines.length * 17 + 12);
          });
          return svg;
        }
        const placed = wordLayout(words, state.metric);
        if (!placed.length) return text(50, 120, 'No positive distinctive terms in this view. Try ranked bars or another scope.', 16);
        return placed.map(word => `<g class="insight-node" data-word="${esc(word.word)}" data-concept="${esc(word.concept_id || 'other')}" data-rare="${!!word.rare}" data-unusual="${!!word.unusual}" ${this.insightAction(detail(word))}>` +
          title(this.wordDescription(word) + ` Log-odds ${word.distinct}.`) +
          text(word.x, word.y, word.word, word.size, `text-anchor="middle" dominant-baseline="central" font-family="Georgia,serif" font-weight="600" fill="${wordColour(word)}"`) +
          (word.rare ? `<line x1="${word.rect.l + 6}" x2="${word.rect.r - 6}" y1="${word.y + word.size * .5 + 1}" y2="${word.y + word.size * .5 + 1}" stroke="${wordColour(word)}" stroke-width="1.5"/>` : '') + '</g>').join('') +
          text(20, HEIGHT - 8, `${placed.length} of ${words.length} ${this.insightWordPoolLabel()} shown. Size: ${state.metric === 'distinct' ? 'positive distinctiveness' : 'frequency'} (expanded scale).`, 12, 'opacity=".8"');
      },

      goalSVG() {
        if (this.insight.goalView === 'outcomes') {
          const papers = this.goalVisiblePapers().map(paper => ({ ...paper, findings: (paper.findings || []).filter(finding => !this.insight.resultDirection || finding.direction === this.insight.resultDirection) }));
          const rendered = window.InsightAnalysisViews.outcomes(papers, { text, textLines, wrapText, color, contextLabel: context => this.findingContext(context),
            action: detail => this.insightAction(detail), findingDetail: (finding, paper) => this.findingDetail(finding, paper) });
          this.insight.height = rendered.height; this.insight.width = rendered.width;
          return rendered.svg;
        }
        if (this.insight.goalView === 'connections') return this.goalRelationsSVG();
        const width = this.insight.selected ? 600 : 1000;
        const visible = this.goalVisiblePapers();
        const rendered = window.InsightGoalViews.atlas(visible, {
          width, columns: this.insight.selected || visible.length === 1 ? 1 : 2, text, textLines, wrapText, esc,
          color: kind => this.goalColour(kind), goalType: kind => this.goalType(kind),
          detail: paper => this.goalPaperDetail(paper), action: value => this.insightAction(value),
          direction: value => this.directionLabel(value), directionColor: value => this.directionColour(value),
          findingDetail: (finding, paper) => this.findingDetail(finding, paper),
        });
        this.insight.width = rendered.width; this.insight.height = rendered.height;
        return rendered.svg;
      },
      /** The connection view uses the same page and filters as the paper atlas. */
      goalRelationsSVG() {
        const state = this.insight;
        state.width = 1000;
        const visible = new Set(this.goalVisiblePapers().map(paper => paper.id));
        const links = (state.data.links || []).map(link => ({ ...link, items: link.items.filter(paper => visible.has(paper.id)) })).filter(link => link.items.length);
        const connected = (nodes, field) => (nodes || []).filter(node => links.some(link => link[field] === node.id))
          .map(node => ({ ...node, items: node.items.filter(id => visible.has(id)), count: node.items.filter(id => visible.has(id)).length }));
        const data = { ...state.data, links, factors: connected(state.data.factors, 'factor'), outcomes: connected(state.data.outcomes, 'outcome') };
        const factors = data.factors || [], outcomes = data.outcomes || [];
        const rows = Math.max(factors.length, outcomes.length);
        const stride = Math.max(86, ...[...factors, ...outcomes].map(node => wrapText(node.label, 242, 14, 500).length * 17 + 50));
        state.height = Math.max(225, rows * stride + 85);
        const positions = nodes => new Map(nodes.map((node, index) => [node.id, 83 + index * stride + Math.max(0, rows - nodes.length) * stride / 2]));
        const factorY = positions(factors), outcomeY = positions(outcomes);
        let svg = text(25, 28, 'FACTORS', 13, 'letter-spacing="1.5" font-weight="600"') +
          text(730, 28, 'OUTCOMES', 13, 'letter-spacing="1.5" font-weight="600"');
        if (links.some(link => link.items.every(paper => paper.negated))) {
          svg += '<path d="M 355 25 h 30" stroke="currentColor" stroke-width="2" stroke-dasharray="7 5" opacity=".65"/>' +
            text(395, 29, 'No effect reported', 12, 'opacity=".8"');
        }
        for (const link of data.links || []) {
          const y1 = factorY.get(link.factor), y2 = outcomeY.get(link.outcome);
          if (y1 === undefined || y2 === undefined) continue;
          const action = this.insightAction({ label: link.factor + ' → ' + link.outcome,
            items: link.items, description: link.relation_label || 'Source-stated relationship; not proof of a causal effect.',
            evidence: link.items.map(paper => ({ id: paper.id, sentence: paper.evidence, source: paper.source || 'Stated research question' })) });
          const path = `M 292 ${y1} C 435 ${y1},565 ${y2},710 ${y2}`;
          svg += `<g class="insight-goal-link" ${action}>${title(`${link.factor} → ${link.outcome}: ${paperCount(link.items.length)}`)}` +
            `<path d="${path}" fill="none" stroke="transparent" stroke-width="20"/>` +
            `<path d="${path}" fill="none" stroke="${this.goalColour(link.kind)}" opacity=".68" stroke-width="${2 + Math.sqrt(link.items.length) * 2}" ${link.items.every(paper => paper.negated) ? 'stroke-dasharray="7 5"' : ''}/></g>`;
        }
        for (const [nodes, yPositions, x, side] of [[factors, factorY, 20, 'factor'], [outcomes, outcomeY, 725, 'outcome']]) {
          for (const node of nodes) {
            const papers = (data.papers || []).filter(paper => node.items.includes(paper.id));
            const evidence = (data.links || []).filter(link => link[side] === node.id)
              .flatMap(link => link.items.map(paper => ({ id: paper.id, sentence: paper.evidence, source: paper.source || 'Available text' })));
            const unique = [...new Map(evidence.map(entry => [entry.id + ':' + entry.sentence, entry])).values()];
            const lines = wrapText(node.label, 242, 14, 500);
            const y = yPositions.get(node.id), boxHeight = Math.max(65, lines.length * 17 + 32);
            svg += `<g class="insight-node" ${this.insightAction({ label: node.label, items: papers, evidence: unique })}>${title(node.label)}` +
              `<rect x="${x - 5}" y="${y - boxHeight / 2}" width="265" height="${boxHeight}" rx="9" class="insight-svg-card"/>` +
              textLines(x + 9, y - boxHeight / 2 + 23, lines, 14, 17, 'font-weight="500"') +
              text(x + 9, y + boxHeight / 2 - 10, paperCount(node.count), 12, 'opacity=".8"') + '</g>';
          }
        }
        if (!rows) svg += text(35, 115, 'No reliable pairs on this page. The question atlas still includes these papers.', 15);
        return svg;
      },

      /** Keep scientific method names intact; wrap labels and size each paper row to its content. */
      pipelineSVG() {
        const state = this.insight;
        const papers = (state.data.papers || []).slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
        const left = 235, column = 157, chipWidth = 147;
        state.width = 1035;
        let y = 65;
        let svg = text(18, 29, 'PAPER', 12, 'letter-spacing="1" font-weight="600"');
        for (const [index, stage] of (state.data.stages || []).entries()) {
          const x = left + index * column;
          svg += `<rect x="${x}" y="12" width="3" height="30" rx="1" fill="${stageColor(stage.id)}"/>` +
            textLines(x + 9, 24, wrapText(stage.label, 135, 12.5, 600), 12.5, 16, 'font-weight="600"');
        }
        for (const paper of papers) {
          const titleLines = wrapText(paper.title, left - 40, 13, 600);
          const columns = (paper.pipeline || []).map(stage => {
            const groups = new Map();
            for (const method of stage.methods) {
              const category = method.category || '';
              if (!groups.has(category)) groups.set(category, []);
              groups.get(category).push(method);
            }
            const order = { drugs: 0, signals: 0, interventions: 1, techniques_tools: 1 };
            const showGroups = [...groups.keys()].some(category => METHOD_GROUPS[category]);
            return [...groups].sort((a, b) => (order[a[0]] ?? 2) - (order[b[0]] ?? 2)).flatMap(([category, methods]) =>
              methods.map((method, index) => {
                const lines = wrapText(method.label, chipWidth - 22, 12);
                const group = showGroups && index === 0 ? METHOD_GROUPS[category] || (stage.stage === 'measurement' ? 'Other measurements' : 'Other methods') : '';
                return { method, lines, group, height: lines.length * 16 + 14 };
              }));
          });
          const height = Math.max(titleLines.length * 17 + 53, 60, ...columns.map(chips => chips.reduce((total, chip) => total + chip.height + 6 + (chip.group ? 23 : 0), 8))) + 12;
          svg += `<rect x="6" y="${y}" width="1023" height="${height}" rx="9" class="insight-svg-card"/>`;
          const detail = { label: paper.title, items: [paper], evidence: paper.detected.map(method => ({ id: paper.id, sentence: method.evidence, source: this.methodConfidence(method) })) };
          svg += `<g class="insight-node insight-paper-title" ${this.insightAction(detail)}>${title(paper.title)}` +
            textLines(18, y + 24, titleLines, 13, 17, 'font-weight="600"') +
            text(18, y + titleLines.length * 17 + 29, `${paper.year || 'Year unknown'} · ${paper.source}`, 11.5, 'opacity=".8"') + '</g>';
          for (const [index, stage] of (paper.pipeline || []).entries()) {
            let chipY = y + 10;
            const x = left + index * column;
            if (!stage.methods.length) svg += text(x + 3, chipY + 20, 'Not detected', 12, 'opacity=".75"');
            for (const { method, lines, group, height: chipHeight } of columns[index]) {
              if (group) {
                svg += text(x + 2, chipY + 12, group, 11, 'opacity=".8" font-weight="600"');
                chipY += 23;
              }
              const confidence = this.methodConfidence(method);
              const action = this.insightAction({ label: method.label, items: [paper],
                evidence: [{ id: paper.id, sentence: method.evidence, source: confidence }], confidence });
              svg += `<g class="insight-method-chip" data-method="${esc(method.id)}" data-category="${esc(method.category || '')}" ${action}>${title(method.label + '\n' + confidence + '\n' + method.evidence)}` +
                `<rect x="${x}" y="${chipY}" width="${chipWidth}" height="${chipHeight}" rx="6" fill="${stageColor(stage.stage)}" opacity=".13"/>` +
                `<circle cx="${x + 8}" cy="${chipY + 15}" r="2.7" fill="${stageColor(stage.stage)}"/>` +
                textLines(x + 17, chipY + 20, lines, 12, 16) + '</g>';
              chipY += chipHeight + 6;
            }
          }
          y += height + 8;
        }
        state.height = Math.max(180, y + 8);
        return svg;
      },

      /** Matrix headers retain their complete method name; source confidence uses explicit symbols. */
      matrixSVG() {
        const state = this.insight, methods = state.data.matrix || [];
        const papers = (state.data.papers || []).slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
        const cell = 37, left = 265;
        const context = document.createElement('canvas').getContext('2d');
        context.font = '12.5px Arial';
        const lengths = methods.map(method => context.measureText(method.label).width);
        const angle = 55 * Math.PI / 180;
        // Reserve the rotated headers' actual extent, including the last column's overhang.
        const top = Math.max(235, 95 + Math.max(0, ...lengths) * Math.sin(angle));
        state.width = Math.max(1035, left + methods.length * cell + 35,
          ...lengths.map((length, index) => left + index * cell + 38 + length * Math.cos(angle)));
        let svg = text(18, 24, 'METHOD PREVALENCE', 12, 'font-weight="600" letter-spacing="1"');
        svg += text(18, 48, '● Methods section    ◐ Repeated body text    ○ Author-stated    · Not detected', 12);
        methods.forEach((method, index) => {
          const x = left + index * cell + 18;
          svg += `<g transform="translate(${x},${top - 23}) rotate(-55)">${title(method.label)}` +
            text(0, 0, method.label, 12.5, `fill="${stageColor(method.stage)}"`) + '</g>' +
            text(x, top - 7, method.items.length, 11, 'text-anchor="middle" opacity=".8"');
        });
        let y = top;
        papers.forEach(paper => {
          const lines = wrapText(paper.title, left - 34, 12.5, 500);
          const rowHeight = Math.max(45, lines.length * 16 + 14);
          svg += `<g class="insight-node insight-paper-title" ${this.insightAction({ label: paper.title, items: [paper], evidence: [] })}>${title(paper.title)}` +
            textLines(16, y + 18, lines, 12.5, 16) + '</g>';
          methods.forEach((method, index) => {
            const found = paper.detected.find(detection => detection.id === method.id), x = left + index * cell;
            const detail = { label: method.label, items: [paper], evidence: found
              ? [{ id: paper.id, sentence: found.evidence, source: this.methodConfidence(found) }]
              : [{ sentence: 'Not detected in the available text. This does not establish that the method was not used.' }] };
            const symbol = !found ? '·' : found.confidence === 'high' ? '●' : found.confidence === 'medium' ? '◐' : '○';
            svg += `<g class="insight-matrix-cell" ${this.insightAction(detail)}>${title(found ? found.label + ': ' + found.evidence : method.label + ': not detected')}` +
              `<rect x="${x}" y="${y + 2}" width="31" height="31" rx="5" fill="${stageColor(method.stage)}" opacity="${found ? .16 : .035}"/>` +
              text(x + 15.5, y + 23, symbol, 17, `text-anchor="middle" ${found ? 'fill="' + stageColor(method.stage) + '"' : 'opacity=".45"'}`) + '</g>';
          });
          y += rowHeight;
        });
        state.height = Math.max(300, y + 20);
        return svg;
      },
      /** Reporting drill-down keeps affirmative and absent statements inspectable per paper. */
      inspectReporting(check) {
        const papers = (this.insight.data.papers || []).filter(p => p.checks.some(c => c.id === check.id && c.evidence));
        this.insight.selected = { label: check.label, items: papers, evidence: papers.map(p => { const c = p.checks.find(c => c.id === check.id); return { id: p.id, sentence: c.evidence, source: c.status || (c.found ? 'detected' : 'not detected') }; }) }; this.clearItem();
      },
      inspectGoalPaper(paper) { this.insight.selected = this.goalPaperDetail(paper); this.clearItem(); this.$nextTick(() => this.renderInsight()); },
      insightCSV() {
        const d = this.insight.data, kind = this.insight.kind;
        if (kind === 'goals' && this.insight.goalView === 'outcomes') return [
          ['paper_id', 'paper_title', 'factor', 'outcome', 'direction', 'source', 'evidence', 'drugs', 'species', 'conditions'],
          ...(d.papers || []).flatMap(paper => (paper.findings?.length ? paper.findings : [null]).map(finding =>
            [paper.id, paper.title, finding?.factor || '', finding?.outcome || '', finding?.direction || 'not_detected', finding?.source || '', finding?.evidence || '',
              (finding?.context?.drugs || []).join(';'), (finding?.context?.species || []).join(';'), (finding?.context?.conditions || []).join(';')]))];
        if (kind === 'methods') return [['paper_id', 'title', 'year', 'text_source', ...(d.matrix || []).map(m => m.label)], ...(d.papers || []).map(p => [p.id, p.title, p.year, p.source, ...(d.matrix || []).map(m => p.detected.some(x => x.id === m.id) ? 1 : 0)])];
        if (kind === 'words') {
          const terms = new Map();
          for (const word of [...(d.words || []), ...(d.rare_words || []), ...(d.outlier_words || [])]) terms.set(word.word, word);
          return [['word', 'occurrences', 'paper_count', 'log_odds', 'paper_ids', 'concept', 'rarity', 'unusual_in_scope', 'comparison_occurrences'],
            ...[...terms.values()].map(word => [word.word, word.count, word.n_items, word.distinct, word.items.join(';'),
              word.concept_label || '', word.rarity || '', !!word.unusual, word.rest_count ?? ''])];
        }
        if (kind === 'goals') return [['factor', 'outcome', 'goal_type', 'paper_id', 'paper_title', 'evidence', 'focus', 'focus_status', 'source', 'relationship_type', 'negated'],
          ...(d.papers || []).flatMap(paper => (paper.pairs?.length ? paper.pairs : [null]).map(pair =>
            [pair?.factor || '', pair?.outcome || '', pair?.kind || paper.goal_type, paper.id, paper.title,
              pair?.evidence || paper.focus || '', paper.focus || '', paper.focus_status || '', pair?.source || paper.focus_source || '', pair?.relation_label || '', pair?.negated ?? '']))];
        return [['record_type', 'id_or_source', 'label_or_target', 'paper_count_or_weight', 'cluster', 'paper_ids', 'prevalence', 'jaccard_overlap', 'broad_indexing_metadata'],
          ...(d.nodes || []).map(node => ['node', node.id, node.label, node.count, node.cluster, node.items.map(paper => paper.id).join(';'), node.prevalence, '', !!node.broad_metadata]),
          ...(d.edges || []).map(edge => ['edge', edge.source, edge.target, edge.weight, '', (edge.item_ids || []).join(';'), '', edge.jaccard, !!edge.broad_metadata])];
      },
      async exportInsight(format) {
        const s = this.insight, name = `ReviewHunter-${s.kind}-${(s.data.scope?.label || 'library').replace(/[^a-z0-9-]/gi, '_')}`;
        try {
          if (format === 'json') return download(new Blob([JSON.stringify(s.data, null, 2)], { type: 'application/json' }), name + '.json');
          if (format === 'csv') return download(new Blob(['\ufeff' + this.insightCSV().map(row => row.map(csvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }), name + '.csv');
          const background = this.dark ? '#0f111c' : '#f8f9fb', foreground = this.dark ? '#eef0f4' : '#272b3c';
          const notes = s.kind === 'methods' ? [this.insightLimits(), 'Reporting checks describe available text, not study quality.',
            ...(s.data.checks || []).map(c => `${c.label}: ${c.count}/${c.assessed ?? s.data.scope?.items ?? 0} assessed papers${c.unavailable ? `; ${c.unavailable} unavailable` : ''}`),
            ...(s.data.insights?.gaps || [])] : s.kind === 'words'
              ? [this.insightLimits(), 'Colours: lexical concept groups; gold and underlining = exactly one occurrence; pink = unusual in this scope.',
                'Unusual: at least two occurrences, absent from available comparison text, and log-odds >= 1. Not a novelty claim.']
              : s.kind === 'goals' ? [this.insightLimits(),
                `${s.goalView === 'outcomes' ? 'Reported outcomes' : 'Question atlas / connections'}: page ${s.goalPage + 1} of ${this.goalPageCount()}; ${paperCount(this.goalFilteredPapers().length)} matching. CSV/JSON include every scoped paper.`,
                'Aims and reported findings are separate evidence types. Directions require source evidence; missing extraction is not a null result.',
                ...(s.goalView === 'outcomes' ? this.resultComparisons().map(group => `${group.factor} / ${group.outcome}: ` + Object.entries(group.direction_counts).map(([direction, count]) => `${direction}: ${paperCount(count)}`).join('; ') + '. Matching reported context only; not a statistical outlier test.') : [])]
              : [this.insightLimits(), `${this.visibleConcepts().length} concepts shown; analytics use all retained concepts. Network positions group themes, not effect sizes.`,
                'Jaccard overlap = shared papers / papers containing either concept. Counts measure text or metadata co-occurrence, not causality.',
                ...(s.data.analytics?.core_themes || []).slice(0, 3).map(node => `Core concept: ${node.label}, ${node.count}/${s.data.scope.items} papers.`),
                ...(s.data.analytics?.strongest_links || []).slice(0, 3).map(edge => `${edge.source_label} + ${edge.target_label}: ${edge.shared_count} shared papers; ${Math.round(edge.jaccard * 100)}% overlap.`),
                ...(s.data.analytics?.unusual_combinations || []).slice(0, 2).map(entry => `Unusual concept combinations: ${entry.title}, ${Math.round(entry.score * 100)}% of eligible pairs unique to this paper. Not a novelty claim.`)];
          const footer = notes.filter(Boolean).flatMap(line => String(line).match(/.{1,115}(?:\s|$)|.{1,115}/g) || []);
          const legend = s.kind === 'words' ? this.insightWordLegend() : [];
          const height = s.height + 90 + footer.length * 22 + Math.ceil(legend.length / 3) * 24;
          const legendMarkup = legend.map((category, index) => {
            const x = 20 + index % 3 * (s.width - 40) / 3;
            const y = s.height + 90 + footer.length * 22 + Math.floor(index / 3) * 24;
            return `<circle cx="${x + 4}" cy="${y - 4}" r="4" fill="${wordColour({ concept_id: category.id })}"/>` + text(x + 15, y, category.label, 11);
          }).join('');
          const markup = `<svg xmlns="http://www.w3.org/2000/svg" width="${s.width}" height="${height}" viewBox="0 0 ${s.width} ${height}"><style>svg{color:${foreground}}text:not([font-family]){font-family:Arial,sans-serif}text:not([fill]){fill:${foreground}}.insight-svg-card{fill:${this.dark ? '#181b29' : '#fff'};stroke:${this.dark ? '#272b3c' : '#dde1ea'}}.insight-label{paint-order:stroke;stroke:${background};stroke-width:3px;stroke-linejoin:round}</style><rect width="100%" height="100%" fill="${background}"/>${text(20, 27, this.insightTitle() + ' · ' + (s.data.scope?.label || ''), 20)}${text(20, 49, `${s.data.scope?.items || 0} papers · ${s.kind === 'methods' ? 'Displayed page ' + (s.page + 1) + '; CSV/JSON include every paper. 0 = not detected.' : 'Evidence and complete data in the accompanying JSON.'}`, 11)}<g transform="translate(0,65)">${s.svg}</g>${footer.map((line, i) => text(20, s.height + 90 + i * 22, line, 12)).join('')}${legendMarkup}</svg>`;
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
  window.InsightLayouts = { forceLayout, wordLayout, csvCell, wrapText, graphHeight };
})();
