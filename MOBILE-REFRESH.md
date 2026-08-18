# Refrescar el dashboard desde el movil (Pixel 9 / Android)

Los runners de GitHub Actions estan geo-bloqueados por Bybit/Binance (403/451),
asi que `scripts/refresh.sh` no puede correr en CI: tiene que ejecutarse desde
una IP europea. Tu movil con datos moviles europeos sirve perfectamente.

Esta guia deja un **icono en la pantalla de inicio** que lanza el refresh con
un solo toque — sin teclear comandos despues del setup inicial.

## Apps que necesitas (todas gratis, todas en F-Droid)

| App | Para que sirve | Obligatoria |
|-----|----------------|-------------|
| [Termux](https://f-droid.org/packages/com.termux/) | Terminal Linux dentro de Android | si |
| [Termux:Widget](https://f-droid.org/packages/com.termux.widget/) | Widget que ejecuta scripts con un tap | si |
| [Termux:API](https://f-droid.org/packages/com.termux.api/) | Notificaciones Android + wake-lock fiable | recomendada |

> ⚠️ **No instales Termux desde Google Play.** Esa version esta abandonada
> desde 2020 y no funciona en Android 14/15. Si la tienes instalada,
> desinstalala antes de instalar la de F-Droid (las firmas no son compatibles).

## Setup inicial (una sola vez, ~20 min mientras compila pandas)

Abre Termux y pega esta linea (manten pulsado en el terminal → Paste):

```bash
pkg install -y curl && \
  curl -fsSL https://raw.githubusercontent.com/masiaroa/crypto-pump-detector/main/scripts/termux-setup.sh | bash
```

El script:

1. Instala `python`, `git`, `gh`, `util-linux` y dependencias de build.
2. Clona el repo en `~/crypto-pump-detector`.
3. Crea el venv e instala `requirements.txt`.
4. Te pide email y nombre para `git config`.
5. Crea el shortcut `~/.shortcuts/refresh-crypto` que lee Termux:Widget.

Al terminar, ejecuta **una vez** (no se puede hacer dentro del script porque
abre el navegador):

```bash
gh auth login
```

Elige `GitHub.com` → `HTTPS` → `Login with a web browser`. Te da un codigo
de 8 caracteres, abre el navegador del movil, lo pegas y autorizas. A partir
de aqui los `git push` desde el movil funcionan sin password.

### Probar antes de mergear

Si quieres validar todo desde esta rama (`claude/mobile-script-refresh-FACbV`)
antes de que se mergee a main:

```bash
pkg install -y curl && \
  REFRESH_BRANCH=claude/mobile-script-refresh-FACbV \
  curl -fsSL https://raw.githubusercontent.com/masiaroa/crypto-pump-detector/claude/mobile-script-refresh-FACbV/scripts/termux-setup.sh | bash
```

## Anadir el boton a la pantalla de inicio

1. Manten pulsado en un hueco de la pantalla de inicio.
2. **Widgets** → busca **Termux:Widget**.
3. Arrastra el widget:
   - **Termux:Widget 2x2** para una lista de scripts.
   - **Termux shortcut** para un icono individual (recomendado).
4. Cuando te pida script, elige **refresh-crypto**.

A partir de ahi: toque al icono → scan + commit + push. Sin teclado.

## Que pasa cuando lo lanzas

`refresh-crypto` ejecuta `scripts/termux-refresh.sh`, que:

1. Activa `termux-wake-lock` para que Android no mate el proceso.
2. Llama a `scripts/refresh.sh` redirigiendo stdout/stderr a
   `data/termux-refresh.log`.
3. Si Termux:API esta instalado, manda una notificacion a la barra de Android
   con el resultado (OK + ultima linea del log, o ERROR + ultimas 3 lineas).

Para ver el log en vivo desde Termux:

```bash
tail -f ~/crypto-pump-detector/data/termux-refresh.log
```

## Troubleshooting

**El push pide usuario/password al final.** No has hecho `gh auth login`, o la
sesion caduco. Vuelve a ejecutarlo.

**`pandas` o `numpy` no instalan / fallan compilando.** Termux suele tener
wheels prebuilt; si fallan, prueba `pip install --upgrade pip` y reintenta.
En Android 14/15 con Pixel 9 (ARM64) las wheels son estables — si te falla,
abre un issue con la salida.

**Android mata Termux a media ejecucion.** Instala Termux:API (paquete y app)
para que `termux-wake-lock` funcione. Ademas quita Termux de optimizacion de
bateria: `Settings → Apps → Termux → Battery → Unrestricted`.

**Quiero correr desde una rama feature, no main.** Dentro de Termux:

```bash
cd ~/crypto-pump-detector
git checkout mi-rama
./scripts/branch-refresh.sh   # despliega Pages desde tu rama
```

`branch-refresh.sh` necesita `gh` CLI autenticado (lo dejaste listo en el
setup).

**Quiero programarlo a una hora fija (ej. 07:00).** Termux no tiene cron por
defecto. Opciones:

- `pkg install -y cronie` y montar un cron clasico (requiere mantener Termux
  abierto o con Termux:Boot).
- Usar `termux-job-scheduler` (Termux:API), mas amigable con la gestion de
  bateria de Android pero menos preciso (Android decide cuando despertar).

Si necesitas algo verdaderamente fiable 24/7, un VPS europeo (Hetzner ~4€/mes)
con cron clasico es lo correcto — el movil esta pensado para refresh manual o
semi-programado.
