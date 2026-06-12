#!/usr/bin/env bash
# Refresca datos y despliega Pages desde la rama actual (NO main).
#
# Pensado para validar una rama feature end-to-end sin tener que mergear antes:
#
#   1. Ejecuta scan.py en local (geo-block en GitHub Actions impide hacerlo en CI)
#   2. Commitea data/charts, data/liquidations, latest_scan.csv y event_history.csv
#      a la rama actual y la pushea
#   3. Dispara workflow_dispatch del pipeline de Pages apuntando a la rama actual,
#      asi CI construye el HTML con TU codigo de la rama y redepliega Pages
#
# IMPORTANTE: el deploy es global — Pages se actualiza con esta rama y pisara
# la version desplegada desde main hasta que el siguiente push a main vuelva
# a redesplegar la version "estable". Por eso este script aborta si estas en
# main: para eso ya existe scripts/refresh.sh.
#
# Requiere: gh CLI autenticado (`gh auth status`).
#
# Uso:
#   ./scripts/branch-refresh.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "HEAD" ]; then
  echo "❌ Estas en '$BRANCH'. Para main usa scripts/refresh.sh." >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "❌ gh CLI no encontrado. Instalalo (https://cli.github.com) o dispara el" >&2
  echo "   workflow a mano: Actions → 'Build & Deploy Dashboard' → Run workflow" >&2
  echo "   → Use workflow from: $BRANCH" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "❌ gh no esta autenticado. Ejecuta: gh auth login" >&2
  exit 1
fi

LOCK="/tmp/crypto-pump-refresh.lock"
exec 9>"$LOCK"
flock -n 9 || { echo "[$(date -Iseconds)] otro refresh en curso, abortando"; exit 0; }

if [ ! -f ".venv/bin/activate" ]; then
  echo "❌ No se encontro .venv. Ejecuta primero:" >&2
  echo "   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi
source .venv/bin/activate

echo "[$(date -Iseconds)] === scan (rama: $BRANCH) ==="
PYTHONPATH=src python scripts/scan.py

FRESH=$(find data/charts -name '*.json' -mmin -10 2>/dev/null | wc -l)
if [ "$FRESH" -eq 0 ]; then
  echo "❌ scan.py no produjo ningun JSON fresco (<10 min). No se commitea." >&2
  exit 1
fi
echo "✅ $FRESH archivos de chart frescos."

git add -f data/charts data/liquidations 2>/dev/null || true
git add -f data/latest_scan.csv data/event_history.csv 2>/dev/null || true

if git diff --cached --quiet; then
  echo "Sin diffs respecto al repo — no se commitea."
else
  git commit -m "data: refresh from branch $BRANCH $(date -u +%Y-%m-%dT%H:%MZ)"
fi

git push -u origin "$BRANCH"

echo "[$(date -Iseconds)] === disparando workflow Pages desde $BRANCH ==="
gh workflow run pages.yml --ref "$BRANCH"

# Pequeño grace para que GitHub registre el run antes de mostrar el link
sleep 3
RUN_URL=$(gh run list --workflow=pages.yml --branch "$BRANCH" --limit 1 --json url --jq '.[0].url' 2>/dev/null || true)
if [ -n "$RUN_URL" ]; then
  echo "✅ Deploy en curso: $RUN_URL"
else
  echo "✅ Deploy en curso. Mira: https://github.com/$(gh repo view --json nameWithOwner --jq .nameWithOwner)/actions"
fi
echo "ℹ️  Cuando termine, Pages mostrara el HTML construido desde la rama '$BRANCH'."
echo "ℹ️  Al mergear a main, el siguiente push restaurara la version 'estable'."
