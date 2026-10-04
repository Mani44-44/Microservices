# TicketFlow - Start Phase 3 AI Agent Services
# Requires: docker compose up -d postgres rag-postgres  (and Phase 1 services running via start_all.ps1)
# Usage: .\start_phase3.ps1

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  TicketFlow - Starting Phase 3 Services  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$env:PYTHONPATH = "X:\ticket-booking\ticketflow"
$env:INTERNAL_SHARED_SECRET = "dev-internal-secret-change-me"
# ponytail: a native PostgreSQL 17 Windows service squats on host port 5433,
# shadowing the docker-compose "postgres" container's old port mapping for
# ALL localhost connections. Moved that container to host port 5440 instead
# of touching the system service. Only Phase 3 services need this override
# (Phase 1 processes already running keep their existing connections).
$env:POSTGRES_PORT = "5440"

Write-Host ""
Write-Host "Starting AI agent microservices..." -ForegroundColor Cyan

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\rag-service; uvicorn app.main:app --port 8010 --reload`""
Write-Host "[OK] RAG Retrieval Service -> http://localhost:8010" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\guardrail-service; uvicorn app.main:app --port 8011 --reload`""
Write-Host "[OK] Guardrail Service     -> http://localhost:8011" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\eval-service; uvicorn app.main:app --port 8012 --reload`""
Write-Host "[OK] Evaluation Service    -> http://localhost:8012" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\travel-planner-agent; uvicorn app.main:app --port 8008 --reload`""
Write-Host "[OK] Travel Planner Agent  -> http://localhost:8008" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\support-agent; uvicorn app.main:app --port 8009 --reload`""
Write-Host "[OK] Support Agent         -> http://localhost:8009" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit -Command `"cd X:\ticket-booking\ticketflow\services\agent-gateway; uvicorn app.main:app --port 8007 --reload`""
Write-Host "[OK] Agent Gateway         -> http://localhost:8007" -ForegroundColor Green

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  All Phase 3 services started!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Entry point:" -ForegroundColor White
Write-Host "  Agent Gateway (chat)   : http://localhost:8007/docs" -ForegroundColor Yellow
Write-Host ""
Write-Host "To stop, close the opened terminal windows (or run .\stop_all.ps1 if it covers these too)." -ForegroundColor Gray
