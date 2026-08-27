# Migración a Render: pump detector + ciclos dentro de albtrader

Contexto: hoy el dashboard vive en GitHub Pages (repo público) y los datos los
genera `refresh.sh` en el portátil, porque Bybit y Binance geo-bloquean los
runners de GitHub Actions. El objetivo es llevarlo todo a la infraestructura de
Render que ya se paga para `my-quan-screener` (13,30 $/mes: web Starter +
Postgres basic-256mb, ambos en Frankfurt), **sin pagar un céntimo más**, con
alertas de volumen por Telegram cada hora, y con el repo volviendo a privado y
Pages apagado una vez la mudanza esté hecha.

Decisiones ya tomadas por Alberto (27-ago-2026):

- El scan corre **en Render**, no en Actions ni en local.
- Pages y el repo público **se retiran al terminar**, no antes.
- Alerta de Telegram por defecto: **VOLUME_SURGE**. Los demás tipos existen y se
  encienden o apagan desde la configuración, no tocando código.
- El report de ciclos (`crypto-futures-analyzer`) sigue el mismo camino, si su
  refresco también sobrevive desde la IP de Render.

---

## 0. Lo verificado (medido hoy, no supuesto)

Prueba de conectividad desde un runner cloud (IP de datacenter, EE.UU.):

| API | Resultado | Nota |
|---|---|---|
| Bybit `/v5/market/kline` | **403** | CloudFront: *blocked access from your country* |
| Binance `fapi` klines | **451** | *restricted location* |
| OKX candles / OI history / funding / L-S | **200** | sirve todo lo que el scanner necesita |
| Bitget mix candles | **200** | pero `_fetch_bitget` acaba siempre en `DataUnavailable`: no tiene OI histórico |
| Coinalyze `/v1/*` | **401** | red OK; solo falta la key |

Inventario del lado albtrader (piezas que ya existen y hay que reutilizar, no
reinventar):

- `web_app/api/auth_routes.py` → decorador **`requires_admin`** ya montado, con
  redirección a login para páginas y 403 JSON para API.
- `backend/services/notification_service.py` → cola en Postgres + Telegram +
  email, reintentos, dedupe por destinatario, y **`ADMIN_ONLY_EVENTS`**, que es
  exactamente el gancho para que una alerta cripto no salga del círculo admin.
  Añadir un tipo nuevo es una rama más en `render_message()`, como `EVENT_OPS`.
- `web_app/api/fundamental_watch_routes.py` → patrón de endpoint cerrado con
  token compartido (`X-Watch-Token`), que es el molde para publicar el HTML de
  ciclos desde fuera.
- `render.yaml` es *Blueprint managed*: **toda variable nueva va en el YAML** o
  se la come la siguiente sincronización. Los secretos, con `sync: false`.

Tamaños que condicionan el diseño: `docs/index.html` pesa **14,2 MB** porque
embebe las series; `data/charts/` son **15,3 MB** en 80 JSON. El Postgres de
Render está en 270 MB de 1 GB.

---

## 1. Fase 0 — el test que decide todo el resto

Nada de lo que sigue tiene sentido hasta saber **qué alcanza la IP de Render
Frankfurt**. El 403/451 de arriba es de una IP estadounidense; Frankfurt es otro
país y otro rango, y Bybit y Binance sí operan en la UE. Puede salir bien.

- [x] Ruta temporal `GET /api/crypto/diagnostico-red` en albtrader, con
      `requires_admin`. **Hecha** (27-ago-2026, `web_app/api/crypto_diag_routes.py`
      + `test/test_crypto_diag_routes.py`, 20 tests en verde). Cubre 19 sondas:
      las del pump detector y las del report de ciclos, filtrables por
      `?grupo=` y `?clave=`. Devuelve código, latencia, muestra y veredicto, y
      traduce el resultado al camino A/B/C de la tabla de abajo. Sin
      dependencias nuevas. Corre entera en ~30 s desde una IP cloud.
- [x] Correrla en producción y **anotar el resultado en este fichero**.
      **Hecho el 27-ago-2026 a las 20:40 UTC: camino A.** 18 sondas `ok` y una
      `alcanzable` (Coinalyze, solo le falta la key). Responden Bybit (velas, OI
      y funding), Binance USDT-M (velas, OI, funding) y Binance spot, que es
      justo lo que el 403/451 desde una IP estadounidense hacía temer. También
      responden las seis fuentes del report de ciclos. La pasada entera tardó
      **4,8 s** — desde una IP cloud de EE.UU. tardaba 30 s, así que Frankfurt
      no solo llega: llega cerca (~250 ms por llamada).
- [ ] Repetirla a las 24 h: un bloqueo geográfico puede aparecer después.

Y según lo que salga:

| Resultado | Camino |
|---|---|
| Bybit y Binance responden | **Camino A**: el scanner se mueve tal cual, sin tocar `data_clients.py`. Es el caso bueno. ← **es el que salió** |
| Solo OKX (y Coinalyze) responden | **Camino B**: añadir proveedor OKX a `data_clients.py` y mapear el watchlist. Trabajo real, pero el fallback por proveedor ya existe. |
| Ni OKX responde | **Camino C**: el scan se queda en local y Render solo publica y avisa. Es el plan de retirada, no el objetivo. |

> El `AGENTS.md` de este repo afirma que el despliegue cloud **no es viable**.
> Esa frase es de mayo y ya es falsa a medias: OKX y Bitget contestan. Sea cual
> sea el camino, hay que reescribir esa sección al terminar.

---

## 2. Arquitectura destino

```
  Render web service "albtrader"  (Starter, Frankfurt, YA PAGADO)
  ├── proceso Flask/gunicorn
  │   ├── scheduler de posiciones (bolsa)   ← existe, NO se toca
  │   └── scheduler cripto (cada hora)      ← nuevo, ENABLE_CRYPTO_SCHEDULER
  │        scan → disco efímero (charts + HTML) → eventos a Postgres
  │                                              → cola de notificaciones
  ├── /crypto            requires_admin  → dashboard del pump detector
  ├── /crypto/ciclos     requires_admin  → report de crypto-futures-analyzer
  └── /crypto/config     requires_admin  → qué tipos de evento avisan
  
  Render Postgres (basic-256mb, YA PAGADO)
  ├── crypto_events            eventos con su vela → histórico y dedupe
  ├── crypto_alert_config      qué avisa y en qué marco temporal
  └── notification_queue       la cola que YA existe, con un tipo más
```

**Lo regenerable vive en disco efímero, no en Postgres.** Los 15 MB de charts y
el HTML montado los rehace el scan cada hora: meterlos en una base de 1 GB con
270 MB ya ocupados sería pagar espacio por algo que se reconstruye solo. En
Postgres queda únicamente lo que tiene que sobrevivir a un reinicio: los
eventos, la configuración y la cola de avisos.

Contrapartida: tras cada deploy el disco está vacío y no hay dashboard hasta el
primer scan. Se resuelve como ya lo resuelve el robot trader — una pasada al
arrancar (`socketio.start_background_task(...run_once)`), que además es el
patrón que el repo ya conoce.

---

## 3. Fases

### Fase 1 — El scan corre en Render

- [ ] Fase 0 cerrada y camino elegido (A, B o C).
- [ ] Vendorizar el paquete: `pump_detector` tiene que ser importable desde
      albtrader. Decidir entre subcarpeta `backend/crypto/` (simple, duplica
      código) o dependencia pip desde el repo git (limpio, pero ata los
      despliegues). **Recomendación: subcarpeta**, y este repo sigue siendo el
      sitio donde se investiga.
- [ ] `ENABLE_CRYPTO_SCHEDULER` (default `false`) + `CRYPTO_SCAN_INTERVAL_HOURS`
      (default `1`) en `render.yaml`, sin `sync: false`.
- [ ] Servicio `crypto_scan_scheduler_service.py`, calcado del de robot trader:
      hilo propio, `try/except` total, timeout duro por símbolo.
- [ ] **Apagar el burst de WebSockets** (`burst_seconds: 0`): 60 s de sockets
      abiertos dentro del proceso que gestiona stops es un riesgo que no compra
      nada. Las liquidaciones históricas vienen de Coinalyze.
- [ ] Medir RAM y duración de una pasada completa antes de subir el intervalo.
      Starter son 512 MB y ahí dentro ya vive la web.
- [ ] Guardar eventos en `crypto_events` con la marca de tiempo **de la vela**,
      no la del scan.

> Frontera que no se cruza: el scan cripto **no toca** el cerrojo de cartera de
> `portfolio_lock_service.py`. Es otro dominio y no compite por nada. Lo único
> que comparte con la bolsa es el proceso, y por eso el interruptor de apagado
> es innegociable.

### Fase 2 — Alertas de Telegram

- [ ] `EVENT_CRYPTO` en `notification_service.py`, dentro de
      `ADMIN_ONLY_EVENTS`, con su rama en `render_message()`.
- [ ] Tabla `crypto_alert_config`: por tipo de evento (`VOLUME_SURGE`,
      `OI_SURGE`, `ENTRY`, `WHALE_ACCUM`, `SQUEEZE_SETUP`…), si avisa y en qué
      marcos. **Arranca con VOLUME_SURGE encendido y el resto apagado.**
- [ ] Dedupe por `(símbolo, marco, tipo, vela)`. `already_enqueued_today()` no
      sirve aquí: trabaja por día y en 4h hay seis velas al día.
- [ ] **Solo velas cerradas.** Un scan horario ve la vela de 4h a medias; avisar
      de un volumen que aún puede desinflarse es avisar de nada.
- [ ] Repasar el volumen de mensajes con datos reales antes de encender:
      625 VOLUME_SURGE en ~3 meses son ~7 al día. Si molesta, el ajuste es
      `volume_surge_3bar_ratio` en `settings.yaml`, no filtrar en el aviso.

### Fase 3 — El dashboard en /crypto

- [ ] `create_crypto_routes(app)` registrado como los demás, con
      `requires_admin` en todas las rutas.
- [ ] `/crypto` sirve el HTML montado desde disco efímero. Con `whitenoise` ya
      en la web y gzip, los 14,2 MB viajan como ~2-3 MB.
- [ ] Si la primera medición dice que 14 MB por visita es demasiado: partir el
      HTML de las series y que la página pida `/api/crypto/chart/<sym>/<tf>`.
      `build_html.py` ya distingue `load_charts()` de `load_embedded_charts()`,
      así que el corte está medio hecho. **No hacerlo por adelantado.**
- [ ] Entrada en el menú solo visible con `is_admin` (`base.html` ya recibe la
      variable).
- [ ] `/crypto/config`: los interruptores de la Fase 2, editables.

### Fase 4 — Ciclos (crypto-futures-analyzer)

- [ ] Averiguar si su refresco aguanta en Render. **No es la misma pregunta**
      que la del pump detector: `update_futstats.sh` tira de Coinalyze (que
      responde), pero también de TradingView, Deribit/Tardis y bgeometrics.
      Extender el diagnóstico de la Fase 0 a esos cuatro dominios.
- [ ] Si aguanta: mismo esquema, un scan diario que regenera
      `report_ciclos.html` en disco efímero y lo sirve en `/crypto/ciclos`.
- [ ] Si no aguanta: el cron local sigue generando el HTML —como hoy— y se le
      añade un `POST /api/crypto/ciclos` con `X-Watch-Token`, calcado de
      `fundamental_watch_routes.py`. Son 277 KB: ahí sí cabe en Postgres.
- [ ] El report pide Plotly a `cdn.plot.ly`. Detrás del login sigue funcionando,
      pero conviene saberlo antes de que alguien monte una CSP.

### Fase 5 — Cerrar el círculo

- [ ] Un ciclo entero (alerta → Telegram → dashboard) funcionando **una semana**
      contra Pages todavía vivo, comparando resultados.
- [ ] Apagar `pages.yml` y desactivar GitHub Pages.
- [ ] Pasar el repo a **privado**.
- [ ] `refresh.sh` deja de ser el camino de producción; se queda para research
      local.
- [ ] Reescribir la sección "Despliegue en la nube — NO VIABLE" de `AGENTS.md`.
- [ ] Documentar la pieza nueva en albtrader: `AGENTS.md`,
      `docs/guides/COMO-FUNCIONA-produccion.md` y la tabla de piezas del
      `CLAUDE.md`, donde hoy solo hay web, base y worker.

---

## 4. Riesgos

1. **La IP de Render está bloqueada igual.** Es el riesgo que se come el plan, y
   por eso la Fase 0 va primero y sola. Camino C es la salida.
2. **El scan tumba la web.** Comparte proceso con quien gestiona stops y TP1.
   Mitigado con hilo aislado, timeouts, interruptor de apagado y una medición de
   RAM antes de encender. Si no cabe, un Background Worker de Render son 7 $/mes
   más — fuera de lo pedido, así que sería momento de volver a hablarlo.
3. **Lluvia de mensajes.** ~7 VOLUME_SURGE al día es mucho para un móvil. Se
   empieza con un solo tipo encendido y se sube desde ahí.
4. **El disco efímero engaña.** Funciona hasta el deploy siguiente. Si el scan
   de arranque falla, la página queda vacía sin que nadie se entere: merece un
   `EVENT_OPS` si el HTML tiene más de dos horas.
5. **Repo privado y Actions.** Al pasar a privado los minutos se miden (2.000/mes
   gratis). Da igual si el scan ya vive en Render, pero `pages.yml` y
   `api-test.yml` tienen que estar apagados para entonces.

## 5. Lo que aún no está decidido

- Vendorizar `pump_detector` o instalarlo desde git.
- Si `/crypto` sirve el HTML entero o series por API — se decide midiendo.
- ~~Si el watchlist de 43 símbolos se queda como está.~~ **Resuelto por el
  camino A**: Bybit y Binance responden, así que el watchlist se queda tal cual
  y no hay que mapear nada ni escribir el proveedor OKX.

Y una que abre el resultado: el diagnóstico prueba que **se llega**, no que
quepa el scan entero. Son ~520 peticiones por pasada (43 símbolos × 2 marcos ×
sus endpoints, más las de posicionamiento por símbolo); a los ~250 ms medidos
salen ~2,5 min secuenciales, que cabe de sobra en una pasada horaria. Lo que
falta por medir es el **límite de peticiones** de cada exchange y la **RAM**.
