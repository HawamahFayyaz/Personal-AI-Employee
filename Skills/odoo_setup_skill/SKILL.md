# Skill: Odoo Setup

## Purpose
Stand up a local or server Odoo 19 Community instance via Docker Compose in under five minutes.

## Trigger phrases
- "set up Odoo"
- "start Odoo"
- "deploy Odoo locally"
- "init Odoo database"
- "run Odoo with Docker"

---

## Skill steps

### 1. Navigate to the Odoo directory
```bash
cd /mnt/d/HACKATHON_00/AI_Employee_Vault/Odoo
```

### 2. Create `.env` from the template
```bash
cp .env.odoo .env
```
Edit `.env` and set:
- `POSTGRES_PASSWORD` — strong random password
- `ODOO_MASTER_PASSWORD` — strong admin password

### 3. Run the initialization script
```bash
chmod +x init.sh
./init.sh [optional_db_name]
```

The script will:
1. Pull `odoo:19` and `postgres:15` images if not cached.
2. Start both containers (`odoo_db`, `odoo_app`).
3. Wait for Odoo's HTTP port to respond.
4. POST to `/web/database/create` to bootstrap the initial database.
5. Print the access URL.

### 4. Open the browser
```
http://localhost:8069
```
Log in with **admin / admin** and change the password immediately.

---

## Files

| Path | Role |
|------|------|
| `Odoo/docker-compose.yml` | Service definitions (Odoo + PostgreSQL) |
| `Odoo/.env.odoo` | Config template (copy → `.env`) |
| `Odoo/odoo.conf` | Server config mounted at `/etc/odoo/odoo.conf` |
| `Odoo/init.sh` | Bootstrap script |
| `Odoo/addons/` | Drop custom modules here |
| `Odoo/config/` | Additional config files (mounted) |
| `Odoo/README.md` | Full setup & operations guide |

---

## Common operations

### Restart Odoo (after config change)
```bash
docker compose restart odoo
```

### Tail logs
```bash
docker compose logs -f odoo
```

### Stop without deleting data
```bash
docker compose down
```

### Full teardown (data deleted)
```bash
docker compose down -v
```

### Install a custom module
1. Place module directory in `Odoo/addons/`
2. `docker compose restart odoo`
3. In Odoo UI: Settings → Apps → Update Apps List → find and install

---

## Troubleshooting checklist

- [ ] `POSTGRES_PASSWORD` set and not the placeholder value
- [ ] Port 8069 not used by another process (`lsof -i :8069`)
- [ ] Docker daemon running (`docker info`)
- [ ] `odoo:19` image available on Docker Hub (verify with `docker pull odoo:19`)

---

## Notes
- Odoo 19 Community is open-source (LGPL-3.0).
- The `odoo:19` Docker image follows the official Odoo Docker Hub tag convention.
- For production, add a reverse proxy (Nginx/Traefik) with TLS and set `workers >= 2` in `odoo.conf`.
