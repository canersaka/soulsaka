# Native Windows environment for the always-on listener (WSL2 cannot see the microphone).
# Run in PowerShell from the repo root:  .\scripts\setup-windows.ps1
$ErrorActionPreference = "Stop"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { winget install --id astral-sh.uv -e }
uv sync --extra listener
Write-Host "pair with the hub running in WSL2:"
Write-Host "  uv run soulsaka hub login --url http://localhost:8765 --code XXXXXXXX"
Write-Host "then: uv run soulsaka listen"
