# Installed as run-relay.ps1 beside installation.json. All CLI arguments are forwarded literally.
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$bindingPath = Join-Path $PSScriptRoot 'installation.json'
if (!(Test-Path -LiteralPath $bindingPath)) { throw 'Run the Relay skill installer first.' }
$binding = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($binding.schema_version -ne 1 -or $binding.package -ne 'relay-collaboration') {
    throw 'Invalid Relay installation binding; reinstall the skill.'
}
if ($binding.executable) {
    $command = $binding.executable
    $prefix = @()
    $requiredFiles = @($command, $binding.config)
} else {
    $command = $binding.python
    $entry = Join-Path $binding.package_root 'relay.py'
    $prefix = @('-B', '-X', 'utf8', $entry)
    $requiredFiles = @($command, $entry, $binding.config)
}
foreach ($required in $requiredFiles) {
    if (![IO.Path]::IsPathRooted($required) -or !(Test-Path -LiteralPath $required -PathType Leaf)) {
        throw 'Relay moved or a file is missing. Reinstall with Relay.exe install --rebind or install.ps1 -InstallSkills both -RebindSkills.'
    }
}
Push-Location -LiteralPath $binding.package_root
try {
    & $command @prefix --config $binding.config @args
    $relayExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $relayExitCode
