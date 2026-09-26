# Freeze engine.py (recorder + processor + faster-whisper) into engine/dist/thinkaloud-engine/
# Run from anywhere: powershell -ExecutionPolicy Bypass -File engine/build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m pip install --quiet pyinstaller==6.22.3
& $py -m PyInstaller `
  --noconfirm --clean --onedir --console `
  --name thinkaloud-engine `
  --distpath (Join-Path $PSScriptRoot "dist") `
  --workpath (Join-Path $PSScriptRoot "build") `
  --specpath (Join-Path $PSScriptRoot "build") `
  --paths (Join-Path $root "recorder") `
  --paths (Join-Path $root "processor") `
  --hidden-import record `
  --hidden-import thinkaloud.__main__ `
  --hidden-import pynput.keyboard._win32 `
  --hidden-import pynput.mouse._win32 `
  --collect-all faster_whisper `
  --collect-all ctranslate2 `
  --collect-all sounddevice `
  --collect-data _sounddevice_data `
  --exclude-module tkinter `
  --exclude-module matplotlib `
  (Join-Path $PSScriptRoot "engine.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "engine built: $PSScriptRoot\dist\thinkaloud-engine"
