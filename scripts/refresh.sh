#!/usr/bin/env bash
# Refresca los datos del scanner y publica en GitHub Pages.
#
# Uso manual:  ./scripts/refresh.sh
# Uso cron:    0 7 * * * /home/alb/projects/crypto-pump-detector/scripts/refresh.sh >> /tmp/crypto-refresh.log 2>&1
#
# Diseñado para ejecutarse sin supervisión: si el scan no produce datos frescos
# (sin red, exchanges caídos, etc.) sale con código !=0 SIN commitear nada.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

LOCK="/tmp/crypto-pump-refresh.lock"
exec 9>"$LOCK"
flock -n 9 || { echo "[$(date -Iseconds)] otro refresh en curso, abortando"; exit 0; }

if [ ! -f ".venv/bin/activate" ]; then
  echo "❌ No se encontró .venv. Ejecuta primero:"
  echo "   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
source .venv/bin/activate

echo "[$(date -Iseconds)] === scan ==="
PYTHONPATH=src python scripts/scan.py

FRESH=$(find data/charts -name '*.json' -mmin -10 2>/dev/null | wc -l)
if [ "$FRESH" -eq 0 ]; then
  echo "❌ scan.py no produjo ningún JSON fresco (<10 min). No se commitea."
  exit 1
fi
echo "✅ $FRESH archivos de chart frescos."

git add -f data/charts data/liquidations 2>/dev/null || true
# Las metricas del scan tambien viajan al repo: CI no puede ejecutar scan.py
# (Bybit/Binance geo-bloquean los runners de GitHub Actions), asi que sin
# estos CSVs la tabla overview se renderiza con ceros.
git add -f data/latest_scan.csv data/event_history.csv 2>/dev/null || true

if git diff --cached --quiet; then
  echo "Sin diffs respecto al repo. Nada que pushear."
  exit 0
fi

git commit -m "data: refresh $(date -u +%Y-%m-%dT%H:%MZ)"
git push
echo "[$(date -Iseconds)] ✅ push completado. Pages se redesplegará en ~30s."
