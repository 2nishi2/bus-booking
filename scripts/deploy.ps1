param([Parameter(Mandatory=$true)][string]$Tag)

$history = "deploy\history.log"
if (-not (Test-Path $history)) { New-Item $history -ItemType File -Force | Out-Null }

$prev = $null
$lastOk = Get-Content $history | Where-Object { $_ -match " OK" } | Select-Object -Last 1
if ($lastOk) { $prev = ($lastOk -split " ")[1] }

function Test-Version($expected) {
  $versionPattern = '"version":\s*"' + [regex]::Escape($expected) + '"'
  for ($i = 0; $i -lt 30; $i++) {
    $ready = curl.exe -k -s https://localhost:8443/ready
    $health = curl.exe -k -s https://localhost:8443/health
    if (($ready -match '"status":\s*"ready"') -and ($health -match $versionPattern)) { return $true }
    Start-Sleep -Seconds 2
  }
  return $false
}

Write-Host "Deploying $Tag (previous good: $prev)"
$env:APP_VERSION = $Tag
docker compose up -d --no-build
$composeOk = ($LASTEXITCODE -eq 0)

if ($composeOk -and (Test-Version $Tag)) {
  Add-Content $history "$(Get-Date -Format s) $Tag OK"
  Write-Host "Deploy of $Tag succeeded"
  exit 0
}

Add-Content $history "$(Get-Date -Format s) $Tag FAILED"
Write-Host "Deploy of $Tag FAILED"
docker compose logs --tail=30 app
if ($prev -and $prev -ne $Tag) {
  Write-Host "Rolling back to $prev"
  $env:APP_VERSION = $prev
  docker compose up -d --no-build
  if (Test-Version $prev) { Add-Content $history "$(Get-Date -Format s) $prev OK (rollback)" }
}
exit 1