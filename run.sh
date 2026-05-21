#!/bin/bash
set -e

echo "=== Checking Virtual Environment ==="
# -d checks if the directory exists
if [ ! -d "venv" ]; then
  echo "Directory 'venv' not found. Creating a fresh virtual environment..."
  python3 -m venv venv
else
  echo "Existing 'venv' directory found. Skipping creation."
fi

echo "=== Activating Environment ==="
source venv/bin/activate

echo "=== Upgrading Core Packages ==="
pip install --upgrade pip

echo "=== Installing/Updating Dependencies ==="
if [ -f "requirements.txt" ]; then
  # pip is smart: it will skip packages that are already installed and match requirements
  pip install -r requirements.txt
else
  echo "No requirements.txt found."
fi
echo "=== Running Python Script ==="
python main.py
