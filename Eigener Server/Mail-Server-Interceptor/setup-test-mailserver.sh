#!/usr/bin/env bash
set -e

# Logo und Banner anzeigen
if [ -f logo ]; then
  cat logo
else
  echo "-------------------------------------------------------------"
  echo " ACHTUNG: Dieses Skript wird ohne jegliche Gewähr bereitgestellt."
  echo " Es wird keine Haftung für eventuelle Schäden oder Fehlkonfigurationen übernommen."
  echo " DIESER MAILSERVER IST NICHT FÜR PRODUKTIVE EINSÄTZE GEEIGNET!"
  echo "-------------------------------------------------------------"
fi


if [ "$EUID" -ne 0 ]; then
  echo "Bitte als root ausführen."
  exit 1
fi

echo "=== Mail-Setup (Postfix + Dovecot + stunnel, transparenter IMAPS & SMTPS) ==="

# ---------------------------------------------------------------------
# Eingaben
# ---------------------------------------------------------------------

CURRENT_FQDN=$(hostname -f 2>/dev/null || hostname)
read -rp "Hostname/FQDN für den Mailserver [${CURRENT_FQDN}]: " MAIL_HOSTNAME
MAIL_HOSTNAME=${MAIL_HOSTNAME:-$CURRENT_FQDN}

read -rp "Netzwerk-Interface für externen Traffic [eth0]: " MAIL_IFACE
MAIL_IFACE=${MAIL_IFACE:-eth0}

read -rp "Mail-Benutzername [it-an-der-bar]: " MAIL_USER
MAIL_USER=${MAIL_USER:-it-an-der-bar}

MAIL_PASS_DEFAULT=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c40)
echo "Vorgeschlagenes Passwort (40 Zeichen): ${MAIL_PASS_DEFAULT}"
read -rp "Passwort (Enter = Vorschlag verwenden): " MAIL_PASS
MAIL_PASS=${MAIL_PASS:-$MAIL_PASS_DEFAULT}

echo
read -rp "Let's Encrypt-Zertifikat nutzen? (j/N): " MAIL_USE_LE
MAIL_USE_LE=${MAIL_USE_LE:-N}
MAIL_USE_LE=$(echo "$MAIL_USE_LE" | tr '[:upper:]' '[:lower:]')

if [ "$MAIL_USE_LE" = "j" ] || [ "$MAIL_USE_LE" = "y" ]; then
  MAIL_USE_LE="y"
  read -rp "E-Mail-Adresse für Let's Encrypt (ACME): " MAIL_LE_EMAIL
  if [ -z "$MAIL_LE_EMAIL" ]; then
    echo "Keine E-Mail angegeben, Let's Encrypt wird deaktiviert."
    MAIL_USE_LE="n"
  fi
else
  MAIL_USE_LE="n"
fi

export DEBIAN_FRONTEND=noninteractive

echo
echo "Verwende:"
echo "  Hostname     : $MAIL_HOSTNAME"
echo "  Interface    : $MAIL_IFACE"
echo "  Benutzer     : $MAIL_USER"
echo "  Let'sEncrypt : $MAIL_USE_LE"
echo

# ---------------------------------------------------------------------
# Pakete installieren
# ---------------------------------------------------------------------

echo ">>> Pakete installieren ..."

echo "postfix postfix/mailname string ${MAIL_HOSTNAME}" | debconf-set-selections
echo "postfix postfix/main_mailer_type select Internet Site" | debconf-set-selections

echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean false" | debconf-set-selections

apt-get update
apt-get install -y postfix dovecot-imapd stunnel4 iptables-persistent tcpdump tshark

if [ "$MAIL_USE_LE" = "y" ]; then
  apt-get install -y certbot
fi

# ---------------------------------------------------------------------
# Postfix konfigurieren
# ---------------------------------------------------------------------

echo ">>> Postfix konfigurieren (Port 25 öffentlich, SMTP-AUTH, kein Open Relay) ..."

postconf -e "myhostname = ${MAIL_HOSTNAME}"
postconf -e 'inet_interfaces = all'
postconf -e 'mydestination = $myhostname, localhost.localdomain, localhost'
postconf -e 'smtpd_sasl_type = dovecot'
postconf -e 'smtpd_sasl_path = private/auth'
postconf -e 'smtpd_sasl_auth_enable = yes'
postconf -e 'smtpd_sasl_security_options = noanonymous'
postconf -e 'broken_sasl_auth_clients = yes'
postconf -e 'smtpd_recipient_restrictions = permit_sasl_authenticated, reject_unauth_destination'

# ---------------------------------------------------------------------
# Dovecot konfigurieren
# ---------------------------------------------------------------------

echo ">>> Dovecot konfigurieren ..."

DOVECOT_CONF="/etc/dovecot/dovecot.conf"
SSL_CONF="/etc/dovecot/conf.d/10-ssl.conf"
AUTH_CONF="/etc/dovecot/conf.d/10-auth.conf"
MASTER_CONF="/etc/dovecot/conf.d/10-master.conf"

# IMAP-Listener (für transparenten Proxy)
if grep -q '^listen =' "$DOVECOT_CONF"; then
  sed -i 's/^listen =.*/listen = 127.0.0.1 127.1.1.1/' "$DOVECOT_CONF"
else
  echo 'listen = 127.0.0.1 127.1.1.1' >> "$DOVECOT_CONF"
fi

# TLS aus
if grep -q '^ssl =' "$SSL_CONF"; then
  sed -i 's/^ssl =.*/ssl = no/' "$SSL_CONF"
else
  echo 'ssl = no' >> "$SSL_CONF"
fi

# Klartext-Auth erlauben
if grep -q '^auth_allow_cleartext' "$AUTH_CONF"; then
  sed -i 's/^auth_allow_cleartext.*/auth_allow_cleartext = yes/' "$AUTH_CONF"
else
  echo 'auth_allow_cleartext = yes' >> "$AUTH_CONF"
fi

# Mechanismen (plain, login)
if grep -q '^auth_mechanisms' "$AUTH_CONF"; then
  sed -i 's/^auth_mechanisms.*/auth_mechanisms = plain login/' "$AUTH_CONF"
else
  echo 'auth_mechanisms = plain login' >> "$AUTH_CONF"
fi

# Username Rewrite
echo 'auth_username_format = %{user|username|lower}' >> "$AUTH_CONF"

# Dovecot-Auth-Socket für Postfix (SMTP-AUTH)
if ! grep -Eq '^[[:space:]]*unix_listener /var/spool/postfix/private/auth[[:space:]]*{' "$MASTER_CONF"; then
  sed -i '/service auth {/a\  unix_listener /var/spool/postfix/private/auth {\n    mode = 0660\n    user = postfix\n    group = postfix\n  }\n' "$MASTER_CONF"
fi

# ---------------------------------------------------------------------
# Zertifikat (LE oder self-signed)
# ---------------------------------------------------------------------

STUNNEL_CERT=""
STUNNEL_KEY=""

if [ "$MAIL_USE_LE" = "y" ]; then
  echo ">>> Versuche Let's Encrypt Zertifikat (Standalone) ..."
  systemctl stop nginx apache2 2>/dev/null || true
  if certbot certonly --standalone --non-interactive --agree-tos \
      -m "$MAIL_LE_EMAIL" -d "$MAIL_HOSTNAME"; then
    STUNNEL_CERT="/etc/letsencrypt/live/${MAIL_HOSTNAME}/fullchain.pem"
    STUNNEL_KEY="/etc/letsencrypt/live/${MAIL_HOSTNAME}/privkey.pem"
    echo "Let's Encrypt erfolgreich – Zertifikat wird verwendet."
  else
    echo "Let's Encrypt fehlgeschlagen, verwende self-signed Zertifikat."
    MAIL_USE_LE="n"
  fi
fi

if [ "$MAIL_USE_LE" != "y" ]; then
  echo ">>> Self-signed Zertifikat für stunnel erzeugen (falls nicht vorhanden) ..."
  mkdir -p /etc/stunnel
  STUNNEL_CERT="/etc/stunnel/mail.pem"
  STUNNEL_KEY="/etc/stunnel/mail.pem"
  if [ ! -f "$STUNNEL_CERT" ]; then
    openssl req -new -x509 -days 365 -nodes \
      -subj "/CN=${MAIL_HOSTNAME}" \
      -out "$STUNNEL_CERT" -keyout "$STUNNEL_KEY"
    chmod 600 "$STUNNEL_CERT"
  fi
fi

# ---------------------------------------------------------------------
# stunnel konfigurieren
# ---------------------------------------------------------------------

echo ">>> stunnel konfigurieren ..."

STUNNEL_DEFAULT="/etc/default/stunnel4"
if grep -q '^ENABLED=' "$STUNNEL_DEFAULT"; then
  sed -i 's/^ENABLED=.*/ENABLED=1/' "$STUNNEL_DEFAULT"
else
  echo 'ENABLED=1' >> "$STUNNEL_DEFAULT"
fi

# IMAPS transparent (993 -> 127.1.1.1:143)
cat > /etc/stunnel/imaps.conf << EOF
pid = /run/stunnel4-imaps.pid
debug = 7
output = /var/log/stunnel-imaps.log
foreground = no

[imaps-transparent]
accept      = 0.0.0.0:993
connect     = 127.1.1.1:143
transparent = source
cert        = ${STUNNEL_CERT}
key         = ${STUNNEL_KEY}
client      = no
EOF

# SMTPS transparent (465 -> 127.1.1.2:25)
cat > /etc/stunnel/smtps.conf << EOF
pid = /run/stunnel4-smtps.pid
debug = 7
output = /var/log/stunnel-smtps.log
foreground = no

[smtps-transparent]
accept      = 0.0.0.0:465
connect     = 127.1.1.2:25
transparent = source
cert        = ${STUNNEL_CERT}
key         = ${STUNNEL_KEY}
client      = no
EOF

# ---------------------------------------------------------------------
# sysctl für transparent Proxy (persistent)
# ---------------------------------------------------------------------

echo ">>> sysctl-Einstellungen für transparenten Proxy setzen ..."

SYSCTL_FILE="/etc/sysctl.d/99-mail.conf"
cat > "$SYSCTL_FILE" << EOF
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.lo.rp_filter=0
net.ipv4.conf.${MAIL_IFACE}.rp_filter=0
net.ipv4.conf.default.route_localnet=1
net.ipv4.conf.all.route_localnet=1
EOF

sysctl --system >/dev/null

# ---------------------------------------------------------------------
# iptables + Policy Routing für transparenten IMAPS & SMTPS
# ---------------------------------------------------------------------

echo ">>> iptables-Regeln und Policy Routing setzen ..."

# 1) 127/8 von außen droppen
iptables -t raw   -C PREROUTING ! -i lo -d 127.0.0.0/8 -j DROP 2>/dev/null || \
iptables -t raw   -A PREROUTING ! -i lo -d 127.0.0.0/8 -j DROP

iptables -t mangle -C POSTROUTING ! -o lo -s 127.0.0.0/8 -j DROP 2>/dev/null || \
iptables -t mangle -A POSTROUTING ! -o lo -s 127.0.0.0/8 -j DROP

# 2) Verbindungen von stunnel zu 127.1.1.1:143 markieren
iptables -t nat -C OUTPUT -d 127.1.1.1 -p tcp --tcp-flags FIN,SYN,RST,ACK SYN \
  -j CONNMARK --set-xmark 0x01/0x0f 2>/dev/null || \
iptables -t nat -A OUTPUT -d 127.1.1.1 -p tcp --tcp-flags FIN,SYN,RST,ACK SYN \
  -j CONNMARK --set-xmark 0x01/0x0f

# 3) Verbindungen von stunnel zu 127.1.1.2:25 markieren
iptables -t nat -C OUTPUT -d 127.1.1.2 -p tcp --tcp-flags FIN,SYN,RST,ACK SYN \
  -j CONNMARK --set-xmark 0x01/0x0f 2>/dev/null || \
iptables -t nat -A OUTPUT -d 127.1.1.2 -p tcp --tcp-flags FIN,SYN,RST,ACK SYN \
  -j CONNMARK --set-xmark 0x01/0x0f

# 4) Mark in Routing-Mark (fwmark) kopieren
iptables -t mangle -C OUTPUT ! -o lo -p tcp -m connmark --mark 0x01/0x0f \
  -j CONNMARK --restore-mark --mask 0x0f 2>/dev/null || \
iptables -t mangle -A OUTPUT ! -o lo -p tcp -m connmark --mark 0x01/0x0f \
  -j CONNMARK --restore-mark --mask 0x0f

# 5) Policy Routing: fwmark 0x1 -> Tabelle 100
ip rule add fwmark 0x1 lookup 100 2>/dev/null || true
ip route add local 0.0.0.0/0 dev lo table 100 2>/dev/null || true

# 6) iptables-Regeln persistent speichern
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save
else
  iptables-save > /etc/iptables/rules.v4
fi

# 7) Policy-Routing beim Interface-Up
IFUP_SCRIPT="/etc/network/if-pre-up.d/mail-routing"
cat > "$IFUP_SCRIPT" << 'EOF'
#!/bin/sh
ip rule add fwmark 0x1 lookup 100 2>/dev/null || true
ip route add local 0.0.0.0/0 dev lo table 100 2>/dev/null || true
exit 0
EOF
chmod +x "$IFUP_SCRIPT"

# ---------------------------------------------------------------------
# Mail-Benutzer anlegen
# ---------------------------------------------------------------------

echo ">>> Mail-Benutzer anlegen ..."

if id "$MAIL_USER" >/dev/null 2>&1; then
  echo "User $MAIL_USER existiert bereits, Passwort wird aktualisiert."
else
  adduser --disabled-password --gecos "" "$MAIL_USER"
fi

echo "${MAIL_USER}:${MAIL_PASS}" | chpasswd

# ---------------------------------------------------------------------
# Dienste NICHT automatisch starten
# ---------------------------------------------------------------------

echo ">>> Dienste stoppen und Autostart deaktivieren ..."

systemctl stop postfix dovecot stunnel4 2>/dev/null || true
systemctl disable postfix dovecot stunnel4 >/dev/null 2>&1 || true

# ---------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------

SERVER_IP=$(hostname -I | awk '{print $1}')

echo
echo "=== Setup fertig ==="
echo
echo "Mail-Benutzer:"
echo "  Benutzername : $MAIL_USER"
echo "  Passwort     : $MAIL_PASS"
echo
echo "Dienste für die Demo manuell starten:"
echo "  systemctl start stunnel4"
echo "  systemctl start dovecot"
echo "  systemctl start postfix"
echo
echo "IMAP im Mail-Client:"
echo "  Server : ${MAIL_HOSTNAME} (oder ${SERVER_IP})"
echo "  Port   : 993 (SSL/TLS)"
echo "  User   : $MAIL_USER"
echo "  Pass   : (siehe oben)"
echo
echo "SMTP im Mail-Client:"
echo "  Server : ${MAIL_HOSTNAME} (oder ${SERVER_IP})"
echo "  Port   : 465 (SSL/TLS)"
echo "  Auth   : normal, User/Pass wie oben"
echo
echo "tcpdump für die Demo (Klartext + Original-IP):"
echo "  IMAP : sudo tcpdump -i lo -nn -A port 143"
echo "  SMTP : sudo tcpdump -i lo -nn -A port 25"
echo
echo "Optional (verschlüsselte Sicht, Interface ${MAIL_IFACE}):"
echo "  IMAPS: sudo tcpdump -i ${MAIL_IFACE} -nn -X port 993"
echo "  SMTPS: sudo tcpdump -i ${MAIL_IFACE} -nn -X port 465"
echo
echo "Fertig. 🙂"
