# Interceptor

Skripte, um HTTP/HTTPS-Traffic in einem **eigenen Testnetz** mit mitmproxy transparent abzufangen.

**Wichtig (Rechtliches):**

- Nur in **eigenen Testumgebungen** oder mit **expliziter Zustimmung aller Beteiligten** verwenden.
- Einsatz in fremden Netzen ohne Einwilligung kann strafbar sein.
- Nutzung auf eigene Gefahr, keine Haftung für Schäden oder Datenverlust.

---

## Varianten

- Raspberry-Pi Version: `setup-interceptor.sh`
- Debian 13 LXC / VM Version: `setup-lxc-debia-13-interceptor.sh`

Beide Varianten:

- installieren und konfigurieren mitmproxy / mitmweb im transparenten Modus
- leiten Client-Traffic von Port 80/443 auf Port 8080 um
- stellen ein Web-GUI auf Port 8081 bereit
- erzeugen eine eigene Root-CA, die auf den Clients importiert werden muss

---

## 1. Raspberry Pi – `setup-interceptor.sh`

### Zielsetup

[Client] ))) WLAN ((( [Raspberry Pi] --- [Router / Internet]  
                         wlan0               eth0  

- wlan0 → Access Point (interceptor-WLAN)  
- eth0  → Uplink ins normale LAN / Internet  

### Voraussetzungen

- Raspberry Pi (3/4/5) mit aktuellem Raspberry Pi OS (Debian-basiert)
- Root-Zugriff (`sudo`)
- Internetzugang zum Installieren der Pakete

### Installation

1. Skript auf den Pi kopieren und ausführen:

   ```bash
   chmod +x setup-interceptor.sh
   sudo ./setup-interceptor.sh
   ```

2. Während des Setups werden u. a. abgefragt:

   - SSID des WLANs  
   - WLAN-Passwort  
   - WLAN-Land (z. B. `DE`)  
   - Netzwerkbasis für das Clientnetz (nur /24, z. B. `10.3.7.0`)

3. Das Skript richtet ein:

   - `hostapd` (WLAN-AP auf `wlan0`)  
   - `dnsmasq` (DHCP + DNS für das WLAN-Netz)  
   - `iptables` (NAT und Redirect 80/443 → 8080)  
   - `mitmproxy` / `mitmweb` als systemd-Services  

4. Am Ende werden u. a. ausgegeben:

   - SSID und Netzbereich  
   - IP des Pis im LAN (`eth0`)  
   - URL für mitmweb: `http://<eth0-IP>:8081`  
   - Pfad zur CA: `/var/lib/mitmproxy/mitmproxy-ca-cert.pem`

---

## 2. Debian 13 LXC / VM – `setup-lxc-debia-13-interceptor.sh`

Hinweis: Primär für LXC geschrieben, läuft aber auch in einer normalen Debian-VM.

### Zielsetup

[Client] --- (internes Netz) --- [Debian Interceptor] --- (Uplink) --- [Internet]  
                     eth1                    eth0  

- `eth1` → internes Clientnetz  
- `eth0` → Uplink ins LAN / Internet  

Die beiden Interfaces müssen je nach Hypervisor/Host selbst angelegt werden.

### Voraussetzungen

- Debian 13 (LXC-Container oder VM)
- Zwei Netzwerkschnittstellen: `eth0` (Uplink), `eth1` (intern)
- Root-Zugriff
- Internetzugang zum Installieren der Pakete

### Installation

1. Skript ausführbar machen und starten:

   ```bash
   chmod +x setup-lxc-debia-13-interceptor.sh
   sudo ./setup-lxc-debia-13-interceptor.sh
   ```

2. Abfragen im Skript:

   - interne Schnittstelle (Client-seitig), Default: `eth1`  
   - externe Schnittstelle (Uplink), Default: `eth0`  
   - Netzwerkbasis für Clientnetz (nur /24), z. B. `10.3.7.0`  
     → daraus wird z. B. `10.3.7.1` als Gateway, DHCP-Range `10.3.7.100–200`  
   - mitmweb-Passwort, Default: `int3rC3pTR1#a`  
   - Captive-Portal-Ausnahmen (y/N):  
     - `y` → bestimmte Domains (Google Connectivity Check etc.) werden via `ipset` von der MITM-Interception ausgenommen  
     - `N` oder Enter → alles geht durch den Proxy

3. Das Skript richtet ein:

   - statische IP auf `eth1` (Gateway für das Clientnetz)  
   - `dnsmasq` als DHCP + DNS für das Clientnetz  
   - optional `ipset` + systemd-Service für Captive-Portal-Ausnahmen  
   - `iptables` (NAT über `eth0`, Redirect 80/443 → 8080)  
   - `mitmproxy` / `mitmweb` als systemd-Service mit festem Passwort  

4. Am Ende werden u. a. ausgegeben:

   - Clientnetz (z. B. `10.3.7.0/24`)  
   - Gateway/VM intern (IP auf `eth1`)  
   - Uplink-IP extern (`eth0`)  
   - URL für mitmweb: `http://<eth0-IP>:8081`  
   - Pfad zur CA: `/var/lib/mitmproxy/mitmproxy-ca-cert.pem`  

---

## Clients anbinden

### Raspberry-Pi-Variante

- Mit dem konfigurierten WLAN verbinden.  
- IP per DHCP beziehen (Gateway + DNS = Raspberry Pi).

### Debian-LXC/VM-Variante

- Clientnetz mit `eth1` der VM / des Containers verbinden  
  (z. B. Host-Only-Netz, vSwitch, physischer Port).  
- IP per DHCP beziehen (Gateway + DNS = Interceptor-VM).

---

## CA-Zertifikat installieren

Damit der Client keine Zertifikatswarnungen zeigt, muss die von mitmproxy
erzeugte Root-CA installiert werden.

### Variante A: über `mitm.it`

Auf einem Client im Interceptor-Netz im Browser aufrufen:

```text
http://mitm.it:8080/
```

Dort bietet mitmproxy die CA für verschiedene Plattformen (Windows, macOS, Android, …) an.

### Variante B: direkt aus dem Dateisystem

Auf der Interceptor-Maschine liegt die CA immer hier:

```text
/var/lib/mitmproxy/mitmproxy-ca-cert.pem
```

Diese Datei auf den Client kopieren und als neue vertrauenswürdige
Zertifizierungsstelle importieren.

---

## mitmweb (GUI)

- Läuft standardmäßig auf Port `8081` (HTTP, kein TLS).
- Zeigt in Echtzeit:
  - HTTP-Anfragen/Antworten
  - HTTPS-Verbindungen (wenn CA installiert ist)
  - Header, Bodies, Zertifikatsdetails usw.

### Zugriff

- Raspberry-Pi-Variante:  
  `http://<eth0-IP-des-Pi>:8081`

- Debian LXC / VM:  
  `http://<eth0-IP-der-VM>:8081`  
  Passwort: das im Setup eingegebene (Default: `int3rC3pTR1#a`)

---

## Bekannte Stolpersteine

- CA nicht installiert → Browser zeigt Zertifikatswarnungen.  
- Interfaces vertauscht (`eth0`/`eth1`) → kein Internet oder kein MITM.  
- In LXC-Umgebungen muss IP-Forwarding/NAT ggf. auch auf dem Host erlaubt sein.  
