// /static/js/screeny.js

(function(){
  // ---------- Helpers ----------
  const $  = (sel, root=document)=> root.querySelector(sel);
  const $$ = (sel, root=document)=> Array.from(root.querySelectorAll(sel));

  const prettyMode = (m)=> (m||'').toLowerCase()==='random' ? 'Random' :
                         (m||'').toLowerCase()==='once'   ? 'Once'   : 'Repeat';

  // base64url -> str
  function b64urlToStr(b64){
    if (!b64) return '';
    b64 = b64.replace(/-/g,'+').replace(/_/g,'/');
    while (b64.length % 4) b64 += '=';
    try { return decodeURIComponent(escape(atob(b64))); }
    catch(e){ try { return atob(b64); } catch(_){ return ''; } }
  }

  // text preview from token
  function textPreviewFromFile(file){
    if (!file) return 'Text';
    let token = '';
    if (file.startsWith('text://')) token = file.slice(7);
    else if (file.includes('/stream/text')) {
      try {
        const q = file.split('?')[1] || '';
        token = new URLSearchParams(q).get('token') || '';
      } catch(_) {}
    }
    if (!token) return 'Text';
    const raw = b64urlToStr(token);
    try {
      const cfg = JSON.parse(raw || '{}');
      let t = String(cfg.text || '').replace(/\s+/g,' ').trim();
      if (!t) return 'Text';
      t = t.split('\n')[0];
      if (t.length > 60) t = t.slice(0,57) + '…';
      return `„${t}”`;
    } catch(_) { return 'Text'; }
  }

  function prettyItemName(it){
    if (!it) return '—';
    const f = it.file || '';
    const t = (it.type || '').toLowerCase();
    if (t === 'text')   return textPreviewFromFile(f);
    if (t === 'clock')  return (f || 'clock://time').replace('clock://','Uhr: ');
    if (t === 'stream') {
      try { const u = new URL(f); return `Stream: ${u.hostname}${u.pathname}`; }
      catch(_) { return 'Stream'; }
    }
    if (f) return f.split('/').pop();
    return '—';
  }

  // Thumb-URL
  function thumbForItem(it){
    if (!it) return '';
    const f = it.file || '';
    if (it.type === 'clock' && f) return '/api/thumb?file=' + encodeURIComponent(f);
    if (it.type === 'image' && it.local && f) return '/api/thumb?file=' + encodeURIComponent(f);
    if (it.type === 'video' && it.local && f) return '/api/thumb?file=' + encodeURIComponent(f);
    if (it.type === 'text' && f){
      let proto = '';
      if (f.startsWith('text://')) proto = f;
      else if (f.includes('/stream/text')){
        try { const token = new URLSearchParams((f.split('?')[1]||'')).get('token'); if (token) proto = 'text://' + token; } catch(_){}
      }
      if (proto) return '/api/thumb?file=' + encodeURIComponent(proto);
    }
    return '';
  }

  // ---------- UI wiring ----------
  function liPlaylistName(li){
    const a = li.querySelector('a[href^="/playlist/"][href$="/edit"]');
    if (a){
      const m = a.getAttribute('href').match(/^\/playlist\/(.+?)\/edit$/);
      if (m) return decodeURIComponent(m[1]);
    }
    const f = li.querySelector('form[action^="/playlist/"][action$="/start"]');
    if (f){
      const m = f.getAttribute('action').match(/^\/playlist\/(.+?)\/start$/);
      if (m) return decodeURIComponent(m[1]);
    }
    return (a ? a.textContent.trim() : '').trim();
  }

  function updatePlaylistListUI(active, activeName){
  $$('.list-group.playlists > .list-group-item').forEach(li=>{
    const name = liPlaylistName(li);
    const isActive = !!active && name && name === activeName;

    // Markierung IMMER setzen – unabhängig von Buttons
    li.classList.toggle('active2', isActive);

    // Buttons finden (dein Markup verwendet .player-controls)
    const controls = li.querySelector('.player-controls');
    if (!controls) return;

    const forms   = controls.querySelectorAll('form');
    const btnStart = forms[0]?.querySelector('button');
    const btnStop  = forms[1]?.querySelector('button');


    if (btnStart) btnStart.disabled = !!active;
    if (btnStop)  btnStop.disabled  = !isActive;

    const link = li.querySelector('a[href^="/playlist/"][href$="/edit"]');
    if (link) {
      if (isActive) link.setAttribute('aria-current','true');
      else          link.removeAttribute('aria-current');
    }
  });
}


  function updatePlayerHeader(state){
    const active = !!(state && state.active);
    const plName = state?.playlist || '—';
    const idx = (state?.index ?? -1) + 1;
    const tot = state?.total ?? 0;
    const it  = state?.item || null;

    $('#pl-name').textContent  = active ? plName : '—';
    $('#pl-count').textContent = active && tot>0 ? `(${idx}/${tot})` : '';
    $('#pl-item').textContent  = prettyItemName(it);

    $('#btn-stop').disabled = !active;
    $('#btn-prev').disabled = !active;
    $('#btn-next').disabled = !active;

    const badge = $('#pl-mode-badge');
    badge.textContent = active ? prettyMode(state.playlist_mode || 'repeat') : 'stopped';

    const th = $('#player-thumb');
    const src = active ? thumbForItem(it) : '';
    th.src = src || 'static/img/stopped-player-icon.png';
  }

  function wirePlaylistForms(){
    $$('.list-group-item .btn-group form').forEach(form=>{
      form.addEventListener('submit', (ev)=>{
        ev.preventDefault();
        fetch(form.getAttribute('action'), { method: 'POST' })
          .then(()=> scheduleImmediate())
          .catch(()=>{});
      });
    });
  }
  function wirePlayerControls(){
    const post = (u)=> fetch(u, {method:'POST'}).then(()=>scheduleImmediate()).catch(()=>{});
    const p = $('#btn-prev'), n = $('#btn-next'), s = $('#btn-stop');
    if (p) p.onclick = ()=> post('/player/prev');
    if (n) n.onclick = ()=> post('/player/next');
    if (s) s.onclick = ()=> post('/player/stop');
  }

  // ---------- Adaptive Polling ----------
  let pollTimer = null;
  let nextDelay = 1000;      // start schnell
  let lastActive = null;
  let backoff = 0;           // Fehler-Backoff

  function setDelay(ms){
    nextDelay = Math.max(800, Math.min(ms, 30000));
  }
  function schedule(){
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(refresh, nextDelay);
  }
  function scheduleImmediate(){
    if (pollTimer) clearTimeout(pollTimer);
    nextDelay = 800;
    pollTimer = setTimeout(refresh, 50);
  }

  function refresh(){
    if (document.hidden){     // Tab verborgen -> selten updaten
      setDelay(15000);
      schedule();
      return;
    }
    fetch('/api/player/state')
      .then(r=>r.json())
      .then(state=>{
        backoff = 0;
        updatePlayerHeader(state);
        updatePlaylistListUI(!!state?.active, state?.playlist || '');

        const active = !!state?.active;
        // Delay anpassen: aktiv => 1s, inaktiv => 8s
        if (active) setDelay(1000);
        else        setDelay(8000);

        // nur bei Zustandswechsel sofort nachladen (fühlt sich “snappy” an)
        if (lastActive !== active){
          lastActive = active;
          setDelay(800);
        }
      })
      .catch(()=>{
        // Fehler: exponentiell langsamer bis 30s
        backoff = Math.min(backoff + 1, 5);
        setDelay(1000 * Math.pow(2, backoff)); // 2s,4s,8s,16s,32s
      })
      .finally(schedule);
  }

  // ---------- Power/Energy Polling (adaptiv) ----------
  (function powerBlock(){
    const dot   = $('#power-dot');
    const stat  = $('#power-status');
    const tog   = $('#power-toggle');
    const engEl = $('#power-energy');

    if (!dot || !stat || !tog || !engEl) return; // Tasmota-Block evtl. nicht sichtbar

    function setUI(online, state){
      dot.style.background = online ? (state==='ON' ? '#28a745' : '#dc3545') : '#6c757d';
      stat.textContent = online ? (state==='ON' ? 'ON' : (state==='OFF' ? 'OFF' : 'UNBEKANNT')) : 'OFFLINE';
      tog.disabled = !online || state==='UNKNOWN';
      tog.checked = state==='ON';
    }
    function setEnergyUI(d){
      if (!d || !d.online){
        engEl.textContent = 'Leistung: —';
        return;
      }
      const p = d.power_w, v = d.voltage_v, a = d.current_a;
      let s = 'Leistung: ';
      if (p != null && !isNaN(p)) s += `${Number(p).toFixed(0)} W`; else s += '—';
      if (v != null && a != null && !isNaN(v) && !isNaN(a)) s += `  (${Number(v).toFixed(0)} V, ${Number(a).toFixed(2)} A)`;
      engEl.textContent = s;
    }

    let pTimer = null, eTimer = null;
    function pollPower(delay=3000){
      if (pTimer) clearTimeout(pTimer);
      pTimer = setTimeout(()=>{
        fetch('/api/power')
          .then(r=>r.json())
          .then(s=>{
            const online = !!s.online;
            const state  = s.state || 'UNKNOWN';
            setUI(online, state);
            // adaptiver Rhythmus: offline/aus => 10–15s, an => 3–5s
            const d = (!online || state==='OFF') ? 12000 : 4000;
            pollPower(d);
          })
          .catch(()=> { setUI(false,'UNKNOWN'); pollPower(15000); });
      }, delay);
    }
    function pollEnergy(delay=4000){
      if (eTimer) clearTimeout(eTimer);
      eTimer = setTimeout(()=>{
        fetch('/api/energy')
          .then(r=>r.json())
          .then(setEnergyUI)
          .catch(()=> engEl.textContent='Leistung: —')
          .finally(()=> pollEnergy(12000)); // Energie reicht seltener
      }, delay);
    }

    tog.addEventListener('change', function(){
      const want = this.checked ? 'ON' : 'OFF';
      tog.disabled = true;
      const fd = new FormData(); fd.append('state', want);
      fetch('/api/power', {method:'POST', body: fd})
        .then(()=> { pollPower(800); pollEnergy(1000); })
        .catch(()=> { setUI(false,'UNKNOWN'); });
    });

    pollPower(0);
    pollEnergy(0);
  })();

  // ---------- Init ----------
  document.addEventListener('visibilitychange', ()=>{
    if (!document.hidden) scheduleImmediate(); // zurück auf die Seite -> sofort aktualisieren
  });

  document.addEventListener('DOMContentLoaded', ()=>{
    wirePlaylistForms();
    wirePlayerControls();
    scheduleImmediate();
  });
})();


(function(){
  const $tbody = $('#rules-tbody');

  function fetchState(){
    return fetch('/api/tasmota/schedule').then(r => r.json());
  }

  function labelDays(days){
    const sorted = [...new Set(days)].sort((a,b)=>a-b).join(',');
    if (sorted === '0,1,2,3,4') return 'Mo–Fr';
    if (sorted === '5,6') return 'Sa–So';
    if (sorted === '0,1,2,3,4,5,6') return 'Täglich';
    const map = ['Mo','Di','Mi','Do','Fr','Sa','So'];
    return (days||[]).map(d=>map[d]).join(', ');
  }

  function renderRules(rules){
    $tbody.empty();
    if (!rules || !rules.length){
      $tbody.append('<tr><td colspan="4" class="text small">Keine Regeln definiert</td></tr>');
      return;
    }
    rules.sort((a,b)=>a.time.localeCompare(b.time));
    for (const r of rules){
      const tr = $('<tr></tr>');
      tr.append(`<td><code>${r.time}</code></td>`);
      tr.append(`<td><span class="badge ${r.action==='ON'?'bg-success':'bg-secondary'}">${r.action}</span></td>`);
      tr.append(`<td>${labelDays(r.days||[])}</td>`);
      const del = $('<button class="btn btn-sm btn-outline-danger">Löschen</button>');
      del.on('click', ()=>{
        fetch('/api/tasmota/schedule/daily_rules/'+r.id, {method:'DELETE'})
          .then(()=>loadRules());
      });
      const td = $('<td class="text-end"></td>').append(del);
      tr.append(td);
      $tbody.append(tr);
    }
  }

  function getSelectedDays(){
    const arr = [];
    $('#rule-form input[type=checkbox]:checked').each(function(){
      arr.push(parseInt($(this).val(),10));
    });
    if (arr.length===0) return [0,1,2,3,4,5,6];
    return arr;
  }

  function setDays(list){
    $('#rule-form input[type=checkbox]').prop('checked', false);
    for (const d of list){ $('#rule-form input[value='+d+']').prop('checked', true); }
  }

  // Presets
  $('#btn-weekdays').on('click', ()=> setDays([0,1,2,3,4]));
  $('#btn-weekends').on('click', ()=> setDays([5,6]));
  $('#btn-all').on('click', ()=> setDays([0,1,2,3,4,5,6]));

  // Submit add
  $('#rule-form').on('submit', function(e){
    e.preventDefault();
    const time = $('#rule-time').val();
    const action = $('#rule-action').val();
    const days = getSelectedDays();
    if (!time) return;
    fetch('/api/tasmota/schedule/daily_rules/add', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ time, action, days })
    }).then(()=> {
      $('#rule-time').val('');
      loadRules();
    });
  });

  function loadRules(){
    fetchState().then(s => renderRules(s.daily_rules || []));
  }

  loadRules();
  setInterval(loadRules, 15000);
})();


// ===== Einfache Timer (collapse: #sched-card) =====
(function(){
  const list = $('#timer-list');
  const dailyOn = $('#daily-on');
  const dailyOff = $('#daily-off');

  function load(){
    fetch('/api/tasmota/schedule').then(r=>r.json()).then(s=>{
      dailyOn.val(s.daily && s.daily.on ? s.daily.on : '');
      dailyOff.val(s.daily && s.daily.off ? s.daily.off : '');
      list.empty();
      (s.timers||[]).forEach(t=>{
        const li = $('<li class="list-group-item d-flex justify-content-between align-items-center"></li>');
        const when = new Date(t.run_at).toLocaleString();
        li.append(`<span><code>${t.action}</code> @ ${when}</span>`);
        const btn = $('<button class="btn btn-sm btn-outline-danger">Löschen</button>');
        btn.on('click', ()=>{
          fetch('/api/tasmota/schedule/timer/'+t.id, {method:'DELETE'}).then(()=>load());
        });
        li.append(btn);
        list.append(li);
      });
      if (list.children().length===0){
        list.append('<li class="list-group-item small text">Keine Timer</li>');
      }
    });
  }

  $('#daily-form').on('submit', function(ev){
    ev.preventDefault();
    fetch('/api/tasmota/schedule/daily', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({on: dailyOn.val()||null, off: dailyOff.val()||null})
    }).then(()=>load());
  });

  $('.add-timer').on('click', function(){
    const h = parseFloat($(this).data('hours'));
    const a = $(this).data('action');
    fetch('/api/tasmota/schedule/timer', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({hours: h, action: a})
    }).then(()=>load());
  });

  $('#btn-add-on').on('click', function(){
    const h = parseFloat($('#timer-hours').val());
    if (!h || h<=0) return;
    fetch('/api/tasmota/schedule/timer', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({hours: h, action: 'ON'})
    }).then(()=>load());
  });

  $('#btn-add-off').on('click', function(){
    const h = parseFloat($('#timer-hours').val());
    if (!h || h<=0) return;
    fetch('/api/tasmota/schedule/timer', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({hours: h, action: 'OFF'})
    }).then(()=>load());
  });

  load();
  setInterval(load, 15000);
})();