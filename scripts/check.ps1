$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $Root
try {
  if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Missing .venv\Scripts\python.exe. Create the virtual environment and install requirements first."
  }

  .\.venv\Scripts\python.exe -m compileall asoul_reminder run.py
  .\.venv\Scripts\python.exe -m unittest discover -s tests
  .\.venv\Scripts\python.exe -c "from pathlib import Path; from asoul_reminder.config import load_config; t=load_config(Path('config.example.json')); c=load_config(); print('template ok:', len(t.members), 'members'); print('config ok:', len(c.members), 'members')"

  Write-Host "Checks completed."
}
finally {
  Pop-Location
}
