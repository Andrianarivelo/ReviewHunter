/* Evidence-first SVG cards for the research-question atlas. No inferred aims or hidden papers. */
(() => {
  'use strict';

  /** Measure every line before drawing so long scientific titles cannot overlap the next card. */
  function atlas(papers, options) {
    const { width, columns, text, textLines, wrapText, esc, color, goalType, detail, action, direction, directionColor, findingDetail } = options;
    const gap = 18, margin = 12, cardWidth = (width - margin * 2 - gap * (columns - 1)) / columns;
    const inner = cardWidth - 36, pairWidth = (inner - 32) / 2;
    const cards = papers.map(paper => {
      const focus = paper.focus_status === 'unavailable'
        ? 'Add an abstract or index the PDF to recover this paper’s research focus.'
        : paper.focus || paper.aims?.[0]?.sentence || paper.title || 'No source text available';
      const focusLines = wrapText(focus, inner, 15, 500);
      const titleLines = wrapText(paper.title || 'Untitled paper', inner, 12, 400);
      const pairs = (paper.pairs || []).slice(0, 2).map(pair => {
        const factor = wrapText(pair.factor, pairWidth - 16, 12.5);
        const outcome = wrapText(pair.outcome, pairWidth - 16, 12.5);
        return { pair, factor, outcome, height: Math.max(factor.length, outcome.length) * 16 + 50 };
      });
      const flowHeight = pairs.reduce((sum, pair) => sum + pair.height + 9, 0);
      const findings = (paper.findings || []).slice(0, 2).map(finding => ({ finding,
        lines: wrapText(`${direction(finding.direction)}: ${finding.outcome}`, inner - 18, 12.5) }));
      const resultHeight = findings.length ? 32 + findings.reduce((sum, entry) => sum + entry.lines.length * 17 + 17, 0) + 23 : 0;
      const titleFocus = paper.focus_status === 'title_focus';
      const height = 79 + focusLines.length * 20 + (pairs.length ? flowHeight + 28 : 34) +
        (titleFocus ? 22 : titleLines.length * 16 + 28) + 23 + resultHeight;
      return { paper, focusLines, titleLines, pairs, findings, height, titleFocus };
    });
    let y = 12, svg = '';
    for (let index = 0; index < cards.length; index += columns) {
      const row = cards.slice(index, index + columns);
      const rowHeight = Math.max(...row.map(card => card.height));
      row.forEach((card, column) => {
        const { paper, focusLines, titleLines, pairs, findings, titleFocus } = card;
        const x = margin + column * (cardWidth + gap), left = x + 18;
        const tint = color(paper.goal_type);
        const status = paper.focus_label || (paper.aims?.length ? 'Explicit aim' : 'From title');
        const source = paper.focus_status === 'unavailable' ? 'source unavailable' : paper.focus_source || (titleFocus ? 'title' : 'available text');
        const selected = action(detail(paper));
        svg += `<g class="insight-goal-card" data-paper="${paper.id}" data-focus-status="${esc(paper.focus_status || '')}" ${selected}>` +
          `<title>${esc(paper.title + ': ' + status)}</title>` +
          `<rect x="${x}" y="${y}" width="${cardWidth}" height="${rowHeight}" rx="12" class="insight-svg-card"/>` +
          `<rect x="${x}" y="${y + 13}" width="4" height="32" rx="2" fill="${tint}"/>` +
          text(left, y + 29, goalType(paper.goal_type), 12, `font-weight="600" fill="${tint}"`) +
          text(x + cardWidth - 18, y + 29, status, 11, 'text-anchor="end" opacity=".8"') +
          text(left, y + 52, paper.focus_status === 'explicit_aim' ? 'RESEARCH AIM' : 'RESEARCH FOCUS', 10, 'letter-spacing="1" opacity=".65"');
        let cursor = y + 77;
        svg += textLines(left, cursor, focusLines, 15, 20, 'font-weight="500"');
        cursor += focusLines.length * 20 + 11;
        if (pairs.length) {
          svg += text(left + 8, cursor, 'FACTOR / EXPOSURE', 9.5, 'letter-spacing=".6" opacity=".7"') +
            text(left + pairWidth + 40, cursor, 'OUTCOME / RESPONSE', 9.5, 'letter-spacing=".6" opacity=".7"');
          cursor += 9;
          for (const { pair, factor, outcome, height } of pairs) {
            const right = left + pairWidth + 32;
            const boxHeight = height - 18;
            svg += `<g class="insight-goal-link" ${action({ label: pair.factor + ' → ' + pair.outcome,
              items: [paper], description: pair.relation_label || 'Stated research question', evidence: [{ id: paper.id, sentence: pair.evidence, source: pair.source || source }] })}>` +
              `<rect x="${left}" y="${cursor}" width="${pairWidth}" height="${boxHeight}" rx="6" fill="${tint}" opacity=".10"/>` +
              `<rect x="${right}" y="${cursor}" width="${pairWidth}" height="${boxHeight}" rx="6" fill="${tint}" opacity=".10"/>` +
              textLines(left + 8, cursor + 22, factor, 12.5, 16) +
              textLines(right + 8, cursor + 22, outcome, 12.5, 16) +
              `<path d="M ${left + pairWidth + 6} ${cursor + boxHeight / 2} h 19 m -5 -4 l 5 4 -5 4" fill="none" stroke="${tint}" stroke-width="1.5" ${pair.negated ? 'stroke-dasharray="3 2"' : ''}/>` +
              text(left + 4, cursor + height - 3, pair.relation_label || 'Research question', 10, 'opacity=".7"') + '</g>';
            cursor += height + 9;
          }
          if ((paper.pairs || []).length > pairs.length) {
            svg += text(left, cursor + 6, `+${paper.pairs.length - pairs.length} more relations in the evidence panel`, 10, 'opacity=".7"');
          }
        } else {
          const explanation = paper.focus_status === 'unavailable'
            ? 'No source text available to extract relationships.'
            : titleFocus ? 'An explicit aim was not found in the available text.' : 'No reliable factor–outcome pair extracted from this aim.';
          svg += text(left, cursor + 5, explanation, 11, 'opacity=".7"');
          cursor += 25;
        }
        if (findings.length) {
          cursor += 25;
          svg += text(left, cursor, 'REPORTED RESULTS', 10, 'letter-spacing="1" opacity=".65"');
          cursor += 10;
          for (const { finding, lines } of findings) {
            const height = lines.length * 17 + 11;
            svg += `<g class="insight-finding" ${action(findingDetail(finding, paper))}>` +
              `<rect x="${left}" y="${cursor}" width="${inner}" height="${height}" rx="5" fill="${directionColor(finding.direction)}" opacity=".09"/>` +
              textLines(left + 9, cursor + 19, lines, 12.5, 17, `fill="${directionColor(finding.direction)}"`) + '</g>';
            cursor += height + 6;
          }
          if ((paper.findings || []).length > findings.length) svg += text(left, cursor + 11, `+${paper.findings.length - findings.length} findings in Outcomes`, 10, 'opacity=".7"');
        }
        // Source titles remain complete. A title-derived focus already displays the full title above.
        const footerY = y + rowHeight - (titleFocus ? 22 : titleLines.length * 16 + 28);
        svg += `<line x1="${left}" x2="${x + cardWidth - 18}" y1="${footerY - 11}" y2="${footerY - 11}" stroke="currentColor" opacity=".12"/>`;
        if (!titleFocus) svg += textLines(left, footerY + 4, titleLines, 12, 16, 'opacity=".8"');
        svg += text(left, y + rowHeight - 13, `${paper.year || 'Year unknown'} · ${source}`, 10.5, 'opacity=".65"') +
          text(x + cardWidth - 18, y + rowHeight - 13, 'Inspect evidence ↗', 10.5, `text-anchor="end" fill="${tint}"`) + '</g>';
      });
      y += rowHeight + gap;
    }
    if (!cards.length) svg = text(30, 75, 'No papers match these filters. Clear a filter to explore the full scope.', 15);
    return { svg, height: Math.max(150, y), width };
  }

  window.InsightGoalViews = { atlas };
})();
