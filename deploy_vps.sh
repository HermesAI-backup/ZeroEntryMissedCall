#!/usr/bin/env bash
# deploy_vps.sh — Option A: one-command, test-gated deploy (missed-call-ai)
#
# Protocol: missed-call-ai skill -> references/vps-deploy.md
# Decision 2026-08-18 (Sevin): Option A until changes become minor, then
# upgrade to Option B (webhook auto-deploy, still test-gated).
#
# Usage (from anywhere with SSH access to the VPS):
#   ./deploy_vps.sh                          # defaults below
#   VPS_HOST=user@host ./deploy_vps.sh       # override host
#   VPS_DIR=/srv/app  ./deploy_vps.sh        # override project dir
#   SERVICE=myapp     ./deploy_vps.sh        # override systemd unit
#
# Behavior:
#   1. git fetch + pull --ff-only origin main
#   2. TEST GATE: run_tests.sh MUST pass — otherwise exit 1, old version stays live
#   3. systemctl restart <service>
#   4. Health-check /health for up to 30s — on failure: git reset --hard to the
#      previous commit, restart, re-check. Rollback is automatic.
set -euo pipefail

VPS_HOST="${VPS_HOST:-missedcall@vps}"          # override: VPS_HOST=user@1.2.3.4
VPS_DIR="${VPS_DIR:-/opt/missed-call-ai}"       # override: VPS_DIR=/srv/app
SERVICE="${SERVICE:-missed-call-ai}"            # systemd unit name
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/health}"

echo "==> Deploying to ${VPS_HOST}:${VPS_DIR} (service=${SERVICE})"

ssh -o ConnectTimeout=15 "${VPS_HOST}" "bash -s" -- \
    "${VPS_DIR}" "${SERVICE}" "${HEALTH_URL}" <<'REMOTE'
set -euo pipefail
DIR="$1"; SERVICE="$2"; HEALTH="$3"
cd "${DIR}"

# The repo's tests need the project venv on PATH (same python the unit runs).
if [ -d .venv ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

echo "[1/4] git fetch + pull (ff-only)"
git fetch origin main
BEFORE=$(git rev-parse --short HEAD)
git pull --ff-only origin main
AFTER=$(git rev-parse --short HEAD)
if [ "${BEFORE}" = "${AFTER}" ]; then
    echo "    no new commits (${BEFORE}) — nothing to deploy."
    exit 0
fi
echo "    ${BEFORE} -> ${AFTER}"

echo "[2/4] test gate (run_tests.sh)"
if ! bash run_tests.sh; then
    echo "!! TESTS FAILED on ${AFTER} — keeping ${BEFORE} live, NOT restarting."
    exit 1
fi

echo "[3/4] restart service"
systemctl restart "${SERVICE}"

echo "[4/4] health check (${HEALTH})"
for i in $(seq 1 15); do
    if curl -sf "${HEALTH}" >/dev/null 2>&1; then
        echo "OK: ${AFTER} live and healthy after ~$((i * 2))s"
        exit 0
    fi
    sleep 2
done

echo "!! HEALTH CHECK FAILED after deploy — rolling back to ${BEFORE}"
git reset --hard --quiet "${BEFORE}"
systemctl restart "${SERVICE}"
sleep 5
if curl -sf "${HEALTH}" >/dev/null 2>&1; then
    echo "OK: rolled back to ${BEFORE}, healthy."
    exit 0
fi
echo "!! ROLLBACK ALSO FAILED — manual intervention required."
exit 1
REMOTE
