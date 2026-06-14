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
- [ ] Línea móvil del `whale_accum_score` en el gráfico, al estilo de la línea
      de basis. Pendiente de confirmar ubicación: panel propio "ACCUM" en el
      pager vs overlay sobre el panel de OI. (El score por vela ya se exporta al
      JSON, no hace falta dato nuevo.)

## Notas
- `refresh.sh` es local; en CI Bybit/Binance están geo-bloqueados. Validar build
  con `PYTHONPATH=src python scripts/build_html.py` + `validate_static_html.py`.
- No tocar alertas existentes (OI surge / volumen).
