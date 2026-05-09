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
        └── symbols.py      # normalize_symbol() → MarketInfo
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

# Escaneo CLI (genera data/latest_scan.csv, signals.sqlite, alerts.csv)
PYTHONPATH=src python scripts/scan.py

# Dashboard interactivo
PYTHONPATH=src streamlit run app.py
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
```

---

## Patrones de código importantes

- **`PYTHONPATH=src`** es obligatorio para que el paquete `pump_detector` resuelva correctamente.
- Todas las rutas se calculan relativas a `ROOT = Path(__file__).resolve().parents[2]` (raíz del repo) en `config.py`.
- `scan_watchlist()` devuelve `(DataFrame, dict[(symbol, timeframe) → DataFrame])`. Nunca asumas que `persist=True` por defecto (en scripts/tests puede ser `False`).
- `SignalSnapshot` es un `@dataclass(frozen=True)`. Para generar snapshots vacíos usa `_blank_snapshot()` en `scanner.py`.
- Tests en `tests/` usan `pytest` con `pythonpath = ["src"]` declarado en `pyproject.toml`.

---

## Reglas para agentes

1. **Tests primero**: Antes de modificar `signals.py`, `scanner.py` o `storage.py`, ejecuta `pytest -q` para confirmar que el estado base es verde.
2. **No modificar `data/`**: Los archivos CSV/SQLite son datos de runtime, nunca código.
3. **Umbrales viven en `settings.yaml`**, no hardcodeados en Python (salvo los defaults de `config.py`).
4. **No cambiar la firma pública de `SignalSnapshot`** sin actualizar `storage.py` y `app.py`.
5. **Símbolo de watchlist**: El formato esperado es `EXCHANGE:SYMBOL` (e.g. `BINANCE:BTCUSDT.P`). La normalización la hace `symbols.normalize_symbol()`.
6. **Añadir dependencias**: Actualiza tanto `requirements.txt` como la sección `dependencies` en `pyproject.toml`.
7. **WSL paths**: En Windows usa `\\wsl.localhost\Ubuntu\home\alb\projects\crypto-pump-detector\…` como ruta absoluta.

