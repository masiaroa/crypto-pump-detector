# Plan 2026-06-14 — ¿Tiene edge alguna versión del whale indicator?

Decisión del usuario: intentar una formulación con edge antes de quitarlo. Informe
estático en `report/` → `docs/report.html`. **No** tocar scanner/dashboard ni las
alertas existentes (OI surge / volumen) hasta que el informe lo justifique y el
usuario lo apruebe.

## Hallazgos de partida (ya validados)
- [x] `whale_pump_flag` (violeta) no salta NUNCA: 0/21120 (4h), 0/9584 (1d).
- [x] `whale_accum_flag` (azul) sin edge: ruido en 4h, negativo en 1d (5d: −12.8% vs
      −6.8% base; solo 7/25 símbolos baten la base).

## Investigación
- [x] **Auditoría de datos**: cobertura temporal de cvd / ls_long / whale_accum_score
      (sospecha: solo poblados en velas recientes → el flag histórico es inconsistente).
- [x] **Score continuo**: ¿el `whale_accum_score` por cuantiles tiene relación monótona
      con el retorno futuro? (solo velas con score>0).
- [x] **Proxies de OI/precio (siempre disponibles)**: OI-build con precio plano/abajo
      vs base; varias definiciones; des-clusterizado + consistencia por símbolo.
- [x] **Métrica "precede a pump"**: P(máx retorno futuro ≥ +10/20% en N velas | señal)
      vs base, además del retorno medio.
- [x] **Diagnóstico del pump muerto**: reconstruir volume_zscore/green/retail y ver qué
      condición lo bloquea; probar una ignición corregida/relajada y su edge.
- [x] **Filtro de régimen**: ¿algo funciona solo en tendencia alcista (precio > media)?

## Resultados (4h, 40 símbolos, 21.120 velas)
- **Cobertura**: ls_long 0% en la 1ª mitad / 73% en la 2ª; cvd 37%; score>0 solo 25%.
  El whale_accum_score NO es computable de forma consistente en el histórico.
- **Score por quintiles**: sin relación monótona (Q5 +0.33%/hit48%, Q1 −0.09%). Ruido.
- **Acumulación (OI sube + precio plano/abajo)**: edge NEGATIVO y P(pump) por DEBAJO
  de la base (11% vs 16%). El filtro de régimen no la rescata. La tesis no se sostiene.
- **Pump muerto**: lo mata la conjunción de 5 condiciones (ALL=6 velas); vol_z≥2 (6%) +
  prior_score≥45 (depende del score escaso) + retail_up (depende de L/S escaso) ≈ 0.
- **Lo único con edge = momentum**: "OI sube + precio YA sube" y "OI-build + vela verde
  vol_z≥2" elevan P(pump≥20% en 5d) a 11–16% vs 4% base. Pero alta varianza, hit ≤ base
  y consistencia por símbolo ≤50% → no fiable, y **solapa con las alertas OI/volumen**
  existentes (que no se deben duplicar).

## Veredicto
La señal de **acumulación** (lead, manos fuertes antes que retail) **no tiene edge** y
sus inputs no están disponibles en el histórico. Recomendación: **quitar** el whale
indicator. El edge de momentum es duplicado de OI/volumen → no justifica alerta nueva.

## Barrido OI × volumen × precio (responde a la duda del usuario)
- [x] "Precio plano + OI subiendo": SIN edge (0.8× la base; con volumen 0.6×). Descartado.
- [x] Lo único con edge = **co-ocurrencia OI surge + volumen surge** (2.7×) y con vela
      verde/momentum (3.2–3.9×). Es la `PUMP_ALERT` ya validada en el informe §5.

## Entrega (en rama claude/youthful-lovelace-wo1dnc, NO en main todavía)
- [x] Informe: nueva **§6 (whale debunk)** + la duda "plano+OI" respondida; §5 ya documenta
      el composite `PUMP_ALERT`. `report/index.html` + `docs/report.html` regenerados.
- [x] Dashboard: whale retirado (línea, columna Whl, chips, badges, tooltip, 'i').
- [x] **Prototipo `PUMP_ALERT`**: marcadores ▲ en el panel Price (solo 4h), calculado en
      cliente. 104 disparos / 32 símbolos. Para que el usuario lo VEA antes de decidir.
- [ ] Usuario revisa los HTML. Si le gusta el composite → mover `PUMP_ALERT` a evento en
      el scanner (sin tocar OI/VOL surge) + merge a main. Si no → solo whale-out + informe.

