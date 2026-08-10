#!/usr/bin/bash
# Installs the package into the active environment and exercises every
# console script against a fixture database, to catch packaging/import
# regressions that unit-level checks might miss. Assumes `pip` on PATH
# points at the environment to test.
set -e

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing package from ${REPO_DIR}"
pip install --quiet "${REPO_DIR}"

echo "==> Creating fixture database and config"
python3 "${REPO_DIR}/scripts/make-fixture-db.py" "${WORKDIR}/readings.db"

cat > "${WORKDIR}/config.ini" <<EOF
[daemon]
log_level = INFO

[storage]
db_path = ${WORKDIR}/readings.db
EOF

echo "==> trividia-truemetrix-daemon"
trividia-truemetrix-daemon --version
trividia-truemetrix-daemon --help > /dev/null
trividia-truemetrix-daemon --config "${WORKDIR}/config.ini" --check-config

echo "==> trividia-truemetrix-report"
trividia-truemetrix-report --version
trividia-truemetrix-report --help > /dev/null
trividia-truemetrix-report --config "${WORKDIR}/config.ini" --output "${WORKDIR}/out.pdf"
test -s "${WORKDIR}/out.pdf"
trividia-truemetrix-report --config "${WORKDIR}/config.ini" --format csv --output "${WORKDIR}/out.csv"
grep -q "Glucose (mg/dL)" "${WORKDIR}/out.csv"

echo "==> trividia-truemetrix-alert-check"
trividia-truemetrix-alert-check --version
trividia-truemetrix-alert-check --help > /dev/null
trividia-truemetrix-alert-check --config "${WORKDIR}/config.ini" | grep -q "disabled"

echo "==> trividia-truemetrix-prune"
trividia-truemetrix-prune --version
trividia-truemetrix-prune --help > /dev/null
trividia-truemetrix-prune --config "${WORKDIR}/config.ini" --older-than 9999 | grep -q "Would delete 0"

echo "==> trividia-truemetrix-find-unassigned"
trividia-truemetrix-find-unassigned --version
trividia-truemetrix-find-unassigned --help > /dev/null
trividia-truemetrix-find-unassigned --config "${WORKDIR}/config.ini" | grep -q "Trividia-BLU-12345678"

echo "==> trividia-truemetrix-api"
trividia-truemetrix-api --version
trividia-truemetrix-api --help > /dev/null
trividia-truemetrix-api --config "${WORKDIR}/config.ini" | grep -q "disabled"

echo "==> Smoke test passed"
