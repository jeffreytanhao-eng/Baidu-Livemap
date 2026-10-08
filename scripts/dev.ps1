# 本地一键启动：API(8000) + Web(3000)，不依赖 Docker
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path "$Root\.env")) {
  Copy-Item "$Root\.env.example" "$Root\.env"
  Write-Host "[dev] 已复制 .env.example -> .env（无 AK 将走 DEMO_MODE 快照）"
}

if (-not (Test-Path "$Root\apps\api\.venv")) {
  python -m venv "$Root\apps\api\.venv"
}
$VenvPy = "$Root\apps\api\.venv\Scripts\python.exe"
& $VenvPy -m pip install -q -r "$Root\apps\api\requirements.txt"
& $VenvPy -m pip install -q -e "$Root\packages\geo"

if (-not (Test-Path "$Root\apps\web\node_modules")) {
  Push-Location "$Root\apps\web"; npm install; Pop-Location
}

$api = Start-Process -PassThru -NoNewWindow $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--reload","--port","8000" -WorkingDirectory "$Root\apps\api"
try {
  Push-Location "$Root\apps\web"
  npm run dev
} finally {
  Pop-Location
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
