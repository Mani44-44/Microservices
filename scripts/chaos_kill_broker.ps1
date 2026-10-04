# Kills and restarts the Redpanda broker container to verify consumers resume
# from their last committed offset (uncommitted messages replay, none are lost).
Write-Host "Stopping redpanda..."
docker stop redpanda
Start-Sleep -Seconds 3
Write-Host "Starting redpanda..."
docker start redpanda
Write-Host "Done. Check redpanda-console (http://localhost:8080) for consumer lag draining back to zero."
