# GearBlast global liste: Firebase -> data/leaderboard.json (+ istege bagli git push)
# Kullanim: powershell -ExecutionPolicy Bypass -File scripts\run_update.ps1
#           powershell -ExecutionPolicy Bypass -File scripts\run_update.ps1 -Push

param([switch]$Push)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$AdminKey = "C:\Gelisim\gearblast-35ada-firebase-adminsdk-fbsvc-e54e8ec38f.json"

function Get-PythonExe {
    $candidates = @(
        "C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe",
        "C:\Users\USER\AppData\Local\Programs\Python\Python311\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    foreach ($name in @("python", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

$Python = Get-PythonExe
if (-not $Python) {
    Write-Host "HATA: Python bulunamadi." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $AdminKey)) {
    Write-Host "HATA: Admin anahtar yok: $AdminKey" -ForegroundColor Red
    exit 1
}

$env:FIREBASE_DB_URL = "https://gearblast-35ada-default-rtdb.europe-west1.firebasedatabase.app"
$env:FIREBASE_SERVICE_ACCOUNT_PATH = $AdminKey

Set-Location $RepoRoot
& $Python -m pip install firebase-admin -q
& $Python scripts\update_leaderboard.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Guncellendi: data\leaderboard.json" -ForegroundColor Green

if ($Push) {
    git add data/leaderboard.json
    git commit -m "📊 Global Leaderboard manual update"
    git push origin main
    Write-Host "GitHub'a push edildi." -ForegroundColor Green
} else {
    Write-Host "Yayin icin: git add data/leaderboard.json && git push"
    Write-Host "veya: .\scripts\run_update.ps1 -Push"
}
