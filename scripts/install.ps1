[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Config = 'relay.local.json',
    [switch]$PreparePythonOnly,
    [switch]$Initialize,
    [switch]$CodexApp,
    [ValidateSet('none', 'both', 'codex', 'claude')][string]$InstallSkills = 'none',
    [string]$CodexSkillsDir,
    [string]$ClaudeSkillsDir,
    [switch]$RebindSkills
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$projectPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $projectPath
$venvPath = Join-Path $projectPath '.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
if (Test-Path -LiteralPath $venvPath) {
    $item = Get-Item -LiteralPath $venvPath -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        !(Test-Path -LiteralPath (Join-Path $venvPath 'pyvenv.cfg')) -or
        !(Test-Path -LiteralPath $venvPython)) {
        throw 'Existing .venv is not a recognized local Python environment; nothing was overwritten.'
    }
} else {
    $pythonCommand = Get-Command $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1
    & $pythonCommand.Source -X utf8 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 2)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
    & $pythonCommand.Source -X utf8 -m venv --without-pip $venvPath
    if ($LASTEXITCODE -ne 0) { throw 'Local Python environment creation failed.' }
}
& $venvPython -B -X utf8 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 2)'
if ($LASTEXITCODE -ne 0) { throw 'The local environment needs Python 3.11 or newer; preserve it and install in a new folder.' }
if ($PreparePythonOnly) {
    Write-Output ('Python ready: ' + $venvPython)
    return
}
$configPath = if ([IO.Path]::IsPathRooted($Config)) { [IO.Path]::GetFullPath($Config) } else { [IO.Path]::GetFullPath((Join-Path $projectPath $Config)) }
if (!(Test-Path -LiteralPath $configPath)) {
    $templateName = if ($CodexApp) { 'config\codex-app.example.json' } else { 'config\relay.example.json' }
    Copy-Item -LiteralPath (Join-Path $projectPath $templateName) -Destination $configPath
}
Write-Output 'Relay uses the Python standard library. No network download or pip installation is required.'
Write-Output ('Configuration: ' + $configPath)
Write-Output ('Python: ' + $venvPython)
if ($Initialize) {
    & $venvPython -X utf8 (Join-Path $projectPath 'relay.py') --config $configPath init
    if ($LASTEXITCODE -ne 0) { throw 'Runtime initialization failed; existing runtime contents were not adopted.' }
} else {
    Write-Output 'For a fresh profile, run relay.py --config with the configuration path printed above and init.'
}
if ($InstallSkills -ne 'none') {
    $skillArgs = @('--config', $configPath, '--agents', $InstallSkills)
    if ($CodexSkillsDir) { $skillArgs += @('--codex-skills-dir', $CodexSkillsDir) }
    if ($ClaudeSkillsDir) { $skillArgs += @('--claude-skills-dir', $ClaudeSkillsDir) }
    if ($RebindSkills) { $skillArgs += '--rebind' }
    & $venvPython -B -X utf8 (Join-Path $projectPath 'scripts\install_skills.py') @skillArgs
    if ($LASTEXITCODE -ne 0) { throw 'Global skill installation failed; see the diagnostic above.' }
    Write-Output 'Skills installed for this Windows user. Refresh skills or start a new agent session if not listed.'
    Write-Output 'Codex App: $relay-codex-app  |  Claude Code Local: /relay-claude-code'
}
Write-Output 'Codex App mode uses the current App login after app attach. Fresh profiles keep Claude unavailable until configured. See docs/CODEX_APP.md.'
