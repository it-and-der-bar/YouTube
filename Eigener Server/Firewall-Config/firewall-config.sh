#!/bin/bash
set -e

clear

# Logo / Banner
if [ -f logo ]; then
  cat logo
else
  echo "-------------------------------------------------------------"
  echo " ACHTUNG: Dieses Skript wird ohne jegliche Gewähr bereitgestellt."
  echo " Es wird keine Haftung für eventuelle Schäden oder Fehlkonfigurationen übernommen."
  echo "-------------------------------------------------------------"
fi

FIREWALL_DIR="/etc/iptables"
CONFIG_FILE="$FIREWALL_DIR/firewall.conf"
RULES_FILE="$FIREWALL_DIR/rules"
SYSTEMD_UNIT="/etc/systemd/system/iptables-rules.service"
CRON_FILE="/etc/cron.d/iptables_rules_restart"
WG_CONF="/etc/wireguard/wg_manager.conf"

# RustDesk-Standardports (OSS)
RUSTDESK_TCP_BASE=(21115 21116 21117) # hbbs/hbbr
RUSTDESK_UDP_BASE=(21116)             # hbbs UDP

mkdir -p "$FIREWALL_DIR"

# Ggf. existierende Konfiguration laden
if [ -f "$CONFIG_FILE" ]; then
  echo "Vorhandene Firewall-Konfiguration wird geladen..."
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

# ---- Helper ----
has_cmd() { command -v "$1" >/dev/null 2>&1; }

prompt() {
  local var_name="$1"
  local prompt_text="$2"
  local current_val="${!var_name}"
  read -r -p "$prompt_text [${current_val}]: " input
  if [ -n "$input" ]; then
    eval "$var_name=\"$input\""
  fi
}

# RustDesk erkennen (Docker/Kubernetes)
detect_rustdesk() {
  RUSTDESK_DETECTED="nein"
  RUSTDESK_SOURCE=""

  if has_cmd docker; then
    if docker ps -a --format '{{.Image}} {{.Names}}' 2>/dev/null | grep -q 'rustdesk/rustdesk-server'; then
      RUSTDESK_DETECTED="ja"
      RUSTDESK_SOURCE="docker"
    fi
  fi

  if has_cmd kubectl; then
    # Durchsuche alle Pods/Container-Images nach rustdesk/rustdesk-server
    if kubectl get pods -A -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' 2>/dev/null \
       | grep -q 'rustdesk/rustdesk-server'; then
      if [ "$RUSTDESK_DETECTED" = "ja" ]; then
        RUSTDESK_SOURCE="${RUSTDESK_SOURCE}+k8s"
      else
        RUSTDESK_DETECTED="ja"
        RUSTDESK_SOURCE="k8s"
      fi
    fi
  fi
}

# Falls systemd-Dienst existiert → Auswahl
if [ -f "$SYSTEMD_UNIT" ]; then
  echo "Ein systemd-Dienst (iptables-rules.service) existiert bereits."
  echo "Bitte wähle:"
  echo "1) Dienst deaktivieren (Regeln entfernen/flushen)"
  echo "2) Firewall-Regeln bearbeiten (bestehende Konfiguration verwenden/ergänzen)"
  echo ""
  read -r -p "Deine Auswahl (1-2): " svc_choice
  case $svc_choice in
    1)
      systemctl disable iptables-rules.service || true
      systemctl stop iptables-rules.service || true
      rm -f "$SYSTEMD_UNIT"
      systemctl daemon-reload
      if [ -f "$CRON_FILE" ]; then
        rm -f "$CRON_FILE"
        echo "Cronjob zum stündlichen Neustart wurde entfernt."
      fi
      # Alle IPv4-Regeln entfernen
      iptables -P INPUT ACCEPT
      iptables -F
      iptables -P FORWARD ACCEPT
      iptables -F FORWARD
      iptables -P OUTPUT ACCEPT
      iptables -F OUTPUT
      # IPv6: auf ACCEPT zurücksetzen
      ip6tables -F
      ip6tables -X
      ip6tables -Z
      ip6tables -P INPUT ACCEPT
      ip6tables -P FORWARD ACCEPT
      ip6tables -P OUTPUT ACCEPT
      echo "Dienst wurde deaktiviert. Du kannst das Skript erneut ausführen, um Regeln neu zu setzen."
      exit 0
      ;;
    2)
      echo "Bearbeitung der Firewall-Regeln wird fortgesetzt."
      ;;
    *)
      echo "Ungültige Auswahl, das Skript wird beendet."
      exit 1
      ;;
  esac
fi

# ---- Interaktive Abfragen ----

# 1) Vertrauenswürdige Endpunkte
prompt TRUSTED_ENDPOINTS "Bitte gib vertrauenswürdige IPs/DNS-Endpunkte (kommagetrennt) ein"

# 2) WireGuard
ALLOW_WG_UDP="${ALLOW_WG_UDP:-ja}"
if [ -f "$WG_CONF" ]; then
  # shellcheck disable=SC1090
  source "$WG_CONF"
  if [ -n "${LISTEN_PORT:-}" ]; then
    prompt ALLOW_WG_UDP "Wireguard-Konfiguration gefunden. UDP-Port $LISTEN_PORT freigeben? (ja/nein)"
  fi
fi

# 3) RustDesk erkennen & Ports anbieten
detect_rustdesk
RUSTDESK_ADD="${RUSTDESK_ADD:-ja}"

if [ "$RUSTDESK_DETECTED" = "ja" ]; then
  echo "RustDesk erkannt (Quelle: $RUSTDESK_SOURCE)."
  echo "Standardports: TCP ${RUSTDESK_TCP_BASE[*]}, UDP ${RUSTDESK_UDP_BASE[*]}."
  prompt RUSTDESK_ADD "RustDesk-Standardports automatisch freigeben? (ja/nein)"
else
  echo "RustDesk wurde nicht automatisch erkannt."
  echo "Falls du RustDesk-Ports generell freigeben willst, kannst du hier trotzdem 'ja' wählen."
  prompt RUSTDESK_ADD "RustDesk-Standardports automatisch freigeben? (ja/nein)"
fi

# 4) Zusätzliche öffentliche Ports
prompt PUBLIC_PORTS "Bitte gib zusätzliche öffentliche Ports zum Freigeben (kommagetrennt) ein"

# 5) SSH-Regel
SSH_RULE="${SSH_RULE:-public}"
prompt SSH_RULE "Soll SSH öffentlich freigegeben werden oder nur für vertrauenswürdige Endpunkte? (public/trusted)"


# ---- Konfiguration persistieren ----
cat <<EOF > "$CONFIG_FILE"
# Persistierte Firewall-Konfiguration
TRUSTED_ENDPOINTS="${TRUSTED_ENDPOINTS}"
ALLOW_WG_UDP="${ALLOW_WG_UDP}"
PUBLIC_PORTS="${PUBLIC_PORTS}"
SSH_RULE="${SSH_RULE}"
RUSTDESK_ADD="${RUSTDESK_ADD}"
RUSTDESK_ADD_WS="${RUSTDESK_ADD_WS}"
EOF

echo "Konfiguration gespeichert unter: $CONFIG_FILE"

# ---- Regeln generieren ----
echo "Erstelle $RULES_FILE ..."
cat <<'EOF' > "$RULES_FILE"
#!/bin/bash
# Generierte iptables-Regeln

# IPv4: Policies setzen und flushen
iptables -P INPUT ACCEPT
iptables -F
iptables -P FORWARD ACCEPT
iptables -F FORWARD
iptables -P OUTPUT ACCEPT
iptables -F OUTPUT

# IPv6: restriktiv, loopback/related erlauben
ip6tables -F
ip6tables -X
ip6tables -Z
ip6tables -P INPUT DROP
ip6tables -P FORWARD DROP
ip6tables -P OUTPUT ACCEPT
ip6tables -A INPUT -i lo -j ACCEPT
ip6tables -A INPUT -p tcp --syn -j DROP
ip6tables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A INPUT -p ipv6-icmp -j ACCEPT

# Basisregeln IPv4
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

EOF

# Dynamische Regeln anhängen
{
  echo ""
  echo "# Vertrauenswürdige Endpunkte vollständig erlauben"
  IFS=',' read -r -a ADDR <<< "${TRUSTED_ENDPOINTS:-}"
  for addr in "${ADDR[@]}"; do
    t="$(echo "$addr" | xargs)"
    [ -n "$t" ] && echo "iptables -A INPUT -s $t -j ACCEPT"
  done

  # WireGuard
  if [ -f "$WG_CONF" ]; then
    # shellcheck disable=SC1090
    source "$WG_CONF"
  fi
  if [ "${ALLOW_WG_UDP:-nein}" = "ja" ] && [ -n "${LISTEN_PORT:-}" ]; then
    echo ""
    echo "# Wireguard UDP-Port ${LISTEN_PORT} freigeben"
    echo "iptables -A INPUT -p udp --dport ${LISTEN_PORT} -j ACCEPT"
  fi

  # RustDesk Standardports
  if [ "${RUSTDESK_ADD:-nein}" = "ja" ]; then
    echo ""
    echo "# RustDesk – Standardports freigeben (OSS)"
    # TCP-Basis
    for p in "${RUSTDESK_TCP_BASE[@]}"; do
      echo "iptables -A INPUT -p tcp --dport $p -j ACCEPT"
    done
    # UDP-Basis
    for p in "${RUSTDESK_UDP_BASE[@]}"; do
      echo "iptables -A INPUT -p udp --dport $p -j ACCEPT"
    done
  fi

  # Zusätzliche öffentliche Ports
  if [ -n "${PUBLIC_PORTS:-}" ]; then
    IFS=',' read -r -a PUB <<< "$PUBLIC_PORTS"
    for port in "${PUB[@]}"; do
      p="$(echo "$port" | xargs)"
      if [ -n "$p" ]; then
        echo ""
        echo "# Öffentlicher Port $p"
        echo "iptables -A INPUT -p tcp --dport $p -j ACCEPT"
        echo "iptables -A INPUT -p udp --dport $p -j ACCEPT"
      fi
    done
  fi

  # SSH-Regel
  echo ""
  if [ "${SSH_RULE:-public}" = "public" ]; then
    echo "# SSH öffentlich freigeben"
    echo "iptables -A INPUT -p tcp --dport 22 -j ACCEPT"
  else
    echo "# SSH nur für vertrauenswürdige Endpunkte; (bereits oben via -s erlaubt)"
    # Nichts weiter nötig – alle anderen werden gedroppt.
  fi

  # Default DROP
  echo ""
  echo "# Default: alle eingehenden Pakete verwerfen"
  echo "iptables -P INPUT DROP"
} >> "$RULES_FILE"

chmod +x "$RULES_FILE"
echo "iptables-Regeln generiert: $RULES_FILE"

# ---- systemd-Unit erstellen/aktivieren ----
echo "Erstelle systemd-Unit: $SYSTEMD_UNIT"
cat <<EOF > "$SYSTEMD_UNIT"
[Unit]
Description=Iptables Firewall Rules
After=network.target

[Service]
Type=oneshot
ExecStart=${RULES_FILE}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable iptables-rules.service
systemctl restart iptables-rules.service
echo "Systemd-Dienst aktiviert und gestartet."

# ---- Cronjob für stündliches Refresh ----
echo "Erstelle Cronjob (stündlicher Neustart): $CRON_FILE"
cat <<EOF > "$CRON_FILE"
0 * * * * root systemctl restart iptables-rules.service
EOF
chmod 644 "$CRON_FILE"
echo "Cronjob erstellt."

echo "Firewall-Konfiguration abgeschlossen."
