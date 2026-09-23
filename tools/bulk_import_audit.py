"""Prepare and audit bulk imports against an explicitly disposable offline library.

Use --prepare to create inputs and a manifest. The audit server supplies synthetic
external metadata while retaining actual parsing, ingestion, jobs and API code.
No production library or user PDF is opened by this tool.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import csv
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
import zipfile

from insight_fixture import ROOT, TEMP_ROOT
from insight_detail_audit import ready, script, settle
from ui_snapshot import CDP, find_chrome, free_port

PORT = 8775
VIEWPORTS = {"desktop": (1440, 900), "laptop": (1024, 768)}
THEMES = ("light", "dark")
LOOKUP_DELAY_SECONDS = 0.3


def synthetic_pdf(record: dict) -> bytes:
    """Write a genuine one-page PDF with matching visible and embedded metadata."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    lines = [record["title"], "Synthetic Researcher", str(record["year"]), "doi:" + record["doi"], "Abstract",
             "This invented fixture exercises bulk reference import and PDF attachment.",
             "No actual experiment or publication is represented by this document.", "Methods", "Synthetic fixture text only."]
    stream = DecodedStreamObject()
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    commands = ["BT /F1 10 Tf 40 750 Td"]
    for index, line in enumerate(escaped):
        if index:
            commands.append("0 -20 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": record["title"], "/Author": "Synthetic Researcher", "/Subject": "doi:" + record["doi"]})
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def synthetic_docx(lines: list[str]) -> bytes:
    """Write a minimal valid WordprocessingML document containing DOI paragraphs."""
    from xml.sax.saxutils import escape

    xml = '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    xml += "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in lines)
    xml += "<w:sectPr/></w:body></w:document>"
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", xml)
    return result.getvalue()


def prepare() -> dict:
    """Create new synthetic inputs under Temp/claude; never reuse a database."""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="bulk-import-audit-", dir=TEMP_ROOT)).resolve()
    os.environ["REVIEWHUNTER_HOME"] = str(home)
    os.environ["REVIEWHUNTER_PORT"] = str(PORT)
    for name in ("REVIEWHUNTER_PAPERS_DIR", "DOIHUNTER_PAPERS_DIR"):
        os.environ.pop(name, None)
    import sys
    sys.path.insert(0, str(ROOT))
    from doi_hunter_web import catalog, db, library

    db.reset_connections()
    collections = {name: library.create_collection(f"Synthetic bulk {name}")["id"] for name in ("Alpha", "Beta")}
    records = {}
    keys = ["paste-one", "paste-two", "csv-one", "csv-two", "docx-one", "docx-two", "pdf-match", "pdf-new", "folder-one", "folder-two"]
    keys += [f"cancel-{number:02d}" for number in range(16)]
    for key in keys:
        doi = "10.9999/audit-" + key
        records[doi] = {"doi": doi, "title": "Synthetic bulk neuroscience " + key.replace("-", " "),
                        "authors": ["Researcher, Synthetic"], "creators": [{"firstName": "Synthetic", "lastName": "Researcher", "creatorType": "author"}],
                        "year": 2024, "journal": "Synthetic Audit Journal", "publisher": "Synthetic fixtures", "item_type": "journalArticle", "abstract": "Invented metadata for isolated import testing."}
    existing = catalog.create_item(records["10.9999/audit-pdf-match"])
    inputs = home / "inputs"
    inputs.mkdir()
    csv_text = "doi,title,authors,year,journal\n" + "".join(f'{records["10.9999/audit-"+key]["doi"]},{records["10.9999/audit-"+key]["title"]},"Researcher, Synthetic",2024,Synthetic Audit Journal\n' for key in ("csv-one", "csv-two"))
    (inputs / "references.csv").write_text(csv_text, encoding="utf-8")
    (inputs / "references.docx").write_bytes(synthetic_docx(["10.9999/audit-docx-one", "10.9999/audit-docx-two"]))
    for key in ("pdf-match", "pdf-new"):
        (inputs / f"{key}.pdf").write_bytes(synthetic_pdf(records["10.9999/audit-" + key]))
    (inputs / "broken.pdf").write_bytes(b"This is deliberately not a PDF.")
    folder = inputs / "pdf-folder"
    (folder / "nested").mkdir(parents=True)
    (folder / "folder-one.pdf").write_bytes(synthetic_pdf(records["10.9999/audit-folder-one"]))
    (folder / "nested" / "folder-two.pdf").write_bytes(synthetic_pdf(records["10.9999/audit-folder-two"]))
    (home / "settings.json").write_text(json.dumps({"auto_retrieve_pdf_metadata": False}), encoding="utf-8")
    manifest = {"synthetic": True, "bulk_import_audit": True, "home": str(home), "port": PORT,
                "collections": collections, "existing_id": existing["id"], "records": records,
                "files": {p.name: str(p) for p in inputs.iterdir() if p.is_file()}, "folder": str(folder),
                "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.rglob("*") if p.is_file()}}
    (home / "fixture.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    db.reset_connections()
    return manifest


def read_fixture(path: Path) -> dict:
    """Validate the explicit disposable fixture marker and resolved home boundary."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    home = Path(manifest.get("home", "")).resolve()
    if not manifest.get("synthetic") or not manifest.get("bulk_import_audit") or not home.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError("Only an explicitly synthetic Temp/claude bulk-import fixture is allowed")
    if not (home / "library.db").is_file():
        raise ValueError("Disposable fixture database is absent")
    return manifest


def serve(manifest: dict) -> None:
    """Run the real app with offline service doubles confined to this process."""
    import sys
    sys.path.insert(0, str(ROOT))
    os.environ["REVIEWHUNTER_HOME"] = manifest["home"]
    os.environ["REVIEWHUNTER_PORT"] = str(PORT)
    for name in ("REVIEWHUNTER_PAPERS_DIR", "DOIHUNTER_PAPERS_DIR"):
        os.environ.pop(name, None)
    from doi_hunter_web import catalog, identifiers, library, metadata, pdf_ingest, transport

    records = manifest["records"]
    unexpected_network = []

    def fetch_doi(doi, *args, **kwargs):
        """Resolve only invented DOI records, adding a predictable progress delay."""
        time.sleep(LOOKUP_DELAY_SECONDS)
        return copy.deepcopy(records.get(catalog.normalize_doi(doi)))

    def search(query, *args, **kwargs):
        """Return deterministic candidates, including a deliberate unrelated top hit."""
        time.sleep(LOOKUP_DELAY_SECONDS)
        if query == "Synthetic ambiguous neuroscience study":
            return [{**records["10.9999/audit-paste-one"], "doi": "10.9999/audit-ambiguous-a", "title": query},
                    {**records["10.9999/audit-paste-two"], "doi": "10.9999/audit-ambiguous-b", "title": query}]
        exact = [copy.deepcopy(record) for record in records.values() if record["title"].casefold() in query.casefold()]
        return exact or [{**records["10.9999/audit-paste-one"], "doi": "10.9999/audit-unrelated", "title": "Unrelated top-ranked publication"}]

    def download(item_id, *args, **kwargs):
        """Exercise canonical ingest for successful PDFs and retain one saved failure."""
        item = catalog.get_item(item_id)
        if item.get("doi") == "10.9999/audit-paste-one":
            return {**item, "file_path": "", "error_msg": "Synthetic unavailable-PDF fixture"}
        record = records.get(item.get("doi"))
        if not record:
            raise RuntimeError("No synthetic PDF exists for this DOI")
        pdf_ingest.ingest_pdf(synthetic_pdf(record), filename="synthetic-download.pdf", item_id=item_id)
        return catalog.get_item(item_id)

    def reject_network(*args, **kwargs):
        """Fail unexpected service calls instead of reaching a public server."""
        description = str(kwargs.get("url") or (args[1] if len(args) > 1 else args[0] if args else "unknown"))
        unexpected_network.append(description[:200])
        raise RuntimeError("Unexpected network request in offline bulk-import audit")

    metadata.fetch_doi = fetch_doi
    metadata.search_crossref = search
    identifiers.resolve_doi = fetch_doi
    library.fetch_doi = fetch_doi
    library.search_crossref = search
    library.download_item = download
    transport.get = reject_network
    transport.download = reject_network
    import requests
    requests.sessions.Session.request = reject_network
    try:
        import httpx
        httpx.Client.request = reject_network
        async def reject_async(*args, **kwargs):
            return reject_network(*args, **kwargs)
        httpx.AsyncClient.request = reject_async
    except ImportError:
        pass
    from doi_hunter_web import server
    # Background journal/backup schedules are unrelated to this bounded UI test.
    server._impact_factor_loop = lambda *args: None
    server.gdrive.scheduler_loop = lambda *args: None

    @server.app.get("/api/bulk-audit-marker")
    def marker():
        """Prove the browser is connected to this exact isolated server."""
        return {"synthetic": True, "home": manifest["home"], "unexpected_network": unexpected_network}

    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


async def capture(cdp, folder, name):
    """Check the modal fits the viewport and save a reviewable screenshot."""
    await settle(cdp)
    await asyncio.sleep(0.45)
    result = await script(cdp, """
      const dialog = document.querySelector('.bulk-import-dialog');
      const bounds = dialog.getBoundingClientRect();
      const body = dialog.querySelector('.bulk-import-body');
      const progress=dialog.querySelector('.bulk-progress');
      return {width: bounds.width, height: bounds.height, top: bounds.top, left: bounds.left,
        right: bounds.right, bottom: bounds.bottom, viewport:[innerWidth,innerHeight],
        horizontalOverflow:body.scrollWidth>body.clientWidth+2,
        rows:detailApp.bulkImportRows().length, destination:detailApp.bulkImportDestination(),
        completed:detailApp.importState.job?.status==='completed',
        progressRatio:progress?.clientWidth ? progress.querySelector('i').getBoundingClientRect().width/progress.clientWidth:null};
    """)
    assert result['left'] >= -2 and result['top'] >= -2, result
    assert result['right'] <= result['viewport'][0]+2 and result['bottom'] <= result['viewport'][1]+2, result
    assert not result['horizontalOverflow'], result
    if result['completed']:assert result['progressRatio'] >= .999, result
    await cdp.screenshot(folder / (name + '.png'))
    return result


async def wait_job(cdp):
    """Wait for a job to finish without treating individual entry failures as a crash."""
    await ready(cdp, "!!detailApp.importState.job && !detailApp.bulkImportActive()")
    return await script(cdp, "return detailApp.importState.job;")


async def click_button(cdp, label):
    """Activate the visible import-dialog button with this exact readable label."""
    await settle(cdp)
    await script(cdp, "const button=[...document.querySelectorAll('.bulk-import-dialog button')].find(b=>b.textContent.trim()===" + json.dumps(label) + " && b.getClientRects().length); if(!button)throw Error('Missing visible button'); button.click();")


async def audit_page(cdp, manifest, folder, key):
    """Exercise real upload inputs, destination capture, failures and cancellation."""
    await ready(cdp, "typeof Alpine !== 'undefined' && !!document.querySelector('[x-data]')")
    await cdp.eval("window.detailApp=Alpine.$data(document.querySelector('[x-data]'))")
    await ready(cdp, "detailApp.collections && detailApp.collections.length>=2")
    marker = await script(cdp, "return await (await fetch('/api/bulk-audit-marker')).json();")
    assert marker['synthetic'] and marker['home'] == manifest['home'], marker
    initially_empty = await cdp.eval("fetch('/api/items').then(r=>r.json()).then(items=>items.length===1)")
    alpha, beta = manifest['collections']['Alpha'], manifest['collections']['Beta']
    await script(cdp, f"detailApp.newBulkImport({alpha});detailApp.importState.text='Synthetic draft';detailApp.openImport({beta});")
    assert await cdp.eval('Number(detailApp.importState.collectionId)') == beta
    assert await cdp.eval('detailApp.importState.text') == 'Synthetic draft'
    await script(cdp, f"detailApp.selectCollection({alpha}); detailApp.newBulkImport({alpha}); detailApp.openImport();")
    await settle(cdp)
    assert await cdp.eval("Number(detailApp.importState.collectionId)") == alpha
    await script(cdp, f"detailApp.selectCollection({beta});")
    assert await cdp.eval("Number(detailApp.importState.collectionId)") == alpha
    text = '\n'.join(['10.9999/audit-paste-one', '10.9999/audit-paste-two', '10.9999/audit-missing', 'Synthetic ambiguous neuroscience study', 'Synthetic unmatched neuroscience query'])
    await script(cdp, "const input=document.querySelector('#bulk-import-text'); input.value="+json.dumps(text)+";input.dispatchEvent(new Event('input',{bubbles:true}));")
    result = {'screens': {'paste': await capture(cdp, folder, 'paste')}, 'scenarios': []}
    started=time.perf_counter()
    await click_button(cdp, 'Search and import')
    pasted = await wait_job(cdp)
    assert pasted['total'] == 5 and pasted['collection']['id'] == alpha, pasted
    assert pasted['records'][0]['item_id'] and not pasted['records'][0]['pdf'], pasted
    assert pasted['records'][1]['pdf'], pasted
    assert all(not row['item_id'] for row in pasted['records'][2:]), pasted
    assert any(row['status']=='needs_review' for row in pasted['records']), pasted
    if initially_empty:assert all(row['created'] for row in pasted['records'][:2]),pasted
    assert await cdp.eval('detailApp.bulkImportCounts().review') == 4
    saved=await script(cdp, f"return await (await fetch('/api/items/{pasted['records'][1]['item_id']}')).json();")
    assert saved['doi']=='10.9999/audit-paste-two' and saved['creators'][0]['lastName']=='Researcher',saved
    assert alpha in saved['collections'],saved
    assert 'Researcher' in Path(saved['file_path']).name and '2024' in Path(saved['file_path']).name,saved['file_path']
    result['paste'] = pasted
    result['started_from_seed_only']=initially_empty
    result['scenarios'].append({'scenario':'paste_destination_failures_review','pass':True,'seconds':round(time.perf_counter()-started,3)})
    result['screens']['paste-results'] = await capture(cdp, folder, 'paste-results')
    await click_button(cdp, 'Needs attention')
    assert await cdp.eval('detailApp.bulkImportRows().length') == 4
    await settle(cdp)
    assert await cdp.eval("document.querySelectorAll('.bulk-result-row').length") == 4
    result['screens']['review'] = await capture(cdp, folder, 'review')
    # Intercept only the generated report blob, retaining the real CSV code path.
    csv = await script(cdp, """
      const original=URL.createObjectURL, click=HTMLAnchorElement.prototype.click;
      let report; URL.createObjectURL=blob=>{report=blob;return 'blob:synthetic-audit';};
      HTMLAnchorElement.prototype.click=function(){};
      try {detailApp.exportBulkImportReport(); return await report.text();}
      finally {URL.createObjectURL=original;HTMLAnchorElement.prototype.click=click;}
    """)
    assert len(csv.splitlines()) == 6 and 'Synthetic unavailable-PDF fixture' in csv
    (folder/'import-report.csv').write_bytes(csv.lstrip('\ufeff').encode('utf-8-sig'))

    await script(cdp, "detailApp.newBulkImport(null); detailApp.openImport(null);detailApp.importState.autoDownload=false;")
    await click_button(cdp, 'CSV, Word & PDFs')
    document = await cdp.send('DOM.getDocument')
    node = await cdp.send('DOM.querySelector', nodeId=document['root']['nodeId'], selector='input[aria-label="Choose publication files"]')
    await cdp.send('DOM.setFileInputFiles', nodeId=node['nodeId'], files=[manifest['files'][name] for name in ['references.csv','references.docx','pdf-match.pdf','pdf-new.pdf','broken.pdf']])
    await ready(cdp, 'detailApp.importState.files.length===5')
    await click_button(cdp,'PDF folder')
    assert await cdp.eval('detailApp.importState.files.length')==0
    await click_button(cdp,'CSV, Word & PDFs')
    await cdp.send('DOM.setFileInputFiles', nodeId=node['nodeId'], files=[manifest['files'][name] for name in ['references.csv','references.docx','pdf-match.pdf','pdf-new.pdf','broken.pdf']])
    await ready(cdp, 'detailApp.importState.files.length===5')
    result['screens']['files'] = await capture(cdp, folder, 'files')
    started=time.perf_counter()
    await click_button(cdp, 'Search and import')
    uploaded = await wait_job(cdp)
    assert uploaded['total'] == 7 and uploaded['collection'] is None, uploaded
    assert sum(row['status']=='failed' for row in uploaded['records']) == 1, uploaded
    assert sum(bool(row['item_id']) for row in uploaded['records']) == 6, uploaded
    assert any(row['pdf'] and row['item_id']==manifest['existing_id'] for row in uploaded['records']), uploaded
    matched=await script(cdp, f"return await (await fetch('/api/items/{manifest['existing_id']}')).json();")
    assert matched['creators'][0]['lastName']=='Researcher' and not matched['collections'],matched
    assert len(matched['attachments'])==1,matched
    result['files'] = uploaded
    result['scenarios'].append({'scenario':'csv_docx_multi_pdf_my_library','pass':True,'seconds':round(time.perf_counter()-started,3)})
    result['screens']['file-results'] = await capture(cdp, folder, 'file-results')

    await script(cdp, "detailApp.newBulkImport(null);detailApp.importState.autoDownload=false;")
    await click_button(cdp, 'PDF folder')
    await script(cdp, "detailApp.importState.folderPath="+json.dumps(manifest['folder'])+";detailApp.importState.collectionId='__new__';detailApp.importState.newName="+json.dumps('Synthetic folder '+key)+";")
    result['screens']['folder'] = await capture(cdp, folder, 'folder')
    started=time.perf_counter()
    await click_button(cdp, 'Search and import')
    folder_job = await wait_job(cdp)
    assert folder_job['total']==2 and all(row['pdf'] for row in folder_job['records']), folder_job
    assert folder_job['collection']['name']=='Synthetic folder '+key
    result['folder'] = folder_job
    result['scenarios'].append({'scenario':'recursive_folder_new_collection','pass':True,'seconds':round(time.perf_counter()-started,3)})
    result['screens']['folder-results'] = await capture(cdp, folder, 'folder-results')

    await script(cdp, "detailApp.newBulkImport(null);detailApp.importState.autoDownload=false;detailApp.importState.text="+json.dumps('\n'.join(f'10.9999/audit-cancel-{n:02d}' for n in range(16)))+";")
    started=time.perf_counter()
    await click_button(cdp, 'Search and import')
    await ready(cdp, '!!detailApp.importState.job && detailApp.bulkImportActive()')
    job_id = await cdp.eval('detailApp.importState.job.job_id')
    result['screens']['progress'] = await capture(cdp, folder, 'progress')
    await script(cdp, "document.querySelector('[aria-label=\"Close import\"]').click();")
    assert await cdp.eval("detailApp.dialog!=='import'")
    await script(cdp, 'detailApp.openImport();')
    assert await cdp.eval('detailApp.importState.job.job_id') == job_id
    await click_button(cdp, 'Stop after current entry')
    cancelled = await wait_job(cdp)
    assert cancelled['status']=='cancelled' and 0<cancelled['completed']<cancelled['total'], cancelled
    assert any(row['status']=='cancelled' for row in cancelled['records']), cancelled
    result['cancel'] = cancelled
    result['screens']['cancelled'] = await capture(cdp, folder, 'cancelled')
    await cdp.send('Page.reload')
    await ready(cdp, "typeof Alpine!=='undefined' && !!document.querySelector('[x-data]')")
    await cdp.eval("window.detailApp=Alpine.$data(document.querySelector('[x-data]'))")
    await ready(cdp, '!!detailApp.importState.job')
    assert await cdp.eval('detailApp.importState.job.job_id') == job_id
    await script(cdp, 'detailApp.openImport();')
    result['screens']['restored'] = await capture(cdp, folder, 'restored')
    result['scenarios'].append({'scenario':'progress_cancel_reopen_reload','pass':True,'seconds':round(time.perf_counter()-started,3)})
    marker = await script(cdp, "return await (await fetch('/api/bulk-audit-marker')).json();")
    assert not marker['unexpected_network'], marker
    result['unexpected_network'] = marker['unexpected_network']
    return result


async def run(args):
    """Run each visual case in a private headless browser on the disposable server."""
    import websockets
    manifest=read_fixture(args.fixture)
    address=urlparse(args.url)
    if address.hostname not in ('localhost','127.0.0.1') or address.port != PORT:
        raise ValueError('Only the isolated localhost:8775 server is permitted')
    args.out.mkdir(parents=True,exist_ok=False)
    sources=['doi_hunter_web/bulk_import.py','doi_hunter_web/bulk_api.py','doi_hunter_web/bulk_sources.py','doi_hunter_web/static/js/bulk_import.js','doi_hunter_web/static/index.html','doi_hunter_web/static/styles.css','doi_hunter_web/static/app.css']
    report={'synthetic':True,'fixture':str(args.fixture),'runs':{},'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}}
    for layout,(width,height) in VIEWPORTS.items():
        for theme in THEMES:
            key=f'{layout}-{theme}'
            folder=args.out/key
            folder.mkdir()
            profile=Path(tempfile.mkdtemp(prefix='bulk-import-chrome-'))
            port=free_port()
            process=subprocess.Popen([find_chrome(),'--headless=new',f'--remote-debugging-port={port}',f'--user-data-dir={profile}','--no-first-run','--no-default-browser-check','--disable-extensions','--hide-scrollbars',f'--window-size={width},{height}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                targets=[]
                for _ in range(80):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=1) as response:
                            targets=[t for t in json.load(response) if t['type']=='page']
                        if targets:break
                    except OSError:pass
                    await asyncio.sleep(.2)
                if not targets:raise RuntimeError('Headless Chrome did not start')
                async with websockets.connect(targets[0]['webSocketDebuggerUrl'],max_size=50*1024*1024) as socket:
                    cdp=CDP(socket)
                    listener=asyncio.create_task(cdp.listen())
                    try:
                        for domain in ('Page','Runtime','Log','Network'):await cdp.send(f'{domain}.enable')
                        await cdp.send('Network.setCacheDisabled',cacheDisabled=True)
                        await cdp.send('Emulation.setDeviceMetricsOverride',width=width,height=height,deviceScaleFactor=1,mobile=False)
                        await cdp.send('Emulation.setEmulatedMedia',features=[{'name':'prefers-color-scheme','value':theme}])
                        await cdp.send('Page.navigate',url=args.url)
                        report['runs'][key]=await audit_page(cdp,manifest,folder,key)
                        font_warnings=[message for message in cdp.console_errors if message.startswith('log: Failed to load resource:') and 'https://fonts.googleapis.com/css2?' in message]
                        errors=[message for message in cdp.console_errors if message not in font_warnings]
                        report['runs'][key]['console_errors']=errors
                        report['runs'][key]['external_font_warnings']=font_warnings
                        assert not errors,errors
                    finally:listener.cancel()
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if profile.resolve().parent != Path(tempfile.gettempdir()).resolve():raise ValueError('Unsafe browser profile cleanup path')
                shutil.rmtree(profile,ignore_errors=True)
            assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in manifest['source_sha256'].items())
            report['source_inputs_unchanged']=True
            (args.out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            with (args.out/'scenarios.csv').open('w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=['viewport_theme','scenario','pass','seconds'])
                writer.writeheader()
                for case,data in report['runs'].items():
                    for row in data['scenarios']:writer.writerow({'viewport_theme':case,**row})
            summary={'synthetic':True,'input_hashes_unchanged':True,'runs':{
                case:{'scenarios':data['scenarios'],'screenshots':len(data['screens']),
                      'console_errors':len(data['console_errors']),'external_font_warnings':len(data['external_font_warnings']),
                      'unexpected_resolver_network_calls':len(data['unexpected_network'])}
                for case,data in report['runs'].items()}}
            (args.out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            print(f'Passed {key}',flush=True)
    return report


def main() -> None:
    """Create fixtures or run the isolated server/browser modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8775/")
    parser.add_argument("--out", type=Path, default=ROOT/'docs'/'bulk-import'/'screens'/datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    if args.prepare:
        manifest = prepare()
        print(json.dumps({"home": manifest["home"], "manifest": str(Path(manifest["home"]) / "fixture.json")}, indent=2))
        return
    if args.serve and args.fixture:
        serve(read_fixture(args.fixture))
        return
    if args.fixture:
        report=asyncio.run(run(args))
        print(json.dumps({'runs':len(report['runs']),'artifacts':str(args.out)},indent=2))
        return
    parser.error("Use --prepare or --fixture")


if __name__ == "__main__":
    main()
