# Start the local Vibe Engineer CAD editor and its on-demand assistant service.
# Put OPENROUTER_API_KEY in the ignored repository .env
# when ready; this launcher never stores or prints credentials.
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$source = Join-Path (Split-Path $repo -Parent) 'OpenCADStudio-source'
$service = Join-Path $PSScriptRoot 'assistant_service.py'
$editor = Join-Path $source 'target\debug\OpenCADStudio.exe'

if (-not (Test-Path -LiteralPath $service -PathType Leaf)) {
    throw "Assistant service is missing: $service"
}
if (-not (Test-Path -LiteralPath $editor -PathType Leaf)) {
    throw "Build the Vibe Engineer editor first: $editor"
}
if (Get-Process OpenCADStudio -ErrorAction SilentlyContinue) {
    throw 'Close the running Vibe Engineer window first so new assistant settings take effect. Save any open drawing before closing it.'
}

$python = (Get-Command python -CommandType Application -ErrorAction Stop |
    Select-Object -First 1).Source
& $python --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Python is installed but could not run.'
}

$env:OCS_ASSISTANT_DIR = $repo
$env:OCS_ASSISTANT_COMMAND = '"' + $python + '" "' + $service + '"'
$env:OCS_ASSISTANT_PORT = '8766'

Start-Process -FilePath $editor -WorkingDirectory $source
Write-Host 'Vibe Engineer started. Open a drawing, then View > Palettes > Assistant.'
if (-not $env:OPENROUTER_API_KEY -and -not (Test-Path -LiteralPath (Join-Path $repo '.env') -PathType Leaf)) {
    Write-Host 'OpenRouter key is not configured; local drawing read commands are still available.'
}
