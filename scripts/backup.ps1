Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force backups | Out-Null

$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$file = "backups\bus_booking_$stamp.dump"

docker compose exec -T db sh -c "pg_dump -U bus -d bus_booking -Fc -f /tmp/backup.dump"
docker compose cp db:/tmp/backup.dump $file

if (-not (Test-Path $file) -or (Get-Item $file).Length -eq 0) {
  Write-Host "Backup FAILED"
  exit 1
}

$h = (Get-FileHash $file -Algorithm SHA256).Hash
Set-Content -Path "$file.sha256.txt" -Value "$h  $(Split-Path $file -Leaf)" -Encoding ascii

# Retention: keep only the 7 newest backups
Get-ChildItem backups\*.dump | Sort-Object LastWriteTime -Descending | Select-Object -Skip 7 | Remove-Item

Write-Host "Backup OK: $file"