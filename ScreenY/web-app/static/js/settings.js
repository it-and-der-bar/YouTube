document.addEventListener("DOMContentLoaded", async () => {
  // Roots laden
  try {
    const r = await fetch("/api/importer/roots");
    const { roots=[] } = await r.json();
    const sel = document.getElementById("imp_root");
    roots.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p; opt.textContent = p;
      sel.appendChild(opt);
    });
  } catch {}

  // Scan starten
  document.getElementById("imp_scan").addEventListener("click", async () => {
    const root = document.getElementById("imp_root").value;
    const recursive = document.getElementById("imp_recursive").checked;
    const box = document.getElementById("imp_results");
    box.innerHTML = `<div class="text-muted">Suche läuft…</div>`;
    try {
      const r = await fetch(`/api/importer/scan?root=${encodeURIComponent(root)}&recursive=${recursive}`);
      const data = await r.json();
      const files = data.files || [];
      if (!files.length) {
        box.innerHTML = `<div class="alert alert-info">Keine passenden Playlisten gefunden.</div>`;
        return;
      }
      box.innerHTML = files.map(f => `
        <div class="d-flex align-items-center justify-content-between border rounded p-2 mb-2">
          <code class="small text-truncate" style="max-width:60%">${f}</code>
          <div class="d-flex gap-2 align-items-center">
            <input type="text" class="form-control form-control-sm" style="width:240px"
                   value="${(f.split(/[\\/]/).pop()||'').replace(/\.(txt|plt|npl)$/i,'')}">
            <button class="btn btn-sm btn-primary" data-path="${f}">Importieren</button>
          </div>
        </div>
      `).join("");

      // Import-Buttons verdrahten
      box.querySelectorAll("button.btn-primary").forEach(btn => {
        btn.addEventListener("click", async () => {
          const row = btn.closest(".d-flex");
          const name = row.querySelector("input").value.trim();
          btn.disabled = true; btn.textContent = "Importiere…";
          try {
            const r = await fetch("/api/importer/import", {
              method: "POST",
              headers: {"Content-Type":"application/json"},
              body: JSON.stringify({ path: btn.dataset.path, name })
            });
            const res = await r.json();
            if (res.ok) {
              btn.classList.replace("btn-primary","btn-success");
              btn.textContent = `OK (${res.items} Items)`;
            } else {
              btn.classList.replace("btn-primary","btn-danger");
              btn.textContent = "Fehler";
              alert(res.error || "Import fehlgeschlagen");
            }
          } catch (e) {
            btn.classList.replace("btn-primary","btn-danger");
            btn.textContent = "Fehler";
          } finally {
            btn.disabled = false;
          }
        });
      });
    } catch (e) {
      box.innerHTML = `<div class="alert alert-danger">Scan fehlgeschlagen.</div>`;
    }
  });
});
