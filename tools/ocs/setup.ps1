# Download the pinned Open CAD Studio release for the Vibe Engineer smoke test (Windows).
#
# Pin (do not bump casually — record the change in HANDOFF.md):
#   release : v2026.46 (2026-11-15)
#   commit  : 8b4d27046040b27eb07d3faed7b7472bd7bdaea9
#   asset   : OpenCADStudio-v2026.46-windows-x86_64-portable.exe (~186 MB)
#   sha256  : 9492830b7d2b8d6e4f2cedf1e8ac47c6025757e6e30fdad687df6f5209064989
#
# The digest is the official GitHub release-asset digest for tag v2026.46
# (re-check with: https://api.github.com/repos/HakanSeven12/OpenCADStudio/releases/tags/v2026.46
#  — each asset carries a "digest": "sha256:..." field).
#
# Installs to $env:LOCALAPPDATA\OpenCADStudio-v2026.46\OpenCADStudio.exe
# (matches the default exe path in ocs_smoke_test.py on win32).
# The binary is NOT committed to this repository.
#
# Run: powershell -ExecutionPolicy Bypass -File tools\ocs\setup.ps1
# (Linux/macOS use setup.sh instead.)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # speeds up large downloads on Windows PowerShell 5.1

$OcsTag = "v2026.46"
$OcsFile = "OpenCADStudio-$OcsTag-windows-x86_64-portable.exe"
$OcsUrl = "https://github.com/HakanSeven12/OpenCADStudio/releases/download/$OcsTag/$OcsFile"
$OcsSha256 = "9492830b7d2b8d6e4f2cedf1e8ac47c6025757e6e30fdad687df6f5209064989"

$DestDir = Join-Path $env:LOCALAPPDATA "OpenCADStudio-v2026.46"
$Dest = Join-Path $DestDir "OpenCADStudio.exe"

# --- disk space check (generous margin: download + a self-extracting run) -----
$needBytes = 600MB
$destRoot = [System.IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path)
$drive = Get-PSDrive -Name ($destRoot.Substring(0, 1))
$freeBytes = $drive.Free
Write-Host ("disk: {0:N0} MB free on {1} (need ~{2:N0} MB)" -f ($freeBytes / 1MB), $destRoot, ($needBytes / 1MB))
if ($freeBytes -lt $needBytes) {
    Write-Error "not enough free space on $destRoot; free space and retry"
    exit 1
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

function Get-Sha256([string]$Path) {
    # Raw .NET implementation: Get-FileHash is unavailable in some restricted
    # PowerShell 5.1 environments (module autoloading disabled).
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace("-", "").ToLower()
        }
        finally {
            $stream.Dispose()
        }
    }
    finally {
        $sha.Dispose()
    }
}

if ((Test-Path -LiteralPath $Dest) -and ((Get-Sha256 $Dest) -eq $OcsSha256)) {
    Write-Host "already installed and verified: $Dest"
}
else {
    $Part = "$Dest.part"
    Write-Host "downloading $OcsUrl"
    & curl.exe -fSL --retry 3 -o $Part $OcsUrl
    if ($LASTEXITCODE -ne 0) {
        Write-Error "download failed (curl exit $LASTEXITCODE)"
        exit 1
    }
    $actual = Get-Sha256 $Part
    if ($actual -ne $OcsSha256) {
        Write-Warning "removing corrupted download"
        Remove-Item -LiteralPath $Part -Force
        Write-Error "sha256 mismatch: expected $OcsSha256 got $actual"
        exit 1
    }
    Move-Item -LiteralPath $Part -Destination $Dest -Force
    Write-Host "verified sha256: $actual"
}

Write-Host ""
Write-Host "version check:"
# The portable exe is a GUI-subsystem binary: PowerShell's `&` call operator
# does not wait for it and leaves $LASTEXITCODE unset. Start-Process -Wait
# waits on the process handle and captures the real exit code and output.
$verOut = Join-Path $env:TEMP "ocs_version_out.txt"
$verErr = Join-Path $env:TEMP "ocs_version_err.txt"
try {
    $proc = Start-Process -FilePath $Dest -ArgumentList "--version" -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $verOut -RedirectStandardError $verErr
    Get-Content $verOut
    if ($proc.ExitCode -ne 0) {
        Write-Host (Get-Content $verErr -Raw)
        Write-Error "--version exited with $($proc.ExitCode)"
        exit 1
    }
}
finally {
    Remove-Item $verOut, $verErr -Force -ErrorAction SilentlyContinue
}
