# Start Muneem (Windows PowerShell).
#   ./dev.ps1            -> backend on :8000 (new window) + frontend on :5173 (this window)
#   ./dev.ps1 -Backend   -> backend only (API docs: http://localhost:8000/docs)
#   ./dev.ps1 -Test      -> run the backend test suite
param([switch]$Test, [switch]$Backend, [int]$Port = 8000)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating Python virtual environment..."
    py -3 -m venv (Join-Path $backend ".venv")
    & $py -m pip install --upgrade pip
    & $py -m pip install -r (Join-Path $backend "requirements.txt")
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "No .env found - running without ArangoDB and without AI (copy .env.example to .env)."
}

if ($Test) {
    Push-Location $backend; try { & $py -m pytest } finally { Pop-Location }
    exit
}

$api = "& '$py' -m uvicorn app.main:app --app-dir '$backend' --port $Port --reload --reload-dir '$backend\app'"
if ($Backend) { Invoke-Expression $api; exit }

Start-Process powershell -ArgumentList "-NoExit", "-Command", $api
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Push-Location $frontend; try { npm install } finally { Pop-Location }
}
Write-Host "Open http://localhost:5173"
Push-Location $frontend; try { npm run dev } finally { Pop-Location }
