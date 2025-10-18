#!/usr/bin/env bash
set -euo pipefail

# Logo/Banner anzeigen
if [ -f logo ]; then
    cat logo
else
    echo "-------------------------------------------------------------"
    echo " ACHTUNG: Dieses Skript wird ohne jegliche Gewähr bereitgestellt."
    echo " Es wird keine Haftung für eventuelle Schäden oder Fehlkonfigurationen übernommen."
    echo "-------------------------------------------------------------"
fi
echo "----------------------------------------"


# ==========================
#  Einstellungen
# ==========================
DATA_DIR="/opt/rustdesk-server"
IMAGE="rustdesk/rustdesk-server:latest"
HBBS_NAME="rustdesk-hbbs"
HBBR_NAME="rustdesk-hbbr"

# Mindestports für RustDesk OSS
REQ_TCP=(21115 21116 21117)   # hbbs/hbbr TCP
REQ_UDP=(21116)               # hbbs UDP
# Optional (Web-Client): 21118/tcp (WS zu hbbs), 21119/tcp (WS zu hbbr)

# ==========================
#  Farben/Format
# ==========================
if command -v tput >/dev/null 2>&1 && [[ -n "${TERM:-}" ]]; then
  BOLD="$(tput bold)"; NORM="$(tput sgr0)"
  GREEN="$(tput setaf 2)"; YELLOW="$(tput setaf 3)"; RED="$(tput setaf 1)"; GREY="$(tput setaf 8)"
else
  BOLD=""; NORM=""; GREEN=""; YELLOW=""; RED=""; GREY=""
fi
bold(){ echo -e "${BOLD}$*${NORM}"; }
green(){ echo -e "${GREEN}$*${NORM}"; }
yellow(){ echo -e "${YELLOW}$*${NORM}"; }
red(){ echo -e "${RED}$*${NORM}"; }
grey(){ echo -e "${GREY}$*${NORM}"; }

# ==========================
#  Helpers
# ==========================
need_root(){ [[ $(id -u) -eq 0 ]] || { red "Bitte mit sudo/root ausführen."; exit 1; }; }
confirm(){ read -r -p "${1:-Weiter?} [Y/n] " a || true; [[ -z "${a:-}" || "$a" =~ ^[YyJj]$ ]]; }
pause(){ read -r -p "Weiter mit Enter …" _ || true; }
is_ubuntu(){ [[ -f /etc/os-release ]] && . /etc/os-release && [[ "${ID:-}" == "ubuntu" ]]; }
has_cmd(){ command -v "$1" >/dev/null 2>&1; }
has_docker(){ has_cmd docker; }
docker_active(){ systemctl is-active --quiet docker 2>/dev/null; }

container_exists(){ docker ps -a --format '{{.Names}}' | grep -qx "$1"; }
container_running(){ docker ps --format '{{.Names}}' | grep -qx "$1"; }

ensure_data_dir(){ mkdir -p "$DATA_DIR/data"; chmod 700 "$DATA_DIR/data" || true; }

print_keys_hint(){
  local PUB="$DATA_DIR/data/id_ed25519.pub"
  if [[ -f "$PUB" ]]; then
    echo; bold "Server Public Key:"; cat "$PUB"; echo
  else
    yellow "Public Key noch nicht vorhanden. Er erscheint nach dem ersten Start unter:"
    echo "  $PUB"
  fi
}

# ==========================
#  IP-Infos
# ==========================
get_external_ip(){
  local ip=""
  if has_cmd dig;   then ip="$(dig +short myip.opendns.com @resolver1.opendns.com || true)"; fi
  if [[ -z "$ip" ]] && has_cmd curl; then ip="$(curl -fsS https://api.ipify.org || true)"; fi
  if [[ -z "$ip" ]] && has_cmd wget; then ip="$(wget -qO- https://api.ipify.org || true)"; fi
  echo "$ip"
}

# Prüft, ob eine NIC eine echte Public-IP (kein RFC1918/CGNAT) trägt
has_local_public_ip(){
  local ips
  ips="$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  while read -r ip; do
    [[ -z "$ip" ]] && continue
    # private + CGNAT ausschließen
    if ! [[ "$ip" =~ ^10\. || "$ip" =~ ^192\.168\. || "$ip" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. || "$ip" =~ ^100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\. ]]; then
      return 0
    fi
  done <<< "$ips"
  return 1
}

show_ips(){
  bold "Netzwerk-Infos"
  local ext; ext="$(get_external_ip)"
  if [[ -n "$ext" ]]; then
    echo "Externe IP (via Provider): $ext"
  else
    yellow "Externe IP konnte nicht ermittelt werden (Internet/Tools?). Installiere ggf. 'dnsutils' oder 'curl'."
  fi
  echo "Lokale IPs: $(hostname -I 2>/dev/null || echo 'unbekannt')"
  echo
  echo "RustDesk-Clients: in den Einstellungen → Netzwerk die Server-Adresse auf diese IP setzen."
}

# ==========================
#  Firewall-Checks – iptables ONLY
# ==========================
# "aktiv" = iptables vorhanden und lesbar (auch wenn Policy ACCEPT ist)
fw_any_active() {
  has_cmd iptables && iptables -S >/dev/null 2>&1
}

# Liest die Default-Policy der INPUT-Chain (ACCEPT/DROP/REJECT)
fw_policy_input() {
  iptables -S 2>/dev/null | awk '$1=="-P" && $2=="INPUT"{print $3; exit}'
}

# Prüft, ob eine ACCEPT-Regel für proto/port irgendwo sichtbar ist
# 1) exakte -C-Prüfung (direkt in INPUT)
# 2) Fallback: heuristischer Check über alle Chains (nach "ACCEPT ... dpt:PORT")
iptables_port_open() {
  local proto="$1" port="$2"
  # exakter Check (nur INPUT)
  if iptables -C INPUT -p "$proto" --dport "$port" -j ACCEPT 2>/dev/null; then
    return 0
  fi
  # heuristischer Check (deckt auch Sprünge/benutzerdef. Chains ab)
  iptables -nL 2>/dev/null | awk -v p="$port" -v pr="$proto" '
    BEGIN{IGNORECASE=1}
    $1=="ACCEPT" && tolower($0) ~ ("proto " pr) && $0 ~ ("dpt:" p){found=1}
    END{exit(found?0:1)}
  '
}

# Haupt-Check: bewertet, ob die Mindest-Ports offen sind
# - Wenn iptables fehlt → "ok (keine FW)"
# - Wenn Policy INPUT=ACCEPT → "ok (policy ACCEPT)"
# - Sonst: prüfe erforderliche Ports
firewall_ok() {
  local missing=()

  if ! fw_any_active; then
    echo "ok (keine FW)"
    return 0
  fi

  local policy; policy="$(fw_policy_input)"
  if [[ "${policy^^}" == "ACCEPT" || -z "$policy" ]]; then
    echo "ok (policy ACCEPT)"
    return 0
  fi

  # Striktere Policies → Ports prüfen
  for p in "${REQ_TCP[@]}"; do
    iptables_port_open tcp "$p" || missing+=("${p}/tcp")
  done
  for p in "${REQ_UDP[@]}"; do
    iptables_port_open udp "$p" || missing+=("${p}/udp")
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    echo "ok (iptables)"
    return 0
  else
    echo "failed (fehlend: ${missing[*]})"
    return 1
  fi
}

# ==========================
#  Abhängigkeiten
# ==========================
install_docker(){
  bold "Installiere Docker (Ubuntu)…"
  apt-get update -y
  apt-get install -y curl ca-certificates
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  green "Docker installiert und gestartet."
}

check_deps(){
  bold "Abhängigkeiten prüfen…"
  if ! is_ubuntu; then yellow "Hinweis: Skript ist für Ubuntu optimiert."; fi
  if has_docker; then
    green "Docker gefunden: $(docker --version)"
  else
    yellow "Docker fehlt."
    if confirm "Docker jetzt installieren?"; then install_docker; else red "Ohne Docker geht es nicht."; exit 1; fi
  fi
  if ! docker_active; then bold "Starte Docker-Dienst…"; systemctl enable --now docker; fi
  green "Abhängigkeitscheck abgeschlossen."
}

# ==========================
#  Container-Operationen
# ==========================
pull_image(){ bold "Ziehe Image: $IMAGE"; docker pull "$IMAGE"; }

install_containers(){
  check_deps; ensure_data_dir; pull_image
  if container_exists "$HBBS_NAME"; then yellow "$HBBS_NAME existiert bereits – übersprungen."; else
    bold "Erzeuge $HBBS_NAME (hbbs)…"
    docker run -d --restart unless-stopped --name "$HBBS_NAME" --network host -v "$DATA_DIR/data:/root" "$IMAGE" hbbs
  fi
  if container_exists "$HBBR_NAME"; then yellow "$HBBR_NAME existiert bereits – übersprungen."; else
    bold "Erzeuge $HBBR_NAME (hbbr)…"
    docker run -d --restart unless-stopped --name "$HBBR_NAME" --network host -v "$DATA_DIR/data:/root" "$IMAGE" hbbr
  fi
  green "Installation abgeschlossen."; print_keys_hint
}

start_all(){
  bold "Starte Container…"
  container_exists "$HBBS_NAME" && docker start "$HBBS_NAME" || yellow "$HBBS_NAME nicht vorhanden."
  container_exists "$HBBR_NAME" && docker start "$HBBR_NAME" || yellow "$HBBR_NAME nicht vorhanden."
  green "Fertig."
}

stop_all(){
  bold "Stoppe Container…"
  container_running "$HBBS_NAME" && docker stop "$HBBS_NAME" || yellow "$HBBS_NAME läuft nicht."
  container_running "$HBBR_NAME" && docker stop "$HBBR_NAME" || yellow "$HBBR_NAME läuft nicht."
  green "Fertig."
}

restart_all(){ stop_all; start_all; }

status_all(){
  bold "Status von hbbs/hbbr:"
  docker ps -a --filter "name=^$HBBS_NAME$|^$HBBR_NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"
  print_keys_hint
}

logs_follow(){
  bold "Logs (Strg+C beendet)…"
  set +e
  docker logs -f "$HBBS_NAME" &
  P1=$!
  docker logs -f "$HBBR_NAME" &
  P2=$!
  wait $P1 $P2
  set -e
}

upgrade_all(){
  check_deps; pull_image
  bold "Entferne alte Container (falls vorhanden)…"
  docker rm -f "$HBBS_NAME" 2>/dev/null || true
  docker rm -f "$HBBR_NAME" 2>/dev/null || true
  install_containers
  green "Upgrade abgeschlossen."
}

delete_all(){
  yellow "Dies entfernt die Container. Daten bleiben bestehen, außer du bestätigst deren Löschung."
  if confirm "Container wirklich löschen?"; then
    docker rm -f "$HBBS_NAME" 2>/dev/null || true
    docker rm -f "$HBBR_NAME" 2>/dev/null || true
    green "Container entfernt."
  fi
  if confirm "Persistente Daten unter $DATA_DIR endgültig löschen? (NICHT umkehrbar)"; then
    rm -rf "$DATA_DIR"; green "Datenverzeichnis gelöscht."
  fi
}

# ==========================
#  CLI
# ==========================
usage(){
  cat <<EOF
$(bold "RustDesk Server – Container Helper")
Benutzung: $0 <befehl>

Befehle:
  deps | install | start | stop | restart | status | logs | upgrade | delete | ip | menu | help

Beispiele:
  sudo $0 deps && sudo $0 install
  sudo $0 menu
EOF
}

# ==========================
#  Interaktives, farbiges Menü
# ==========================
render_menu(){
  clear
  # Health-Hinweis vorbereiten
  local pub_status fw_status fw_ok
  if has_local_public_ip; then
    pub_status="ja"
  else
    pub_status="nein (NAT beachten!)"
  fi
  fw_status="$(firewall_ok)"; fw_ok=$?

  bold "RustDesk Server – Menü"
  echo "Datenverzeichnis: $DATA_DIR"
  echo -n "Hinweis: Public IP: "
  if [[ "$pub_status" == "ja" ]]; then echo -n "${GREEN}ja${NORM}"; else echo -n "${YELLOW}nein (NAT beachten!)${NORM}"; fi
  echo -n "   |   Firewall: "
  if [[ $fw_ok -eq 0 ]]; then echo -n "${GREEN}${fw_status}${NORM}"; else echo -n "${RED}${fw_status}${NORM}"; fi
  echo
  echo

  # Zustand ermitteln
  local ex_hbbs=0 ex_hbbr=0 run_hbbs=0 run_hbbr=0
  container_exists "$HBBS_NAME" && ex_hbbs=1
  container_exists "$HBBR_NAME" && ex_hbbr=1
  container_running "$HBBS_NAME" && run_hbbs=1
  container_running "$HBBR_NAME" && run_hbbr=1
  local any_exists=$(( ex_hbbs || ex_hbbr ))
  local any_running=$(( run_hbbs || run_hbbr ))
  local both_exist=$(( ex_hbbs && ex_hbbr ))

  # Enable/Disable Logik
  local en_deps=1
  local en_install=$(( both_exist ? 0 : 1 ))
  local en_start=$(( any_exists && ! any_running ? 1 : 0 ))
  local en_stop=$(( any_running ? 1 : 0 ))
  local en_restart=$(( any_exists ? 1 : 0 ))
  local en_status=1
  local en_logs=$(( any_exists ? 1 : 0 ))
  local en_upgrade=$(( any_exists ? 1 : 0 ))
  local has_data_dir=0
  [[ -d "$DATA_DIR" ]] && has_data_dir=1
  local en_delete=$(( (any_exists || has_data_dir) ? 1 : 0 ))
  local en_ip=1

  local MENU_TEXT=( 
    "Abhängigkeiten prüfen/installieren"
    "Installieren (hbbs/hbbr anlegen)"
    "Starten"
    "Stoppen"
    "Neustarten"
    "Status anzeigen"
    "Logs verfolgen"
    "Upgrade (neu ziehen & neu erstellen)"
    "Löschen (Container, optional Daten)"
    "Externe IP anzeigen"
    "Beenden"
  )
  local MENU_EN=( $en_deps $en_install $en_start $en_stop $en_restart $en_status $en_logs $en_upgrade $en_delete $en_ip 1 )
  local MENU_CMD=( deps install start stop restart status logs upgrade delete ip quit )

  local i
  for ((i=0;i<${#MENU_TEXT[@]};i++)); do
    local idx=$((i+1))
    if [[ "${MENU_EN[$i]}" -eq 1 ]]; then
      echo -e " ${BOLD}${idx})${NORM} ${GREEN}${MENU_TEXT[$i]}${NORM}"
    else
      echo -e " ${BOLD}${idx})${NORM} ${GREY}${MENU_TEXT[$i]} (derzeit nicht verfügbar)${NORM}"
    fi
  done
  echo
}

menu_loop(){
  while true; do
    render_menu
    read -r -p "Auswahl (Zahl): " choice || true
    [[ -z "${choice:-}" ]] && continue
    if ! [[ "$choice" =~ ^[0-9]+$ ]]; then red "Bitte eine Zahl wählen."; sleep 1; continue; fi
    local idx=$((choice-1))
    if (( idx < 0 || idx >= 11 )); then red "Ungültige Auswahl."; sleep 1; continue; fi

    # Arrays wie in render_menu()
    local MENU_CMD=( deps install start stop restart status logs upgrade delete ip quit )

    # Zustand neu evaluieren (Enable/Disable aktuell halten)
    local ex_hbbs=0 ex_hbbr=0 run_hbbs=0 run_hbbr=0
    container_exists "$HBBS_NAME" && ex_hbbs=1
    container_exists "$HBBR_NAME" && ex_hbbr=1
    container_running "$HBBS_NAME" && run_hbbs=1
    container_running "$HBBR_NAME" && run_hbbr=1
    local any_exists=$(( ex_hbbs || ex_hbbr ))
    local any_running=$(( run_hbbs || run_hbbr ))
    local both_exist=$(( ex_hbbs && ex_hbbr ))
    local en_deps=1
    local en_install=$(( both_exist ? 0 : 1 ))
    local en_start=$(( any_exists && ! any_running ? 1 : 0 ))
    local en_stop=$(( any_running ? 1 : 0 ))
    local en_restart=$(( any_exists ? 1 : 0 ))
    local en_status=1
    local en_logs=$(( any_exists ? 1 : 0 ))
    local en_upgrade=$(( any_exists ? 1 : 0 ))
    local has_data_dir=0
    [[ -d "$DATA_DIR" ]] && has_data_dir=1
    local en_delete=$(( (any_exists || has_data_dir) ? 1 : 0 ))
    local en_ip=1
    local MENU_EN=( $en_deps $en_install $en_start $en_stop $en_restart $en_status $en_logs $en_upgrade $en_delete $en_ip 1 )

    if [[ "${MENU_EN[$idx]}" -ne 1 ]]; then
      yellow "Diese Option ist aktuell nicht sinnvoll/verfügbar."
      sleep 1
      continue
    fi

    local cmd="${MENU_CMD[$idx]}"
    case "$cmd" in
      deps)     check_deps; pause ;;
      install)  install_containers; pause ;;
      start)    start_all; pause ;;
      stop)     stop_all; pause ;;
      restart)  restart_all; pause ;;
      status)   status_all; pause ;;
      logs)     logs_follow; pause ;;
      upgrade)  upgrade_all; pause ;;
      delete)   delete_all; pause ;;
      ip)       show_ips; pause ;;
      quit)     break ;;
    esac
  done
}

# ==========================
#  Routing
# ==========================
main(){
  need_root
  case "${1:-menu}" in
    deps)     check_deps ;;
    install)  install_containers ;;
    start)    start_all ;;
    stop)     stop_all ;;
    restart)  restart_all ;;
    status)   status_all ;;
    logs)     logs_follow ;;
    upgrade)  upgrade_all ;;
    delete)   delete_all ;;
    ip)       show_ips ;;
    menu)     menu_loop ;;
    help|-h|--help) usage ;;
    *) red "Unbekannter Befehl: ${1:-}"; echo; usage; exit 2 ;;
  esac
}

main "$@"
