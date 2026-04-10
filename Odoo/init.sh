#!/usr/bin/env bash
# Odoo 19 Docker initialization script
# Usage: ./init.sh [db_name]
set -euo pipefail

DB_NAME="${1:-odoo_main}"
ODOO_PORT="${ODOO_PORT:-8069}"
ENV_FILE=".env"
COMPOSE_FILE="docker-compose.yml"

# ── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────
command -v docker        >/dev/null 2>&1 || error "docker is not installed."
command -v docker compose >/dev/null 2>&1 || \
  command -v docker-compose >/dev/null 2>&1 || \
  error "docker compose (or docker-compose) is not installed."

[[ -f "$COMPOSE_FILE" ]] || error "docker-compose.yml not found. Run this script from the Odoo/ directory."

# ── Load or create .env ───────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f ".env.odoo" ]]; then
    info "No .env found — copying .env.odoo to .env"
    cp .env.odoo "$ENV_FILE"
    warn "Edit $ENV_FILE and replace placeholder passwords before continuing."
    warn "Then re-run this script."
    exit 1
  else
    error "Neither .env nor .env.odoo found."
  fi
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
[[ -n "$POSTGRES_PASSWORD" && "$POSTGRES_PASSWORD" != "change_me_strong_password" ]] \
  || error "Set a real POSTGRES_PASSWORD in $ENV_FILE before running this script."

# ── Start containers ──────────────────────────────────────────────────────────
info "Starting containers..."
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  COMPOSE="docker-compose"
fi

$COMPOSE --env-file "$ENV_FILE" up -d

# ── Wait for Odoo HTTP ────────────────────────────────────────────────────────
info "Waiting for Odoo to become ready on port $ODOO_PORT..."
MAX_WAIT=120
ELAPSED=0
until curl -sf "http://localhost:${ODOO_PORT}/web/health" >/dev/null 2>&1 || \
      curl -sf "http://localhost:${ODOO_PORT}" >/dev/null 2>&1; do
  if (( ELAPSED >= MAX_WAIT )); then
    error "Odoo did not become ready within ${MAX_WAIT}s. Check logs: $COMPOSE logs odoo"
  fi
  sleep 3
  (( ELAPSED += 3 ))
  info "  ...still waiting (${ELAPSED}s / ${MAX_WAIT}s)"
done
info "Odoo is up."

# ── Create initial database ───────────────────────────────────────────────────
MASTER_PASS="${ODOO_MASTER_PASSWORD:-admin}"

info "Creating database '$DB_NAME'..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:${ODOO_PORT}/web/database/create" \
  --data-urlencode "master_pwd=${MASTER_PASS}" \
  --data-urlencode "name=${DB_NAME}" \
  --data-urlencode "lang=en_US" \
  --data-urlencode "password=admin" \
  --data-urlencode "login=admin" \
  --data-urlencode "demo=False" \
  --data-urlencode "phone=" \
  --data-urlencode "country_id=False")

if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "303" || "$HTTP_STATUS" == "302" ]]; then
  info "Database '$DB_NAME' created (or already exists)."
else
  warn "Database creation returned HTTP $HTTP_STATUS."
  warn "The database may already exist, or you can create it manually at:"
  warn "  http://localhost:${ODOO_PORT}/web/database/manager"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Odoo 19 Community is ready!"
echo "  URL:      http://localhost:${ODOO_PORT}"
echo "  Database: ${DB_NAME}"
echo "  Login:    admin / admin  (change immediately)"
echo "  DB Mgr:   http://localhost:${ODOO_PORT}/web/database/manager"
echo "============================================================"
