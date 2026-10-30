[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE"
    }
}

if (-not $Python) {
    $candidates = @(
        (Join-Path $repoRoot '.desktop-venv/Scripts/python.exe'),
        (Join-Path $repoRoot '.venv/Scripts/python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            & $candidate -c 'import sys; assert sys.version_info >= (3, 11)'
            if ($LASTEXITCODE -eq 0) { $Python = $candidate; break }
        }
    }
    if (-not $Python) {
        foreach ($commandName in @('python', 'python3', 'py')) {
            $command = Get-Command $commandName -ErrorAction SilentlyContinue
            if ($command) {
                & $command.Source -c 'import sys; assert sys.version_info >= (3, 11)'
                if ($LASTEXITCODE -eq 0) { $Python = $command.Source; break }
            }
        }
    }
}
if (-not $Python) { throw 'No working Python 3.11+ found. Pass -Python <python.exe>.' }
$Python = (Get-Command $Python -ErrorAction Stop).Source
Invoke-Checked $Python @('-c', 'import sys; assert sys.version_info >= (3, 11)')

$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
Invoke-Checked (Get-Command node -ErrorAction Stop).Source @('--version')
Invoke-Checked $npm @('--version')
foreach ($relative in @('frontend/package.json', 'frontend/package-lock.json',
                         'scripts/codeviz.spec', 'scripts/desktop_entry.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $relative) -PathType Leaf)) {
        throw "Required file missing: $relative"
    }
}
$dependencyCheck = @'
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
import sys
environment = Path(sys.prefix).resolve()
for module in ("webview", "PyInstaller", "setuptools", "pythonnet", "clr_loader",
               "pydantic_ai", "pydantic", "httpx2", "httpcore2", "openai"):
    spec = find_spec(module)
    if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(environment):
        raise RuntimeError(f"{module} is missing or outside the selected build environment")
expected = {"pywebview": "6.2.1", "pyinstaller": "6.22.3", "setuptools": "84.0.0",
            "pythonnet": "3.2.0", "clr-loader": "0.3.1", "pydantic-ai-slim": "2.55.0",
            "pydantic": "2.14.1", "httpx2": "2.13.1", "httpcore2": "2.13.1", "openai": "3.29.0"}
for package, required in expected.items():
    installed = version(package)
    print(package, installed)
    if installed != required:
        raise RuntimeError(f"{package} must be {required}; found {installed}")
'@
Invoke-Checked $Python @('-I', '-c', $dependencyCheck)

Push-Location $repoRoot
try {
    if ($CheckOnly) {
        # Check existing dependencies and outputs; do not install or rebuild.
        Invoke-Checked $npm @('--prefix', 'frontend', 'ls', '--depth=0')
        if (-not (Test-Path -LiteralPath 'frontend/dist/index.html' -PathType Leaf)) {
            throw 'frontend/dist/index.html missing. Build the frontend first.'
        }
        Write-Host "Desktop prerequisites OK ($Python). GUI was not launched."
        return
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This desktop build targets Windows; run it on Windows.'
    }
    Invoke-Checked $npm @('--prefix', 'frontend', 'ci')
    Invoke-Checked $npm @('--prefix', 'frontend', 'run', 'build')
    if (-not (Test-Path -LiteralPath 'frontend/dist/index.html' -PathType Leaf)) {
        throw 'Frontend build did not produce frontend/dist/index.html.'
    }
    Invoke-Checked $Python @('-I', '-m', 'PyInstaller', '--noconfirm', '--clean',
                            '--distpath', 'dist', '--workpath', 'build/desktop',
                            'scripts/codeviz.spec')
    foreach ($relative in @('dist/CodeViz/CodeViz.exe',
                            'dist/CodeViz/_internal/frontend/dist/index.html')) {
        if (-not (Test-Path -LiteralPath $relative -PathType Leaf)) {
            throw "Packaging output missing: $relative"
        }
    }
    Write-Host 'Built dist/CodeViz/CodeViz.exe. Distribute the complete CodeViz directory.'
} finally {
    Pop-Location
}
