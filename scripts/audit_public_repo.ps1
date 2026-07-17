$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Python = Get-Command python -ErrorAction SilentlyContinue
if (!$Python) { throw "Python 3 is required to run the public repository audit." }
& $Python.Source (Join-Path $Root "scripts\audit_public_repo.py")
exit $LASTEXITCODE

