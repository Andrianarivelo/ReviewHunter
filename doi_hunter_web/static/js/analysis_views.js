/* Measured, source-backed visual summaries for concepts and reported outcomes. */
(() => {
  'use strict';
  const DIRECTIONS = {
    increase: ['↑', 'Increase', 0], decrease: ['↓', 'Decrease', 1],
    no_detected_change: ['↔', 'No detected change', 6], mixed: ['↕', 'Mixed / conditional', 4],
    unspecified: ['?', 'Direction unclear', 5],
  };

  /** Direction is supplied by the evidence extractor, never guessed from a research aim. */
  function direction(value) { return DIRECTIONS[value] || DIRECTIONS.unspecified; }

  /** Lay out complete labels in stable rows; edges sit behind opaque, non-overlapping cards. */
  function network(nodes, edges, options) {
    const { text, textLines, wrapText, esc, color, action, nodeDetail, edgeDetail } = options;
    const columns = 4, width = 1000, margin = 18, gap = 38;
    const cardWidth = (width - margin * 2 - gap * (columns - 1)) / columns;
    const ordered = [...nodes].sort((a, b) => a.cluster - b.cluster || b.count - a.count || a.label.localeCompare(b.label));
    const cards = [], positions = new Map();
    let top = 40;
    for (let i = 0; i < ordered.length; i += columns) {
      const row = ordered.slice(i, i + columns).map(node => ({ node, lines: wrapText(node.label, cardWidth - 26, 13, 500) }));
      const height = Math.max(78, ...row.map(card => card.lines.length * 17 + 45));
      row.forEach((card, column) => {
        const placed = { ...card, x: margin + column * (cardWidth + gap), y: top, height };
        cards.push(placed); positions.set(card.node.id, placed);
      });
      top += height + 50;
    }
    let svg = text(18, 20, 'CONCEPTS BY THEME · HOVER TO TRACE SHARED PAPERS', 10, 'letter-spacing="1" opacity=".7"');
    for (const edge of edges) {
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if (!a || !b) continue;
      const ax = a.x + cardWidth / 2, bx = b.x + cardWidth / 2;
      const ay = a.y + a.height / 2, by = b.y + b.height / 2;
      const bend = Math.abs(ay - by) < 1 ? ay - a.height / 2 - 28 : (ay + by) / 2;
      svg += `<g class="insight-concept-edge" data-edge="" data-a="${esc(edge.source)}" data-b="${esc(edge.target)}" ${action(edgeDetail(edge))}>` +
        `<title>${esc(edge.source + ' + ' + edge.target + ': ' + edge.weight + ' shared papers')}</title>` +
        `<path d="M ${ax} ${ay} C ${ax} ${bend}, ${bx} ${bend}, ${bx} ${by}" fill="none" stroke="${color(a.node.cluster)}" stroke-width="${1 + 3 * (edge.jaccard || .2)}" opacity=".3"/></g>`;
    }
    for (const card of cards) {
      const { node, lines, x, y, height } = card, tint = color(node.cluster);
      svg += `<g class="insight-node insight-concept-card" data-node="${esc(node.id)}" ${action(nodeDetail(node))}>` +
        `<title>${esc(node.label)}</title><rect x="${x}" y="${y}" width="${cardWidth}" height="${height}" rx="9" class="insight-svg-card"/>` +
        `<rect x="${x}" y="${y + 12}" width="3" height="22" rx="1" fill="${tint}"/>` +
        textLines(x + 13, y + 25, lines, 13, 17, 'font-weight="500"') +
        text(x + 13, y + height - 12, `${node.count} paper${node.count === 1 ? '' : 's'} · ${Math.round((node.prevalence || 0) * 100)}%`, 10, 'opacity=".7"') +
        `<rect x="${x + 13}" y="${y + height - 5}" width="${Math.max(2, (cardWidth - 26) * (node.prevalence || 0))}" height="2" rx="1" fill="${tint}"/></g>`;
    }
    if (!cards.length) svg += text(25, 85, 'No concepts match these filters. Include single-paper or indexing terms.', 14);
    return { svg, height: Math.max(170, top - 20), width };
  }

  /** The matrix encodes normalized overlap and prints shared-paper counts, including zeros. */
  function matrix(nodes, edges, options) {
    const { text, textLines, wrapText, esc, color, action, nodeDetail, edgeDetail } = options;
    const left = 270, cell = 31, width = Math.max(1000, left + nodes.length * cell + 30);
    const lookup = new Map(edges.map(edge => [[edge.source, edge.target].sort().join('\0'), edge]));
    let svg = text(18, 24, 'SHARED PAPERS · COLOUR SHOWS JACCARD OVERLAP', 11, 'letter-spacing=".7" opacity=".7"');
    nodes.forEach((node, j) => { svg += text(left + j * cell + cell / 2, 50, j + 1, 11, 'text-anchor="middle"'); });
    let y = 65;
    nodes.forEach((node, i) => {
      const labelLines = wrapText(node.label, left - 58, 12);
      svg += `<g class="insight-node" data-node="${esc(node.id)}" ${action(nodeDetail(node))}>` +
        text(18, y + 23, i + 1, 11, `fill="${color(node.cluster)}"`) +
        textLines(42, y + 20, labelLines, 12, 15) + '</g>';
      nodes.forEach((other, j) => {
        const edge = lookup.get([node.id, other.id].sort().join('\0'));
        const count = i === j ? node.count : edge?.weight || 0;
        const opacity = i === j ? .1 : edge ? .15 + .6 * edge.jaccard : .025;
        const attrs = i === j ? action(nodeDetail(node)) : edge ? action(edgeDetail(edge)) : action({ label: `${node.label} + ${other.label}`, items: [],
          description: i === j ? `${node.count} papers contain this concept.` : 'No shared paper among the concepts available in this scope.' });
        svg += `<g class="insight-matrix-cell" ${attrs}><rect x="${left + j * cell}" y="${y + 6}" width="${cell - 3}" height="42" rx="4" fill="${color(node.cluster)}" opacity="${opacity}"/>` +
          text(left + j * cell + (cell - 3) / 2, y + 32, count || '·', 11, 'text-anchor="middle"') + '</g>';
      });
      y += Math.max(66, labelLines.length * 15 + 22);
    });
    return { svg, height: Math.max(180, y + 12), width };
  }

  /** A paper remains visible even when no supported directional finding can be extracted. */
  function outcomes(papers, options) {
    const { text, textLines, wrapText, color, action, findingDetail, contextLabel } = options;
    let y = 45, svg = text(20, 22, 'PAPER', 11, 'letter-spacing="1" opacity=".7"') +
      text(285, 22, 'REPORTED FINDINGS · SELECT A ROW FOR SOURCE AND CONTEXT', 11, 'letter-spacing=".7" opacity=".7"');
    for (const paper of papers) {
      const titleLines = wrapText(paper.title, 235, 13, 500);
      const rows = (paper.findings || []).map(finding => {
        const [symbol, label, index] = direction(finding.direction);
        const lines = wrapText(`${finding.factor} → ${finding.outcome}`, 495, 13);
        const context = contextLabel(finding.context);
        const contextLines = wrapText(context || 'Context not resolved; inspect source before comparing.', 655, 10.5);
        return { finding, symbol, label, index, lines, contextLines, height: Math.max(70, lines.length * 17 + contextLines.length * 14 + 28) };
      });
      const height = Math.max(titleLines.length * 17 + 50, rows.reduce((sum, row) => sum + row.height + 8, 0) + 22, 96);
      svg += `<rect x="10" y="${y}" width="980" height="${height}" rx="10" class="insight-svg-card"/>` +
        textLines(24, y + 25, titleLines, 13, 17, 'font-weight="500"') + text(24, y + height - 15, paper.year || 'Year unknown', 10.5, 'opacity=".65"');
      let cursor = y + 12;
      for (const row of rows) {
        const tint = color(row.index);
        svg += `<g class="insight-finding" ${action(findingDetail(row.finding, paper))}>` +
          `<rect x="280" y="${cursor}" width="696" height="${row.height}" rx="6" fill="${tint}" opacity=".08"/>` +
          text(292, cursor + 25, `${row.symbol} ${row.label}`, 12, `fill="${tint}" font-weight="600"`) +
          textLines(470, cursor + 25, row.lines, 13, 17) +
          textLines(292, cursor + row.height - 11 - (row.contextLines.length - 1) * 14, row.contextLines, 10.5, 14, 'opacity=".7"') + '</g>';
        cursor += row.height + 8;
      }
      if (!rows.length) svg += text(292, y + 35, 'No supported directional finding detected in available text.', 13, 'opacity=".7"');
      y += height + 12;
    }
    return { svg, height: Math.max(170, y), width: 1000 };
  }
  window.InsightAnalysisViews = { network, matrix, outcomes, direction };
})();
