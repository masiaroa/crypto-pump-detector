#!/usr/bin/env bash
# Lanza el dashboard Streamlit activando el venv automáticamente.
# Uso: ./run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "❌ No se encontró .venv — ejecuta primero:"
  echo "   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate
PYTHONPATH=src streamlit run app.py

