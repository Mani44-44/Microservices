# Kills and restarts notification-service mid-stream to verify it resumes
# consuming without losing or double-processing events (offset not committed
# until handle_envelope succeeds, idempotency table protects against replay).
Write-Host "Stopping notification-service..."
docker stop notification-service
Start-Sleep -Seconds 3
Write-Host "Starting notification-service..."
docker start notification-service
Write-Host "Done. Check notification-service logs / DB rows to confirm no gaps and no duplicates."
