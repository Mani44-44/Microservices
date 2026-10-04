# TicketFlow - Start All Services
# Usage: .\start_all.ps1

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  TicketFlow - Starting All Services      " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Start Redis (if not running)
$redisPath = "C:\Users\dell\AppData\Local\Microsoft\WinGet\Packages\taizod1024.redis-windows-fork_Microsoft.Winget.Source_8wekyb3d8bbwe\Redis-8.10.1-Windows-x64-msys2\redis-server.exe"
if (-not (Get-Process redis-server -ErrorAction SilentlyContinue)) {
    if (Test-Path $redisPath) {
        Start-Process $redisPath -ArgumentList "--port 6379" -WindowStyle Hidden
        Start-Sleep 2
        Write-Host "[OK] Redis started on port 6379" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Redis executable not found at expected path. Assuming Redis is already running." -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] Redis already running" -ForegroundColor Green
}

$env:PYTHONPATH = "X:\ticket-booking\ticketflow"
$env:INTERNAL_SHARED_SECRET = "dev-internal-secret-change-me"

# Start each service in a new terminal
Write-Host ""
Write-Host "Starting microservices..." -ForegroundColor Cyan

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\user-service; uvicorn app.main:app --port 8001 --reload`""
Write-Host "[OK] User Service    -> http://localhost:8001" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\catalog-service; uvicorn app.main:app --port 8002 --reload`""
Write-Host "[OK] Catalog Service -> http://localhost:8002" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\booking-service; uvicorn app.main:app --port 8003 --reload`""
Write-Host "[OK] Booking Service -> http://localhost:8003" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\payment-service; uvicorn app.main:app --port 8004 --reload`""
Write-Host "[OK] Payment Service -> http://localhost:8004" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\gateway; uvicorn app.main:app --port 8000 --reload`""
Write-Host "[OK] API Gateway     -> http://localhost:8000" -ForegroundColor Green

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  All services started!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service URLs:" -ForegroundColor White
Write-Host "  API Gateway     : http://localhost:8000" -ForegroundColor Yellow
Write-Host "  User Service    : http://localhost:8001" -ForegroundColor Yellow
Write-Host "  Catalog Service : http://localhost:8002" -ForegroundColor Yellow
Write-Host "  Booking Service : http://localhost:8003" -ForegroundColor Yellow
Write-Host "  Payment Service : http://localhost:8004" -ForegroundColor Yellow
Write-Host ""
Write-Host "API Docs (Swagger UI):" -ForegroundColor White
Write-Host "  http://localhost:8000/docs" -ForegroundColor Yellow
Write-Host ""
Write-Host "To stop all services run: .\stop_all.ps1" -ForegroundColor Gray
