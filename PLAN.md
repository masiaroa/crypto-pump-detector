# PLAN — Dual timeframe (4h + 1d) en dashboard estático

> Objetivo: que el HTML del CI (GitHub Pages) muestre datos en **4h** y **Diario (1d)**, con un toggle por vista para alternar rápido.

## Contexto rápido (estado actual)

- `config/settings.yaml` → `timeframes: [1d]`. Env override `SCAN_TIMEFRAME` solo admite **un** valor (`{"1h","4h","1d"}`) — `load_settings()` lo reemplaza, no lo añade.
- `scripts/scan.py` ya genera ficheros por `(symbol, timeframe)`:
  - `data/charts/{EXCHANGE}_{TICKER}_P_{TF}.json` (vía `_sanitize_key`)
  - `data/liquidations/{EXCHANGE}_{TICKER}_P_{TF}.json`
  - `data/latest_scan.csv` (columna `timeframe`)
  - `data/event_history.csv` (columna `timeframe`)
- `scripts/build_html.py` actualmente carga `CHARTS_DIR/*.json` sin filtrar por TF y mete todo en `CHART_DATA[symbol] = [candles]`. Si hay 2 TFs por símbolo, **se pisan** según orden de `sorted()`.
- Frontend (`STATIC_JS`) lee `CHART_DATA[symbol]` directamente — no conoce TFs.

## Cambios

### 1) Backend — generar ambos timeframes

**`config/settings.yaml`**: `timeframes: ["4h", "1d"]`.

**`src/pump_detector/config.py`**: `SCAN_TIMEFRAME` debe poder ser CSV (`"4h,1d"`) o un solo valor. Parsear con `split(",")`, validar contra `VALID_TIMEFRAMES`, mantener orden. Sin override → usar lo del YAML.

**`scripts/scan.py`**: ya itera por `details[(symbol, tf)]` — debería funcionar sin cambios una vez `settings.timeframes` traiga dos valores. Verificar que `scan_watchlist` los recorre.

**Workflow (`.github/workflows/pages.yml`)**: nada que tocar, pero comprobar que el tiempo total no se duplica demasiado (4h pide menos historial; debería ir bien). Considerar `SCAN_TIMEFRAME` env si se quiere forzar desde CI.

### 2) `scripts/build_html.py` — exponer ambos TFs al frontend

**`load_charts()`** debe devolver `dict[symbol, dict[tf, list[candles]]]` en vez de `dict[symbol, list]`. Leer `timeframe` del JSON (ya está en el output de `_export_charts`).

**`load_liquidations()`** análogo: `dict[symbol, dict[tf, list]]`.

**`load_embedded_charts/liquidations()`**: actualizar el regex/JSON parser para el nuevo shape.

**`load_scan()`**: hoy clave por `symbol`. Cambiar a clave compuesta o anidar por TF. Para la slide de eventos basta con el TF "principal" (1d) — los scores/funding son por TF. Sugerencia: `scan: dict[symbol, dict[tf, row]]` y elegir `1d` como vista default para tarjetas/tabla.

**`make_crypto_slide()`**: aceptar `candles_by_tf` y `liqs_by_tf`. Renderizar 4 canvas como siempre, pero añadir en `slide-header` (o `crypto-meta`) un grupo de botones:

```html
<div class="tf-toggle" data-canvas-id="s{idx}">
  <button class="tf-btn active" data-tf="1d">1D</button>
  <button class="tf-btn" data-tf="4h">4H</button>
</div>
```

`data-symbol` ya está en `<section>`; añadir `data-default-tf="1d"`.

**`build_html()`**: cambiar serialización:

```js
const CHART_DATA       = { "BYBIT:BTCUSDT.P": { "1d": [...], "4h": [...] }, ... };
const LIQUIDATION_DATA = { "BYBIT:BTCUSDT.P": { "1d": [...], "4h": [...] }, ... };
```

Mantener compat hacia atrás: si una serie viene como lista plana (build antiguo), envolverla como `{"1d": [...]}`.

### 3) `STATIC_JS` — toggle reactivo

`initCharts(slideEl, idx)` ahora debe:

1. Leer `tf = slideEl.dataset.currentTf || slideEl.dataset.defaultTf || "1d"`.
2. Sacar `raw = CHART_DATA[symbol][tf]` y `liqs = LIQUIDATION_DATA[symbol]?.[tf] || []`.
3. Guardar las instancias Chart.js en `slideEl._charts = {price, oi, vol, fr}` para poder `.destroy()` en el switch.

Añadir handler:

```js
slidesEl.addEventListener('click', (e) => {
  const btn = e.target.closest('.tf-btn');
  if (!btn) return;
  const slide = btn.closest('.slide');
  const tf = btn.dataset.tf;
  if (slide.dataset.currentTf === tf) return;
  slide.dataset.currentTf = tf;
  slide.querySelectorAll('.tf-btn').forEach(b => b.classList.toggle('active', b === btn));
  if (slide._charts) Object.values(slide._charts).forEach(c => c && c.destroy());
  slide._charts = null;
  initCharts(slide, parseInt(slide.dataset.idx, 10));
});
```

`inited` set actual hay que matizarlo: se inicializa al entrar la 1ª vez; el toggle re-inicializa manualmente sin tocar `inited`.

### 4) `STATIC_CSS` — botones del toggle

Mini-estilo arriba-derecha del header de crypto:

```css
.tf-toggle { display: flex; gap: 2px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 2px; }
.tf-btn { background: transparent; color: #8b949e; border: 0; padding: 3px 9px; font-size: 11px; font-weight: 700; border-radius: 4px; cursor: pointer; }
.tf-btn:hover { color: #e6edf3; }
.tf-btn.active { background: #21262d; color: #58a6ff; }
```

### 5) Validador

`scripts/validate_static_html.py`: si chequea estructura, añadir asserts mínimos:
- existen `.tf-toggle` en cada slide de crypto
- `CHART_DATA` parseable y al menos un símbolo tiene claves `1d` y `4h`

### 6) Tests

Añadir/actualizar tests mínimos:
- `tests/test_build_html.py` (si existe): que `build_html()` con `charts={sym: {"1d":[...], "4h":[...]}}` emite el shape JS correcto y los botones.
- `tests/test_config.py`: parseo de `SCAN_TIMEFRAME="4h,1d"`.

## Checklist de aceptación

- [ ] `pytest -q` verde.
- [ ] `PYTHONPATH=src python scripts/scan.py` genera ficheros `*_4h.json` y `*_1d.json` para todos los símbolos del watchlist.
- [ ] `python scripts/build_html.py` emite `docs/index.html` con `CHART_DATA[sym]["1d"]` y `CHART_DATA[sym]["4h"]` poblados.
- [ ] Abrir `docs/index.html`: en cada slide de crypto, botones **1D / 4H** arriba-derecha; al pulsar, los 4 gráficos se redibujan con el TF elegido sin saltar de slide.
- [ ] Default = 1D. Cambio recordado solo dentro de la slide (no global).
- [ ] CI corre el workflow sin timeouts; artifact Pages publicado.

## Notas / decisiones abiertas

- ¿Persistir la elección de TF entre slides (localStorage) o reset al cargar? **Default propuesto: reset** (más simple, sin sorpresas).
- ¿Toggle global arriba además del por-slide? No por ahora; añade complejidad y la navegación slide-a-slide ya es por símbolo.
- La slide de eventos (`slide-0`) usa scores de **1d** para tarjetas. Si en el futuro se quiere alternar también ahí, mismo patrón.

## Frescura de datos en CI

Sí: cada ejecución del workflow `pages.yml` llama a `scripts/scan.py`, que pide datos en vivo a Binance/Bybit/etc. Los caches de `data/charts` y `data/liquidations` se restauran solo para tener un fallback si una API falla — el scan los sobreescribe con la fetch nueva. Triggers: cron diario 02:00 UTC, push a `main` (paths filtrados), y dispatch manual.
