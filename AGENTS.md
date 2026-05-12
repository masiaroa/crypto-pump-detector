# AGENTS.md — Crypto Pump Detector

Guía para agentes de IA que trabajen en este repositorio. Léela antes de proponer o aplicar cualquier cambio.

---

## Descripción del proyecto

MVP local en Python para escanear perpetuos de cripto y detectar setups tipo **leveraged pump / blow-off**.
Detecta señales cuando una vela muestra impulso de precio + impulso de Open Interest (OI) y es la primera vela del impulso (sin señales previas recientes).

---

## Estructura del repositorio

```
crypto-pump-detector/
├── app.py                  # Dashboard Streamlit
├── pyproject.toml          # Metadatos y dependencias del proyecto
├── requirements.txt        # Dependencias pinneadas para pip
├── config/
│   ├── settings.yaml       # Parámetros de escaneo (timeframes, umbrales, almacenamiento)
│   └── watchlist.yaml      # Lista de símbolos a escanear (formato exchange:SYMBOL)
├── data/
│   ├── signals.sqlite      # Historial de señales (escritura vía storage.py)
│   ├── alerts.csv          # Alertas activas
│   ├── latest_scan.csv     # Último escaneo completo
│   └── event_history.csv   # Historial de eventos
├── scripts/
│   └── scan.py             # CLI: ejecuta scan_watchlist y persiste en data/
└── src/
    └── pump_detector/
        ├── __init__.py
        ├── config.py       # Settings, load_settings(), load_watchlist(), rutas ROOT/CONFIG_DIR/DATA_DIR
        ├── data_clients.py # fetch_market_data() → DataUnavailable
        ├── scanner.py      # scan_watchlist(), scan_to_csv()
        ├── signals.py      # SignalSnapshot, compute_indicators(), mark_signal_history(), evaluate_latest()
        ├── storage.py      # append_snapshots() → SQLite + CSV
        ├── symbols.py      # normalize_symbol() → MarketInfo
        └── liquidations/   # Overlay gratis de liquidaciones (ver más abajo)
            ├── __init__.py             # API pública (fetch_liquidation_map, collect_executed_burst, …)
            ├── schema.py               # LIQUIDATION_COLUMNS, empty_liquidations(), helpers
            ├── executed_store.py       # JSONL rolling + filtro por símbolo/ventana
            ├── executed_ws.py          # Colector WS Binance/Bybit/OKX (CLI: python -m …)
            ├── projected_coinglass.py  # Endpoint frontend público de CoinGlass (sin key)
            ├── coinalyze.py            # Histórico agregado vía Coinalyze (free API key)
            └── fetch.py                # Orquestador executed + projected + coinalyze
```

---

## ⚠️ Despliegue en la nube — NO VIABLE

El archivo `render.yaml` existe en el repositorio pero **el despliegue en Render (u otras plataformas cloud con IPs de EE.UU.) no funciona**.

**Motivo:** Las APIs de datos de cripto que usa el proyecto (Binance Futures, Bybit, Bitget…) **bloquean peticiones procedentes de IPs de EE.UU.** por restricciones regulatorias. El escaneo devuelve `DataUnavailable` para todos los símbolos y la app queda vacía.

**Estado actual:** `render.yaml` se conserva como referencia, pero hay que considerarlo **no funcional**. Intentar desplegarlo en regiones fuera de EE.UU. (p.ej. Frankfurt) tampoco garantiza que las APIs sean accesibles desde IPs de servidores cloud.

**Conclusión:** Este proyecto es un **MVP local** pensado para ejecutarse en la máquina del desarrollador (WSL/Ubuntu), donde la IP del usuario no está bloqueada. No planificar despliegue cloud hasta resolver el acceso a las APIs.

---

## Entorno de desarrollo

| Herramienta | Versión mínima |
|-------------|---------------|
| Python | 3.10 |
| pandas | 2.0 |
| numpy | 1.24 |
| requests | 2.31 |
| PyYAML | 6.0 |
| streamlit | 1.34 |
| plotly | 5.20 |

### Setup en WSL/Ubuntu

```bash
sudo apt-get update && sudo apt-get install -y python3.12-venv python3-pip
cd /home/alb/projects/crypto-pump-detector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Nota:** El proyecto usa el Python del sistema (no pyenv) para evitar errores de `ctypes`/`pandas`. Está forzado con `.python-version = system`.

---

## Comandos de uso

```bash
# Tests
pytest -q

# Escaneo CLI (genera data/latest_scan.csv, signals.sqlite, alerts.csv
# + data/liquidations/*.json; el propio scan dispara un burst WS de ~60s
# para llenar data/liquidations/_ws_history.jsonl)
PYTHONPATH=src python scripts/scan.py

# Burst WS suelto (sin pasar por el scan completo)
PYTHONPATH=src python -m pump_detector.liquidations.executed_ws \
  --duration 60 --out data/liquidations/_ws_history.jsonl

# Dashboard interactivo
PYTHONPATH=src streamlit run app.py

# Build estático del dashboard (lo que usa GitHub Pages)
PYTHONPATH=src python scripts/build_html.py
```

---

## Lógica de señales

Las señales se generan en `src/pump_detector/signals.py`. Una señal requiere **los tres** factores simultáneamente:

1. **`price_impulse`** — Vela alcista, cierre en el tercio superior del rango, z-score de retorno ≥ umbral configurable (`price_zscore_threshold`, default 2.5).
2. **`oi_impulse`** — OI crece y su z-score ≥ umbral (`oi_zscore_threshold`, default 2.5).
3. **`first_impulse`** — Sin señales similares en las últimas N velas (`lookback_no_previous_signal`, default 10), sin run de precio reciente excesivo, y sin expansión consecutiva de OI demasiado larga.

El campo `alert_triggered` añade además los filtros opcionales de `settings.yaml`:
- `require_volume_confirmation`
- `require_breakout_20`
- `require_sma200_reclaim`
- `allowed_funding_classes`

### Clasificación de funding

| Clase | Condición |
|-------|-----------|
| `NEGATIVE` | funding < 0 |
| `NEUTRAL` | 0 ≤ funding ≤ 0.0001 |
| `POSITIVE` | 0.0001 < funding ≤ 0.0005 |
| `HOT` | funding > 0.0005 |
| `EXTREME` | funding ≥ percentil 95 de los últimos 270 periodos |
| `UNKNOWN` | dato ausente/NaN |

### Scores

- **`early_bullish_score`** (0–100): pondera z-scores de precio, OI y volumen + funding + breakout + first_impulse.
- **`blowoff_risk_score`** (0–100): pondera z-scores + funding + distancia a SMA200.

---

## Configuración (`config/settings.yaml`)

```yaml
timeframes: [1d]          # Marcos temporales a escanear

alert_conditions:
  require_volume_confirmation: false
  require_breakout_20: false
  require_sma200_reclaim: false
  allowed_funding_classes: [NEGATIVE, NEUTRAL, POSITIVE, HOT]

thresholds:
  lookback_stats: 100               # Ventana para z-scores rolling
  lookback_no_previous_signal: 10   # Cooldown entre señales
  price_zscore_threshold: 2.5
  oi_zscore_threshold: 2.5
  volume_zscore_threshold: 1.5
  close_position_min: 0.65          # Cierre en el top 35% del rango
  max_recent_price_run_pct: 0.45    # Máximo run de precio en 10 velas
  max_consecutive_oi_expansion: 3   # OI expandiéndose N velas seguidas → tarde

storage:
  sqlite_path: data/signals.sqlite
  alerts_csv: data/alerts.csv

liquidations:
  enabled: true
  executed:
    enabled: true
    providers: [binance_ws, bybit_ws, okx_ws]
    burst_seconds: 60                              # 0 = desactiva el burst en el scan
    history_file: data/liquidations/_ws_history.jsonl
    max_age_days: 14                               # prune al inicio de cada burst
  projected:
    enabled: true
    provider: coinglass
    use_frontend_endpoint: true                    # scrape público sin key
    require_paid: false
  coinalyze:
    enabled: true                                  # fetch silencioso si no hay key
```

> **Para ver liquidaciones históricas de SAND (o cualquier altcoin):**
> 1. Crea cuenta en https://coinalyze.net y genera key en /account/api-key/
> 2. `export COINALYZE_API_KEY=...` antes de `streamlit run app.py` o `scripts/scan.py`
> 3. El chart pintará longs (rojo) y shorts (verde) agregados por intervalo. Sin key se omite silenciosamente y se cae al historial WS local.

---

## Overlay de liquidaciones (gratis, sin API key)

Objetivo: pintar capa de liquidaciones sobre el gráfico de precio sin depender de planes de pago.

| Capa | Fuente | Coste |
|------|--------|-------|
| `executed` (live) | WebSockets públicos: Binance `!forceOrder@arr` (USDM + COIN-M), Bybit `allLiquidation.linear`, OKX `liquidation-orders` | 0 € |
| `executed` (histórico) | Coinalyze `/v1/liquidation-history` con `COINALYZE_API_KEY` (registro gratis en https://coinalyze.net/account/api-key/). Agregados por intervalo (long $ / short $) — el chart snapea precio al close de la vela. | 0 € |
| `projected` | Endpoint frontend público de CoinGlass (`fapi.coinglass.com/api/futures/liquidation/aggregated-heatmap`). Si existe `COINGLASS_API_KEY` se intenta primero la API oficial y se cae al frontend si falla. CoinGlass cerró el free tier en 2025-Q4 → desactivado por defecto. | 0 € (roto) |

Flujo:

1. `scripts/scan.py` invoca `collect_executed_burst()` antes del bucle de detalles → abre los WS en paralelo durante `burst_seconds`, normaliza eventos al schema común y los appendiza a `_ws_history.jsonl` (con `flock`).
2. Para cada símbolo del watchlist, `fetch_liquidation_map()` lee del JSONL (filtrado por símbolo canónico + ventana del timeframe) y, en paralelo, pide el heatmap projected.
3. `_export_liquidations()` escribe `data/liquidations/{EXCHANGE}_{TICKER}_{TF}.json` que consumen tanto Streamlit como `scripts/build_html.py`.

Schema común (`LIQUIDATION_COLUMNS`):

```text
timestamp, price, quantity, notional, side, kind, source
```

- `side`: `long` (posición long liquidada → orden SELL) / `short` / `unknown`
- `kind`: `executed` / `projected`
- `source`: `binance_ws` / `bybit_ws` / `okx_ws` / `coinglass` / `coinglass_frontend`

Fallback silencioso: si una fuente falla (timeout, paywall, IP bloqueada) devuelve `empty_liquidations()` y el dashboard sigue funcional.

El burst se dispara en cuatro sitios:

- **Auto al arrancar `streamlit run app.py`**: `_maybe_start_background_burst()` lanza un daemon thread con `collect_executed_burst()`. No bloquea la UI; al terminar limpia el caché de `_cached_liquidations`. Throttled por mtime del JSONL (`auto_burst_min_interval_minutes`, default 5).
- `scripts/scan.py` al inicio (configurable con `burst_seconds` o env `SCAN_BURST_SECONDS`)
- `python -m pump_detector.liquidations.executed_ws` (CLI suelto)
- Botón **🔥 Actualizar liquidaciones (WS 20s)** dentro del propio Streamlit

Configuración relevante (`settings.yaml` → `liquidations.executed`):

```yaml
auto_burst_on_startup: true            # desactívalo si te molesta
auto_burst_seconds: 20
auto_burst_min_interval_minutes: 5     # throttle entre bursts auto
```

---

## Patrones de código importantes

- **`PYTHONPATH=src`** es obligatorio para que el paquete `pump_detector` resuelva correctamente.
- Todas las rutas se calculan relativas a `ROOT = Path(__file__).resolve().parents[2]` (raíz del repo) en `config.py`.
- `scan_watchlist()` devuelve `(DataFrame, dict[(symbol, timeframe) → DataFrame])`. Nunca asumas que `persist=True` por defecto (en scripts/tests puede ser `False`).
- `SignalSnapshot` es un `@dataclass(frozen=True)`. Para generar snapshots vacíos usa `_blank_snapshot()` en `scanner.py`.
- Tests en `tests/` usan `pytest` con `pythonpath = ["src"]` declarado en `pyproject.toml`.
- **`SCAN_TIMEFRAME`** env var override: se lee en `load_settings()`. Solo acepta un valor de `VALID_TIMEFRAMES = {"1h", "4h", "1d"}`.

---

## Reglas para agentes

1. **Tests primero**: Antes de modificar `signals.py`, `scanner.py` o `storage.py`, ejecuta `pytest -q` para confirmar que el estado base es verde.
2. **No modificar `data/`** salvo que el usuario lo autorice explícitamente. Excepción permanente: `data/liquidations/_ws_history.jsonl` es cache compartida con el CI y puede regenerarse.
3. **Umbrales viven en `settings.yaml`**, no hardcodeados en Python (salvo los defaults de `config.py`).
4. **No cambiar la firma pública de `SignalSnapshot`** sin actualizar `storage.py` y `app.py`.
5. **Símbolo de watchlist**: El formato esperado es `EXCHANGE:SYMBOL` (e.g. `BINANCE:BTCUSDT.P`). La normalización la hace `symbols.normalize_symbol()`.
6. **Añadir dependencias**: Actualiza tanto `requirements.txt` como la sección `dependencies` en `pyproject.toml`.
7. **WSL paths**: En Windows usa `\\wsl.localhost\Ubuntu\home\alb\projects\crypto-pump-detector\…` como ruta absoluta.

