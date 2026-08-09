#!/usr/bin/bash
# Installs trividia-truemetrix-daemon: creates a venv, installs the package
# from this checkout, seeds the config, creates the service user, and
# installs and enables the systemd unit. Re-running is safe: it skips steps
# that are already done (existing config, existing user) and upgrades the
# rest.
set -e

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (e.g. with sudo)." >&2
    exit 1
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: sudo ./install.sh"
    echo "Installs trividia-truemetrix-daemon as a systemd service. No options."
    exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/trividia-truemetrix-daemon"
CONFIG_DIR="/etc/trividia-truemetrix-daemon"
SERVICE_USER="trividia-truemetrix-daemon"

echo "==> Creating virtual environment at ${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"

echo "==> Installing trividia-truemetrix-daemon from ${REPO_DIR}"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet "${REPO_DIR}"

echo "==> Linking commands into /usr/bin"
ln -sf "${INSTALL_DIR}/venv/bin/trividia-truemetrix-daemon" /usr/bin/trividia-truemetrix-daemon
ln -sf "${INSTALL_DIR}/venv/bin/trividia-truemetrix-api" /usr/bin/trividia-truemetrix-api
ln -sf "${INSTALL_DIR}/venv/bin/trividia-truemetrix-find-unassigned" \
    /usr/bin/trividia-truemetrix-find-unassigned
ln -sf "${INSTALL_DIR}/venv/bin/trividia-truemetrix-report" /usr/bin/trividia-truemetrix-report
ln -sf "${INSTALL_DIR}/venv/bin/trividia-truemetrix-alert-check" \
    /usr/bin/trividia-truemetrix-alert-check

echo "==> Seeding config"
mkdir -p "${CONFIG_DIR}"
if [[ -f "${CONFIG_DIR}/config.ini" ]]; then
    echo "    ${CONFIG_DIR}/config.ini already exists, leaving it as-is."
else
    cp "${REPO_DIR}/config/trividia-truemetrix-daemon.ini.example" "${CONFIG_DIR}/config.ini"
    echo "    Wrote ${CONFIG_DIR}/config.ini -- edit it before (or after) starting the service."
fi

echo "==> Creating service user"
if ! id "${SERVICE_USER}" &>/dev/null; then
    # plugdev grants USB HID access via the udev rule in the README --
    # without it, the daemon can enumerate the meter but not open it.
    useradd --system --no-create-home --user-group --groups plugdev "${SERVICE_USER}"
fi

echo "==> Installing systemd units"
cp "${REPO_DIR}/systemd/trividia-truemetrix-daemon.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/trividia-truemetrix-api.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/trividia-truemetrix-alert-check.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/trividia-truemetrix-alert-check.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trividia-truemetrix-daemon

echo "==> Done. Edit ${CONFIG_DIR}/config.ini if you haven't, then watch sync with:"
echo "        journalctl -u trividia-truemetrix-daemon -f"
echo "==> The device-assignment API and alert-check timer are installed but not enabled"
echo "    (opt-in). To turn them on:"
echo "        sudo systemctl enable --now trividia-truemetrix-api.service"
echo "        sudo systemctl enable --now trividia-truemetrix-alert-check.timer"
