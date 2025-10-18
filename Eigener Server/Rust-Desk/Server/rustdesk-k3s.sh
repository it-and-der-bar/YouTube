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
IMAGE="rustdesk/rustdesk-server:latest"

# Kubernetes / k3s
KNS="rustdesk"                      # Namespace
KDEPLOY_NAME="rustdesk-server"      # Deployment Name
KPVC_NAME="rustdesk-data"           # PVC Name
KPVC_SIZE="1Gi"                     # PVC Größe
KDIR="/opt/rustdesk-server/k8s"     # Ablagepfad für YAML
KFILE="${KDIR}/rustdesk.yaml"

# Mindestports für RustDesk OSS (Firewall-Check)
REQ_TCP=(21115 21116 21117)         # TCP hbbs/hbbr
REQ_UDP=(21116)                     # UDP hbbs
# Optional Web-Client: 21118/tcp, 21119/tcp

# ==========================
#  Farben / Format
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
has_cmd(){ command -v "$1" >/dev/null 2>&1; }

has_kube(){ has_cmd kubectl && kubectl cluster-info >/dev/null 2>&1; }
has_k3s(){ systemctl is-active --quiet k3s 2>/dev/null && has_kube; }

ensure_dirs(){ mkdir -p "$KDIR"; }

# ==========================
#  IP-Infos
# ==========================
get_external_ip(){
  local ip=""
  if [[ -z "$ip" ]] && has_cmd curl; then ip="$(curl -fsS https://api.ipify.org || true)"; fi
  if [[ -z "$ip" ]] && has_cmd wget; then ip="$(wget -qO- https://api.ipify.org || true)"; fi
  echo "$ip"
}

has_local_public_ip(){
  local ips
  ips="$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  while read -r ip; do
    [[ -z "$ip" ]] && continue
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
#  Firewall-Check – iptables only
# ==========================
fw_any_active(){ has_cmd iptables && iptables -S >/dev/null 2>&1; }
fw_policy_input(){ iptables -S 2>/dev/null | awk '$1=="-P" && $2=="INPUT"{print $3; exit}'; }

iptables_port_open(){
  local proto="$1" port="$2"
  if iptables -C INPUT -p "$proto" --dport "$port" -j ACCEPT 2>/dev/null; then return 0; fi
  iptables -nL 2>/dev/null | awk -v p="$port" -v pr="$proto" '
    BEGIN{IGNORECASE=1}
    $1=="ACCEPT" && tolower($0) ~ ("proto " pr) && $0 ~ ("dpt:" p){found=1}
    END{exit(found?0:1)}
  '
}

firewall_ok(){
  local missing=()
  if ! fw_any_active; then echo "ok (keine FW)"; return 0; fi
  local policy; policy="$(fw_policy_input)"
  if [[ "${policy^^}" == "ACCEPT" || -z "$policy" ]]; then echo "ok (policy ACCEPT)"; return 0; fi
  for p in "${REQ_TCP[@]}"; do iptables_port_open tcp "$p" || missing+=("${p}/tcp"); done
  for p in "${REQ_UDP[@]}"; do iptables_port_open udp "$p" || missing+=("${p}/udp"); done
  if [[ ${#missing[@]} -eq 0 ]]; then echo "ok (iptables)"; return 0; else echo "failed (fehlend: ${missing[*]})"; return 1; fi
}

# ==========================
#  k3s / Kubernetes
# ==========================

check_deps(){
  bold "Abhängigkeiten prüfen…"
  if has_kube; then
    green "Kubernetes verfügbar: $(kubectl version --client --short 2>/dev/null || echo 'kubectl vorhanden')"
  else
    yellow "Kubernetes nicht verfügbar."
  fi
  green "Abhängigkeitscheck abgeschlossen."
}

write_k8s_yaml(){
  ensure_dirs
  cat > "$KFILE" <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: rustdesk

---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rustdesk-data
  namespace: rustdesk
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  # storageClassName: ""   # optional: explizit setzen, sonst Default StorageClass

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rustdesk-server
  namespace: rustdesk
spec:
  replicas: 1
  selector:
    matchLabels:
      app: rustdesk-server
  template:
    metadata:
      labels:
        app: rustdesk-server
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      containers:
        - name: hbbs
          image: rustdesk/rustdesk-server:latest
          args: ["hbbs"]
          imagePullPolicy: IfNotPresent
          volumeMounts:
            - name: data
              mountPath: /root
          ports:
            - containerPort: 21115
              protocol: TCP
            - containerPort: 21116
              protocol: TCP
            - containerPort: 21116
              protocol: UDP
            - containerPort: 21118
              protocol: TCP
        - name: hbbr
          image: rustdesk/rustdesk-server:latest
          args: ["hbbr"]
          imagePullPolicy: IfNotPresent
          volumeMounts:
            - name: data
              mountPath: /root
          ports:
            - containerPort: 21117
              protocol: TCP
            - containerPort: 21119
              protocol: TCP
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: rustdesk-data
YAML
  # Namespace/PVC/Size ersetzen gemäß Variablen
  sed -i "s/namespace: rustdesk/namespace: ${KNS}/g" "$KFILE"
  sed -i "s/name: rustdesk-data/name: ${KPVC_NAME}/" "$KFILE"
  sed -i "s/claimName: rustdesk-data/claimName: ${KPVC_NAME}/" "$KFILE"
  sed -i "s/storage: 1Gi/storage: ${KPVC_SIZE}/" "$KFILE"
}

k8s_apply(){
  if ! has_kube; then red "Kubernetes ist nicht verfügbar. Bitte erst k3s installieren."; return 1; fi
  write_k8s_yaml
  bold "Wende Manifest an: $KFILE"
  kubectl apply -f "$KFILE"
  green "Kubernetes-Deployment angewendet."
}

k8s_status(){
  if ! has_kube; then yellow "Kubernetes nicht verfügbar."; return 0; fi
  bold "Status (Namespace ${KNS}):"
  kubectl -n "$KNS" get deploy,po,pvc 2>/dev/null || true
}

k8s_delete(){
  if ! has_kube; then yellow "Kubernetes nicht verfügbar."; return 0; fi
  if [[ -f "$KFILE" ]]; then
    bold "Lösche Manifestressourcen…"
    kubectl delete -f "$KFILE" --ignore-not-found
  else
    yellow "Manifest $KFILE nicht gefunden – versuche direkte Löschung."
    kubectl -n "$KNS" delete deploy "$KDEPLOY_NAME" --ignore-not-found || true
    kubectl -n "$KNS" delete pvc "$KPVC_NAME" --ignore-not-found || true
    kubectl delete ns "$KNS" --ignore-not-found || true
  fi
  green "Kubernetes-Objekte entfernt."
}

# ==========================
#  CLI
# ==========================
usage(){
  cat <<EOF
$(bold "RustDesk – Kubernetes/k3s Helper")

Befehle:
  deps        – Abhängigkeiten prüfen
  k8s-apply   – Manifest schreiben & deployen (Namespace ${KNS})
  k8s-status  – Status anzeigen
  k8s-delete  – Deployment & PVC & Namespace löschen
  ip          – IP-Infos anzeigen
  menu        – Interaktives Menü
  help        – Hilfe

Variablen anpassen: KNS, KPVC_NAME, KPVC_SIZE, KDIR
EOF
}

# ==========================
#  Menü
# ==========================
render_menu(){
  clear
  local pub iptxt fwtxt fw_ok
  if has_local_public_ip; then pub="ja"; else pub="nein (NAT beachten!)"; fi
  iptxt="$(get_external_ip)"
  fwtxt="$(firewall_ok)"; fw_ok=$?

  bold "RustDesk – Kubernetes Menü"
  echo -n "Hinweis: Public IP: "
  if [[ "$pub" == "ja" ]]; then echo -n "${GREEN}ja${NORM}"; else echo -n "${YELLOW}nein (NAT beachten!)${NORM}"; fi
  echo -n "   |   Firewall: "
  if [[ $fw_ok -eq 0 ]]; then echo -n "${GREEN}${fwtxt}${NORM}"; else echo -n "${RED}${fwtxt}${NORM}"; fi
  echo -n "   |   Kubernetes: "
  if has_kube; then echo -n "${GREEN}verfügbar${NORM}"; else echo -n "${RED}nicht verfügbar${NORM}"; fi
  echo
  [[ -n "$iptxt" ]] && echo "Externe IP: $iptxt"
  echo

  echo -e " ${BOLD}1)${NORM} ${GREEN}Abhängigkeiten prüfen / k3s installieren${NORM}"
  if has_kube; then
    echo -e " ${BOLD}2)${NORM} ${GREEN}Deploy anwenden${NORM}"
    echo -e " ${BOLD}3)${NORM} ${GREEN}Status anzeigen${NORM}"
    echo -e " ${BOLD}4)${NORM} ${GREEN}Löschen (Deploy + PVC + Namespace)${NORM}"
  else
    echo -e " ${BOLD}2)${NORM} ${GREY}Deploy anwenden (nicht verfügbar)${NORM}"
    echo -e " ${BOLD}3)${NORM} ${GREY}Status anzeigen (nicht verfügbar)${NORM}"
    echo -e " ${BOLD}4)${NORM} ${GREY}Löschen (nicht verfügbar)${NORM}"
  fi
  echo -e " ${BOLD}5)${NORM} ${GREEN}IP-Infos anzeigen${NORM}"
  echo -e " ${BOLD}6)${NORM} Beenden"
  echo
}

menu_loop(){
  while true; do
    render_menu
    read -r -p "Auswahl (Zahl): " choice || true
    case "${choice:-}" in
      1) check_deps; pause ;;
      2) has_kube && k8s_apply || yellow "Nicht verfügbar – bitte erst k3s installieren."; pause ;;
      3) has_kube && k8s_status || yellow "Nicht verfügbar – bitte erst k3s installieren."; pause ;;
      4) has_kube && k8s_delete || yellow "Nicht verfügbar – bitte erst k3s installieren."; pause ;;
      5) show_ips; pause ;;
      6) break ;;
      *) red "Ungültige Auswahl."; sleep 1 ;;
    esac
  done
}

# ==========================
#  Routing
# ==========================
main(){
  need_root
  case "${1:-menu}" in
    deps)        check_deps ;;
    k8s-apply)   k8s_apply ;;
    k8s-status)  k8s_status ;;
    k8s-delete)  k8s_delete ;;
    ip)          show_ips ;;
    menu)        menu_loop ;;
    help|-h|--help) usage ;;
    *) red "Unbekannter Befehl: ${1:-}"; echo; usage; exit 2 ;;
  esac
}

main "$@"
