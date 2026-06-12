#!/usr/bin/env bash
# Bootstrap one-shot para correr scripts/refresh.sh desde Termux en Android.
#
# Pensado para Pixel/Android con IP europea: Bybit/Binance geo-bloquean los
# runners de GitHub Actions, asi que el refresh tiene que salir desde una IP
# residencial. Este script deja el movil listo en una sola pegada.
#
# Uso (en Termux, despues de instalarlo desde F-Droid):
#   pkg install -y curl
#   curl -fsSL https://raw.githubusercontent.com/masiaroa/crypto-pump-detector/main/scripts/termux-setup.sh | bash
#
# Variables opcionales:
#   REFRESH_BRANCH=<rama>  clona/checkoutea esa rama en vez de main
#   REPO_DIR=<path>        cambia el destino (default: ~/crypto-pump-detector)

set -euo pipefail

REPO_URL="https://github.com/masiaroa/crypto-pump-detector.git"
REPO_DIR="${REPO_DIR:-$HOME/crypto-pump-detector}"
BRANCH="${REFRESH_BRANCH:-main}"

if [ -z "${PREFIX:-}" ] || [ "${PREFIX#*com.termux}" = "${PREFIX}" ]; then
  echo "❌ Este script solo corre dentro de Termux." >&2
  echo "   Instala Termux desde F-Droid: https://f-droid.org/packages/com.termux/" >&2
  exit 1
fi

echo "==> Actualizando paquetes de Termux..."
pkg update -y
pkg upgrade -y

echo "==> Instalando dependencias del sistema..."
# util-linux trae flock (lo usa refresh.sh); rust/binutils por si pip
# necesita compilar wheels nativos (cryptography, etc).
pkg install -y python git gh util-linux rust binutils openssl libjpeg-turbo libpng

echo "==> Pidiendo permiso de almacenamiento (acepta el popup de Android)..."
termux-setup-storage || true

if [ -d "$REPO_DIR/.git" ]; then
  echo "==> Repo ya existe en $REPO_DIR — actualizando..."
  cd "$REPO_DIR"
  git fetch origin
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  echo "==> Clonando repo en $REPO_DIR (rama: $BRANCH)..."
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

if [ ! -f .venv/bin/activate ]; then
  echo "==> Creando venv (la primera vez tarda ~20 min compilando pandas/numpy en ARM)..."
  python -m venv .venv
fi
source .venv/bin/activate

echo "==> Instalando requirements (pip)..."
pip install --upgrade pip
pip install -r requirements.txt

if [ -z "$(git config --global user.email 2>/dev/null || true)" ]; then
  echo
  echo "==> Configurando git (necesario para commitear desde el movil)."
  read -r -p "    Tu email para git: " GIT_EMAIL
  read -r -p "    Tu nombre para git: " GIT_NAME
  git config --global user.email "$GIT_EMAIL"
  git config --global user.name "$GIT_NAME"
fi

mkdir -p "$HOME/.shortcuts"
chmod 700 "$HOME/.shortcuts"
cat > "$HOME/.shortcuts/refresh-crypto" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec "$REPO_DIR/scripts/termux-refresh.sh"
EOF
chmod 700 "$HOME/.shortcuts/refresh-crypto"

cat <<EOF

===================================================================
✅ Setup completo en: $REPO_DIR (rama: $BRANCH)

Siguientes pasos:

  1) Autentica gh para que el push funcione desde el movil:
       gh auth login
     (elige GitHub.com → HTTPS → Login with a web browser)

  2) Instala Termux:Widget desde F-Droid si no lo tienes:
       https://f-droid.org/packages/com.termux.widget/

  3) (Recomendado) instala Termux:API para tener notificaciones
     y wake-lock fiable:
       pkg install -y termux-api
     y descarga la app Termux:API desde F-Droid.

  4) En la pantalla de inicio del movil:
       manten pulsado → Widgets → Termux:Widget →
       arrastralo → elige "refresh-crypto"

A partir de ahi, un toque al widget = scan + commit + push.
===================================================================
EOF
