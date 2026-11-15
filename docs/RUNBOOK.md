# Operations Runbook

Run all commands from the project folder in PowerShell, with Docker Desktop running.

## Deploy
First time: create `secrets\db_password.txt`, then `docker compose up -d --build` and `docker compose ps`.
New version: `$env:APP_VERSION="X"; docker compose build app; .\scripts\deploy.ps1 X`. The script verifies readiness and the reported version, and rolls back automatically on failure.

## Restart
| What | Command |
|---|---|
| App only | `docker compose restart app` |
| Everything | `docker compose restart` |
| Recreate (keeps data) | `docker compose up -d --force-recreate` |

**Never run `docker compose down -v`**: it deletes the database volume.

## Logs
- App (JSON, one line per request): `docker compose logs --tail 50 app`
- Search: `docker compose logs app 2>&1 | Select-String "seat conflict"`
- Proxy: `docker compose logs --tail 20 proxy`
- Every response has an `X-Request-ID` header; the same id appears in the app log line for that request.

## Health
`/health` = process alive (no database). `/ready` = database reachable, returns 503 otherwise.

## Backup and restore
Backup: `.\scripts\backup.ps1` (writes `backups\bus_booking_<time>.dump` and a checksum, keeps the newest 7).
Schedule (optional): `schtasks /Create /SC DAILY /ST 02:00 /TN "BusBookingBackup" /TR "powershell -ExecutionPolicy Bypass -File <project path>\scripts\backup.ps1"`.
Restore: see the README. Copy backups off the machine for real protection.

## Rollback
Bad release: `$env:APP_VERSION="<previous>"; docker compose up -d --no-build`, then check `/health` shows that version.
Bad or lost data: restore the latest good backup, then roll the app back if needed.

## Incident response
| Symptom | Check | Action |
|---|---|---|
| Site unreachable | `docker compose ps`; `docker compose logs proxy` | `docker compose up -d`; check ports 8443/8081 are free |
| `/ready` is 503, `/health` is 200 | `docker compose ps db` | `docker compose start db`; the app recovers by itself |
| App container down (`Exited`) | `docker compose ps -a`; `docker compose logs --tail 40 app` | `docker compose up -d` |
| 5xx after a deploy | App logs for errors | Roll back to the previous version |
| Data deleted or corrupted | n/a | Stop app, restore latest backup, check `/ready` |
| Image build fails scan | CI Trivy log | Update base packages in the Dockerfile and rebuild |