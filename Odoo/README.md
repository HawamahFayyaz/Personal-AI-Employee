# Odoo 19 Community — Docker Setup

Quick-start for running Odoo 19 Community Edition with PostgreSQL 15 via Docker Compose.

---

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| Docker | 24+ |
| Docker Compose plugin | v2 (bundled with Docker Desktop) |
| curl | any (used by `init.sh`) |

---

## Directory layout

```
Odoo/
├── docker-compose.yml   # Service definitions
├── .env.odoo            # Template — copy to .env and fill in passwords
├── odoo.conf            # Odoo server config (mounted into the container)
├── init.sh              # One-shot bootstrap script
├── config/              # Auto-created; maps to /etc/odoo inside the container
├── addons/              # Drop custom modules here
└── README.md            # This file
```

---

## Quick start (automated)

```bash
cd Odoo/

# 1. Create your .env from the template
cp .env.odoo .env
# Edit .env — set strong POSTGRES_PASSWORD and ODOO_MASTER_PASSWORD

# 2. Make the init script executable and run it
chmod +x init.sh
./init.sh           # creates database "odoo_main" by default
# or: ./init.sh my_company_db
```

The script will:
1. Start `odoo_db` (PostgreSQL) and `odoo_app` containers.
2. Wait until Odoo's HTTP port is responsive.
3. Create the initial database via Odoo's `/web/database/create` endpoint.
4. Print the access URL.

---

## Manual start

```bash
cd Odoo/
cp .env.odoo .env && nano .env   # set passwords

docker compose --env-file .env up -d

# Watch logs
docker compose logs -f odoo
```

Open **http://localhost:8069** in your browser, then navigate to the database manager to create a database.

---

## Stopping & removing

```bash
# Stop (keeps data volumes)
docker compose down

# Stop AND delete all data (irreversible)
docker compose down -v
```

---

## Configuration

### Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DB` | `postgres` | Default PostgreSQL database |
| `POSTGRES_USER` | `odoo` | DB username |
| `POSTGRES_PASSWORD` | *(required)* | DB password — **must be set** |
| `ODOO_MASTER_PASSWORD` | *(required)* | Odoo master/admin password |
| `ODOO_PORT` | `8069` | Host port Odoo is exposed on |

### `odoo.conf`

Edit `odoo.conf` to tune workers, log level, or add SMTP settings. The file is mounted at `/etc/odoo/odoo.conf` inside the container. A restart is required after changes:

```bash
docker compose restart odoo
```

### Custom modules

Place custom module directories inside the `addons/` folder. They are automatically discovered at `/mnt/extra-addons` inside the container.

---

## Production notes

- Set `workers = 2` (or higher) in `odoo.conf` for multi-threaded operation.
- Put Nginx or Traefik in front as a reverse proxy with TLS.
- Back up the `odoo_db_data` and `odoo_web_data` Docker volumes regularly.
- Change the default admin password immediately after first login.
- Restrict port 8069 to localhost; expose only 443 externally.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `odoo_app` exits immediately | Check `docker compose logs odoo` — often a bad DB password or config typo |
| Port 8069 already in use | Set `ODOO_PORT=8070` in `.env` and re-run |
| Database creation fails | Open `http://localhost:8069/web/database/manager` and create manually |
| Module not found | Ensure it's in `addons/` and restart with `docker compose restart odoo` |
