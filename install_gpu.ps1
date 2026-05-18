# Install PyTorch with CUDA support for Whisper GPU acceleration (Windows).
# Requires NVIDIA GPU + driver: https://www.nvidia.com/drivers
# Check CUDA version: nvidia-smi

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Error "Virtual environment not found. Run: python -m venv .venv"
}

Write-Host "==> Activating .venv ..."
. .\.venv\Scripts\Activate.ps1

Write-Host "==> Uninstalling CPU-only torch (if any) ..."
pip uninstall -y torch torchvision torchaudio 2>$null

Write-Host "==> Installing PyTorch with CUDA 12.1 ..."
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

Write-Host ""
Write-Host "==> Verifying GPU ..."
python -c @"
import torch
print('PyTorch:', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU:', torch.cuda.get_device_name(0))
else:
    print('WARNING: CUDA not detected. Check NVIDIA driver and GPU.')
"@

Write-Host ""
Write-Host "Done. Set in .env (optional):"
Write-Host "  WHISPER_DEVICE=auto"
Write-Host "  WHISPER_MODEL=base"
