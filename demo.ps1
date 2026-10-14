<#
.SYNOPSIS
  One-command TicketFlow demo: start the stack, seed data if needed, open the UI.

.EXAMPLE
  .\demo.ps1            # start (or resume) everything and open the UI
  .\demo.ps1 -Stop      # stop the stack (data is kept)
  .\demo.ps1 -Check     # end-to-end + concurrency checks against the running stack
  .\demo.ps1 -UiTest    # browser test of the UI (installs headless Chromium once)

  The stack runs in Docker. On Windows without Docker Desktop it uses Docker
  Engine inside WSL (distro "Ubuntu" by default). WSL shuts down when no
  session is open, so this script keeps one open in the background while the
  demo runs; close it with -Stop.
#>
param(
  [switch]$Stop,
  [switch]$Check,
  [switch]$UiTest,
  [switch]$NoBrowser,
  [string]$Distro = "Ubuntu"
)
# "Continue", not "Stop": in Windows PowerShell 5.1, "Stop" turns any stderr
# output from native tools (pip warnings, docker progress) into a fatal error.
# Failures are detected through $LASTEXITCODE and explicit throws instead.
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot

function Invoke-Compose([string]$composeArgs) {
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    Push-Location $Root; try { cmd /c "docker compose $composeArgs" } finally { Pop-Location }
  } else {
    # wsl.exe strips backslashes from arguments, so translate C:\x\y -> /mnt/c/x/y here.
    $wslRoot = "/mnt/" + $Root.Substring(0, 1).ToLower() + $Root.Substring(2).Replace("\", "/")
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

function Get-DemoPython {
  # A private venv (.venv, git-ignored) with scripts\requirements.txt, so
  # seeding and the checks never depend on the system Python's packages.
  $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path $venvPy)) {
    $base = $null
    foreach ($v in @("3.12", "3.11", "3.13")) {
      if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = $false
        & py "-$v" -c "import sys" 2>$null | Out-Null
        $found = ($LASTEXITCODE -eq 0)
        if ($found) { $base = @("py", "-$v"); break }
      }
    }
    if (-not $base) {
      if (Get-Command python -ErrorAction SilentlyContinue) { $base = @("python") }
      else { throw "Python 3.11+ is required for seeding and checks" }
    }
    Write-Host "Creating .venv with $($base -join ' ') (first run only)..."
    $exe = $base[0]; $pyArgs = @($base | Select-Object -Skip 1)
    & $exe @pyArgs -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv" }
  }
  $req = Join-Path $Root "scripts\requirements.txt"
  $stamp = Join-Path $Root ".venv\.requirements-installed"
  if (-not (Test-Path $stamp) -or (Get-Item $req).LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
    Write-Host "Installing script dependencies into .venv..."
    & $venvPy -m pip install -q --disable-pip-version-check -r $req
    if ($LASTEXITCODE -ne 0) { throw "pip install -r scripts\requirements.txt failed" }
    Set-Content -Path $stamp -Value (Get-Date -Format o)
  }
  return $venvPy
}

if ($Stop) {
  Invoke-Compose "stop"
  Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
    Where-Object { $_.CommandLine -match "sleep infinity" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
  Write-Host "Stack stopped. Data is kept; run .\demo.ps1 to start again."
  exit 0
}

$py = Get-DemoPython
$env:PYTHONPATH = $Root
$env:PYTHONIOENCODING = "utf-8"

if ($Check) {
  & $py (Join-Path $Root "scripts\e2e_booking_checks.py"); $e2e = $LASTEXITCODE
  & $py (Join-Path $Root "scripts\concurrency_checks.py"); $conc = $LASTEXITCODE
  exit ($e2e + $conc)
}

if ($UiTest) {
  & $py -m playwright install chromium
  & $py (Join-Path $Root "scripts\ui_smoke.py")
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
& $py (Join-Path $Root "scripts\seed_data.py") --refresh-dates
if ($LASTEXITCODE -ne 0) { throw "Seeding failed" }

Write-Host ""
Write-Host "TicketFlow is up:"
Write-Host "  Browser UI        $(Join-Path $Root 'ui\index.html')"
Write-Host "  API docs          http://localhost:8000/api/v1/docs"
Write-Host "  Agent API docs    http://localhost:8007/docs"
Write-Host "  Grafana           http://localhost:3000   (admin / admin)"
Write-Host "  Jaeger traces     http://localhost:16686"
Write-Host "  Redpanda console  http://localhost:8080"
Write-Host "Walkthrough: docs\DEMO.md.  Stop with: .\demo.ps1 -Stop"
if (-not $NoBrowser) { Start-Process (Join-Path $Root "ui\index.html") }
