(function () {
  // --------- Utils ---------
  function classify(name){
    const s = (name||"").toLowerCase();
    if (s.startsWith('clock://')) return 'clock';
    if (s.startsWith('text://'))  return 'text';
    if (s.includes('/stream/text')) return 'text'; // Legacy
    if (s.startsWith('/stream/')) return 'stream';
    if (s.includes('://'))        return 'stream';
    if (/\.(png|jpg|jpeg|gif|bmp|webp)$/.test(s)) return 'image';
    if (/\.(mp4|mov|mkv|avi|webm)$/.test(s))     return 'video';
    return 'other';
  }
  function badgeFor(t){
    return t==='image' ? 'Bild'
         : t==='video' ? 'Video'
         : t==='stream'? 'Stream'
         : t==='clock' ? 'Uhr'
         : t==='text'  ? 'Text'
         : 'Datei';
  }
  function thumbUrl(name){
    const enc = encodeURIComponent(name);
    return `/api/thumb?file=${enc}`;
  }

  // ---- Text helpers (token <-> cfg) ----
    function b64urlEncode(str){
    return btoa(unescape(encodeURIComponent(str))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
    }
    function b64urlDecodeToStr(b64){
    b64 = b64.replace(/-/g,'+').replace(/_/g,'/');
    b64 += '='.repeat((4 - b64.length % 4) % 4);
    return decodeURIComponent(escape(atob(b64)));
    }
    function parseTextFile(file){
    if (!file) return null;
    if (file.startsWith('text://')){
        const tok = file.substring(7);
        try { return { token: tok, cfg: JSON.parse(b64urlDecodeToStr(tok)) }; }
        catch(e){ return { token: tok, cfg: { text:'' } }; }
    }
    if (file.includes('/stream/text')){
        try {
        const q = file.split('?')[1] || '';
        const token = new URLSearchParams(q).get('token') || '';
        const cfg = token ? JSON.parse(b64urlDecodeToStr(token)) : { text:'' };
        return { token, cfg };
        } catch(e){ return { token:'', cfg:{ text:'' } }; }
    }
    return null;
    }
    function buildTextFile(cfg){
    const token = b64urlEncode(JSON.stringify(cfg));
    return 'text://' + token;
    }


  // --------- Drag & Drop ---------
  new Sortable(document.getElementById('items'), {
    handle: '.handle',
    animation: 150
  });

  const $items = $('#items');

  // Initial: Badges, Namen, Thumb-Klasse anhand data-mode
  $items.find('.item-card').each(function(){
    const $c = $(this);
    const f = $c.data('file') || '';
    const t = classify(f);
    $c.find('[data-badge]').text(badgeFor(t));
    $c.find('[data-name]').text(f);
    const mode = ($c.data('mode') === 'fit') ? 'fit' : 'fill';
    $c.find('.thumb').toggleClass('fit', mode==='fit');
    const btns = $c.find('[data-setmode]');
    btns.removeClass('active').filter(`[data-setmode="${mode}"]`).addClass('active');
    if (t==='text' && $c.find('[data-edit-text]').length===0){
      $('<button type="button" class="btn btn-sm btn-outline-primary" data-edit-text>Bearbeiten</button>')
        .insertBefore($c.find('[data-remove]'));
    }
  });

  // --------- Delegierte Events ---------
  $items.on('click', '[data-remove]', function(){
    $(this).closest('.item-card').remove();
  });
  $items.on('click', '[data-adv-toggle]', function(){
    $(this).closest('.item-card').find('.adv').stop(true,true).slideToggle(120);
  });
  $items.on('click', '[data-setmode]', function(e){
    e.preventDefault();
    const m = $(this).data('setmode') === 'fit' ? 'fit' : 'fill';
    const $card = $(this).closest('.item-card');
    $card.attr('data-mode', m);
    const group = $(this).closest('.btn-group');
    group.find('[data-setmode]').removeClass('active');
    $(this).addClass('active');
    $card.find('.thumb').toggleClass('fit', m==='fit');
  });
  $items.on('input',  '[data-duration]', function(){ $(this).closest('.item-card').attr('data-duration', this.value || 0); });
  $items.on('input',  '[data-loop]',     function(){ $(this).closest('.item-card').attr('data-loop', this.value || 1); });
  $items.on('change', '[data-start]',    function(){ $(this).closest('.item-card').attr('data-start', this.value || ''); });
  $items.on('change', '[data-end]',      function(){ $(this).closest('.item-card').attr('data-end', this.value || ''); });

  // --------- Medien hinzufügen ---------
  const media = (window.PL_MEDIA || []);
  const mediaModal = new bootstrap.Modal('#mediaModal');

  function renderMediaGrid(filter){
    const q = (filter || '').toLowerCase();
    const $grid = $('#mediaGrid').empty();
    media
      .filter(name => !q || name.toLowerCase().includes(q))
      .forEach(name => {
        const t = classify(name);
        let thumb = `<div class="thumb placeholder">Datei</div>`;
        if (t==='image') thumb = `<img class="thumb" src="${thumbUrl(name)}" loading="lazy">`;
        else if (t==='video') thumb = `<img class="thumb" src="${thumbUrl(name)}" loading="lazy">`;
        const col = $(`
          <div class="col-6 col-sm-4 col-md-3">
            <div class="card p-2 h-100" role="button">
              ${thumb}
              <div class="small mt-2 text-truncate" title="${name}">${name}</div>
            </div>
          </div>
        `);
        col.on('click', () => {
          addCard({ file: name, mode: 'fill', loop: 1, duration: (t==='image'?10:0) });
          mediaModal.hide();
        });
        $grid.append(col);
      });
  }

  $('[data-add-media]').on('click', function(){
    $('#mediaSearch').val('');
    renderMediaGrid('');
    mediaModal.show();
  });
  $('#mediaSearch').on('input', function(){ renderMediaGrid(this.value); });

  // --------- Stream hinzufügen ---------
  const streamModal = new bootstrap.Modal('#streamModal');
  $('[data-add-stream]').on('click', () => { $('#streamForm')[0].reset(); streamModal.show(); });
  $('#streamForm').on('submit', function(e){
    e.preventDefault();
    const url = this.url.value.trim();
    if (!url) return;
    addCard({ file: url, mode: 'fill', loop: 1, duration: 0 });
    streamModal.hide();
  });

  // --------- Uhr hinzufügen ---------
  const clockModal = new bootstrap.Modal('#clockModal');
  $('[data-add-clock]').on('click', () => { $('#clockForm')[0].reset(); clockModal.show(); });
  $('#clockForm').on('submit', function(e){
    e.preventDefault();
    const variant = this.variant.value;
    const duration = parseInt(this.duration.value || '0', 10) || 0;
    const url = 'clock://' + variant;
    addCard({ file: url, mode: 'fit', loop: 1, duration: duration });
    clockModal.hide();
  });

  // --------- Text: Add & Edit ---------
  const textModal = new bootstrap.Modal('#textModal');
  let editingCard = null;

  $('[data-add-text]').on('click', () => {
    editingCard = null;
    $('#textForm')[0].reset();
    textModal.show();
  });

  $items.on('click', '[data-edit-text]', function(){
    const $card = $(this).closest('.item-card');
    const file = ($card.attr('data-file') || '').trim();
    const parsed = parseTextFile(file) || { cfg:{ text:'', color:'#ffffff', bg:'#000000', font_size:24, speed_px_s:40, align_h:'center', align_v:'middle' } };
    const f = $('#textForm')[0];
    f.text.value       = parsed.cfg.text || '';
    f.color.value      = parsed.cfg.color || '#ffffff';
    f.bg.value         = parsed.cfg.bg || '#000000';
    f.font_size.value  = parseInt(parsed.cfg.font_size || 24, 10);
    f.speed_px_s.value = parseInt(parsed.cfg.speed_px_s || 40, 10);
    f.align_h.value     = (parsed.cfg.align_h || 'center');
    f.align_v.value     = (parsed.cfg.align_v || 'middle');
    editingCard = $card;
    textModal.show();
    });

  $('#textPreview').off('click').on('click', function(){
  const f = $('#textForm')[0];
  const payload = {
      text: (f.text.value || '').trim(),
      color: f.color.value || '#ffffff',
      bg: f.bg.value || '#000000',
      font_size: parseInt(f.font_size.value || '24', 10),
      speed_px_s: parseInt(f.speed_px_s.value || '40', 10),
      align_h: f.align_h.value || 'center',
      align_v: f.align_v.value || 'middle'
  };
  if (!payload.text){ alert('Bitte Text eingeben'); return; }
  const token = b64urlEncode(JSON.stringify(payload));
  window.open('/stream/text?token=' + token, '_blank');
  });
  
  $('#textForm').off('submit').on('submit', function(e){
  e.preventDefault();
  const f = this;
  const payload = {
      text: (f.text.value || '').trim(),
      color: f.color.value || '#ffffff',
      bg: f.bg.value || '#000000',
      font_size: parseInt(f.font_size.value || '24', 10),
      speed_px_s: parseInt(f.speed_px_s.value || '40', 10),
      align_h: f.align_h.value || 'center',
      align_v: f.align_v.value || 'middle'
  };
  if (!payload.text) return;
  
  const file = buildTextFile(payload);
  
  if (editingCard){
      const $card = editingCard;
      $card.attr('data-file', file);
      $card.find('[data-name]').text(file);
      const img = $card.find('.thumb');
      const url = '/api/thumb?file=' + encodeURIComponent(file);
      if (img.length){ img.attr('src', url); }
      else {
      $card.find('.thumb-wrap').empty().append(
          `<img class="thumb ${$card.attr('data-mode')==='fit'?'fit':''}" src="${url}" loading="lazy">`
      );
      }
      $card.find('[data-badge]').text('Text');
      editingCard = null;
  } else {
      addCard({ file: file, mode: 'fit', loop: 1, duration: 6 });
  }
  
  bootstrap.Modal.getInstance(document.getElementById('textModal'))?.hide();
  ajaxSave();
  });

  // --------- Leeres Element ---------
  $('[data-add]').on('click', () => addCard({ file: '', mode: 'fill', loop: 1, duration: 0 }));

  // --------- Card-Builder ---------
  function addCard(item){
    const t = classify(item.file);
    const isLocal = item.file && !item.file.includes('://');
    const thumb =
      t==='image' && isLocal ? `<img class="thumb ${item.mode==='fit'?'fit':''}" src="${thumbUrl(item.file)}" loading="lazy">` :
      t==='video' && isLocal ? `<img class="thumb ${item.mode==='fit'?'fit':''}" src="${thumbUrl(item.file)}" loading="lazy">` :
      t==='clock'            ? `<div class="thumb placeholder">Uhr</div>` :
      t==='text'             ? `<img class="thumb ${item.mode==='fit'?'fit':''}" src="${thumbUrl(item.file)}" loading="lazy">` :
                               `<div class="thumb placeholder">${t==='stream'?'Stream':'Datei'}</div>`;

    const $card = $(`
      <div class="card item-card p-2"
           data-file="${item.file||''}"
           data-mode="${item.mode||'fill'}"
           data-loop="${item.loop||1}"
           data-duration="${item.duration||0}"
           data-start="${item.start||''}"
           data-end="${item.end||''}">
        <div class="d-flex gap-3 align-items-center">
          <span class="handle px-2">☰</span>
          <div class="thumb-wrap">${thumb}</div>
          <div class="flex-grow-1">
            <div class="d-flex flex-wrap align-items-center gap-2">
              <span class="badge rounded-pill text-bg-secondary badge-type">${badgeFor(t)}</span>
              <code class="small text-break code-wrap" data-name></code>
            </div>
            <div class="mt-2 d-flex flex-wrap align-items-center gap-2">
              <div class="btn-group btn-group-sm" role="group">
                <button type="button" class="btn btn-outline-primary ${item.mode==='fit'?'':'active'}" data-setmode="fill">Cover</button>
                <button type="button" class="btn btn-outline-primary ${item.mode==='fit'?'active':''}" data-setmode="fit">Fit</button>
              </div>
              <button type="button" class="btn btn-sm btn-outline-secondary" data-adv-toggle>Erweitert</button>
              ${t==='text' ? '<button type="button" class="btn btn-sm btn-outline-primary" data-edit-text>Bearbeiten</button>' : ''}
              <button type="button" class="btn btn-sm btn-outline-danger" data-remove>Entfernen</button>
            </div>
            <div class="adv mt-2">
              <div class="row g-2">
                <div class="col-6 col-sm-3">
                  <label class="form-label small">Dauer (s, Bild/Stream)</label>
                  <input type="number" class="form-control form-control-sm" min="0" value="${item.duration||0}" data-duration>
                </div>
                <div class="col-6 col-sm-3">
                  <label class="form-label small">Loop</label>
                  <input type="number" class="form-control form-control-sm" min="1" value="${item.loop||1}" data-loop>
                </div>
                <div class="col-12 col-sm-3">
                  <label class="form-label small">Start</label>
                  <input type="datetime-local" class="form-control form-control-sm" value="${item.start||''}" data-start>
                </div>
                <div class="col-12 col-sm-3">
                  <label class="form-label small">Ende</label>
                  <input type="datetime-local" class="form-control form-control-sm" value="${item.end||''}" data-end>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>`);

    $card.find('[data-name]').text(item.file||'');
    $('#items').append($card);
  }

  // --------- SAVE: Hidden-Felder füllen & AJAX an ?json=1 ---------
  async function ajaxSave(){
    const form = document.getElementById('pl-form');
    const $hid = $('#hidden').empty();

    $('#items .item-card').each(function(){
      const el = $(this);
      const file = (el.attr('data-file') || el.find('[data-name]').text() || '').trim();
      if (!file) return;
      const mode      = (el.attr('data-mode') || 'fill');
      const loopVal   = (el.find('[data-loop]').val()     ?? el.attr('data-loop')     ?? 1);
      const durVal    = (el.find('[data-duration]').val() ?? el.attr('data-duration') ?? 0);
      const startVal  = (el.find('[data-start]').val()    ?? el.attr('data-start')    ?? '');
      const endVal    = (el.find('[data-end]').val()      ?? el.attr('data-end')      ?? '');

      function add(n,v){ $hid.append(`<input type="hidden" name="${n}[]" value="${String(v ?? '')}">`); }
      add('file', file);
      add('mode', mode);
      add('loop', (loopVal === '' ? 1 : loopVal));
      add('duration', (durVal === '' ? 0 : durVal));
      add('start', startVal);
      add('end', endVal);
    });

    const fd = new FormData(form);
    try{
      const res = await fetch(form.action + '?json=1', {
        method: 'POST',
        body: fd,
        headers: { 'Accept': 'application/json' }
      });
      if (!res.ok){
        const t = await res.text();
        alert('Fehler beim Speichern: ' + t);
        return;
      }
      const toastEl = document.getElementById('saveToast');
      if (toastEl){
        new bootstrap.Toast(toastEl, {delay: 1500}).show();
      }
    }catch(err){
      alert('Netzwerkfehler: ' + err);
    }
  }

  // Intercept normaler Submit -> AJAX
  $('#pl-form').on('submit', function(e){
    e.preventDefault();
    ajaxSave();
  });
})();
