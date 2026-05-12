# Crypto Pump Detector

MVP local para escanear perpetuos cripto y detectar setups tipo leveraged pump / blow-off.

## Setup

En WSL/Ubuntu instala primero soporte de venv para el Python del sistema:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv python3-pip
```

Este proyecto fuerza `pyenv` a usar `system` con `.python-version`, porque un Python `pyenv` mal compilado puede romper `ctypes`/`pandas`.

```bash
cd /home/alb/projects/crypto-pump-detector
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

## Watchlist

La watchlist activa de TradingView quedó guardada en:

```text
config/watchlist.yaml
```

## Scanner CLI

```bash
PYTHONPATH=src python scripts/scan.py
```

Esto genera:

```text
data/latest_scan.csv
data/signals.sqlite
data/alerts.csv
```

## Dashboard

```bash
PYTHONPATH=src streamlit run app.py
```

La señal principal exige:

- impulso de precio,
- impulso de Open Interest,
- primera vela del impulso.

Funding se clasifica como `NEGATIVE`, `NEUTRAL`, `POSITIVE`, `HOT`, `EXTREME` o `UNKNOWN`.

## Overlay de liquidaciones historicas

El dashboard usa Coinalyze como fuente principal para pintar liquidaciones ya ejecutadas, agregadas por intervalo (`1h`, `4h`, `1d`). Para activarlo crea `.env` en la raiz:

```bash
COINALYZE_API_KEY=...
```

La app consulta Coinalyze automaticamente al abrir el detalle de una moneda y muestra un panel de diagnostico con estado, simbolo resuelto, filas, nocional, ultimo bucket y antiguedad del dato.

Los mapas de liquidaciones pendientes por nivel de precio no se muestran en modo gratis: CoinGlass Liquidation Map requiere API/key de pago y el endpoint frontend publico no es una fuente fiable.

## Levantar en local

```bash
source .venv/bin/activate

# Dashboard (igual que antes, http://localhost:8501)
PYTHONPATH=src streamlit run app.py
```

La web no lanza capturas realtime al arrancar. Si quieres forzar una nueva consulta historica, pulsa **Reconsultar liquidaciones historicas** dentro de `⚙️ Ajustes de escaneo`; solo borra cache y vuelve a pedir Coinalyze.

### Comandos extra

| Cómo | Qué hace | Cuándo usarlo |
|------|----------|---------------|
| Abrir detalle en la web | Consulta historica Coinalyze cacheada | Uso normal |
| Botón **Reconsultar liquidaciones historicas** | Borra cache y vuelve a consultar Coinalyze | Si quieres refrescar datos |
| `python scripts/scan.py` | Recalcula señales y exporta overlays historicos disponibles | Cuando quieras refrescar `latest_scan.csv` / `event_history.csv` / per-symbol JSONs |
| `python -m pump_detector.liquidations.executed_ws --duration 60` | Captura live opcional por CLI | Solo debug/experimentos, fuera del flujo normal |

Variables de entorno útiles para el scan:

- `SCAN_BURST_SECONDS=0` → asegura que el scan no haga captura live.
- `SCAN_TIMEFRAME=1h` → fuerza el timeframe.

Versión estática estilo GitHub Pages (opcional):

```bash
PYTHONPATH=src python scripts/build_html.py
python scripts/validate_static_html.py docs/index.html
python -m http.server 8000 --directory docs   # http://localhost:8000
```

GitHub Pages se publica desde GitHub Actions como artifact, sin commits
automaticos al repo. Configuracion necesaria una vez:

```text
Settings -> Pages -> Build and deployment -> Source -> GitHub Actions
```

Notas:

- Coinalyze devuelve historico ejecutado; no liquidez pendiente ni heatmap futuro.
- Si Coinalyze falla, falta key o responde vacio, el panel lo muestra explicitamente.
- La capa live WS se mantiene en CLI para debug, pero esta desactivada por defecto.
