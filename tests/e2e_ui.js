/*
 * End-to-end UI scenario, evaluated inside the running web app by tools/ui_snapshot.py:
 *
 *   .venv/Scripts/python.exe tools/ui_snapshot.py --url http://127.0.0.1:8775/ --out shots/e2e.png \
 *       --step "$(cat tests/e2e_ui.js)" --initial-wait 3
 *
 * It drives the Alpine component exactly like the UI buttons do, then checks the server state
 * through the API. Returns {passed, failed, results} (printed by ui_snapshot).
 * Run against a TEST library only: it creates and deletes data.
 */
(async () => {
  const A = document.documentElement._x_dataStack[0];
  const results = [];
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const check = (name, cond, info = '') => results.push({ name, ok: !!cond, info: cond ? '' : String(info) });
  const step = async (name, fn) => {
    try { await fn(); } catch (e) { results.push({ name, ok: false, info: `threw: ${e.message}` }); }
  };
  const stamp = Date.now().toString(36);

  await step('create item via New Item', async () => {
    await A.createItem('journalArticle');
    check('new item selected', A.selectedItem && A.selectedItem.title === 'Untitled');
    await A.saveTitle(`E2E dendritic computation ${stamp}`);
    await A.saveField('publicationTitle', 'Neuron');
    await A.saveField('date', '2019-06-01');
    A.selectedItem.creators.push({ creatorType: 'author', lastName: 'Poirazi', firstName: 'Panayiota' });
    await A.saveCreators();
    const it = await api.get(`/api/items/${A.selectedItem.id}`);
    check('fields persisted', it.journal === 'Neuron' && it.year === 2019, JSON.stringify([it.journal, it.year]));
    check('creator persisted', it.creators[0].lastName === 'Poirazi');
    check('citation key generated', /^poirazi2019e2e/.test(it.citation_key), it.citation_key);
    check('list row patched', A.items.some(r => r.id === it.id && r.journal === 'Neuron'));
  });
  const itemId = A.selectedItem && A.selectedItem.id;

  await step('change item type', async () => {
    await A.changeItemType('conferencePaper');
    await A.saveField('conferenceName', 'Cosyne');
    const it = await api.get(`/api/items/${itemId}`);
    check('type changed', it.item_type === 'conferencePaper' && it.fields.conferenceName === 'Cosyne', JSON.stringify(it.fields));
  });

  await step('tags + tag selector filter', async () => {
    A.tagInput = `e2e-${stamp}, dendrites`;
    await A.addTagToItem();
    check('two tags added', A.selectedItem.tags.length === 2, A.selectedItem.tags.map(t => t.name));
    A.selectVirtual('all');
    await sleep(300);
    await A.loadItems();
    A.toggleTagFilter(`e2e-${stamp}`);
    await sleep(600);
    check('tag filter narrows list', A.items.length === 1 && A.items[0].id === itemId, A.items.length);
    A.clearTagFilter();
    await sleep(400);
  });

  await step('child note autosave', async () => {
    await A.openItem(itemId);
    await A.addChildNote(A.selectedItem);
    await sleep(200);
    const el = document.getElementById('note-editor');
    el.innerHTML = '<h1>Key findings</h1><p>NMDA spikes <b>boost</b> capacity</p>';
    A.onNoteInput();
    await sleep(1200);
    await A.closeNoteEditor();
    const it = await api.get(`/api/items/${itemId}`);
    check('note saved with formatting', it.notes_list.some(n => n.title === 'Key findings' && n.html.includes('<b>boost</b>')), JSON.stringify(it.notes_list));
    const found = await api.get('/api/items', { q: 'NMDA', mode: 'fields' });
    check('note text searchable', found.some(r => r.id === itemId));
  });

  await step('nested collections + drag semantics', async () => {
    const parent = await api.post('/api/collections', { name: `E2E parent ${stamp}` });
    const child = await api.post('/api/collections', { name: `E2E child ${stamp}`, parent_id: parent.id });
    await A.loadCollections();
    A.expanded = { ...A.expanded, [parent.id]: true };
    check('child row indented under parent', A.collectionRows.some(r => r.id === child.id && r.depth === 1));
    A.selectedIds = [itemId];
    await A.assignSelectedToCollection(child.id);
    A.selectCollection(parent.id);
    await sleep(600);
    check('parent shows subcollection items', A.items.some(r => r.id === itemId));
    A.includeSub = false;
    await A.loadItems();
    check('subcollections toggle hides them', !A.items.some(r => r.id === itemId));
    A.includeSub = true;
    await A.moveCollection(child.id, null);
    check('collection moved to top level', A.collections.find(c => c.id === child.id).parent_id === null);
    await api.del(`/api/collections/${parent.id}`);
    await api.del(`/api/collections/${child.id}`);
    await A.loadCollections();
  });

  await step('advanced search + saved search', async () => {
    A.advanced = { id: null, name: `E2E search ${stamp}`, match: 'all', conditions: [
      { field: 'creator', op: 'contains', value: 'Poirazi' }, { field: 'tag', op: 'is', value: 'dendrites' }] };
    A.runAdvanced();
    await sleep(700);
    check('advanced search finds item', A.virtual === 'advanced' && A.items.length === 1 && A.items[0].id === itemId, A.items.length);
    await A.saveAdvanced();
    await sleep(600);
    const s = A.savedSearches.find(x => x.name === `E2E search ${stamp}`);
    check('saved search listed and selected', s && A.virtual === 'search' && A.items.length === 1);
    if (s) await api.del(`/api/saved-searches/${s.id}`);
    await A.loadSavedSearches();
  });

  await step('bibliography + citation engine', async () => {
    const cite = new RHCitations();
    await cite.useStyle('nature', 'en-US');
    const bib = await cite.bibliography([itemId], 'text');
    check('nature bibliography contains author', bib.text.includes('Poirazi'), bib.text);
    await cite.useStyle('apa', 'en-US');
    const c = await cite.citation([{ id: itemId }], 'text');
    check('APA in-text citation', /Poirazi.*2019/.test(c), c);
    const doc = await cite.processDocument([{ citationID: 'a', items: [{ id: itemId }] }], 'text');
    check('processDocument works', doc.citations.a && doc.bibliography.entries.length === 1);
  });

  await step('export formats', async () => {
    const bib = await (await fetch(`/api/export?format=bibtex&ids=${itemId}`)).text();
    check('bibtex export', bib.includes('@inproceedings') && bib.includes('Poirazi'), bib.slice(0, 120));
    const xml = await (await fetch(`/api/export?format=wordxml&ids=${itemId}`)).text();
    check('word xml export', xml.includes('<b:SourceType>ConferenceProceedings</b:SourceType>'));
  });

  await step('duplicate detection + merge', async () => {
    await A.duplicateItem(A.items.find(i => i.id === itemId) || { id: itemId });
    A.selectVirtual('duplicates');
    await sleep(900);
    const g = A.duplicateGroups.find(gr => gr.items.some(i => i.id === itemId));
    check('duplicate group found', g && g.items.length === 2, JSON.stringify(A.duplicateGroups.map(x => x.items.length)));
    if (g) {
      g.master = itemId;
      await A.mergeGroup(g);
      const it = await api.get(`/api/items/${itemId}`);
      check('merged keeps tags', it.tags.length === 2 && !it.deleted_at);
    }
  });

  await step('trash, restore, delete permanently', async () => {
    A.selectVirtual('all');
    await sleep(500);
    A.selectedIds = [itemId];
    await A.trashSelected();
    check('item gone from library', !(await api.get('/api/items', { q: stamp, mode: 'fields' })).some(r => r.id === itemId));
    A.selectVirtual('trash');
    await sleep(600);
    check('item in trash view', A.items.some(r => r.id === itemId));
    A.selectedIds = [itemId];
    await A.restoreSelected();
    check('restored', (await api.get(`/api/items/${itemId}`)).deleted_at === null);
    // Clean up everything created by this run (the merged duplicate is already in the trash).
    const leftovers = (await api.get('/api/items', { q: `E2E dendritic computation ${stamp}`, mode: 'titleCreatorYear', trash: true })).map(r => r.id);
    await api.post('/api/items/delete-permanently', { item_ids: [itemId, ...leftovers] });
    const tag = (await api.get('/api/tags')).find(t => t.name === `e2e-${stamp}`);
    if (tag) await api.del(`/api/tags/${tag.id}`);
    check('cleanup done', !(await api.get('/api/items', { q: stamp, mode: 'fields' })).length);
  });

  await step('settings dialog + style list', async () => {
    await A.openSettings('citations');
    check('styles loaded', A.styles.length >= 20, A.styles.length);
    check('drive status loaded', A.drive && A.drive.backups && A.drive.api);
    A.closeDialog();
  });

  const failed = results.filter(r => !r.ok);
  return { passed: results.length - failed.length, failed: failed.length, failures: failed };
})()
