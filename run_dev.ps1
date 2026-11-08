# Запуск экрана copilot: http://127.0.0.1:8710. Приложение само читает .env.local из папки данных.
param([int]$Port = 8710)
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Порт $Port занят. Остановите прежний экземпляр или запустите с -Port 8711"; exit 1
}
python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
