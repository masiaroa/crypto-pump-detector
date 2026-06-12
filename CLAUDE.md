# CLAUDE.md

Detector de pumps en perpetuos cripto. `refresh.sh` (local, fuera de CI) baja datos
de Bybit/Binance, `scripts/scan.py` puntúa señales y `scripts/build_html.py` genera
el dashboard estático que GitHub Pages sirve desde `docs/`.

## Cómo le gusta planificar al usuario

Cuando el usuario pide un "plan" (o llegan varias peticiones juntas):

- Crear un fichero de planificación en `plans/` con la fecha en el nombre:
  `plans/YYYY-MM-DD-tema.md`.
- El plan lleva checkboxes (`- [ ]`) y se van marcando (`- [x]`) según se completa
  cada punto, actualizando el fichero durante el trabajo.
- El fichero se commitea junto con el trabajo para que quede el registro.

## Flujo de trabajo que prefiere

- Idioma: español en informes, dashboard y comunicación; código y commits en inglés.
- Señales nuevas: primero un informe estático con estadísticas que demuestren que
  aportan (en `report/`, copiado a `docs/report.html`); solo se implementan en el
  scanner/dashboard cuando el informe lo justifica y el usuario lo aprueba.
- No modificar las alertas existentes (OI surge / volumen) al añadir nuevas:
  crear tipos de alerta custom aparte.
- Integrar a `main` cuando lo pide: GitHub Pages despliega desde `main` (workflow
  `pages.yml`; se dispara con cambios en `scripts/**`, `src/**`, `config/**` y
  `data/**` — `docs/**` y `report/**` NO lo disparan, usar workflow_dispatch).
- De las señales clásicas, confía sobre todo en OI y volumen; funding y L/S poco.
  Validado en `docs/report.html`: el basis es coincidente/tardío al alza (solo el
  descuento fuerte lidera) y el OI confirma en las primeras velas del tramo.

## Entorno

- Los runners cloud (este entorno y GitHub Actions) tienen Bybit (403) y
  Binance (451) geo-bloqueados; OKX funciona (con User-Agent de navegador).
  Validar con los JSON commiteados en `data/charts/` o con OKX.
- pandas 3: los timestamps son `datetime64[us]`, no ns — usar `.dt.as_unit("ms")`
  para epoch-ms, nunca dividir `astype("int64")` por constantes.
- Tests: `PYTHONPATH=src:scripts python -m pytest tests/ -q`.
- Dashboard: `PYTHONPATH=src python scripts/build_html.py` +
  `python scripts/validate_static_html.py docs/index.html`.
- Informe: `PYTHONPATH=src python scripts/build_report.py` (cachés OKX en `report/`).
