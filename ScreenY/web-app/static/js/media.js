// ---------- Helpers ----------
async function apiGet(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPost(url, body){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPostForm(url, formData){
  const r = await fetch(url, {method:'POST', body: formData});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
function fmtBytes(n){
  if(n===undefined||n===null) return '';
  const k=1024, u=['B','KB','MB','GB','TB']; let i=0;
  while(n>=k && i<u.length-1){ n/=k; i++; }
  return `${n.toFixed(i?1:0)} ${u[i]}`;
}
function fmtTime(ts){
  try{ return new Date(ts*1000).toLocaleString(); }catch(_){ return ''; }
}
function kindOf(name){
  const s = name.toLowerCase();
  if(/\.(png|jpe?g|gif|bmp|webp)$/i.test(s)) return 'image';
  if(/\.(mp4|mov|mkv|avi|webm|m4v)$/i.test(s)) return 'video';
  return 'other';
}
function thumbUrl(name){ return '/api/thumb?file='+encodeURIComponent(name); }

// ---------- State ----------
const MM = {
  view: 'grid',
  sort: 'name',
  query: '',
  files: [],
  plmode: false,
  selected: new Set(),
  playlists: [],
  types: new Set(['image','video','other']),
  plFilter: 'all',          // 'all' | 'in' | 'notin'
  filterPLName: null        // Name der Playlist, die als Referenz dient
};

// ---------- DOM ----------
const elSearch   = document.getElementById('mm-search');
const elGrid     = document.getElementById('grid');
const elListWrap = document.getElementById('list-wrap');
const elListBody = document.querySelector('#list tbody');
const elBtnGrid  = document.getElementById('btn-grid');
const elBtnList  = document.getElementById('btn-list');
const elUpload   = document.getElementById('uploadForm');
const elUpFiles  = document.getElementById('upFiles');

const elPlMode   = document.getElementById('mm-plmode');
const elPlSidebar= document.getElementById('pl-sidebar');
const elPlList   = document.getElementById('mm-pl-list');
const elCnt1     = document.getElementById('mm-selected-count');
const elCnt2     = document.getElementById('mm-selected-count-2');

const elPlFilter = document.getElementById('mm-plfilter');
const elPlFilterHint = document.getElementById('mm-plfilter-hint');
const elPlFilterPL = document.getElementById('mm-plfilter-pl');

const elBtnUpload = document.getElementById('btnUpload');
const elProgWrap  = document.getElementById('upProgWrap');
const elProgBar   = document.getElementById('upProg');

const $plPanel = document.querySelector('#pl-panel');


const renameModal= new bootstrap.Modal(document.getElementById('renameModal'));
const renameForm = document.getElementById('renameForm');

// ---------- Init ----------
(async function init(){
  bindUI();
  await loadFiles();
  render();
})();

function bindUI(){
  // Suche
  elSearch.addEventListener('input', ()=>{ MM.query = elSearch.value.trim().toLowerCase(); render(); });

  // Ansicht
  elBtnGrid.addEventListener('click', ()=>{ MM.view='grid'; render(); elBtnGrid.classList.add('active'); elBtnList.classList.remove('active'); });
  elBtnList.addEventListener('click', ()=>{ MM.view='list'; render(); elBtnList.classList.add('active'); elBtnGrid.classList.remove('active'); });

  // Sortierung
  document.querySelectorAll('.mm-sort').forEach(a=>{
    a.addEventListener('click', async ()=>{
      MM.sort = a.getAttribute('data-sort') || 'name';
      await loadFiles();
      render();
    });
  });
  
  // Typ-Filter (Dropdown)
  const typeWrap = document.getElementById('mm-type-filter');
  if (typeWrap) {
    typeWrap.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', () => {
        const v = cb.value;
        if (cb.checked) MM.types.add(v);
        else MM.types.delete(v);
        render();
      });
    });
  }
    // Playlist-Filter
    if (elPlFilter) {
    elPlFilter.addEventListener('change', () => {
        MM.plFilter = elPlFilter.value || 'all';
        render();
    });
    }


// Hilfsfunktion: eine Datei mit Progress hochladen
function uploadOne(file){
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/media/upload');
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300) ? resolve() : reject(new Error(xhr.responseText || 'HTTP '+xhr.status));
    xhr.onerror = () => reject(new Error('Netzwerkfehler'));
    const fd = new FormData();
    fd.append('files', file, file.name);
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      // Fortschritt pro Datei in globaler Aggregation wird im submit-Handler berechnet
      document.dispatchEvent(new CustomEvent('mm-upload-progress', { detail: { loaded: ev.loaded, total: ev.total } }));
    };
    xhr.send(fd);
  });
}

// Bestehenden Submit-Handler komplett ersetzen:
elUpload.addEventListener('submit', async (ev) => {
  ev.preventDefault();

  const files = Array.from(elUpFiles.files || []);
  if (!files.length) return;

  // UI sperren
  const originalBtnHtml = elBtnUpload.innerHTML;
  const originalDisabled = elBtnUpload.disabled;
  const originalInputDisabled = elUpFiles.disabled;

  // Spinner + Text
  const setBtnBusy = (txt) => {
    elBtnUpload.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>${txt}`;
  };
  elBtnUpload.disabled = true;
  elUpFiles.disabled   = true;
  elProgWrap.style.display = 'block';
  elProgBar.style.width = '0%';
  elProgBar.textContent = '0%';

  // Gesamtgrößen berechnen
  const totalBytes = files.reduce((a,f) => a + (f.size||0), 0);
  let uploadedBytes = 0;
  let lastFileLoaded = 0;

  // Globalen Progress-Aggregator registrieren
  const onProg = (e) => {
    // e.detail.loaded ist die geladene Bytezahl der aktuellen Datei (inkrementell)
    // Wir korrigieren auf den Zuwachs gegenüber letztem bekannten Stand
    const cur = e.detail.loaded;
    const delta = Math.max(0, cur - lastFileLoaded);
    lastFileLoaded = cur;
    uploadedBytes = Math.min(totalBytes, uploadedBytes + delta);
    const pct = totalBytes ? Math.floor((uploadedBytes / totalBytes) * 100) : 100;
    elProgBar.style.width = pct + '%';
    elProgBar.textContent = pct + '%';
  };
  document.addEventListener('mm-upload-progress', onProg);

  try {
    // Dateien nacheinander hochladen, damit x/y Sinn ergibt
    for (let i = 0; i < files.length; i++) {
      lastFileLoaded = 0; // Reset für die jeweilige Datei
      setBtnBusy(`Hochladen… ${i+1}/${files.length}`);
      await uploadOne(files[i]);
    }

    // Fertig
    elBtnUpload.innerHTML = 'Fertig';
    elProgBar.style.width = '100%';
    elProgBar.textContent = '100%';

    // Eingabefeld zurücksetzen
    elUpFiles.value = '';

    // Liste neu laden
    await loadFiles();
    render();
  } catch (err) {
    alert('Upload-Fehler: ' + err.message);
  } finally {
    // Cleanup UI
    document.removeEventListener('mm-upload-progress', onProg);
    setTimeout(() => { 
      elProgWrap.style.display = 'none';
      elProgBar.style.width = '0%';
      elProgBar.textContent = '0%';
      elBtnUpload.innerHTML = originalBtnHtml;
      elBtnUpload.disabled = originalDisabled;
      elUpFiles.disabled = originalInputDisabled;
    }, 800);
  }
});

  // Playlist-Modus
  elPlMode.addEventListener('change', async ()=>{
    MM.plmode = elPlMode.checked;
    elPlSidebar.classList.toggle('d-none', !MM.plmode);
    MM.selected.clear();
    document.querySelectorAll('[data-name].media-selected').forEach(n=>n.classList.remove('media-selected'));
    if(MM.plmode && !MM.playlists.length){ await loadPlaylists(); }
    updateSidebar();
  });

  document.addEventListener('click', async (ev)=>{
    const bRen = ev.target.closest('[data-rename]');
    if(bRen){
      const row = bRen.closest('[data-name]'); const name = row.getAttribute('data-name');
      renameForm.oldname.value = name; renameForm.newname.value = name;
      renameModal.show(); return;
    }
    const bDel = ev.target.closest('[data-del]');
    if(bDel){
      const row = bDel.closest('[data-name]'); const name = row.getAttribute('data-name');
      if(!confirm(`„${name}“ löschen?`)) return;
      try{
        await apiPost('/api/media/delete', { names:[name] });
        await loadFiles(); render();
      }catch(err){ alert('Lösch-Fehler: '+err); }
      return;
    }
    const card = ev.target.closest('[data-name]');
    if(card && MM.plmode){
      const name = card.getAttribute('data-name');
      if(MM.selected.has(name)){ MM.selected.delete(name); card.classList.remove('media-selected'); }
      else { MM.selected.add(name); card.classList.add('media-selected'); }
      updateSidebar(); return;
    }
  });

  renameForm.addEventListener('submit', async (ev)=>{
    ev.preventDefault();
    const oldn = renameForm.oldname.value;
    const newn = renameForm.newname.value.trim();
    if(!newn || newn===oldn) return;
    try{
      await apiPost('/api/media/rename', { old_name: oldn, new_name: newn });
      await loadFiles(); render(); renameModal.hide();
    }catch(err){ alert('Rename-Fehler: '+err); }
  });

elPlList.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-pl][data-act]');
  if (!btn) return;

  const plName = btn.getAttribute('data-pl');
  const act    = btn.getAttribute('data-act');

  if (act === 'mark') {
    selectByPlaylist(plName);
    MM.filterPLName = plName;
    if (elPlFilterPL) elPlFilterPL.textContent = plName;
    if (elPlFilterHint) elPlFilterHint.classList.remove('d-none');
    render();
    return;
  }

  if (act === 'open') {
    window.location.href = `/playlist/${encodeURIComponent(plName)}/edit`;
    return;
  }

  if (act === 'set') {
    if (!MM.selected || MM.selected.size === 0) {
      alert('Keine Medien ausgewählt.');
      return;
    }
    const files = Array.from(MM.selected);
    try {
      await apiPost(`/api/playlist/${encodeURIComponent(plName)}/bulk`, {
        action: 'set',          
        files
      });
      const pl = MM.playlists.find(p => p.name === plName);
      if (pl) pl.items = files.slice();
      render();
    } catch (err) {
      alert('Playlist-Übernahme fehlgeschlagen: ' + err);
    }
    return;
  }
  // -------------------------------------------------------------------


  if (!MM.selected || MM.selected.size === 0) {
    selectByPlaylist(plName);
    return;
  }

  const files = Array.from(MM.selected);

  try {
    await apiPost(`/api/playlist/${encodeURIComponent(plName)}/bulk`, {
      action: (act === 'add') ? 'add' : 'remove',
      files,
      defaults: { mode: 'fill', loop: 1, duration: 0 }
    });

    const pl = MM.playlists.find(p => p.name === plName);
    if (pl) {
      if (act === 'add') {
        files.forEach(f => { if (!pl.items.includes(f)) pl.items.push(f); });
      } else {
        const rem = new Set(files);
        pl.items = pl.items.filter(f => !rem.has(f));
      }
    }
    render();
  } catch (err) {
    alert('Playlist-Update fehlgeschlagen: ' + err);
  }
});

  

}

function selectByPlaylist(plName) {
  const pl = MM.playlists.find(p => p.name === plName);
  if (!pl) return;
  const fileSet = new Set(MM.files.map(f => f.name));
  MM.selected = new Set((pl.items || []).filter(n => fileSet.has(n)));
  render();
}

async function loadFiles(){
  const q = new URLSearchParams({ q: MM.query || '', sort: MM.sort || 'name' }).toString();
  const data = await apiGet('/api/media/list?'+q);
  MM.files = (data.items||[]);
}

async function loadPlaylists(){
  const namesResp = await apiGet('/api/playlists'); 
  const names = namesResp.playlists || [];
  MM.playlists = await Promise.all(names.map(async n=>{
    const pl = await apiGet(`/api/playlist/${encodeURIComponent(n)}`); 
    return {
      name: pl.name,
      items: (pl.items || []).map(x => (typeof x === 'string' ? x : (x.file || x.name || ''))).filter(Boolean)
    };
  }));
}
async function fetchPlaylists() {
  const r = await fetch('/api/media/playlists');
  const j = await r.json();
  return (j.playlists || []);
}

async function renderPlaylistPane(state) {
  const pane = document.querySelector('#rightPane');
  if (!pane) return;
  const count = state.selected.size;
  pane.innerHTML = `
    <div class="card">
      <div class="card-header d-flex align-items-center">
        <strong>Zu Playlists zuweisen</strong>
        <span class="badge text-bg-secondary ms-auto">${count}</span>
      </div>
      <div class="card-body vstack gap-3">
        <div id="plList" class="vstack gap-1"></div>
        <div class="row g-2">
          <div class="col-4">
            <label class="form-label small">Modus</label>
            <select id="defMode" class="form-select form-select-sm">
              <option value="fill">Cover</option>
              <option value="fit">Fit</option>
            </select>
          </div>
          <div class="col-4">
            <label class="form-label small">Dauer (s)</label>
            <input id="defDur" type="number" class="form-control form-control-sm" value="0" min="0">
          </div>
          <div class="col-4">
            <label class="form-label small">Loop</label>
            <input id="defLoop" type="number" class="form-control form-control-sm" value="1" min="1">
          </div>
        </div>
        <div class="d-flex gap-2">
          <button id="btnAssign" class="btn btn-primary btn-sm">Hinzufügen</button>
          <button id="btnUnassign" class="btn btn-outline-danger btn-sm">Entfernen</button>
        </div>
        <small class="text-secondary">Änderungen wirken auf die ausgewählten Datei(en).</small>
      </div>
    </div>
  `;

  const list = pane.querySelector('#plList');
  const names = await fetchPlaylists();
  list.innerHTML = names.map(n => `
    <label class="form-check">
      <input class="form-check-input pl-item" type="checkbox" value="${n}">
      <span class="form-check-label">${n}</span>
    </label>
  `).join('');

  pane.classList.toggle('d-none', count === 0);
}

document.addEventListener('click', async (ev) => {
  if (ev.target?.id === 'btnAssign' || ev.target?.id === 'btnUnassign') {
    const pane = document.querySelector('#rightPane');
    const boxes = pane.querySelectorAll('.pl-item:checked');
    const pls = Array.from(boxes).map(b => b.value);
    if (!pls.length) return;

    const toAdd = (ev.target.id === 'btnAssign') ? pls : [];
    const toRemove = (ev.target.id === 'btnUnassign') ? pls : [];
    await assignSelected(toAdd, toRemove, window.__mediaState);
  }
});

function render(){
  const q = (MM.query || '').toLowerCase();

  // <-- HIER: let statt const
  let rows = MM.files.filter(f => {
    const nameOk = !q || f.name.toLowerCase().includes(q);
    const typeOk = MM.types.has(kindOf(f.name));
    return nameOk && typeOk;
  });

  // Playlist-Filter anwenden (nur wenn Referenz-Playlist gesetzt ist)
  if (MM.plFilter !== 'all' && MM.filterPLName) {
    const pl = MM.playlists?.find(p => p.name === MM.filterPLName);
    if (pl) {
      const inSet = new Set(pl.items || []);
      if (MM.plFilter === 'in') {
        rows = rows.filter(f => inSet.has(f.name));
      } else if (MM.plFilter === 'notin') {
        rows = rows.filter(f => !inSet.has(f.name));
      }
    }
  }

  const gridOn = (MM.view === 'grid');
  elGrid.classList.toggle('d-none', !gridOn);
  elListWrap.classList.toggle('d-none', gridOn);

  if (gridOn) renderGrid(rows);
  else renderList(rows);

  updateSidebar();
}

function renderGrid(rows){
  elGrid.innerHTML = '';
  rows.forEach(f=>{
    const k = kindOf(f.name);
    const prev = (k!=='other') ? thumbUrl(f.name) : '/static/img/file-icon.png';
    const card = document.createElement('div');
    card.className = 'file-card p-2 h-100';
    card.setAttribute('data-name', f.name);
    if(MM.selected.has(f.name)) card.classList.add('media-selected');
    card.innerHTML = `
      <img class="file-thumb" src="${prev}" loading="lazy" alt="">
      <div class="file-name" title="${f.name}">${f.name}</div>
      <span class="badge bg-secondary file-type text-uppercase">${k}</span>
      <div class="file-actions">
        <button class="btn btn-outline-secondary btn-sm" data-rename>Umben.</button>
        <button class="btn btn-outline-danger btn-sm" data-del>Löschen</button>
      </div>
    `;
    elGrid.appendChild(card);
  });
}

function renderList(rows){
  elListBody.innerHTML = '';
  rows.forEach(f=>{
    const k = kindOf(f.name);
    const prev = (k!=='other') ? thumbUrl(f.name) : '/static/img/file-icon.png';
    const tr = document.createElement('tr');
    tr.setAttribute('data-name', f.name);
    if(MM.selected.has(f.name)) tr.classList.add('media-selected');
    tr.innerHTML = `
      <td class="list-check-cell" style="display:none"><input class="form-check-input" type="checkbox" ${MM.selected.has(f.name)?'checked':''} data-select></td>
      <td class="list-thumb-cell"><img class="mini-thumb" src="${prev}" loading="lazy" alt=""></td>
      <td class="text-break">${f.name}</td>
      <td class="text-end">${k}</td>
      <td class="text-end">${fmtBytes(f.size)}</td>
      <td class="text-end">${fmtTime(f.mtime)}</td>
      <td>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" data-rename>Umben.</button>
          <button class="btn btn-outline-danger" data-del>Del</button>
        </div>
      </td>
      `;
    tr.querySelector('[data-select]').addEventListener('change', (e)=>{
      if(!MM.plmode){ e.target.checked=false; return; }
      const name=f.name;
      if(e.target.checked){ MM.selected.add(name); tr.classList.add('media-selected'); }
      else{ MM.selected.delete(name); tr.classList.remove('media-selected'); }
      updateSidebar();
    });
    elListBody.appendChild(tr);
  });
}

function updateSidebar(){
  const cnt = MM.selected.size;
  elCnt1.textContent = String(cnt);
  elCnt2.textContent = String(cnt);

  if (!MM.plmode) return;

  const sel = Array.from(MM.selected);
  elPlList.innerHTML = '';

  MM.playlists.forEach(pl => {
    const hasAll = sel.length && sel.every(f => pl.items.includes(f));
    const hasAny = sel.some(f => pl.items.includes(f));

    // status badge
    let statusTxt = 'keine', statusCls = 'badge-status-none';
    if (sel.length) {
      if (hasAll) { statusTxt = 'alle'; statusCls = 'badge-status-all'; }
      else if (hasAny) { statusTxt = 'teils'; statusCls = 'badge-status-some'; }
    }

    const row = document.createElement('div');
    row.className = 'pl-row';

    const colTitle = document.createElement('div');
    colTitle.className = 'pl-col-title';              
    colTitle.innerHTML = `<a class="pl-title" href="/playlist/${encodeURIComponent(pl.name)}/edit">${pl.name}</a>`;

    const colBadges = document.createElement('div');
    colBadges.className = 'pl-badges';
    colBadges.innerHTML = `
    <span class="badge rounded-pill badge-count">${pl.items.length}</span>
    <span style="width: 50px" class="badge rounded-pill ${statusCls}">${statusTxt}</span>
    `;

    const colActions = document.createElement('div');
    colActions.className = 'pl-actions';
    colActions.innerHTML = `
      <button class="btn btn-sm btn-outline-primary"
              title="Medien dieser Playlist markieren"
              data-pl="${pl.name}" data-act="mark">Laden</button>

      <button class="btn btn-sm btn-outline-warning"
              title="Playlist exakt auf Auswahl setzen"
              data-pl="${pl.name}" data-act="set">Speichern</button>
    `;
    //colActions.innerHTML += `
    //  <button class="btn btn-sm btn-outline-success"
    //          title="Auswahl zur Playlist hinzufügen"
    //          data-pl="${pl.name}" data-act="add">+</button>
//
    //  <button class="btn btn-sm btn-outline-danger"
    //          title="Auswahl aus Playlist entfernen"
    //          data-pl="${pl.name}" data-act="remove">−</button>
    //`;
    row.appendChild(colTitle);
    row.appendChild(colBadges);
    row.appendChild(colActions);
    elPlList.appendChild(row);
  });
}
