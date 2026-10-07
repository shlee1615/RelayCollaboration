[CmdletBinding()]
param([string]$Python = 'python', [string]$Node = 'node', [switch]$RequireFrontend)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $projectRoot
try {
    & $Python -B -X utf8 -m unittest discover -s tests -t . -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (Get-Command $Node -ErrorAction SilentlyContinue) {
        & $Node --test tests/test_frontend.mjs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } elseif ($RequireFrontend) {
        throw 'Node.js is required for frontend verification but was not found.'
    } else {
        Write-Warning 'Frontend tests skipped: Node.js unavailable. Python verification is complete.'
    }
    Write-Host 'Fixture verification complete. Real model acceptance is separate and has not been performed by this test script.'
} finally {
    Pop-Location
}
