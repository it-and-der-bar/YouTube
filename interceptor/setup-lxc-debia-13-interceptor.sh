#!/bin/bash
set -euo pipefail
clear

echo "-------------------------------------------------------------"
echo " ACHTUNG: Dieses Skript wird ohne jegliche Gewähr bereitgestellt."
echo " Es wird keine Haftung für eventuelle Schäden oder Fehlkonfigurationen übernommen."
echo " Nur in Testumgebungen / mit expliziter Zustimmung der Nutzer verwenden."
echo "-------------------------------------------------------------"
echo

# ── Konfiguration abfragen ────────────────────────────────────
read -rp "Interne Schnittstelle (Client-seitig) [eth1]: " INT_IFACE
INT_IFACE=${INT_IFACE:-eth1}

read -rp "Externe Schnittstelle (Uplink) [eth0]: " EXT_IFACE
EXT_IFACE=${EXT_IFACE:-eth0}

read -rp "Netzwerkbasis für Clientnetz (nur /24) [10.3.7.0]: " LAN_BASE
LAN_BASE=${LAN_BASE:-10.3.7.0}

# /24-Validierung (x.x.x.0)
if [[ ! "$LAN_BASE" =~ ^([0-9]{1,3}\.){3}0$ ]]; then
  echo "Ungültiger Netzwerkbereich. Beispiel: 10.3.7.0 oder 192.168.199.0 (nur /24 erlaubt)."
  exit 1
fi

read -rp "mitmweb Passwort [int3rC3pTR1#a]: " MITM_PASSWORD
MITM_PASSWORD=${MITM_PASSWORD:-int3rC3pTR1#a}

read -rp "Captive-Portal-Check-Domains (Google Connectivity Check etc. | Buggy!) von MITM ausnehmen? [y/N]: " CAPPORT
CAPPORT=${CAPPORT:-N}
USE_CAPPORT_IPSET=false
if [[ "$CAPPORT" =~ ^[YyJj]$ ]]; then
  USE_CAPPORT_IPSET=true
fi

LAN_NET="${LAN_BASE}/24"
LAN_IP=${LAN_BASE%0}1
DHCP_RANGE_START=${LAN_BASE%0}100
DHCP_RANGE_END=${LAN_BASE%0}200

echo
echo "Interne Schnittstelle: ${INT_IFACE}"
echo "Externe Schnittstelle: ${EXT_IFACE}"
echo "Client-Netz:           ${LAN_NET}"
echo "Gateway-IP (VM):       ${LAN_IP}"
echo "DHCP-Range:            ${DHCP_RANGE_START} - ${DHCP_RANGE_END}"
echo "mitmweb Passwort:      ${MITM_PASSWORD}"
if $USE_CAPPORT_IPSET; then
  echo "Captive-Portal-Ausnahmen: AKTIV (Google & Co. gehen nicht durch MITM)"
else
  echo "Captive-Portal-Ausnahmen: AUS (alles geht durch MITM)"
fi
echo

read -rp "Passt das so? [ENTER = ja, CTRL+C = abbrechen] " _dummy

# ── Pakete installieren ───────────────────────────────────────
apt update
DEBIAN_FRONTEND=noninteractive apt install -y \
  dnsmasq ipset iptables-persistent netfilter-persistent python3-pip \
  libffi-dev libssl-dev iproute2

# mitmproxy / mitmweb
pip3 install --break-system-packages --upgrade mitmproxy

# ── Interne Schnittstelle direkt jetzt konfigurieren ──────────
ip addr flush dev "${INT_IFACE}" || true
ip addr add "${LAN_IP}/24" dev "${INT_IFACE}"
ip link set "${INT_IFACE}" up

# ── Statische IP für internes Interface (per systemd-Service) ─
cat >/etc/systemd/system/${INT_IFACE}-static.service <<EOF
[Unit]
Description=Static IP for interception LAN on ${INT_IFACE}
After=network.target

[Service]
Type=oneshot
ExecStart=/sbin/ip addr flush dev ${INT_IFACE}
ExecStart=/sbin/ip addr add ${LAN_IP}/24 dev ${INT_IFACE}
ExecStart=/sbin/ip link set ${INT_IFACE} up
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${INT_IFACE}-static.service
systemctl restart ${INT_IFACE}-static.service

# ── dnsmasq konfigurieren (DHCP + DNS + optional ipset) ───────
if [ -f /etc/dnsmasq.conf ]; then
  cp /etc/dnsmasq.conf /etc/dnsmasq.conf.bak.$(date +%s)
fi

CAPPORT_DNSMASQ=""
if $USE_CAPPORT_IPSET; then
  CAPPORT_DNSMASQ=$(cat <<'EOF'
# Captive-Portal-Ausnahmen via ipset (capport4)
ipset=/connectivitycheck.gstatic.com/capport4
ipset=/clients3.google.com/capport4
ipset=/www.google.com/capport4
ipset=/www.google.eu/capport4
# Samsung/OneUI (conn-service/allawnos)
ipset=/allawnos.com/capport4
ipset=/conn-service-eu-04.allawnos.com/capport4
ipset=/conn-service-eu-05.allawnos.com/capport4
EOF
)
fi

cat >/etc/dnsmasq.conf <<EOF
# Basis-Konfiguration für Interceptor-VM
interface=${INT_IFACE}
bind-interfaces

# DHCP für Clientnetz
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},12h
dhcp-option=3,${LAN_IP}   # Default-Gateway
dhcp-option=6,${LAN_IP}   # DNS-Server

# Eigener DNS, der nach draußen weiterleitet
server=9.9.9.9
server=1.1.1.1

# mitmproxy-Zertifikatsseite lokal auflösen
address=/mitm.it/${LAN_IP}

${CAPPORT_DNSMASQ}
EOF

# ── ipset-Setup / Cleanup ─────────────────────────────────────
if $USE_CAPPORT_IPSET; then
  # Service anlegen/aktivieren
  cat >/etc/systemd/system/ipset-capport.service <<'EOF'
[Unit]
Description=Create ipset for captive-portal exemptions
DefaultDependencies=no
Before=netfilter-persistent.service dnsmasq.service
Wants=dnsmasq.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/ipset create capport4 hash:ip family inet -exist

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable ipset-capport.service
  systemctl start ipset-capport.service

else
  # Falls vorher aktiv: Service stoppen/deaktivieren und Set löschen
  if systemctl list-unit-files | grep -q '^ipset-capport.service'; then
    systemctl stop ipset-capport.service || true
    systemctl disable ipset-capport.service || true
    rm -f /etc/systemd/system/ipset-capport.service
    systemctl daemon-reload
  fi

  if ipset list 2>/dev/null | grep -q '^Name: capport4'; then
    ipset destroy capport4 || true
  fi
fi

# ── IP-Forwarding aktivieren ──────────────────────────────────
if grep -q "^#\?net.ipv4.ip_forward" /etc/sysctl.conf; then
  sed -i 's/^#\?net.ipv4.ip_forward=.*/net.ipv4.ip_forward=1/' /etc/sysctl.conf
else
  echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
fi
sysctl -p

# ── iptables: NAT + MITM-Redirects + Forwarding ──────────────
# alles flushen, damit alte capport-Regeln garantiert weg sind
iptables -t nat -F
iptables -F

# optionale capport-Ausnahme
if $USE_CAPPORT_IPSET; then
  iptables -t nat -I PREROUTING 1 -i "${INT_IFACE}" -p tcp \
    -m set --match-set capport4 dst \
    -m multiport --dports 80,443 -j RETURN
fi

# NAT über externe Schnittstelle
iptables -t nat -A POSTROUTING -o "${EXT_IFACE}" -j MASQUERADE

# Transparente Umleitung auf mitmproxy (Port 8080)
iptables -t nat -A PREROUTING -i "${INT_IFACE}" -p tcp --dport 80  -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -i "${INT_IFACE}" -p tcp --dport 443 -j REDIRECT --to-port 8080

# Forwarding-Regeln
iptables -A FORWARD -i "${INT_IFACE}" -o "${EXT_IFACE}" -j ACCEPT
iptables -A FORWARD -i "${EXT_IFACE}" -o "${INT_IFACE}" -m state --state ESTABLISHED,RELATED -j ACCEPT

# Regeln persistent speichern
netfilter-persistent save

# ── Optional: Resolver der VM setzen (Upstream) ───────────────
echo "nameserver 9.9.9.9" > /etc/resolv.conf || true

# ── mitmweb: Systemnutzer + Service ───────────────────────────
id -u mitm >/dev/null 2>&1 || useradd --system --no-create-home --group nogroup mitm

mkdir -p /var/lib/mitmproxy
chown -R mitm:nogroup /var/lib/mitmproxy

cat >/etc/systemd/system/mitmweb.service <<EOF
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
  --set confdir=/var/lib/mitmproxy \
  --set web_password=${MITM_PASSWORD}
User=mitm
Group=nogroup
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# ── Dienste aktivieren & starten ──────────────────────────────
systemctl daemon-reload
systemctl enable dnsmasq
systemctl enable mitmweb.service
systemctl restart dnsmasq
systemctl restart mitmweb.service

# ── Abschluss / Infos ausgeben ────────────────────────────────
AP_IP_INT=$(ip -4 addr show "${INT_IFACE}" | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 || true)
AP_IP_EXT=$(ip -4 addr show "${EXT_IFACE}" | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 || true)

echo
echo "----------------------------------------------"
echo " Interceptor-Setup abgeschlossen"
echo "----------------------------------------------"
echo "Client-Netz:        ${LAN_NET}"
echo "Gateway/VM intern:  ${AP_IP_INT:-$LAN_IP}"
echo "Uplink-IP extern:   ${AP_IP_EXT:-unbekannt}"
echo
echo "mitmweb GUI:        http://${AP_IP_EXT:-<EXT-IP-unbekannt>}:8081"
echo "mitmweb Passwort:   ${MITM_PASSWORD}"
echo "mitmproxy CA:       /var/lib/mitmproxy/mitmproxy-ca-cert.pem"
echo "CA-Installation:    Client-Browser:  http://mitm.it:8080/"
echo
echo "Clients im internen Netz:"
echo "  - IP per DHCP (Gateway/DNS = ${AP_IP_INT:-$LAN_IP})"
echo "  - CA-Zertifikat installieren & vertrauen,"
echo "    dann wird der HTTP/HTTPS-Traffic über mitmproxy sichtbar."
if $USE_CAPPORT_IPSET; then
  echo "  - Captive-Portal-Checks (Google & Co.) werden NICHT mitgemitmproxyt."
else
  echo "  - Alle Ziele (inkl. Google Connectivity Check) gehen durch MITM."
fi
