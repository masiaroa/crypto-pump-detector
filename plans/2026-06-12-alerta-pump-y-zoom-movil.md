# Plan 2026-06-12 — Alerta PUMP custom + zoom táctil en móvil

Petición: (1) estudio de qué condiciones discriminan de verdad las subidas rápidas
y diseño de UNA alerta custom ("si tuvieras que disparar una alerta de pump,
¿cuándo exactamente?") — primero en informe, sin tocar las alertas existentes
(OI surge / volumen); si convence, se implementa en el scanner. (2) Zoom de
precio en móvil con gesto de pellizco (dos dedos), sincronizado con todos los
indicadores, sin romper el swipe arriba/abajo.

## Tareas

- [x] CLAUDE.md con las preferencias de planificación y flujo
- [x] Este fichero de plan con checkboxes
- [x] Estudio: grid de reglas candidatas sobre 40 símbolos × 4h con split
      temporal train/test (evitar sobreajuste), métricas: precisión P(+8%/+10%
      en 48h), lift, frecuencia de alertas, drawdown
- [x] Comparar contra las alertas existentes (OI surge 3-velas ≥4%, volumen
      3-velas ≥2.5×) como baseline
      → OI surge solo apenas supera el baseline en train (9.8% vs 7.9%);
        vol surge mejor; las dos a la vez bastante mejor (18.8/23.8)
- [x] Elegir la regla ganadora (estable en train y test) → "ALERTA PUMP"
      → vela verde ≥2% + volumen ≥2.5× mediana + OI 3-velas ≥2%
        (P+8%: 23.4% train / 34.1% test; P+10%: 25.9%; ~108 velas)
      → extra: el filtro "primer disparo" EMPEORA — las repeticiones dentro
        del tramo son las mejores; no hay que gatear por alerta previa
- [x] Sección nueva en el informe (`scripts/build_report.py`): sección 5
      "Si tuviera que disparar UNA alerta", con validación temporal, meseta
      de umbrales, avisos honestos (cazador de colas, re-disparos buenos),
      qué NO aportó, y últimos 12 disparos (ZEC 05-jun: 2 disparos → +26/+30%)
- [x] Zoom táctil: pellizco con dos dedos en cualquier gráfico del slide →
      zoom del eje X sincronizado en precio + OI + volumen + F/B
      (decisión: swipe de UN dedo intacto; dos dedos nunca navegan —
      no compiten porque se distinguen por número de dedos)
- [x] Regenerar informe + dashboard, validar, 140 tests OK
- [ ] Commit + merge a main + push
- [ ] PENDIENTE DE APROBACIÓN: implementar PUMP_ALERT como evento custom en
      el scanner + chip en dashboard (no tocar OI surge / VOL surge)
