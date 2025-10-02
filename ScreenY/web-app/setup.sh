set -euo pipefail

SERVICE_NAME="${1:-screeny}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ENTRY="${APP_DIR}/app.py"
PY_BIN="$(command -v python3 || true)"

if [[ -z "${PY_BIN}" ]]; then
  echo "python3 nicht gefunden." >&2
  exit 1
fi

if [[ ! -f "${APP_ENTRY}" ]]; then
  echo "app.py nicht im Ordner gefunden: ${APP_ENTRY}" >&2
  exit 1
fi

# Root-Rechte sicherstellen
if [[ "${EUID}" -ne 0 ]]; then
  echo "→ brauche Root. Re-exec mit sudo…"
  exec sudo -E bash "$0" "$SERVICE_NAME"
fi

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Screeny (${SERVICE_NAME})
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${PY_BIN} ${APP_ENTRY}
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "→ Dienstdatei geschrieben: ${UNIT_PATH}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"
sleep 0.5
systemctl --no-pager --full status "${SERVICE_NAME}" || true

echo
echo "Fertig. Befehle:"
echo "  journalctl -u ${SERVICE_NAME} -f"
echo "  systemctl restart ${SERVICE_NAME}"
echo "  systemctl disable --now ${SERVICE_NAME} && rm -f ${UNIT_PATH}   # entfernen"
