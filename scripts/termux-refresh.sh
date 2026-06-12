#!/usr/bin/env bash
# Wrapper de scripts/refresh.sh pensado para ejecutarse desde Termux:Widget.
#
# Diferencias con refresh.sh:
#   - Mantiene wake-lock durante toda la ejecucion (Android puede matar
#     procesos en background y dejar el push a medias).
#   - Captura stdout/stderr en data/termux-refresh.log para depurar offline.
#   - Lanza una notificacion Android al terminar (si Termux:API esta
#     instalado) con el resultado, asi sabes si fue OK sin abrir el terminal.
#
# Uso normal: lo lanza el shortcut ~/.shortcuts/refresh-crypto que crea
# scripts/termux-setup.sh. Tambien se puede ejecutar a mano:
#   ./scripts/termux-refresh.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

LOG="$SCRIPT_DIR/data/termux-refresh.log"
mkdir -p "$(dirname "$LOG")"

notify() {
  command -v termux-notification >/dev/null 2>&1 || return 0
  termux-notification \
    --id crypto-refresh \
    --title "$1" \
    --content "$2" \
    --priority "${3:-default}" >/dev/null 2>&1 || true
}

wake_on()  { command -v termux-wake-lock   >/dev/null 2>&1 && termux-wake-lock   || true; }
wake_off() { command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true; }

trap wake_off EXIT
wake_on

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
START_TS="$(date -Iseconds)"

{
  echo "==================================================================="
  echo "[$START_TS] termux-refresh.sh iniciado (rama: $BRANCH)"
} >> "$LOG"

notify "Crypto refresh" "Lanzando scan en rama $BRANCH..." low

if ./scripts/refresh.sh >> "$LOG" 2>&1; then
  TAIL="$(tail -n 1 "$LOG" | cut -c1-120)"
  echo "[$(date -Iseconds)] OK" >> "$LOG"
  notify "✅ Crypto refresh OK" "${TAIL:-push completado}" default
  exit 0
fi

RC=$?
TAIL="$(tail -n 3 "$LOG" | tr '\n' ' ' | cut -c1-200)"
echo "[$(date -Iseconds)] FAIL rc=$RC" >> "$LOG"
notify "❌ Crypto refresh FALLO (rc=$RC)" "${TAIL:-revisa data/termux-refresh.log}" high
exit "$RC"
