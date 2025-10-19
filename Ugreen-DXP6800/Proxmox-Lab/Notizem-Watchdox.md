# IT87 Hardware-Watchdog (it87_wdt) unter Linux einrichten

> ⚠️ Hinweise
>
> - Der Watchdog ist aktuell noch ungetestet!
> - KeepAlive Service
> - Zur Installation sollte der WatchDog ausgeschalten sein im BIOS (per default 3 Minuten) 

---

## 1) Modul zum test laden (sofort)

```bash
modprobe -r it87_wdt || true
modprobe -v it87_wdt
```

---

## 2) Optionen **persistent** setzen (Timeout 1200 s - gleich UGOS)

```bash
tee /etc/modprobe.d/it87_wdt.conf >/dev/null <<'EOF'
options it87_wdt timeout=1200 nowayout=0 testmode=0
EOF

echo it87_wdt | tee /etc/modules-load.d/it87_wdt.conf
```

> 💡 `timeout` in Sekunden. `testmode=0` aktiv, `1` wäre Test (kein echter Reboot).

Damit die neuen Optionen sofort wirken, Modul neu laden:
```bash
modprobe -r it87_wdt || true
modprobe -v it87_wdt
```

---

## 3) systemd Keepalive-Service anlegen

```bash
tee /etc/systemd/system/it87-keepalive.service >/dev/null <<'EOF'
[Unit]
Description=Keepalive for IT87 hardware watchdog
After=multi-user.target
ConditionPathExists=/dev/watchdog1

[Service]
Type=simple
# FD 3 wird geöffnet und für Pings genutzt. Stop sendet "V" (falls MagicClose), dann wird FD geschlossen.
ExecStart=/bin/sh -c 'exec 3>/dev/watchdog1; trap "echo V >&3 2>/dev/null; exec 3>&-; exit 0" TERM INT; while :; do sleep 60; echo . >&3; done'
ExecStop=/bin/sh -c 'echo V >/dev/watchdog1 2>/dev/null || true'
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now it87-keepalive.service
```

---

## 4) Verifizieren

```bash
# Gerät vorhanden?
ls -l /dev/watchdog*

# Service-Status
systemctl status it87-keepalive.service --no-pager

# Kernel-Logs
dmesg | grep -i watchdog

# Effektive Modul-Parameter anzeigen
modinfo -p it87_wdt
```

> ✅ Wenn der Service läuft, wird alle 60 s ein **Ping** an `/dev/watchdog1` gesendet. Bleiben Pings aus und der Timeout (1200 s) läuft ab, rebootet das System automatisch.

---

## 5) Anpassungen

- **Timeout ändern:** `/etc/modprobe.d/it87_wdt.conf` editieren und neu laden:
  ```bash
  sed -i 's/timeout=[0-9]\+/timeout=600/' /etc/modprobe.d/it87_wdt.conf
  modprobe -r it87_wdt && modprobe -v it87_wdt
  systemctl restart it87-keepalive.service
  ```
- **Ping-Intervall:** Im Service `sleep 60` anpassen (z. B. `sleep 30`), danach:
  ```bash
  systemctl daemon-reload
  systemctl restart it87-keepalive.service
  ```

---

## 6) Deaktivieren / Rückbau

```bash
systemctl disable --now it87-keepalive.service
rm -f /etc/systemd/system/it87-keepalive.service
systemctl daemon-reload

modprobe -r it87_wdt || true
rm -f /etc/modprobe.d/it87_wdt.conf /etc/modules-load.d/it87_wdt.conf
# Optional: falls automatisch geladen wurde, Reboot durchführen oder Module ggf. blacklisten.
```

---

## 7) Troubleshooting

- **`/dev/watchdog1` fehlt:** Falscher Device-Index – prüfe `ls -l /dev/watchdog*` und passe die Unit-Datei an (z. B. `/dev/watchdog0`).  
- **Sofortiger Reboot nach Start:** Timeout sehr klein oder kein Keepalive – Service-Status prüfen. Zur Not `nowayout=0` setzen und Service stoppen.  
- **Secure Boot aktiv:** Signierte Module nötig, sonst schlägt `modprobe` ggf. fehl.
