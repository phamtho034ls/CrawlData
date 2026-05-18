# Install PyTorch with CUDA support for Whisper GPU acceleration (Windows).
# Requires NVIDIA GPU + driver: https://www.nvidia.com/drivers
# Check CUDA version: nvidia-smi
#
# Python 3.13+ needs CUDA 12.4 wheels (cu124); older Python may use cu121.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Error "Virtual environment not found. Run: python -m venv .venv"
}

Write-Host "==> Activating .venv ..."
. .\.venv\Scripts\Activate.ps1

$pyVer = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "==> Python $pyVer"

function Get-PytorchCudaIndex {
    param([string]$Version)
    $parts = $Version.Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    # cu121 index has no cp313 wheels; cu124+ supports Python 3.13 on Windows.
    if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 13)) {
        return "https://download.pytorch.org/whl/cu124"
    }
    return "https://download.pytorch.org/whl/cu121"
}

$cudaIndex = Get-PytorchCudaIndex -Version $pyVer
$cudaLabel = if ($cudaIndex -match "cu124") { "CUDA 12.4" } else { "CUDA 12.1" }

Write-Host "==> Uninstalling existing torch packages (if any) ..."
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
foreach ($pkg in @("torch", "torchvision", "torchaudio")) {
    & pip uninstall -y $pkg 2>&1 | Out-Null
}
$ErrorActionPreference = $prevEAP

Write-Host "==> Installing PyTorch with $cudaLabel ($cudaIndex) ..."
& pip install torch torchvision torchaudio --index-url $cudaIndex
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "CUDA wheel install failed. Trying CPU build from PyPI as fallback ..."
    & pip install torch torchvision torchaudio
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install PyTorch. Check Python version and network."
    }
    Write-Host "WARNING: Installed CPU-only PyTorch. Whisper will run on CPU."
}

Write-Host ""
Write-Host "==> Verifying GPU ..."
& python -c @"
import torch
print('PyTorch:', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU:', torch.cuda.get_device_name(0))
else:
    print('WARNING: CUDA not detected. Check NVIDIA driver and GPU.')
    print('For Python 3.13+, this script uses cu124 wheels (not cu121).')
"@

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyTorch verification failed (import error)."
}

Write-Host ""
Write-Host "Done. Set in .env (optional):"
Write-Host "  WHISPER_DEVICE=auto"
Write-Host "  WHISPER_MODEL=base"
