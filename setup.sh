#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "==> Creating virtual environment (.venv)..."
python3 -m venv .venv

echo "==> Activating virtual environment..."
# shellcheck source=/dev/null
source .venv/bin/activate

echo "==> Upgrading pip..."
pip install --upgrade pip

echo "==> Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo "==> Installing Playwright browsers..."
playwright install chromium

echo "==> Creating folder structure..."
mkdir -p src data_trends logs

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example — please add your API keys."
fi

touch src/__init__.py
touch logs/.gitkeep

echo ""
echo "Setup complete!"
echo ""
echo "Activate the virtual environment:"
echo "  macOS/Linux:  source .venv/bin/activate"
echo "  Windows CMD:  .venv\\Scripts\\activate.bat"
echo "  Windows PS:   .venv\\Scripts\\Activate.ps1"
