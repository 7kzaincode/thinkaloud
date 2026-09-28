# Freeze engine.py (recorder + processor + exports + AI review + batch) into engine/dist/thinkaloud-engine/
# Run from anywhere: powershell -ExecutionPolicy Bypass -File engine/build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m pip install --quiet pyinstaller==6.22.3 `
  -r (Join-Path $root "recorder\requirements.txt") `
  -r (Join-Path $root "processor\requirements.txt") `
  -r (Join-Path $root "processor\requirements-ai.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# comtypes generates its UI Automation wrapper on first use; generate it now so the
# frozen engine ships it (a frozen app can't write into its own package folder).
& $py -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll'); import comtypes.gen.UIAutomationClient as m; print('uia wrapper:', m.__name__)"
if ($LASTEXITCODE -ne 0) { throw "could not generate the UI Automation wrapper" }

& $py -m PyInstaller `
  --noconfirm --clean --onedir --console `
  --name thinkaloud-engine `
  --distpath (Join-Path $PSScriptRoot "dist") `
  --workpath (Join-Path $PSScriptRoot "build") `
  --specpath (Join-Path $PSScriptRoot "build") `
  --paths (Join-Path $root "recorder") `
  --paths (Join-Path $root "processor") `
  --hidden-import record `
  --hidden-import uia `
  --hidden-import winctx `
  --hidden-import thinkaloud.__main__ `
  --hidden-import thinkaloud.commands `
  --hidden-import thinkaloud.export `
  --hidden-import thinkaloud.dataset `
  --hidden-import thinkaloud.ai_review `
  --hidden-import thinkaloud.batch `
  --hidden-import thinkaloud.details `
  --hidden-import pynput.keyboard._win32 `
  --hidden-import pynput.mouse._win32 `
  --collect-submodules comtypes `
  --collect-all faster_whisper `
  --collect-all ctranslate2 `
  --collect-all sounddevice `
  --collect-all anthropic `
  --collect-all google.genai `
  --collect-data _sounddevice_data `
  --exclude-module tkinter `
  --exclude-module matplotlib `
  (Join-Path $PSScriptRoot "engine.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
# dataset.py is copied into every export bundle, so the frozen engine needs it as a file too
$dst = Join-Path $PSScriptRoot "dist\thinkaloud-engine\_internal\thinkaloud"
New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item (Join-Path $root "processor\thinkaloud\dataset.py") $dst -Force
Write-Host "engine built: $PSScriptRoot\dist\thinkaloud-engine"
