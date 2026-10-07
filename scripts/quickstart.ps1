[CmdletBinding()]
param(
    [string]$DataDir,
    [string]$Name = 'default',
    [string]$Claude,
    [string]$Model,
    [string]$Effort,
    [ValidateSet('en', 'zh-TW')][string]$Language,
    [switch]$NoWizard,
    [switch]$Login,
    [switch]$Start,
    [switch]$NoBrowser,
    [switch]$NoDetect,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$quickRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $quickRoot
try {
    & (Join-Path $PSScriptRoot 'install.ps1') -Python $Python -PreparePythonOnly | ForEach-Object { [Console]::Error.WriteLine($_) }
    $quickPython = Join-Path $quickRoot '.venv\Scripts\python.exe'
    $quickArgs = @('-B', '-X', 'utf8', '-m', 'relay_collaboration.quickstart', '--name', $Name)
    if ($DataDir) { $quickArgs += @('--data-dir', $DataDir) }
    if ($Claude) { $quickArgs += @('--claude', $Claude) }
    if ($PSBoundParameters.ContainsKey('Model')) { $quickArgs += @('--model', $Model) }
    if ($PSBoundParameters.ContainsKey('Effort')) { $quickArgs += @('--effort', $Effort) }
    if ($Language) { $quickArgs += @('--language', $Language) }
    if (!$NoWizard) { $quickArgs += '--wizard' }
    if ($Login) { $quickArgs += '--login' }
    if ($Start) { $quickArgs += '--start' }
    if ($NoBrowser) { $quickArgs += '--no-browser' }
    if ($NoDetect) { $quickArgs += '--no-detect' }
    & $quickPython @quickArgs
    $quickExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $quickExit
