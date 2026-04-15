$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Starting backend on http://127.0.0.1:5000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; .\.venv\Scripts\Activate.ps1; python -m uvicorn backend.asgi:asgi_app --app-dir . --host 127.0.0.1 --port 5000 --reload"

Write-Host "Starting frontend on http://127.0.0.1:5500"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\\frontend'; npm run dev"

Write-Host "Demo startup commands launched."
