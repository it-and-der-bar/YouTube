# Ugreen-LEDs unter Proxmox (Debian *trixie* - geht vmtl. auch mit *bookworm*) einrichten

Diese Anleitung fasst den Ablauf grob zusammen, kann aber sein das noch was fehlt.

> ⚠️ **Hinweise & Voraussetzungen**
>
> - Backup empfohlen (insb. vor `apt full-upgrade`).
> - Stelle sicher, dass die **Kernel-Headers** zur laufenden Kernelversion installiert sind.
> - Bei **Secure Boot** müssen Fremd-Kernelmodule signiert werden oder Secure Boot deaktiviert sein.
> - Ersetze `<NIC>` unten durch dein Netz-Interface (z. B. `enp89s0`).

---

## 1) Proxmox-Quellen nach frischer Proxmox Installation umstellen

**Enterprise Quellen entfernen:**
```bash
rm /etc/apt/sources.list.d/ceph.sources
rm /etc/apt/sources.list.d/pve-enterprise.sources
```

**Proxmox-Keyring einrichten:**
```bash
install -d /usr/share/keyrings
wget -O /usr/share/keyrings/proxmox-archive-keyring.gpg   https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg
```

**No-Subscription-Repo hinzufügen:**
```bash
cat >/etc/apt/sources.list.d/proxmox.sources <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
```

**System aktualisieren & Basis-Pakete:**
```bash
apt update && apt full-upgrade -y
apt install -y git build-essential i2c-tools pve-headers pve-headers-$(uname -r) smartmontools
```

> ✅ **Prüfen:**  
> `uname -r` und `dpkg -l | grep -E '^ii\s+pve-headers'` – stimmen Kernel & Header überein?

---

## 2) Ugreen-LEDs Controller klonen & CLI bauen

```bash
cd /opt
git clone https://github.com/miskcoo/ugreen_leds_controller
cd ugreen_leds_controller/cli
make
```

**Schnelltest (CLI):**
```bash
cd /opt/ugreen_leds_controller/cli
./ugreen_leds_cli all   -off -status
./ugreen_leds_cli power -color 255 0 255 -blink 400 600 
sleep 0.1
./ugreen_leds_cli netdev -color 255 0 0   -blink 400 600
sleep 0.1
./ugreen_leds_cli disk1  -color 255 255 0 -blink 400 600
sleep 0.1
./ugreen_leds_cli disk2  -color 0 255 0   -blink 400 600
sleep 0.1
./ugreen_leds_cli disk3  -color 0 255 255 -blink 400 600
sleep 0.1
./ugreen_leds_cli disk4  -color 0 0 255   -blink 400 600
```

> ℹ️ **Farben & Blink:** `-color R G B` in 0–255, `-blink on_ms off_ms` in Millisekunden.

---

## 3) Kernelmodul bauen & laden

```bash
cd /opt/ugreen_leds_controller/kmod
make
insmod led-ugreen.ko
install -D -m 0644 *.ko /lib/modules/$(uname -r)/extra/led-ugreen.ko
depmod -a
modprobe -v led-ugreen
```

**LED-Probe testen:**
```bash
cd ../scripts
./ugreen-probe-leds
```

> ✅ **Prüfen:** `lsmod | grep ugreen` und `dmesg | tail -n 50` auf Fehlermeldungen.

---

## 4) Module & Trigger beim Boot laden

```bash
cat > /etc/modules-load.d/ugreen-led.conf << 'EOF'
i2c-dev
led-ugreen
ledtrig-oneshot
ledtrig-netdev
EOF
```

---

## 5) Skripte, Config & systemd-Services installieren

```bash
# Kopieren
scripts=(ugreen-diskiomon ugreen-netdevmon ugreen-probe-leds ugreen-power-led)
for f in "${scripts[@]}"; do
    chmod +x "scripts/$f"
    cp "scripts/$f" /usr/bin
done

cp scripts/ugreen-leds.conf /etc/ugreen-leds.conf
cp scripts/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
```

**Dienste starten (Interface anpassen!):**
```bash
# <NIC> z.B. enp89s0
systemctl start ugreen-netdevmon@<NIC>
systemctl start ugreen-diskiomon
systemctl start ugreen-power-led
```

**Autostart aktivieren:**
```bash
systemctl enable ugreen-netdevmon@<NIC>
systemctl enable ugreen-diskiomon
systemctl enable ugreen-power-led
```

> ✅ **Prüfen:**  
> `systemctl status ugreen-netdevmon@<NIC> ugreen-diskiomon ugreen-power-led --no-pager`  
> `journalctl -u ugreen-* -b --no-pager`

---

## 6) Konfiguration anpassen

**Datei:** `/etc/ugreen-leds.conf`  
Hier konfigurierst du u. a. Farben, Schwellen, Devices und Trigger. Nach Änderungen:

```bash
systemctl restart ugreen-netdevmon@<NIC>
systemctl restart ugreen-diskiomon
systemctl restart ugreen-power-led
```

---

## 7) Nützliche Zusatz-Checks

- **I²C-Bus vorhanden?**
  ```bash
  i2cdetect -l
  i2cdetect -y 0   # oder anderer Bus (vorsichtig, nur lesen!)
  ```
- **Netz-Interface bestätigen:**
  ```bash
  ip -br link
  ```

---

## 8) Troubleshooting

- **Modul lädt nicht / “Unknown symbol”:**
  - Prüfe, ob `pve-headers-$(uname -r)` installiert ist.
  - Modul neu bauen nach Kernel-Update:  
    ```bash
    cd /opt/ugreen_leds_controller/kmod
    make clean && make
    install -D -m 0644 *.ko /lib/modules/$(uname -r)/extra/led-ugreen.ko
    depmod -a && modprobe -v led-ugreen
    ```
- **Secure Boot aktiv:** Modul signieren oder Secure Boot deaktivieren.
- **Dienste starten, aber keine LED-Änderung:**
  - `ugreen-probe-leds` ausführen, um Grundfunktion zu prüfen.
  - Schnittstellennamen `<NIC>` korrekt? (`ip -br link`)
  - Rechte/Ownership der Skripte prüfen (`chmod +x /usr/bin/ugreen-*`).
---

## 9) Rückbau / Deinstallation

```bash
# Dienste stoppen & deaktivieren
systemctl disable --now ugreen-netdevmon@<NIC> ugreen-diskiomon ugreen-power-led

# Skripte & Units entfernen
rm -f /usr/bin/ugreen-diskiomon /usr/bin/ugreen-netdevmon /usr/bin/ugreen-probe-leds /usr/bin/ugreen-power-led
rm -f /etc/systemd/system/ugreen-*.service
systemctl daemon-reload

# Config & Autoload entfernen
rm -f /etc/ugreen-leds.conf
rm -f /etc/modules-load.d/ugreen-led.conf

# Kernelmodul entfernen
modprobe -r led-ugreen || true
rm -f /lib/modules/$(uname -r)/extra/led-ugreen.ko
depmod -a
```

---

## 10) Kurze FAQ / Best Practices

- **Welche LED-Namen gibt es?**  
  Meist `power`, `netdev`, `disk1`…`disk4` (abhängig vom Modell). `ugreen-probe-leds` listet und testet sie.
- **Was bedeuten die Blinkwerte?**  
  `-blink 400 600` = 400 ms an, 600 ms aus → 1 Hz mit 40 % Duty-Cycle.
- **Farben konsistent halten:**  
  Z. B. **Grün** = OK, **Gelb** = Aktivität, **Rot** = Fehler/Alarm, **Cyan/Magenta/Blau** für Rollen.
