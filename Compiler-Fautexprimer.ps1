#!/usr/bin/env pwsh
# Compile la version Windows complete de Fautexprimer avec PyInstaller.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python introuvable: $python"
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg est introuvable dans le PATH. Ajoutez son dossier bin avant la compilation."
}

& $python "tools\generate_icon.py"
& $python -m pip install -r "requirements.txt"
& $python -m PyInstaller --noconfirm --clean "Fautexprimer.spec"

$exe = Join-Path $root "dist\Fautexprimer\Fautexprimer.exe"
if (-not (Test-Path $exe)) {
    throw "La compilation a termine sans produire $exe"
}

$distDir = Split-Path $exe
Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination (Join-Path $distDir ".env.example") -Force
Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination (Join-Path $distDir "LICENSE") -Force

$forbiddenDb = Get-ChildItem $distDir -Recurse -Force -File -Include *.db,*.sqlite,*.sqlite3,*.db-wal,*.db-shm
if ($forbiddenDb) {
    throw "Une base de donnees a ete incluse dans la distribution. Supprimez-la avant livraison."
}

# Nettoie les caches Python/PyInstaller sans parcourir le virtualenv ni la distribution.
Remove-Item (Join-Path $root "__pycache__") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root "build") -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Compilation terminee." -ForegroundColor Green
Write-Host "Executable: $exe"
Write-Host "Dossier distribuable: $distDir"
Write-Host "Configuration: renommez .env.example en .env si des cles API sont necessaires."
