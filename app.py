from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components

from pump_detector.config import ROOT, load_settings, load_watchlist
from pump_detector.liquidations import fetch_liquidation_report
from pump_detector.scanner import scan_watchlist
from pump_detector.storage import read_recent_alerts


CACHE_TTL_SECONDS = 2 * 60 * 60  # 2 hours


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _cached_scan_overview(symbols_tuple: tuple[str, ...], timeframe: str, persist: bool, limit: int, _settings_dict: dict, _ts: int):
    """Cached overview scan. _ts is a cache-buster key (epoch // TTL)."""
    from pump_detector.config import Settings
    s = Settings(
        timeframes=[timeframe],
        alert_conditions=_settings_dict["alert_conditions"],
        thresholds=_settings_dict["thresholds"],
        storage=_settings_dict["storage"],
        liquidations=_settings_dict.get("liquidations", {}),
    )
    return scan_watchlist(symbols=list(symbols_tuple), settings=s, persist=persist, limit=limit)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _cached_liquidation_report(symbol: str, timeframe: str, _settings_dict: dict, _ts: int):
    return fetch_liquidation_report(symbol, timeframe, settings=_settings_dict)


def _cache_ts() -> int:
    """Return a time bucket so the cache auto-expires every TTL window."""
    return int(time.time()) // CACHE_TTL_SECONDS


st.set_page_config(
    page_title="Crypto Pump Detector",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Global CSS: disable Ctrl+C "Clear caches" dialog, mobile-friendly layout,
# collapsible sidebar helpers
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* ---- Hide Streamlit "Clear caches" dialog triggered by Ctrl+C ---- */
    div[data-testid="stClearCacheDialog"],
    div[data-testid="stModal"],
    div[role="dialog"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    /* Also hide the modal overlay/backdrop */
    div[data-testid="stModalOverlay"],
    .stModal {
        display: none !important;
    }

    /* ---- Mobile responsive: stack columns vertically ---- */
    @media (max-width: 768px) {
        div[data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        div[data-testid="stHorizontalBlock"] > div {
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }
        .js-plotly-plot .plotly .main-svg {
            width: 100% !important;
        }
        .block-container {
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Inject JS via components.html to intercept Ctrl+C shortcut that triggers
# Streamlit's "Clear caches" dialog. This actually executes (unlike st.markdown
# <script> which Streamlit strips). Height=0 makes the iframe invisible.
components.html(
    """
    <script>
    // Access the parent Streamlit document from the iframe
    const parentDoc = window.parent.document;

    // Intercept keydown on the parent document to block Streamlit's handler
    parentDoc.addEventListener('keydown', function(e) {
        // Streamlit triggers "Clear caches" on Ctrl+C (or Cmd+C on Mac)
        // when there's no text selection. We block propagation to Streamlit
        // only when nothing is selected (pure Ctrl+C without copy intent).
        if ((e.ctrlKey || e.metaKey) && e.key === 'c' && !e.shiftKey) {
            const selection = parentDoc.getSelection();
            if (!selection || selection.toString().trim() === '') {
                e.stopPropagation();
                e.preventDefault();
            }
        }
        // Also block Ctrl+Shift+C entirely
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'C') {
            e.stopPropagation();
            e.preventDefault();
        }
    }, true);  // useCapture=true to run before Streamlit's handlers

    // Also close any existing modal that may already be open
    const observer = new MutationObserver(function(mutations) {
        for (const m of mutations) {
            for (const node of m.addedNodes) {
                if (node.nodeType === 1) {
                    const dialog = node.querySelector && node.querySelector('[role="dialog"], [data-testid="stModal"]');
                    if (dialog) {
                        dialog.style.display = 'none';
                        // Try to find and click the close/cancel button
                        const closeBtn = dialog.querySelector('button[aria-label="Close"], button:last-child');
                        if (closeBtn) closeBtn.click();
                    }
                }
            }
        }
    });
    observer.observe(parentDoc.body, { childList: true, subtree: true });
    </script>
    """,
    height=0,
)


def main() -> None:
    st.image("data/img/title.png", width=480)
    st.caption("Scanner de inicio de pump apalancado. Las senales usan solo datos disponibles al cierre de cada vela.")

    settings = load_settings()
    symbols = load_watchlist()
    if "selected_symbol" not in st.session_state:
        st.session_state.selected_symbol = _default_symbol(symbols)
    if "selected_timeframe" not in st.session_state:
        st.session_state.selected_timeframe = "1d"
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "detail"
    if "overview_tf" not in st.session_state:
        st.session_state.overview_tf = "1d"

    with st.sidebar:
        st.header("Watchlist")
        if st.button("📊 Overview", key="watch-overview", use_container_width=True):
            st.session_state.view_mode = "overview"
            st.session_state.pop("scan_result", None)
            st.session_state.pop("selected_market", None)
        _watchlist_buttons(symbols)
        st.divider()
        st.write(f"{len(symbols)} simbolos en watchlist")

    # ---- Scan settings in a collapsible expander on main area ----
    with st.expander("⚙️ Ajustes de escaneo", expanded=False):
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            selected_timeframes = st.multiselect("Timeframes", ["1h", "4h", "1d"], default=settings.timeframes)
            symbol_filter = st.text_input("Filtrar simbolo", value="")
        with sc2:
            limit = st.slider("Candles", min_value=120, max_value=500, value=260, step=20)
            persist = st.checkbox("Guardar snapshots y alertas", value=True)
        with sc3:
            run_selected = st.button("Actualizar seleccionado", type="primary", use_container_width=True)
            run_all = st.button("Actualizar toda la watchlist", use_container_width=True)
            force_refresh = st.button("🔄 Forzar refresco (borrar caché)", use_container_width=True)
            refresh_liqs = st.button(
                "↻ Reconsultar liquidaciones historicas",
                use_container_width=True,
                help="Borra la cache de Coinalyze y vuelve a consultar el historico agregado.",
            )

    # Force cache clear
    if force_refresh:
        _cached_scan_overview.clear()
        _cached_liquidation_report.clear()
        st.session_state.pop("scan_result", None)
        st.toast("Caché borrada. Reescaneando...", icon="🔄")

    if refresh_liqs:
        _cached_liquidation_report.clear()
        st.toast("Cache de liquidaciones historicas borrada.", icon="↻")

    pending_tf = st.session_state.pop("pending_timeframe", None)
    if pending_tf:
        st.session_state.selected_timeframe = pending_tf
        st.session_state.pop("scan_result", None)

    selected_symbol = st.session_state.selected_symbol
    selected_timeframe = st.session_state.selected_timeframe
    needs_initial_scan = "scan_result" not in st.session_state

    settings_dict = {
        "alert_conditions": settings.alert_conditions,
        "thresholds": settings.thresholds,
        "storage": settings.storage,
        "liquidations": settings.liquidations,
    }

    if run_all or force_refresh:
        st.session_state.view_mode = "overview"
        overview_tf = st.session_state.get("overview_tf", "1d")
        if force_refresh:
            ts = int(time.time()) // 1
        else:
            ts = _cache_ts()
        with st.spinner("Escaneando watchlist..."):
            st.session_state.scan_result = _cached_scan_overview(
                tuple(symbols), overview_tf, persist, limit, settings_dict, ts
            )
        st.session_state.overview_last_refresh = datetime.now()
    elif st.session_state.view_mode == "overview" and needs_initial_scan:
        overview_tf = st.session_state.get("overview_tf", "1d")
        with st.spinner("Escaneando overview (cacheado 2h)..."):
            st.session_state.scan_result = _cached_scan_overview(
                tuple(symbols), overview_tf, persist, limit, settings_dict, _cache_ts()
            )
        if "overview_last_refresh" not in st.session_state:
            st.session_state.overview_last_refresh = datetime.now()
    elif needs_initial_scan or run_selected:
        active_settings = settings.__class__(
            timeframes=[selected_timeframe],
            alert_conditions=settings.alert_conditions,
            thresholds=settings.thresholds,
            storage=settings.storage,
            liquidations=settings.liquidations,
        )
        with st.spinner(f"Actualizando {_coin_label(selected_symbol)}..."):
            st.session_state.scan_result = scan_watchlist(symbols=[selected_symbol], settings=active_settings, persist=False, limit=limit)


    df, details = st.session_state.scan_result
    df = df.copy()
    if df.empty:
        st.warning("No hay resultados. Revisa config/watchlist.yaml.")
        return
    df["coin"] = df["symbol"].map(_coin_label)

    _summary_metrics(df)
    if st.session_state.view_mode == "overview":
        _overview(df, details, symbol_filter, sort_by_default="early_bullish_score")
        return

    selected_key = f"{st.session_state.selected_symbol} | {st.session_state.selected_timeframe}"
    available = [f"{row.symbol} | {row.timeframe}" for row in df.itertuples()]
    if selected_key not in available and available:
        selected_key = available[0]
    symbol, timeframe = selected_key.split(" | ", 1)
    _detail(symbol, timeframe, df, details, settings)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _overview(df, details, symbol_filter: str, sort_by_default: str = "early_bullish_score") -> None:
    # ---- Header: title + last refresh info + force refresh button ----
    hdr_left, hdr_right = st.columns([0.7, 0.3])
    with hdr_left:
        st.subheader("Overview")
        last_refresh = st.session_state.get("overview_last_refresh")
        if last_refresh:
            elapsed = datetime.now() - last_refresh
            mins = int(elapsed.total_seconds() // 60)
            if mins < 1:
                ago = "hace unos segundos"
            elif mins < 60:
                ago = f"hace {mins} min"
            else:
                ago = f"hace {mins // 60}h {mins % 60}min"
            st.caption(f"📅 Última actualización: {last_refresh.strftime('%H:%M:%S')} ({ago})")
    with hdr_right:
        if st.button("🔄 Refrescar overview", use_container_width=True, key="force_refresh_overview"):
            _cached_scan_overview.clear()
            st.session_state.pop("scan_result", None)
            st.rerun()

    # ---- Timeframe selector for overview ----
    col_tf, col_sort = st.columns([0.3, 0.7])
    with col_tf:
        new_tf = st.segmented_control(
            "Timeframe",
            options=["4h", "1d"],
            selection_mode="single",
            default=st.session_state.get("overview_tf", "1d"),
            key="overview_tf_ctrl",
        )
        if new_tf and new_tf != st.session_state.get("overview_tf"):
            st.session_state.overview_tf = new_tf
            st.session_state.pop("scan_result", None)
            st.rerun()

    with col_sort:
        sort_options = [
            "early_bullish_score",
            "blowoff_risk_score",
            "oi_change_pct",
            "price_return_zscore",
            "funding_rate",
        ]
        sort_by = st.selectbox("Ordenar por", sort_options, index=sort_options.index(sort_by_default))

    # ---- 1. Eventos recientes (arriba) ----
    history = _event_history(details)
    st.subheader("🔔 Eventos recientes")
    if history.empty:
        st.info("No hay PRE_ENTRY, HOT_PRE_ENTRY o ENTRY recientes en el scan actual.")
    else:
        # Add raw symbol column for navigation lookup
        history["_raw_symbol"] = history["raw_symbol"]
        event_cols = [
            "symbol",
            "event_type",
            "timestamp",
            "timeframe",
            "close",
            "early_bullish_score",
            "blowoff_risk_score",
            "funding_classification",
        ]
        display_history = history[event_cols].copy()
        st.caption("👆 Selecciona una fila para ir al detalle")
        ev_event = st.dataframe(
            display_history,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="event_history_table",
        )
        # Navigate to detail on row selection
        try:
            ev_rows = ev_event.selection.rows
        except AttributeError:
            ev_rows = []
        if ev_rows:
            clicked = history.iloc[ev_rows[0]]
            st.session_state.selected_symbol = clicked["_raw_symbol"]
            st.session_state.selected_timeframe = clicked["timeframe"]
            st.session_state.view_mode = "detail"
            st.session_state.pop("scan_result", None)
            st.rerun()

        history_path = Path(ROOT / "data" / "event_history.csv")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history.drop(columns=["_raw_symbol"], errors="ignore").to_csv(history_path, index=False)

    # ---- 2. Tabla principal (abajo) ----
    st.subheader("📋 Tabla de mercados")
    table = df.sort_values(sort_by, ascending=False)
    if symbol_filter.strip():
        needle = symbol_filter.strip()
        table = table[
            table["coin"].str.contains(needle, case=False, na=False)
            | table["symbol"].str.contains(needle, case=False, na=False)
        ]

    visible_columns = [
        "coin",
        "timeframe",
        "close",
        "price_return_pct",
        "oi_change_pct",
        "funding_classification",
        "early_bullish_score",
        "blowoff_risk_score",
        "signal_active",
    ]

    # Build styled DataFrame with red gradient on early_bullish_score
    display_table = table[visible_columns].copy()
    display_table = display_table.reset_index(drop=True)

    def _style_overview(styler):
        """Apply white-to-red gradient based on early_bullish_score."""
        scores = styler.data["early_bullish_score"]
        max_score = max(scores.max(), 1)  # avoid div by zero

        def _row_bg(row):
            score = row["early_bullish_score"]
            intensity = min(score / max_score, 1.0)
            # White (255,255,255) -> Soft red (217,79,79) interpolation
            r = int(255 - (255 - 217) * intensity)
            g = int(255 - (255 - 120) * intensity)
            b = int(255 - (255 - 120) * intensity)
            bg = f"background-color: rgba({r},{g},{b},0.35)"
            return [bg] * len(row)

        return styler.apply(_row_bg, axis=1)

    styled = display_table.style.pipe(_style_overview)
    styled = styled.format({
        "close": "{:.6g}",
        "price_return_pct": "{:.2f}%",
        "oi_change_pct": "{:.2f}%",
        "early_bullish_score": "{:.1f}",
        "blowoff_risk_score": "{:.1f}",
    })

    st.caption("👆 Selecciona una fila para ir al detalle")
    event = st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="dashboard_table",
        column_config={
            "coin": "Symbol",
            "price_return_pct": st.column_config.NumberColumn("Price %"),
            "oi_change_pct": st.column_config.NumberColumn("OI %"),
            "signal_active": st.column_config.CheckboxColumn("Signal"),
            "funding_classification": "Funding",
            "early_bullish_score": "Bullish",
            "blowoff_risk_score": "Risk",
        },
    )

    # Navigate to detail on row selection
    try:
        rows = event.selection.rows
    except AttributeError:
        rows = []
    if rows:
        row = table.iloc[rows[0]]
        st.session_state.selected_symbol = row["symbol"]
        st.session_state.selected_timeframe = row["timeframe"]
        st.session_state.view_mode = "detail"
        st.session_state.pop("scan_result", None)
        st.rerun()


def _alerts(settings) -> None:
    st.subheader("Alertas guardadas")
    alerts = read_recent_alerts(Path(ROOT / settings.storage["alerts_csv"]))
    if alerts.empty:
        st.info("Todavia no hay alertas persistidas.")
    else:
        st.dataframe(alerts.tail(100).sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)


def _summary_metrics(df) -> None:
    active = int(df["signal_active"].sum())
    hottest = df.sort_values("early_bullish_score", ascending=False).iloc[0]
    riskiest = df.sort_values("blowoff_risk_score", ascending=False).iloc[0]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Senales activas", active)
    col2.metric("Mas hot", _coin_label(hottest["symbol"]), f'{hottest["early_bullish_score"]:.1f}')
    col3.metric("Mayor blow-off risk", _coin_label(riskiest["symbol"]), f'{riskiest["blowoff_risk_score"]:.1f}')
    col4.metric("Mercados", df["symbol"].nunique())


def _safe_filter(candles, col: str):
    """Return rows where *col* is truthy; return empty DF if column missing."""
    if col not in candles.columns:
        return candles.iloc[0:0]
    return candles[candles[col].astype(bool)]


def _safe_filter_or(candles, *cols: str):
    """Return rows where ANY of *cols* is truthy; missing columns are ignored."""
    mask = False
    for c in cols:
        if c in candles.columns:
            mask = mask | candles[c].astype(bool)
    if isinstance(mask, bool):
        return candles.iloc[0:0]
    return candles[mask]


def _detail(symbol: str, timeframe: str, df, details, settings) -> None:
    row = df[(df["symbol"] == symbol) & (df["timeframe"] == timeframe)].iloc[0]
    title_col, tf_col = st.columns([0.72, 0.28])
    with title_col:
        st.markdown(f"### {_coin_label(symbol)} `{timeframe.upper()}`")
        st.caption(f"Exchange: {row['exchange']} | TradingView: {symbol}")
    with tf_col:
        selected_tf = st.segmented_control(
            "Timeframe",
            options=["1h", "4h", "1d"],
            selection_mode="single",
            default=timeframe,
            format_func=lambda value: value.upper(),
            key=f"timeframe-{symbol}",
        )
        if selected_tf and selected_tf != timeframe:
            st.session_state.selected_symbol = symbol
            st.session_state.pending_timeframe = selected_tf
            st.rerun()
    cols = st.columns(5)
    cols[0].metric("Price", f'{row["close"]:.6g}')
    cols[1].metric("Price z", f'{row["price_return_zscore"]:.2f}')
    cols[2].metric("OI z", f'{row["oi_change_zscore"]:.2f}')
    cols[3].metric("Funding", f'{row["funding_rate"]:.4%}', row["funding_classification"])
    cols[4].metric("Scores", f'{row["early_bullish_score"]:.1f}', f'Risk {row["blowoff_risk_score"]:.1f}')
    st.write(row["notes"])

    candles = details.get((symbol, timeframe))
    if candles is None or candles.empty:
        st.warning("No hay serie historica disponible para este simbolo/timeframe.")
        return

    events = _safe_filter(candles, "signal_active_flag")
    pre_alerts = _safe_filter(candles, "pre_alert_flag")
    hot_pre_alerts = _safe_filter(candles, "hot_pre_entry_flag")
    impulses = _safe_filter_or(candles, "price_impulse_flag", "oi_impulse_flag").tail(12)
    liquidations, liq_diagnostics = _cached_liquidation_report(
        symbol, timeframe, settings.liquidations, _cache_ts()
    )
    _render_liquidation_status(symbol, timeframe, liquidations, liq_diagnostics, candles)

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.52, 0.18, 0.15, 0.15],
        subplot_titles=("Price + SMA200 + senales", "Open Interest", "Funding Rate", "Volume"),
    )
    _add_liquidation_overlay(fig, candles, liquidations)
    fig.add_trace(
        go.Candlestick(
            x=candles["timestamp"],
            open=candles["open"],
            high=candles["high"],
            low=candles["low"],
            close=candles["close"],
            name="OHLC",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(go.Scatter(x=candles["timestamp"], y=candles["sma200"], name="SMA 200", line=dict(color="#2563eb")), row=1, col=1)
    if not events.empty:
        fig.add_trace(
            go.Scatter(
                x=events["timestamp"],
                y=events["close"],
                mode="markers+text",
                name="ENTRY confirmed",
                text=["ENTRY"] * len(events),
                textposition="top center",
                marker=dict(color="#16a34a", size=16, symbol="triangle-up"),
                customdata=events[["price_return_zscore", "oi_change_zscore", "funding_classification"]],
                hovertemplate="ENTRY confirmed<br>%{x}<br>close=%{y}<br>price z=%{customdata[0]:.2f}<br>OI z=%{customdata[1]:.2f}<br>funding=%{customdata[2]}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    if not pre_alerts.empty:
        fig.add_trace(
            go.Scatter(
                x=pre_alerts["timestamp"],
                y=pre_alerts["close"],
                mode="markers",
                name="Pre-alert",
                marker=dict(color="#f59e0b", size=12, symbol="diamond"),
                customdata=pre_alerts[["price_return_zscore", "oi_change_zscore", "funding_classification"]],
                hovertemplate="Pre-alert<br>%{x}<br>close=%{y}<br>price z=%{customdata[0]:.2f}<br>OI z=%{customdata[1]:.2f}<br>funding=%{customdata[2]}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    if not hot_pre_alerts.empty:
        fig.add_trace(
            go.Scatter(
                x=hot_pre_alerts["timestamp"],
                y=hot_pre_alerts["close"],
                mode="markers+text",
                name="Hot pre-entry",
                text=["HOT"] * len(hot_pre_alerts),
                textposition="bottom center",
                marker=dict(color="#ef4444", size=14, symbol="star"),
                customdata=hot_pre_alerts[["price_return_zscore", "volume_zscore", "funding_classification"]],
                hovertemplate="HOT PRE-ENTRY<br>%{x}<br>close=%{y}<br>price z=%{customdata[0]:.2f}<br>volume z=%{customdata[1]:.2f}<br>funding=%{customdata[2]}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    if bool(row["signal_active"]):
        fig.add_vline(x=row["timestamp"], line_width=1, line_dash="dash", line_color="#16a34a")

    _add_oi_candles(fig, candles, timeframe)
    if not events.empty:
        fig.add_trace(
            go.Scatter(
                x=events["timestamp"],
                y=events["open_interest"],
                mode="markers",
                name="OI en senal",
                marker=dict(color="#16a34a", size=9),
            ),
            row=2,
            col=1,
        )
    if not pre_alerts.empty:
        fig.add_trace(
            go.Scatter(
                x=pre_alerts["timestamp"],
                y=pre_alerts["open_interest"],
                mode="markers",
                name="OI pre-alert",
                marker=dict(color="#f59e0b", size=8, symbol="diamond"),
            ),
            row=2,
            col=1,
        )
    funding_bps = candles["funding_rate"] * 10000
    fig.add_trace(go.Bar(x=candles["timestamp"], y=funding_bps, name="Funding (bps)", marker_color="#f97316"), row=3, col=1)
    fig.add_hline(y=0, row=3, col=1, line_width=1, line_color="#64748b")
    if funding_bps.notna().any():
        max_abs = max(abs(funding_bps.min()), abs(funding_bps.max()), 1.0)
        fig.update_yaxes(title_text="bps", range=[-max_abs * 1.2, max_abs * 1.2], row=3, col=1)
    fig.add_trace(go.Bar(x=candles["timestamp"], y=candles["volume"], name="Volume", marker_color="#64748b"), row=4, col=1)
    # Desactivar rangeslider en TODOS los ejes x (go.Candlestick lo reactiva en xaxis2 por defecto)
    fig.update_xaxes(rangeslider_visible=False)
    # Fijar rango x para que go.Candlestick no restrinja la vista a solo datos recientes
    if len(candles) > 0:
        fig.update_xaxes(range=[candles["timestamp"].iloc[0], candles["timestamp"].iloc[-1]])
    fig.update_layout(height=780, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True)



def _event_history(details, days: int = 21):
    rows = []
    for (symbol, timeframe), hist in details.items():
        if hist.empty or "pre_alert_flag" not in hist or "signal_active_flag" not in hist:
            continue
        events = hist[(hist["signal_active_flag"] == True) | (hist["pre_alert_flag"] == True)].copy()  # noqa: E712
        if events.empty:
            continue
        cutoff = events["timestamp"].max() - pd.Timedelta(days=days)
        events = events[events["timestamp"] >= cutoff]
        for row in events.itertuples():
            rows.append(
                {
                    "event_type": _event_type(row._asdict()),
                    "timestamp": row.timestamp,
                    "symbol": _coin_label(symbol),
                    "raw_symbol": symbol,
                    "timeframe": timeframe,
                    "close": row.close,
                    "price_return_pct": row.price_return_pct,
                    "oi_change_pct": row.oi_change_pct,
                    "volume_zscore": row.volume_zscore,
                    "volume_ratio": getattr(row, "volume_ratio", 0.0),
                    "funding_classification": row.funding_classification,
                    "early_bullish_score": row.early_bullish_score,
                    "blowoff_risk_score": row.blowoff_risk_score,
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["timestamp", "event_type"], ascending=[False, True])


def _event_type(row) -> str:
    get = row.get if isinstance(row, dict) else lambda key, default=None: row[key] if key in row else default
    if bool(get("signal_active_flag", False)):
        return "ENTRY"
    if bool(get("hot_pre_entry_flag", False)):
        return "HOT_PRE_ENTRY"
    return "PRE_ENTRY"


def _add_oi_candles(fig, candles, timeframe: str) -> None:
    fig.add_trace(
        go.Candlestick(
            x=candles["timestamp"],
            open=candles["oi_open"],
            high=candles["oi_high"],
            low=candles["oi_low"],
            close=candles["oi_close"],
            name="Open Interest",
            increasing_line_color="#0f766e",
            decreasing_line_color="#ef4444",
            increasing_fillcolor="#0f766e",
            decreasing_fillcolor="#ef4444",
        ),
        row=2,
        col=1,
    )
    oi_min = candles["oi_low"].min()
    oi_max = candles["oi_high"].max()
    if pd.notna(oi_min) and pd.notna(oi_max) and oi_max > oi_min:
        padding = max((oi_max - oi_min) * 0.15, oi_max * 0.01)
        fig.update_yaxes(range=[oi_min - padding, oi_max + padding], row=2, col=1)


def _render_liquidation_status(symbol: str, timeframe: str, liquidations, diagnostics, candles=None) -> None:
    diagnostic = next((d for d in diagnostics if getattr(d, "provider", "") == "coinalyze"), None)
    if diagnostic is None:
        st.info(
            "Liquidaciones historicas: Coinalyze no esta activo para este simbolo. "
            "Los mapas de liquidaciones pendientes no se muestran en modo gratis."
        )
        return

    coin = _coin_label(symbol)
    if diagnostic.status == "ok":
        status_text, freshness = _liquidation_freshness(diagnostic.last_timestamp, timeframe)
        cols = st.columns(4)
        cols[0].metric("Coinalyze", status_text, diagnostic.resolved_symbol or coin)
        cols[1].metric("Filas", f"{diagnostic.rows:,}".replace(",", "."))
        cols[2].metric("Nocional", _format_notional(diagnostic.notional))
        cols[3].metric("Ultimo bucket", _format_ts(diagnostic.last_timestamp), freshness)
        side_summary, winner, ratio = _liquidation_side_summary(liquidations, candles)
        if not side_summary.empty:
            st.caption(f"Balance historico: {winner} ({ratio}).")
            st.dataframe(side_summary, hide_index=True, use_container_width=True)
        st.caption(
            "Historico ejecutado agregado por Coinalyze. "
            "Liquidaciones pendientes por nivel: no disponible con una fuente gratis fiable."
        )
        return

    message = _coinalyze_status_message(diagnostic)
    if diagnostic.status in {"missing_key", "disabled", "empty"}:
        st.info(message)
    else:
        st.warning(message)


def _coinalyze_status_message(diagnostic) -> str:
    if diagnostic.status == "missing_key":
        return "Liquidaciones historicas: falta COINALYZE_API_KEY en el entorno o .env."
    if diagnostic.status == "disabled":
        return "Liquidaciones historicas: Coinalyze esta desactivado en settings.yaml."
    if diagnostic.status == "empty":
        resolved = f" ({diagnostic.resolved_symbol})" if diagnostic.resolved_symbol else ""
        return f"Liquidaciones historicas: Coinalyze respondio OK{resolved}, pero no devolvio filas."
    if diagnostic.status == "http_error":
        return f"Liquidaciones historicas: Coinalyze devolvio HTTP {diagnostic.http_status}."
    if diagnostic.status == "symbol_unresolved":
        return "Liquidaciones historicas: no se pudo mapear este simbolo a Coinalyze."
    if diagnostic.status == "request_error":
        return diagnostic.message or "Liquidaciones historicas: error consultando Coinalyze."
    return diagnostic.message or "Liquidaciones historicas: estado desconocido."


def _liquidation_freshness(timestamp, timeframe: str) -> tuple[str, str]:
    if timestamp is None or pd.isna(timestamp):
        return "OK", ""
    ts = pd.to_datetime(timestamp, utc=True)
    age = pd.Timestamp.now(tz="UTC") - ts
    stale_after = {
        "1h": pd.Timedelta(hours=2),
        "4h": pd.Timedelta(hours=6),
        "1d": pd.Timedelta(hours=36),
    }.get(timeframe, pd.Timedelta(hours=6))
    status = "OK" if age <= stale_after else "Antiguo"
    return status, f"hace {_format_age(age)}"


def _format_age(delta: pd.Timedelta) -> str:
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 48:
        days, rem_hours = divmod(hours, 24)
        return f"{days}d {rem_hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_ts(timestamp) -> str:
    if timestamp is None or pd.isna(timestamp):
        return "n/a"
    return pd.to_datetime(timestamp, utc=True).strftime("%Y-%m-%d %H:%M UTC")


def _format_notional(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.0f}"


def _liquidation_side_summary(liquidations, candles=None) -> tuple[pd.DataFrame, str, str]:
    if liquidations is None or liquidations.empty:
        return pd.DataFrame(), "Sin datos", "0.00x"
    frame = liquidations.copy()
    if "source" in frame.columns:
        frame = frame[frame["source"] == "coinalyze"]
    if candles is not None and not candles.empty and "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        candle_ts = pd.to_datetime(candles["timestamp"], utc=True, errors="coerce").dropna()
        if not candle_ts.empty:
            start = candle_ts.min()
            end = candle_ts.max()
            frame = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= end)]
    frame = frame[frame.get("side").isin(["long", "short"])]
    if frame.empty:
        return pd.DataFrame(), "Sin datos", "0.00x"

    totals = frame.groupby("side")["notional"].sum()
    long_total = float(totals.get("long", 0.0))
    short_total = float(totals.get("short", 0.0))
    total = long_total + short_total
    if total <= 0:
        return pd.DataFrame(), "Sin datos", "0.00x"

    if abs(long_total - short_total) < 1e-9:
        winner = "Empate"
        ratio = "1.00x"
    elif long_total > short_total:
        winner = "Longs mas liquidados"
        ratio = f"{long_total / max(short_total, 1.0):.2f}x"
    else:
        winner = "Shorts mas liquidados"
        ratio = f"{short_total / max(long_total, 1.0):.2f}x"

    rows = [
        {
            "Lado": "Longs liquidados",
            "Nocional": _format_notional(long_total),
            "%": f"{(long_total / total) * 100:.1f}%",
        },
        {
            "Lado": "Shorts liquidados",
            "Nocional": _format_notional(short_total),
            "%": f"{(short_total / total) * 100:.1f}%",
        },
    ]
    return pd.DataFrame(rows), winner, ratio


def _add_liquidation_overlay(fig, candles, liquidations) -> None:
    if liquidations is None or liquidations.empty:
        return
    frame = liquidations.copy()
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], utc=True, errors="coerce"
    ).astype("datetime64[ns, UTC]")
    frame = frame.dropna(subset=["timestamp"])
    if frame.empty:
        return

    # Aggregated providers (Coinalyze) return bucket rows without a price
    # level. Snap NaN/zero prices to the candle close at that timestamp so
    # the chart can still plot them.
    if "price" in frame.columns and not candles.empty:
        candle_ref = (
            candles[["timestamp", "close"]]
            .dropna(subset=["timestamp", "close"])
            .copy()
        )
        candle_ref["timestamp"] = pd.to_datetime(
            candle_ref["timestamp"], utc=True, errors="coerce"
        ).astype("datetime64[ns, UTC]")
        candle_ref = candle_ref.dropna(subset=["timestamp"]).sort_values("timestamp")
        if not candle_ref.empty:
            missing = frame["price"].isna() | (frame["price"] <= 0)
            if missing.any():
                lookup = (
                    frame.loc[missing, ["timestamp"]]
                    .reset_index()
                    .sort_values("timestamp")
                )
                snapped = pd.merge_asof(
                    lookup,
                    candle_ref,
                    on="timestamp",
                    direction="nearest",
                )
                idx = snapped["index"].to_numpy()
                values = snapped["close"].to_numpy()
                frame.loc[idx, "price"] = values

    frame = frame.dropna(subset=["price"])
    if frame.empty:
        return

    low = float(candles["low"].min())
    high = float(candles["high"].max())
    frame = frame[(frame["price"] >= low * 0.98) & (frame["price"] <= high * 1.02)]
    if frame.empty:
        return

    projected = frame[frame["kind"] == "projected"]
    if not projected.empty:
        max_notional = max(float(projected["notional"].max()), 1.0)
        fig.add_trace(
            go.Scatter(
                x=projected["timestamp"],
                y=projected["price"],
                mode="markers",
                name="Projected liquidations",
                marker=dict(
                    color=projected["notional"],
                    colorscale="YlOrRd",
                    cmin=0,
                    cmax=max_notional,
                    size=18,
                    symbol="square",
                    opacity=0.28,
                    line=dict(width=0),
                ),
                customdata=projected[["notional", "side", "source"]],
                hovertemplate="Projected liquidation zone<br>%{x}<br>price=%{y:.6g}<br>notional=%{customdata[0]:,.0f}<br>side=%{customdata[1]}<br>source=%{customdata[2]}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    executed = frame[frame["kind"] == "executed"]
    if not executed.empty:
        sizes = executed["notional"].clip(lower=0)
        if sizes.max() > sizes.min():
            sizes = 8 + ((sizes - sizes.min()) / (sizes.max() - sizes.min())) * 18
        else:
            sizes = pd.Series([10] * len(executed), index=executed.index)
        colors = executed["side"].map({"long": "#ef4444", "short": "#22c55e"}).fillna("#f59e0b")
        fig.add_trace(
            go.Scatter(
                x=executed["timestamp"],
                y=executed["price"],
                mode="markers",
                name="Executed liquidations",
                marker=dict(color=colors, size=sizes, opacity=0.45, symbol="circle"),
                customdata=executed[["notional", "quantity", "side", "source"]],
                hovertemplate="Executed liquidation<br>%{x}<br>price=%{y:.6g}<br>notional=%{customdata[0]:,.0f}<br>qty=%{customdata[1]:.6g}<br>side=%{customdata[2]}<br>source=%{customdata[3]}<extra></extra>",
            ),
            row=1,
            col=1,
        )


def _selected_symbol(event, table, available: list[str]) -> str | None:
    selected_key = st.session_state.get("selected_market")
    if selected_key in available:
        return selected_key
    try:
        rows = event.selection.rows
    except AttributeError:
        rows = []
    if rows:
        row = table.iloc[rows[0]]
        selected = f"{row['symbol']} | {row['timeframe']}"
        st.session_state.selected_market = selected
        return selected
    if not available:
        return None
    near = [item for item in available if "NEAR" in item.upper()]
    if near:
        return near[0]
    active = table[table["signal_active"] == True]  # noqa: E712
    if not active.empty:
        row = active.iloc[0]
        return f"{row['symbol']} | {row['timeframe']}"
    return available[0]


def _watchlist_buttons(symbols: list[str]) -> None:
    clean = sorted(symbols, key=_coin_label)
    for symbol in clean:
        coin = _coin_label(symbol)
        if st.button(coin, key=f"watch-{symbol}", use_container_width=True):
            st.session_state.selected_symbol = symbol
            st.session_state.selected_timeframe = "1d"
            st.session_state.view_mode = "detail"
            st.session_state.pop("scan_result", None)
            st.session_state.pop("selected_market", None)


def _coin_label(symbol: str) -> str:
    ticker = symbol.split(":", 1)[-1].replace(".P", "")
    for suffix in ("USDT", "USDC", "USD"):
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)]
    return ticker


def _default_symbol(symbols: list[str]) -> str:
    for preferred in ("BINANCE:NEARUSD.P", "BYBIT:TONUSDT.P", "BITGET:FILUSDT.P"):
        if preferred in symbols:
            return preferred
    return symbols[0] if symbols else ""


if __name__ == "__main__":
    main()
