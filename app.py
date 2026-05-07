from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from pump_detector.config import ROOT, load_settings, load_watchlist
from pump_detector.scanner import scan_watchlist
from pump_detector.storage import read_recent_alerts


st.set_page_config(page_title="Crypto Pump Detector", layout="wide")


def main() -> None:
    st.title("Crypto Perp Pump Detector")
    st.caption("Scanner de inicio de pump apalancado. Las senales usan solo datos disponibles al cierre de cada vela.")

    settings = load_settings()
    symbols = load_watchlist()
    if "selected_symbol" not in st.session_state:
        st.session_state.selected_symbol = _default_symbol(symbols)
    if "selected_timeframe" not in st.session_state:
        st.session_state.selected_timeframe = "1d"
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "detail"

    with st.sidebar:
        st.header("Watchlist")
        if st.button("Overview", key="watch-overview", use_container_width=True):
            st.session_state.view_mode = "overview"
            st.session_state.pop("scan_result", None)
            st.session_state.pop("selected_market", None)
        _watchlist_buttons(symbols)
        st.divider()

        st.header("Scan")
        selected_timeframes = st.multiselect("Timeframes", settings.timeframes, default=settings.timeframes)
        symbol_filter = st.text_input("Filtrar simbolo", value="")
        limit = st.slider("Candles", min_value=120, max_value=500, value=260, step=20)
        persist = st.checkbox("Guardar snapshots y alertas", value=True)
        run_selected = st.button("Actualizar seleccionado", type="primary", use_container_width=True)
        run_all = st.button("Actualizar toda la watchlist", use_container_width=True)
        st.divider()
        st.write(f"{len(symbols)} simbolos en watchlist")
        st.caption("La izquierda usa simbolos limpios; el exchange aparece solo en el detalle.")

    pending_tf = st.session_state.pop("pending_timeframe", None)
    if pending_tf:
        st.session_state.selected_timeframe = pending_tf
        st.session_state.pop("scan_result", None)

    selected_symbol = st.session_state.selected_symbol
    selected_timeframe = st.session_state.selected_timeframe
    needs_initial_scan = "scan_result" not in st.session_state
    if st.session_state.view_mode == "overview" and (needs_initial_scan or run_all):
        active_settings = settings.__class__(
            timeframes=selected_timeframes or settings.timeframes,
            alert_conditions=settings.alert_conditions,
            thresholds=settings.thresholds,
            storage=settings.storage,
        )
        with st.spinner("Escaneando overview..."):
            st.session_state.scan_result = scan_watchlist(symbols=symbols, settings=active_settings, persist=persist, limit=limit)
    elif needs_initial_scan or run_selected:
        active_settings = settings.__class__(
            timeframes=[selected_timeframe],
            alert_conditions=settings.alert_conditions,
            thresholds=settings.thresholds,
            storage=settings.storage,
        )
        with st.spinner(f"Actualizando {_coin_label(selected_symbol)}..."):
            st.session_state.scan_result = scan_watchlist(symbols=[selected_symbol], settings=active_settings, persist=False, limit=limit)

    if run_all:
        st.session_state.view_mode = "overview"
        active_settings = settings.__class__(
            timeframes=selected_timeframes or settings.timeframes,
            alert_conditions=settings.alert_conditions,
            thresholds=settings.thresholds,
            storage=settings.storage,
        )
        with st.spinner("Escaneando watchlist..."):
            st.session_state.scan_result = scan_watchlist(symbols=symbols, settings=active_settings, persist=persist, limit=limit)

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
    _detail(symbol, timeframe, df, details)
    _alerts(settings)


def _overview(df, details, symbol_filter: str, sort_by_default: str = "early_bullish_score") -> None:
    st.subheader("Overview")
    sort_options = [
        "early_bullish_score",
        "blowoff_risk_score",
        "oi_change_pct",
        "price_return_zscore",
        "funding_rate",
        "volume_zscore",
    ]
    sort_by = st.selectbox(
        "Ordenar por",
        sort_options,
        index=sort_options.index(sort_by_default),
    )
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
        "oi",
        "oi_change_pct",
        "oi_change_zscore",
        "funding_rate",
        "funding_classification",
        "volume_zscore",
        "breakout_20_flag",
        "sma200_reclaim_flag",
        "early_bullish_score",
        "blowoff_risk_score",
        "signal_active",
        "last_signal_time",
        "notes",
    ]
    event = st.dataframe(
        table[visible_columns],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="dashboard_table",
        column_config={
            "coin": "Symbol",
            "price_return_pct": st.column_config.NumberColumn("Price candle %", format="%.2f%%"),
            "oi_change_pct": st.column_config.NumberColumn("OI change %", format="%.2f%%"),
            "funding_rate": st.column_config.NumberColumn("Funding", format="%.4f"),
            "signal_active": st.column_config.CheckboxColumn("Signal"),
        },
    )

    available = [f"{row.symbol} | {row.timeframe}" for row in table.itertuples()]
    selected = _selected_symbol(event, table, available)
    if selected and st.button("Abrir detalle seleccionado", type="primary"):
        symbol, timeframe = selected.split(" | ", 1)
        st.session_state.selected_symbol = symbol
        st.session_state.selected_timeframe = timeframe
        st.session_state.view_mode = "detail"
        st.session_state.pop("scan_result", None)
        st.rerun()

    history = _event_history(details)
    st.subheader("Eventos recientes")
    if history.empty:
        st.info("No hay PRE_ENTRY, HOT_PRE_ENTRY o ENTRY recientes en el scan actual.")
    else:
        st.dataframe(history, use_container_width=True, hide_index=True)
        history_path = Path(ROOT / "data" / "event_history.csv")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history.to_csv(history_path, index=False)


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


def _detail(symbol: str, timeframe: str, df, details) -> None:
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

    events = candles[candles["signal_active_flag"] == True].copy() if "signal_active_flag" in candles else candles.iloc[0:0].copy()  # noqa: E712
    pre_alerts = candles[candles["pre_alert_flag"] == True].copy() if "pre_alert_flag" in candles else candles.iloc[0:0].copy()  # noqa: E712
    hot_pre_alerts = candles[candles.get("hot_pre_entry_flag", False) == True].copy() if "hot_pre_entry_flag" in candles else candles.iloc[0:0].copy()  # noqa: E712
    impulses = candles[
        (candles.get("price_impulse_flag", False) == True) | (candles.get("oi_impulse_flag", False) == True)  # noqa: E712
    ].tail(12)

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.52, 0.18, 0.15, 0.15],
        subplot_titles=("Price + SMA200 + senales", "Open Interest", "Funding Rate", "Volume"),
    )
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

    if {"oi_open", "oi_high", "oi_low", "oi_close"}.issubset(candles.columns):
        _add_oi_candles(fig, candles, timeframe)
    else:
        fig.add_trace(go.Scatter(x=candles["timestamp"], y=candles["open_interest"], name="OI", line=dict(color="#0f766e")), row=2, col=1)
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
    fig.update_layout(height=780, xaxis_rangeslider_visible=False, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns([0.55, 0.45])
    with left:
        st.markdown("**Eventos historicos detectados**")
        signal_table = candles[(candles["signal_active_flag"] == True) | (candles["pre_alert_flag"] == True)].copy()  # noqa: E712
        if signal_table.empty:
            st.info("No hay eventos historicos ni pre-alertas con la configuracion actual.")
        else:
            signal_table["event_type"] = signal_table.apply(_event_type, axis=1)
            st.dataframe(
                signal_table[
                    [
                        "event_type",
                        "timestamp",
                        "close",
                        "price_return_pct",
                        "price_return_zscore",
                        "oi_change_pct",
                        "oi_change_zscore",
                        "funding_rate",
                        "funding_classification",
                        "volume_zscore",
                        "volume_ratio",
                        "breakout_20_flag",
                        "sma200_reclaim_flag",
                        "early_bullish_score",
                        "blowoff_risk_score",
                    ]
                ].sort_values("timestamp", ascending=False),
                use_container_width=True,
                hide_index=True,
            )
    with right:
        st.markdown("**Ultimas velas con impulso parcial**")
        if impulses.empty:
            st.info("Sin impulsos parciales recientes.")
        else:
            st.dataframe(
                impulses[
                    [
                        "timestamp",
                        "price_impulse_flag",
                        "oi_impulse_flag",
                        "first_impulse_flag",
                        "price_return_zscore",
                        "oi_change_zscore",
                        "funding_classification",
                        "early_bullish_score",
                        "blowoff_risk_score",
                    ]
                ].sort_values("timestamp", ascending=False),
                use_container_width=True,
                hide_index=True,
            )


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
    width_ms = {"1h": 45 * 60 * 1000, "4h": 3.1 * 60 * 60 * 1000, "1d": 18 * 60 * 60 * 1000}[timeframe]
    up = candles["oi_close"] >= candles["oi_open"]
    colors = up.map({True: "#0f766e", False: "#ef4444"})
    wick_x = []
    wick_y = []
    for row in candles.itertuples():
        wick_x.extend([row.timestamp, row.timestamp, None])
        wick_y.extend([row.oi_low, row.oi_high, None])
    fig.add_trace(
        go.Scatter(
            x=wick_x,
            y=wick_y,
            mode="lines",
            name="OI wick",
            line=dict(color="#64748b", width=1),
            hoverinfo="skip",
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    oi_min = candles["oi_low"].min()
    oi_max = candles["oi_high"].max()
    padding = max((oi_max - oi_min) * 0.15, oi_max * 0.01)
    fig.update_yaxes(range=[oi_min - padding, oi_max + padding], row=2, col=1)
    fig.add_trace(
        go.Bar(
            x=candles["timestamp"],
            y=(candles["oi_close"] - candles["oi_open"]).abs(),
            base=candles[["oi_open", "oi_close"]].min(axis=1),
            width=width_ms,
            name="Open Interest",
            marker_color=colors,
            customdata=candles[["oi_open", "oi_close", "oi_change_pct", "oi_change_zscore"]],
            hovertemplate="OI<br>%{x}<br>open=%{customdata[0]:,.0f}<br>close=%{customdata[1]:,.0f}<br>change=%{customdata[2]:.2%}<br>z=%{customdata[3]:.2f}<extra></extra>",
        ),
        row=2,
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
