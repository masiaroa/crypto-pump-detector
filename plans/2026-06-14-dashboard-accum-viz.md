# Dashboard: explicación de paneles + visualización ACCUM

Contexto: el usuario vio `ACCUM` en HBAR. Análisis previo: la señal
whale-accumulation saltó en 4h por build de OI (+13.8 % en 20 velas) con el
precio plano; score candle-native (solo OI, Bybit no da CVD) = 100, score
plegado final = 68.2 (≥55 y flow 20 ≥15 → flag). De ahí salieron dos peticiones
sobre el dashboard.

## Tareas
- [x] Icono "i" clicable junto a la etiqueta de cada panel (Price / Open
      Interest / Volume / Funding·Basis) con un popover que explique qué lleva
      dentro — incluida la línea azul del panel de funding = basis (premium index).
- [x] Línea del `whale_accum_score` como overlay sobre el panel de OI (eje
      0-100 a la izquierda), coloreada por el flag de cada vela: azul = ACCUM,
      violeta = WHALE PUMP. Solo se dibuja en velas con señal (hueco donde no hay,
      sin baseline gris). Requiere exportar `whale_accum_flag` y
      `whale_pump_flag` por vela (hecho en scan.py `_export_charts`); la línea
      aparece tras el próximo `refresh.sh` — los JSON commiteados aún no llevan
      los flags. Verificado recomputando el flag por vela de HBAR: enciende en
      06-13 08:00→20:00 (score≥75 en OI-only) y deja hueco en las de score 74.

## Notas
- `refresh.sh` es local; en CI Bybit/Binance están geo-bloqueados. Validar build
  con `PYTHONPATH=src python scripts/build_html.py` + `validate_static_html.py`.
- No tocar alertas existentes (OI surge / volumen).
