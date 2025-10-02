(() => {
  // Kurz-Helper
  const $ = (id) => document.getElementById(id);
  const status = (msg) => { $('status').textContent = msg; };

  let discovered = [];
  let grid = { cols:2, rows:1, panel_w:128, panel_h:128 };
  let tiles = [];

  function buildGrid() {
    grid.cols = parseInt($('cols').value)||1;
    grid.rows = parseInt($('rows').value)||1;
    grid.panel_w = parseInt($('panel_w').value)||128;
    grid.panel_h = parseInt($('panel_h').value)||128;
    const g = $('grid');
    g.style.setProperty('--cols', grid.cols);
    g.style.setProperty('--rows', grid.rows);
    g.innerHTML = '';
    for (let y=0; y<grid.rows; y++) {
      for (let x=0; x<grid.cols; x++) {
        const c = document.createElement('div');
        c.className = 'cell';
        c.dataset.x = x;
        c.dataset.y = y;
        c.dataset.pos = `${x+1},${y+1}`;
        c.onclick = () => removeTile(x,y);
        g.appendChild(c);
      }
    }
    redrawTiles();
  }

  function redrawTiles() {
    document.querySelectorAll('.cell').forEach(c=>{
      c.classList.remove('assigned');
      c.innerHTML = `<div>frei</div>`;
    });
    tiles.forEach((t,i)=>{
      const cx = t.offx / grid.panel_w;
      const cy = t.offy / grid.panel_h;
      const cell = document.querySelector(`.cell[data-x='${cx}'][data-y='${cy}']`);
      if (cell) {
        cell.classList.add('assigned');
        cell.innerHTML = `<div>#${i+1}</div><div>MAC ${t.mac16.toString(16).toUpperCase()}</div>`;
      }
    });
    const list = $('panel-list'); list.innerHTML='';
    discovered.forEach(d=>{
      const item = document.createElement('div');
      item.className='panel-item';
      item.textContent = `MAC ${d.mac16.toString(16)} – ${d.w16*16}x${d.h16*16}`;
      item.draggable = true;
      item.dataset.mac16 = d.mac16;
      item.ondragstart = e => { e.dataTransfer.setData('text/plain', String(d.mac16)); };
      if (tiles.find(t=>t.mac16===d.mac16)) item.classList.add('used');
      list.appendChild(item);
    });
  }

  function removeTile(x,y){
    tiles = tiles.filter(t => !(t.offx === x*grid.panel_w && t.offy === y*grid.panel_h));
    redrawTiles();
  }

  function installDnD(){
    document.querySelectorAll('.cell').forEach(cell=>{
      cell.ondragover = e => e.preventDefault();
      cell.ondrop = e => {
        e.preventDefault();
        const mac16 = parseInt(e.dataTransfer.getData('text/plain'));
        const offx = cell.dataset.x * grid.panel_w;
        const offy = cell.dataset.y * grid.panel_h;
        tiles = tiles.filter(tt => !(tt.offx===offx && tt.offy===offy));
        tiles.push({mac16, w:grid.panel_w, h:grid.panel_h, offx, offy});
        redrawTiles();
      };
    });
  }

  function currentLayout() {
    const ordered = [];
    const sorted = [...tiles].sort((a,b) => (a.offy - b.offy) || (a.offx - b.offx));
    sorted.forEach((t, i) => {
      ordered.push({
        mac16: t.mac16,
        w: grid.panel_w,
        h: grid.panel_h,
        offx: t.offx,
        offy: t.offy,
        nblock: i + 1
      });
    });
    return {
      grid_cols: grid.cols,
      grid_rows: grid.rows,
      panel_w: grid.panel_w,
      panel_h: grid.panel_h,
      tiles: ordered
    };
  }

  async function discover(){
    const res = await fetch('/api/panels/discover');
    discovered = await res.json();
    redrawTiles();
    installDnD();
    status(`${discovered.length} Panels gefunden`);
  }

  async function saveCfg(){
    const L = currentLayout();
    await fetch('/api/panels/save', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(L)
    });
    status('Layout gespeichert');
  }

  async function sendCfg(){
    const L = currentLayout();
    await fetch('/api/panels/send_config', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({...L, dest_ip: $('dest_ip').value})
    });
    status('Konfiguration gesendet');
  }

  async function sendTest(fromLoop = false){
    if (!fromLoop) {
      sendCfg(); 
    }
    const L = currentLayout();
    await fetch('/api/panels/test', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({...L, dest_ip: $('dest_ip').value})
    });
    status('Testbild gesendet');
  }

let testLoopActive = false;
let testLoopInterval = null;

function sendTestLoop() {
  testLoopActive = !testLoopActive;
  sendCfg()
  if (testLoopActive) {
    status('Testbild-Loop gestartet');
    $('btn-test-loop').textContent = 'Testbild Loop stoppen';
    $('btn-test-loop').className = 'btn btn-warning';

    testLoopInterval = setInterval(() => {
      sendTest(true); 
    }, 1000);
  } else {
    clearInterval(testLoopInterval);
    testLoopInterval = null;
    status('Testbild-Loop gestoppt');
    $('btn-test-loop').textContent = 'Testbild loop';
    $('btn-test-loop').className = 'btn btn-outline-warning';
  }
}

  function sendImageFile() {
    const f = $('imgfile').files[0];
    if (!f) { status('Kein Bild gewählt'); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      const b64 = reader.result;
      const L = currentLayout();
      await fetch('/api/panels/image', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ image: b64, layout: L, dest_ip: $('dest_ip').value })
      });
      status('Bild gesendet');
    };
    reader.readAsDataURL(f);
  }

  // ---- Event-Bindings & initial load ----
  window.addEventListener('DOMContentLoaded', () => {
    $('btn-sendimg').onclick = sendImageFile;

    $('btn-discover').onclick = discover;
    $('btn-save').onclick = saveCfg;
    $('btn-send').onclick = sendCfg;
    $('btn-test').onclick = () => sendTest(false);
    $('btn-test-loop').onclick = sendTestLoop;

    $('cols').addEventListener('change', ()=>{ buildGrid(); installDnD(); });
    $('rows').addEventListener('change', ()=>{ buildGrid(); installDnD(); });
    $('panel_w').addEventListener('change', ()=>{ buildGrid(); installDnD(); });
    $('panel_h').addEventListener('change', ()=>{ buildGrid(); installDnD(); });

    buildGrid();
    installDnD();
    discover();
    loadLayout(); // lädt gespeichertes Layout und zeichnet es

    async function loadLayout() {
      try {
        const res = await fetch("/api/panels/get");
        const data = await res.json();
        if (!data || !data.tiles) return;
        $('cols').value = data.grid_cols || 1;
        $('rows').value = data.grid_rows || 1;
        $('panel_w').value = data.panel_w || 128;
        $('panel_h').value = data.panel_h || 128;
        buildGrid();
        installDnD();
        tiles = data.tiles || [];
        redrawTiles();
      } catch (e) {
        console.error("loadLayout failed", e);
      }
    }
  });
})();
