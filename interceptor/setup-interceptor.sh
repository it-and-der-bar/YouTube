#!/bin/bash
set -euo pipefail
clear

# Logo und Banner anzeigen
if [ -f logo ]; then
  cat logo
else
  echo "-------------------------------------------------------------"
  echo " ACHTUNG: Dieses Skript wird ohne jegliche Gewähr bereitgestellt."
  echo " Es wird keine Haftung für eventuelle Schäden oder Fehlkonfigurationen übernommen."
  echo "-------------------------------------------------------------"
fi

# ── Abfrage der Konfiguration ────────────────────────────────
read -rp "SSID für WLAN (default: InterceptorAP): " SSID
SSID=${SSID:-InterceptorAP}

read -rp "Passwort für WLAN (min. 8 Zeichen, default: geheim123): " WLAN_PASS
WLAN_PASS=${WLAN_PASS:-geheim123}

if [ "${#WLAN_PASS}" -lt 8 ]; then
  echo "Passwort muss mind. 8 Zeichen haben."
  exit 1
fi

read -rp "WLAN-Land (2 Buchstaben, z.B. DE): " WLAN_COUNTRY
WLAN_COUNTRY=${WLAN_COUNTRY:-DE}

if ! [[ "$WLAN_COUNTRY" =~ ^[A-Z]{2}$ ]]; then
  echo "Ungültiger Ländercode. Beispiel: DE"
  exit 1
fi

read -rp "Netzwerkbasis für WLAN-Clients (nur /24, z.B. 10.3.7.0): " WLAN_BASE
WLAN_BASE=${WLAN_BASE:-10.3.7.0}

# /24-Validierung
if [[ ! $WLAN_BASE =~ ^([0-9]{1,3}\.){3}0$ ]]; then
  echo "Ungültiger Netzwerkbereich. Format z.B. 10.3.7.0 (nur /24 erlaubt)."
  exit 1
fi

# ── Abgeleitete Werte ────────────────────────────────────────
WLAN_IFACE=wlan0
ETH_IFACE=eth0
WLAN_NET="$WLAN_BASE/24"
WLAN_IP=${WLAN_BASE%0}1
DHCP_RANGE_START=${WLAN_BASE%0}100
DHCP_RANGE_END=${WLAN_BASE%0}200

# Datei für Environment (für systemd-Units nutzbar)
ENV_FILE="/etc/default/wlan-ap.env"
echo "WLAN_COUNTRY=$WLAN_COUNTRY" > "$ENV_FILE"

# ── Pakete ───────────────────────────────────────────────────
apt update
DEBIAN_FRONTEND=noninteractive apt install -y \
  hostapd dnsmasq iptables-persistent netfilter-persistent dhcpcd5 \
  python3-pip libffi-dev libssl-dev

pip3 install --break-system-packages --upgrade mitmproxy

# hostapd evtl. entmaskieren
systemctl unmask hostapd || true

# ── WLAN-Entsperrung sehr früh im Boot ───────────────────────
cat >/etc/systemd/system/unblock-wifi.service <<'EOF'
[Unit]
Description=Unblock WiFi on boot
DefaultDependencies=no
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock wifi

[Install]
WantedBy=sysinit.target
EOF

systemctl daemon-reload
systemctl enable unblock-wifi.service

# ── hostapd-Startreihenfolge + Regdom aus ENV ────────────────
mkdir -p /etc/systemd/system/hostapd.service.d
cat >/etc/systemd/system/hostapd.service.d/override.conf <<'EOF'
[Unit]
After=network-online.target sys-subsystem-net-devices-${WLAN_IFACE}.device dhcpcd.service
Wants=network-online.target dhcpcd.service
ConditionPathExists=/sys/class/net/wlan0

[Service]
EnvironmentFile=-/etc/default/wlan-ap.env
# Sicherstellen, dass nichts blockiert und das IF up ist
ExecStartPre=/usr/sbin/rfkill unblock wifi
ExecStartPre=/sbin/ip link set wlan0 up
# WICHTIG: Variablenexpansion erzwingen über /bin/sh -c
ExecStartPre=/bin/sh -c '/sbin/iw reg set "${WLAN_COUNTRY:-DE}"'
Restart=on-failure
RestartSec=1s
EOF

systemctl daemon-reload

# ── Dienste stoppen (für Neu-Konfig) ─────────────────────────
systemctl stop hostapd || true
systemctl stop dnsmasq || true

# ── dhcpcd: statische IP für wlan0 ───────────────────────────
# (überschreibt bewusst, Backup anlegen)
if [ -f /etc/dhcpcd.conf ]; then cp /etc/dhcpcd.conf /etc/dhcpcd.conf.bak.$(date +%s); fi
cat >/etc/dhcpcd.conf <<EOF
interface ${WLAN_IFACE}
static ip_address=${WLAN_IP}/24
nohook wpa_supplicant
EOF

systemctl enable dhcpcd
systemctl restart dhcpcd


# ── dnsmasq: DHCP + ipset-Befüllung ──────────────────────────
cp /etc/dnsmasq.conf /etc/dnsmasq.conf.bak.$(date +%s) 2>/dev/null || true
cat >/etc/dnsmasq.conf <<EOF
interface=${WLAN_IFACE}
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},255.255.255.0,24h
domain-needed
bogus-priv
# ipset-Zuordnung für Captive-Portal-Domains (Android/Google/Samsung)
conf-dir=/etc/dnsmasq.d/,*.conf
EOF

mkdir -p /etc/dnsmasq.d
cat >/etc/dnsmasq.d/10-ipset-capport.conf <<'EOF'
# Alle IPs dieser Domains landen im ipset "capport4"
ipset=/connectivitycheck.gstatic.com/capport4
ipset=/clients3.google.com/capport4
ipset=/www.google.com/capport4
ipset=/www.google.eu/capport4
# Samsung/OneUI (conn-service/allawnos)
ipset=/allawnos.com/capport4
ipset=/conn-service-eu-04.allawnos.com/capport4
ipset=/conn-service-eu-05.allawnos.com/capport4
EOF

# ── ipset: Set beim Boot anlegen (vor dnsmasq & iptables) ───
cat >/etc/systemd/system/ipset-capport.service <<'EOF'
[Unit]
Description=Create ipset for captive-portal exemptions
DefaultDependencies=no
Before=netfilter-persistent.service dnsmasq.service
Wants=dnsmasq.service

[Service]
Type=oneshot
ExecStart=/sbin/ipset create capport4 hash:ip family inet -exist

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ipset-capport.service
systemctl start ipset-capport.service

# ── hostapd konfigurieren ────────────────────────────────────
mkdir -p /etc/hostapd
cat >/etc/hostapd/hostapd.conf <<EOF
interface=${WLAN_IFACE}
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=7
wmm_enabled=0
auth_algs=1
wpa=2
wpa_passphrase=${WLAN_PASS}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
country_code=${WLAN_COUNTRY}
ieee80211d=1
ieee80211n=1
EOF

echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >/etc/default/hostapd

# ── IP-Forwarding aktivieren ─────────────────────────────────
if grep -q "^#\?net.ipv4.ip_forward" /etc/sysctl.conf; then
  sed -i 's/^#\?net.ipv4.ip_forward=.*/net.ipv4.ip_forward=1/' /etc/sysctl.conf
else
  echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
fi
sysctl -p


# ── NAT + MITM-Redirects (mit capport-Ausnahme) ─────────────
iptables -t nat -I PREROUTING 1 -i ${WLAN_IFACE} -p tcp -m set --match-set capport4 dst \
  -m multiport --dports 80,443 -j RETURN

# NAT über erkanntes Upstream-Interface
[ -n "${UP_IF:-}" ] && iptables -t nat -A POSTROUTING -o "${UP_IF}" -j MASQUERADE

# Transparente Umleitung auf mitmproxy (Port 8080)
iptables -t nat -A PREROUTING -i ${WLAN_IFACE} -p tcp --dport 80  -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -i ${WLAN_IFACE} -p tcp --dport 443 -j REDIRECT --to-port 8080

netfilter-persistent save

# ── DNS (optional) ───────────────────────────────────────────
echo "nameserver 9.9.9.9" > /etc/resolv.conf || true

# ── mitmweb: Systemnutzer + Service ──────────────────────────
id -u mitm >/dev/null 2>&1 || useradd --system --no-create-home --group nogroup mitm
mkdir -p /var/lib/mitmproxy
chown -R mitm:nogroup /var/lib/mitmproxy

cat >/etc/systemd/system/mitmweb.service <<'EOF'
[Unit]
Description=Transparent MITMWeb Proxy
After=network.target

[Service]
ExecStart=/usr/local/bin/mitmweb \
  --mode transparent \
  --showhost \
  --web-port 8081 \
  --listen-host 0.0.0.0 --web-host 0.0.0.0 \
  --ssl-insecure \
  --set confdir=/var/lib/mitmproxy
User=mitm
Group=nogroup
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# ── Dienste aktivieren & starten ─────────────────────────────
systemctl daemon-reload
systemctl enable hostapd
systemctl enable dnsmasq
systemctl enable mitmweb.service
systemctl start hostapd
systemctl start dnsmasq
systemctl start mitmweb.service

# ── Abschluss ────────────────────────────────────────────────
AP_IP_ETH=$(ip -4 addr show ${ETH_IFACE} | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 || true)
echo
echo "WLAN-SSID: ${SSID} | Netzwerk: ${WLAN_NET}"
echo "AP-IP (wlan0): ${WLAN_IP}"
echo "mitmweb:       http://${AP_IP_ETH:-<deine-LAN-IP>}:8081"


AP_IP_ETH=$(ip -4 addr show ${ETH_IFACE} | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 || true)

echo
echo "WLAN-SSID: ${SSID} | Netzwerk: ${WLAN_NET}"
echo "AP-IP (wlan0): ${WLAN_IP}"
echo "LAN-IP (${ETH_IFACE}): ${AP_IP_ETH:-<nicht verbunden>}"
echo "mitmweb erreichbar unter: http://${AP_IP_ETH:-<deine-LAN-IP>}:8081"
echo "mitmproxy CA-Zertifikat:  /var/lib/mitmproxy/mitmproxy-ca-cert.pem"