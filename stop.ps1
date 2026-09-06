# Stop the simulator.
$pidFile = Join-Path $PSScriptRoot "app.pid"
if (-not (Test-Path $pidFile)) { Write-Host "not running (no app.pid)"; exit 0 }
$appPid = Get-Content $pidFile
if (Get-Process -Id $appPid -ErrorAction SilentlyContinue) {
    Stop-Process -Id $appPid -Force
    Write-Host "stopped (pid $appPid)"
} else {
    Write-Host "not running (stale pid $appPid)"
}
Remove-Item $pidFile -ErrorAction SilentlyContinue
