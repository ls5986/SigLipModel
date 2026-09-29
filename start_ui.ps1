$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create the virtual environment and install requirements first. See README.md.'
}
& $python -B (Join-Path $PSScriptRoot 'launch.py') --open @args
exit $LASTEXITCODE
