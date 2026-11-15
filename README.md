# Bus Booking: Cloud Deployment and CI/CD

A small trip and seat reservation API (Flask + PostgreSQL) packaged with Docker Compose, an HTTPS reverse proxy (Caddy), a GitHub Actions CI pipeline, health checks, structured logs, backup/restore and versioned deploy with rollback.

**Stack:** Python 3.12, Flask, gunicorn, PostgreSQL 16, Docker Compose, Caddy, GitHub Actions, GHCR, Trivy, ruff, pytest. Tested on Windows with PowerShell.

## Architecture

```mermaid
flowchart LR
    U["Client"] -->|"HTTPS 8443"| P["Caddy proxy"]
    subgraph Docker["Docker host"]
      subgraph FE["frontend network"]
        P -->|"HTTP 8000"| A["Flask API - gunicorn"]
      end
      subgraph BE["backend network - internal only"]
        A -->|"5432"| D[("PostgreSQL - volume pgdata")]
      end
      S["secrets/db_password.txt"] -.-> A
      S -.-> D
      B["scripts/backup.ps1"] --> D
      B --> F[("backups folder")]
    end
    G["GitHub Actions: lint, test, build, Trivy scan"] --> R[("GHCR image: tag + digest")]
    R -.->|"scripts/deploy.ps1 tag"| A
```

## Quick start (Windows, Docker Desktop + PowerShell)

```powershell
git clone https://github.com/2nishi2/bus-booking.git
cd bus-booking
mkdir secrets
Set-Content -NoNewline -Path secrets\db_password.txt -Value "<<choose a long random password>>"
docker compose up -d --build
docker compose ps
curl.exe -k -s https://localhost:8443/ready
```

Wait until `db`, `app` and `proxy` are healthy. `/ready` should return `{"status":"ready"}`. `-k` is needed because the local certificate is self-signed.

## Port map

| Service | Container port | Host port | Reachable from the PC? |
|---|---|---|---|
| proxy (HTTPS) | 443 | 8443 | Yes |
| proxy (HTTP) | 80 | 8081 | Yes |
| app (gunicorn) | 8000 | none | No, only through the proxy |
| db (PostgreSQL) | 5432 | none | No, internal network only |

If 8081 or 8443 is busy on your machine, change the left-hand number in `docker-compose.yml`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_VERSION` | `local` | Image tag; reported by `/health` |
| `IMAGE_REPO` | `bus-booking` | Image repository (set to `ghcr.io/<user>/<repo>` to use CI images) |
| `DB_HOST`, `DB_NAME`, `DB_USER` | set in compose | Database connection |
| `DB_PASSWORD_FILE` | `/run/secrets/db_password` | Password is read from a secret file, never from code or Git |
| `DATABASE_URL` | unset | Used by tests and CI to point at a test database |

No real secrets are committed. `secrets/` and `backups/` are git-ignored.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness (never touches the database) |
| `GET /ready` | Readiness (503 if the database is down) |
| `GET /api/v1/routes`, `/routes/{id}` | Routes and stops |
| `GET /api/v1/trips?route_id=&date=` | Upcoming trips |
| `GET /api/v1/trips/{id}/seats` | Seat map |
| `POST /api/v1/bookings` | Create a booking, returns a PNR |
| `GET /api/v1/bookings/{pnr}` | Ticket details |
| `POST /api/v1/bookings/{pnr}/cancel` | Cancel a booking |

Meaning of codes: **201** booking created, **409** seat already booked (no PNR created), **422** invalid input, **404** not found, **503** database unavailable.

The no-double-booking rule is enforced by the database with a partial unique index on `(trip_id, seat_no)` where `status = 'CONFIRMED'`, so concurrent requests are safe. A cancelled seat can be booked again.

## Tests and CI/CD

```powershell
pip install -r requirements-dev.txt
docker run -d --name testdb -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=bus_test -p 5433:5432 postgres:16-alpine
$env:DATABASE_URL = "postgresql://test:test@localhost:5433/bus_test"
pytest -v
```

The suite includes a test that fires 20 simultaneous requests at one seat: exactly one succeeds and nineteen get 409.

GitHub Actions (`.github/workflows/ci.yml`) runs on every push: ruff format and lint, tests against a PostgreSQL service, then image build, Trivy scan (fails on fixable HIGH/CRITICAL) and push to GHCR with tags `sha-<commit>`, plus `<version>` for `vX.Y.Z` tags. The digest is written to the run summary. Release: `git tag v1.0.1; git push origin v1.0.1`.

## Deploy and rollback

```powershell
$env:APP_VERSION = "1.0.1"
docker compose build app
.\scripts\deploy.ps1 1.0.1        # deploys, verifies /ready and the reported version, rolls back automatically on failure
```

Manual rollback to a previous version:

```powershell
$env:APP_VERSION = "1.0.0"
docker compose up -d --no-build
curl.exe -k -s https://localhost:8443/health
```

Deploy history is recorded in `deploy/history.log`.

## Backup and restore

```powershell
.\scripts\backup.ps1                      # pg_dump + SHA256 checksum, keeps the newest 7
```

Restore (the app is stopped during the restore so nothing writes mid-way):

```powershell
docker compose stop app
docker compose cp "backups\<file>.dump" db:/tmp/restore.dump
docker compose exec -T db sh -c "pg_restore -U bus -d bus_booking --clean --if-exists --no-owner --single-transaction /tmp/restore.dump"
docker compose start app
```

Evidence of a full backup, delete and restore cycle is in `evidence/restore_drill.log`. More operations detail: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Known limitations and assumptions

- Local HTTPS uses Caddy's self-signed certificate. A public domain would get a real certificate automatically.
- Backups are stored on the same machine; they should be copied off-host in production.
- `docker kill` leaves a container down (the restart policy ignores manual stops); recovery is `docker compose up -d`.
- << - **Automatic rollback was unreliable on the development machine.** During the rollback drill, `deploy.ps1` reported failures for versions that had worked before (1.0.1 and then the rollback to 1.0.0). The app's logs showed it had started normally (gunicorn running, workers booted), but Docker marked the container `unhealthy` because its health check exceeded the 3-second timeout on some checks. The database also took about 80 seconds to become healthy at one point. This suggests Docker Desktop was short of CPU or memory, so the health checks timed out rather than the app being broken. Because the proxy waits for a healthy app (`depends_on: service_healthy`), the failed health check also stopped the deploy.
- **Mitigation applied:** the app's health check in `docker-compose.yml` was given a longer timeout, more retries and a start period, so slow machines don't produce false failures.
- **`docker kill` does not trigger the restart policy.** A container stopped manually stays down (`Exited (137)`); recovery is `docker compose up -d`. A crash from inside the container is restarted automatically by `restart: unless-stopped`.
- **First restore drill log was invalid.** The checksum file contained a formatted table instead of a plain hash, so the checksum comparison reported a mismatch and the transcript ended early. The restore itself worked (the same PNR came back with HTTP 200). The backup script was corrected to write only the hash, and the drill was re-run.
- **A vulnerability scan failure was fixed during CI setup.** Trivy found two fixable HIGH issues in the base image's `zlib1g` package; the Dockerfile now upgrades system packages at build time.>>
- Metrics endpoint was not implemented (stretch goal).
- No authentication or payments (out of scope).
- Dates are stored as ISO text for simplicity.