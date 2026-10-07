param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$projectRoot = Split-Path -Parent $PSScriptRoot
$configFile = (Resolve-Path -LiteralPath $Config).Path
$relayExecutable = Join-Path $projectRoot 'Relay.exe'
if (-not $Python -and (Test-Path -LiteralPath $relayExecutable)) {
    & $relayExecutable --config $configFile start
    exit $LASTEXITCODE
}
if (-not $Python) {
    $venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
    $Python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source }
}
& $Python -X utf8 (Join-Path $projectRoot 'relay.py') --config $configFile start
exit $LASTEXITCODE
