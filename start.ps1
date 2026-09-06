# Start the CCT Oil & Cattle source simulator in the background on port 5001.
$root = $PSScriptRoot
$py   = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "no venv -- run: py -3.14 -m venv .venv"; exit 1 }

$existing = Get-Content (Join-Path $root "app.pid") -ErrorAction SilentlyContinue
if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
    Write-Host "already running (pid $existing) -> http://localhost:5001/"; exit 0
}

$p = Start-Process -FilePath $py -ArgumentList "run_app.py" -WorkingDirectory $root `
       -RedirectStandardOutput (Join-Path $root "app.log") `
       -RedirectStandardError  (Join-Path $root "app.err") `
       -PassThru -WindowStyle Hidden
$p.Id | Out-File -FilePath (Join-Path $root "app.pid") -Encoding ascii
Write-Host "started (pid $($p.Id)) -> http://localhost:5001/"
