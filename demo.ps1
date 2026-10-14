<#
.SYNOPSIS
  One-command TicketFlow demo: start the stack, seed data if needed, open the UI.

.EXAMPLE
  .\demo.ps1            # start (or resume) everything and open the UI
  .\demo.ps1 -Stop      # stop the stack (data is kept)
  .\demo.ps1 -Check     # run the end-to-end checks against the running stack

  The stack runs in Docker. On Windows without Docker Desktop it uses Docker
  Engine inside WSL (distro "Ubuntu" by default). WSL shuts down when no
  session is open, so this script keeps one open in the background while the
  demo runs; close it with -Stop.
#>
param(
  [switch]$Stop,
  [switch]$Check,
  [string]$Distro = "Ubuntu"
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

function Invoke-Compose([string]$composeArgs) {
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    Push-Location $Root; try { cmd /c "docker compose $composeArgs" } finally { Pop-Location }
  } else {
    $wslRoot = (wsl -d $Distro -- wslpath -a "$Root").Trim()
    wsl -d $Distro -- bash -c "cd '$wslRoot' && sudo docker compose $composeArgs"
  }
  if ($LASTEXITCODE -ne 0) { throw "docker compose $composeArgs failed" }
}

function Wait-Healthy([string]$url, [int]$timeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $url).StatusCode -eq 200) { return } } catch { }
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for $url"
}

function Find-Python {
  foreach ($c in @("py -3", "python")) {
    $exe, $rest = $c -split " ", 2
    if (Get-Command $exe -ErrorAction SilentlyContinue) { return $c }
  }
  throw "Python 3 is required for seeding / checks"
}

if ($Stop) {
  Invoke-Compose "stop"
  Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
    Where-Object { $_.CommandLine -match "sleep infinity" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
  Write-Host "Stack stopped. Data is kept; run .\demo.ps1 to start again."
  exit 0
}

$py = Find-Python

if ($Check) {
  & cmd /c "$py `"$Root\scripts\e2e_booking_checks.py`""
  exit $LASTEXITCODE
}

# Keep WSL alive for the duration of the demo (no-op with Docker Desktop).
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  $alive = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Where-Object { $_.CommandLine -match "sleep infinity" }
  if (-not $alive) { Start-Process wsl -ArgumentList "-d", $Distro, "--", "sleep", "infinity" -WindowStyle Hidden }
}

Write-Host "Starting the TicketFlow stack (first run builds images: ~10 min; later starts: ~1 min)..."
Invoke-Compose "up -d"

Write-Host "Waiting for services..."
foreach ($u in @(
  "http://localhost:8000/api/v1/health",   # API gateway
  "http://localhost:8007/health",          # agent gateway
  "http://localhost:8009/health",          # support agent
  "http://localhost:8010/health"           # RAG
)) { Wait-Healthy $u }

# Seed the catalog (creates anything missing) and move event dates relative
# to today, so there are always upcoming, bookable events to demo.
Write-Host "Seeding / refreshing catalog data..."
$env:PYTHONPATH = $Root
& cmd /c "$py `"$Root\scripts\seed_data.py`" --refresh-dates"
if ($LASTEXITCODE -ne 0) { throw "Seeding failed (pip install -r scriptsequirements.txt?)" }

Write-Host ""
Write-Host "TicketFlow is up:"
Write-Host "  Browser UI        ui\index.html (opening now)"
Write-Host "  API docs          http://localhost:8000/api/v1/docs"
Write-Host "  Agent API docs    http://localhost:8007/docs"
Write-Host "  Grafana           http://localhost:3000   (admin / admin)"
Write-Host "  Jaeger traces     http://localhost:16686"
Write-Host "  Redpanda console  http://localhost:8080"
Write-Host "Walkthrough: docs\DEMO.md.  Stop with: .\demo.ps1 -Stop"
Start-Process (Join-Path $Root "ui\index.html")
