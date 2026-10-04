# TicketFlow - Stop All Services
# Usage: .\stop_all.ps1

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  TicketFlow - Stopping All Services      " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$killed = 0

# Use WMI to find python processes running uvicorn
$wmiProcs = Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*uvicorn*"
}

if ($wmiProcs) {
    foreach ($proc in $wmiProcs) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "[OK] Stopped uvicorn process (PID: $($proc.ProcessId))" -ForegroundColor Green
            $killed++
        } catch {
            Write-Host "[WARN] Could not stop process PID $($proc.ProcessId): $_" -ForegroundColor Yellow
        }
    }
} else {
    # Fallback: kill by port using netstat
    $ports = @(8000, 8001, 8002, 8003, 8004)
    foreach ($port in $ports) {
        $conn = netstat -ano | Select-String ":$port " | Select-String "LISTENING"
        if ($conn) {
            $pidStr = ($conn -split "\s+")[-1].Trim()
            if ($pidStr -match '^\d+$') {
                try {
                    Stop-Process -Id ([int]$pidStr) -Force -ErrorAction SilentlyContinue
                    Write-Host "[OK] Killed process on port $port (PID: $pidStr)" -ForegroundColor Green
                    $killed++
                } catch {
                    Write-Host "[WARN] Could not stop process on port $port" -ForegroundColor Yellow
                }
            }
        }
    }
}

if ($killed -eq 0) {
    Write-Host "[INFO] No running uvicorn services found." -ForegroundColor Gray
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Done. All TicketFlow services stopped.  " -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
