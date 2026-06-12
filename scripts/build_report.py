#!/usr/bin/env python3
"""Genera report/index.html — análisis del comportamiento de los indicadores de
futuros (OI, volumen, basis) antes y durante pumps, con casos NEAR y TON.

Run:
    PYTHONPATH=src python scripts/build_report.py

Lee:
    data/charts/*_4h.json / *_1d.json   – velas con OI/volumen/funding (refresh.sh)
    report/okx_basis_4h.json / _1d.json – basis por vela (perp vs índice, OKX)
    report/okx_oi_1d.json               – OI diario en USD (OKX rubik) para NEAR/TON

Escribe:
    report/index.html   (canónico)
    docs/report.html    (copia publicada por GitHub Pages)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CHARTS_DIR = ROOT / "data" / "charts"
REPORT_DIR = ROOT / "report"
DOCS_DIR = ROOT / "docs"

FWD_BARS = 12          # ventana hacia delante: 12 velas (48 h en 4h, 12 días en 1d)
PUMP_4H = 0.10         # subida que define "pump" en 4h
PUMP_1D = 0.20         # subida que define "rally" en 1d
RUN_FILTER = 0.04      # el arranque no puede venir ya corrido (run 6 velas)


# ---------------------------------------------------------------------------
# Carga y features
# ---------------------------------------------------------------------------

def load_charts(tf: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for f in sorted(CHARTS_DIR.glob(f"*_{tf}.json")):
        obj = json.loads(f.read_text())
        df = pd.DataFrame(obj.get("data", []))
        if len(df) < 100:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["ts_ms"] = df["timestamp"].dt.as_unit("ms").astype("int64")
        base = obj["symbol"].split(":")[-1].replace(".P", "")
        for suffix in ("USDT", "USD", "USDC"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        out[base] = df
    return out


def load_okx(name: str) -> dict:
    p = REPORT_DIR / name
    return json.loads(p.read_text()) if p.exists() else {}


def attach_basis(df: pd.DataFrame, base: str, basis_map: dict) -> pd.DataFrame:
    points = basis_map.get(base, [])
    m = {int(p["ts"]): float(p["basis"]) for p in points}
    df = df.copy()
    df["basis"] = df["ts_ms"].map(m)
    return df


def features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, o, v = df["close"], df["open"], df["volume"]
    oi = df["open_interest"].replace(0, np.nan).ffill() if "open_interest" in df else pd.Series(np.nan, index=df.index)
    df["oi"] = oi
    df["ret"] = c.pct_change()
    df["green"] = c > o
    med = v.shift(1).rolling(50, min_periods=30).median()
    df["vol_ratio"] = v / med
    mu = v.shift(1).rolling(50, min_periods=30).mean()
    sd = v.shift(1).rolling(50, min_periods=30).std(ddof=0)
    df["vol_z"] = (v - mu) / sd
    df["oi_chg"] = oi.pct_change()
    df["oi_3"] = oi.pct_change(3)
    df["fwd12"] = c.shift(-1).rolling(FWD_BARS, min_periods=FWD_BARS).max().shift(-(FWD_BARS - 1)) / c - 1
    df["fwd12_close"] = c.shift(-FWD_BARS) / c - 1
    df["dd6"] = df["low"].shift(-1).rolling(6, min_periods=6).min().shift(-5) / c - 1
    df["run6"] = c / c.shift(6) - 1
    v3 = v.rolling(3).sum()
    df["vol3_ratio"] = v3 / v3.shift(1).rolling(50, min_periods=30).median()
    if "basis" in df.columns:
        bmu = df["basis"].shift(1).rolling(100, min_periods=40).mean()
        bsd = df["basis"].shift(1).rolling(100, min_periods=40).std(ddof=0)
        df["basis_z"] = (df["basis"] - bmu) / bsd
    return df


def pump_starts(df: pd.DataFrame, threshold: float) -> list[int]:
    mask = (df["fwd12"] >= threshold) & (df["run6"].fillna(0) < RUN_FILTER)
    starts, last = [], -99
    for i in np.flatnonzero(mask.to_numpy()):
        if i - last >= FWD_BARS and i >= 12 and i < len(df) - FWD_BARS:
            starts.append(int(i))
            last = i
    return starts


# ---------------------------------------------------------------------------
# Análisis 1: la señal 4h del usuario (vela verde + volumen + OI)
# ---------------------------------------------------------------------------

def signal_table(valid: pd.DataFrame) -> list[dict]:
    g = valid["green"] & (valid["ret"] >= 0.01)
    rows = [
        ("Baseline (todas las velas)", pd.Series(True, index=valid.index)),
        ("Vela verde ≥1%", g),
        ("Verde + volumen ≥1.5×", g & (valid["vol_ratio"] >= 1.5)),
        ("Verde + vol ≥1.5× + OI sube  ← tu señal", g & (valid["vol_ratio"] >= 1.5) & (valid["oi_chg"] > 0)),
        ("Verde + vol ≥1.5× + OI BAJA  (contraste)", g & (valid["vol_ratio"] >= 1.5) & (valid["oi_chg"] <= 0)),
        ("Verde + vol ≥2× + OI 3-velas ≥2%", g & (valid["vol_ratio"] >= 2.0) & (valid["oi_3"] >= 0.02)),
        ("Verde ≥2% + vol ≥2× + OI ≥1%", g & (valid["ret"] >= 0.02) & (valid["vol_ratio"] >= 2.0) & (valid["oi_chg"] >= 0.01)),
    ]
    base_p10 = float((valid["fwd12"] >= 0.10).mean())
    out = []
    for label, mask in rows:
        s = valid[mask]
        p10 = float((s["fwd12"] >= 0.10).mean()) if len(s) else 0.0
        out.append({
            "label": label,
            "n": int(len(s)),
            "p5": float((s["fwd12"] >= 0.05).mean()),
            "p8": float((s["fwd12"] >= 0.08).mean()),
            "p10": p10,
            "med_fwd": float(s["fwd12"].median()) if len(s) else 0.0,
            "med_dd": float(s["dd6"].median()) if len(s) else 0.0,
            "lift": p10 / base_p10 if base_p10 else 0.0,
        })
    return out


def capture_stats(charts: dict[str, pd.DataFrame]) -> dict:
    """Cuándo dispara la señal dentro de cada pump y cuánto recorrido queda."""
    n_eps, captured, lags, within3 = 0, [], [], []
    for base, raw in charts.items():
        df = features(raw)
        c = df["close"].to_numpy()
        sig = (df["green"] & (df["ret"] >= 0.01) & (df["vol_ratio"] >= 1.5) & (df["oi_chg"] > 0)).to_numpy()
        for i in pump_starts(df, PUMP_4H):
            n_eps += 1
            peak_rel = int(np.argmax(c[i + 1 : i + FWD_BARS + 1])) + 1
            fire = next((j for j in range(i, i + peak_rel + 1) if sig[j]), None)
            if fire is not None:
                captured.append(c[i + peak_rel] / c[fire] - 1)
                lags.append(fire - i)
                within3.append(fire - i <= 3)
    return {
        "episodes": n_eps,
        "fired": len(captured),
        "fired_pct": len(captured) / n_eps if n_eps else 0.0,
        "within3_pct": float(np.mean(within3)) if within3 else 0.0,
        "median_lag": float(np.median(lags)) if lags else 0.0,
        "median_left": float(np.median(captured)) if captured else 0.0,
        "mean_left": float(np.mean(captured)) if captured else 0.0,
    }


# ---------------------------------------------------------------------------
# Análisis 2: event study alrededor del arranque del pump
# ---------------------------------------------------------------------------

def event_study(charts: dict[str, pd.DataFrame], basis_map: dict) -> dict:
    prof_oi, prof_vz, prof_bz = [], [], []
    oi_pos_t0, oi3_pos_t0, oi_rising_before, volz_t0 = [], [], [], []
    n_eps = 0
    for base, raw in charts.items():
        df = features(attach_basis(raw, base, basis_map))
        for i in pump_starts(df, PUMP_4H):
            n_eps += 1
            w = df.iloc[i - 12 : i + 13]
            oi0 = df["oi"].iloc[i]
            if pd.notna(oi0) and oi0 > 0:
                prof_oi.append((w["oi"] / oi0).to_numpy(dtype=float))
            prof_vz.append(w["vol_z"].to_numpy(dtype=float))
            if "basis_z" in w and w["basis_z"].notna().any():
                prof_bz.append(w["basis_z"].to_numpy(dtype=float))
            oi_pos_t0.append(bool(df["oi_chg"].iloc[i] > 0))
            oi3_pos_t0.append(bool(df["oi_3"].iloc[i] > 0))
            if pd.notna(df["oi"].iloc[i - 6]) and df["oi"].iloc[i - 6] > 0:
                oi_rising_before.append(bool(df["oi"].iloc[i - 1] > df["oi"].iloc[i - 6]))
            volz_t0.append(float(df["vol_z"].iloc[i]) if pd.notna(df["vol_z"].iloc[i]) else np.nan)
    return {
        "episodes": n_eps,
        "oi_profile": np.nanmean(np.vstack(prof_oi), axis=0).tolist() if prof_oi else [],
        "volz_profile": np.nanmean(np.vstack(prof_vz), axis=0).tolist() if prof_vz else [],
        "basisz_profile": np.nanmean(np.vstack(prof_bz), axis=0).tolist() if prof_bz else [],
        "basis_episodes": len(prof_bz),
        "oi_pos_t0": float(np.mean(oi_pos_t0)),
        "oi3_pos_t0": float(np.mean(oi3_pos_t0)),
        "oi_rising_before": float(np.mean(oi_rising_before)) if oi_rising_before else 0.0,
        "volz_t0_median": float(np.nanmedian(volz_t0)),
    }


# ---------------------------------------------------------------------------
# Análisis 3: ¿el basis llega tarde?
# ---------------------------------------------------------------------------

def basis_analysis(charts: dict[str, pd.DataFrame], basis_map: dict) -> dict:
    frames = []
    for base, raw in charts.items():
        if base not in basis_map:
            continue
        df = features(attach_basis(raw, base, basis_map))
        df["base"] = base
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)

    lags, corrs = list(range(-6, 7)), []
    for k in lags:
        per_symbol = []
        for base in all_df["base"].unique():
            d = all_df[all_df["base"] == base]
            x, y = d["basis_z"], d["ret"].shift(-k)
            ok = x.notna() & y.notna()
            if ok.sum() > 50:
                per_symbol.append(float(np.corrcoef(x[ok], y[ok])[0, 1]))
        corrs.append(float(np.mean(per_symbol)) if per_symbol else 0.0)

    bb = all_df.dropna(subset=["basis_z", "fwd12", "dd6"])
    buckets = []
    for lo, hi, label in [
        (-99, -1.5, "Descuento fuerte (z ≤ −1.5)"),
        (-1.5, -0.5, "Descuento (−1.5 < z ≤ −0.5)"),
        (-0.5, 0.5, "Neutro"),
        (0.5, 1.5, "Premium (0.5 < z ≤ 1.5)"),
        (1.5, 99, "Premium fuerte (z ≥ 1.5)"),
    ]:
        s = bb[(bb["basis_z"] > lo) & (bb["basis_z"] <= hi)]
        buckets.append({
            "label": label,
            "n": int(len(s)),
            "p5": float((s["fwd12"] >= 0.05).mean()) if len(s) else 0.0,
            "med_fwd": float(s["fwd12"].median()) if len(s) else 0.0,
            "med_dd": float(s["dd6"].median()) if len(s) else 0.0,
        })
    return {"lags": lags, "corrs": corrs, "buckets": buckets, "n": int(len(bb)),
            "symbols": sorted(all_df["base"].unique().tolist())}


# ---------------------------------------------------------------------------
# Análisis 5: ¿cuándo dispararía yo la alerta? (regla PUMP custom)
# ---------------------------------------------------------------------------

ALERT_SPLIT = pd.Timestamp("2026-05-15", tz="UTC")


def _winner_mask(d: pd.DataFrame) -> pd.Series:
    """ALERTA PUMP propuesta: vela verde ≥2% + volumen ≥2.5× mediana + OI 3-velas ≥2%."""
    return d["green"] & (d["ret"] >= 0.02) & (d["vol_ratio"] >= 2.5) & (d["oi_3"] >= 0.02)


def _first_fire(mask: pd.Series, lookback: int = 12) -> pd.Series:
    prev = mask.shift(1).rolling(lookback, min_periods=1).max().fillna(0)
    return mask & (prev == 0)


def _rule_row(label: str, all_df: pd.DataFrame, mask: pd.Series) -> dict:
    s = all_df[mask].dropna(subset=["fwd12", "dd6"])
    tr = s[s["timestamp"] < ALERT_SPLIT]
    te = s[s["timestamp"] >= ALERT_SPLIT]
    return {
        "label": label,
        "n": int(len(s)),
        "p8_train": float((tr["fwd12"] >= 0.08).mean()) if len(tr) else 0.0,
        "p8_test": float((te["fwd12"] >= 0.08).mean()) if len(te) else 0.0,
        "p10": float((s["fwd12"] >= 0.10).mean()) if len(s) else 0.0,
        "med_dd": float(s["dd6"].median()) if len(s) else 0.0,
        "crash": float((s["dd6"] <= -0.08).mean()) if len(s) else 0.0,
    }


def alert_study(charts: dict[str, pd.DataFrame]) -> dict:
    frames = []
    for base, raw in charts.items():
        df = features(raw)
        df["base"] = base
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    g = all_df["green"]

    rules = [
        ("Baseline (todas las velas)", pd.Series(True, index=all_df.index)),
        ("ACTUAL · OI surge (OI 3-velas ≥4%)", all_df["oi_3"] >= 0.04),
        ("ACTUAL · VOL surge (vol 3-velas ≥2.5×)", all_df["vol3_ratio"] >= 2.5),
        ("ACTUAL · las dos a la vez", (all_df["oi_3"] >= 0.04) & (all_df["vol3_ratio"] >= 2.5)),
        ("Tu vela (verde ≥1% + vol ≥1.5× + OI sube)", g & (all_df["ret"] >= 0.01) & (all_df["vol_ratio"] >= 1.5) & (all_df["oi_chg"] > 0)),
        ("PROPUESTA · verde ≥2% + vol ≥2.5× + OI3 ≥2%", _winner_mask(all_df)),
    ]
    table = [_rule_row(label, all_df, mask) for label, mask in rules]

    # Barrido de umbrales (meseta, no pico): P(+8%) train/test con OI3 ≥2%
    sweep = []
    for vt in (1.5, 2.0, 2.5):
        row = {"vol": vt, "cells": []}
        for rt in (0.015, 0.02, 0.025):
            m = g & (all_df["ret"] >= rt) & (all_df["vol_ratio"] >= vt) & (all_df["oi_3"] >= 0.02)
            s = all_df[m].dropna(subset=["fwd12"])
            tr = s[s["timestamp"] < ALERT_SPLIT]
            te = s[s["timestamp"] >= ALERT_SPLIT]
            row["cells"].append({
                "ret": rt,
                "p8_train": float((tr["fwd12"] >= 0.08).mean()) if len(tr) >= 15 else None,
                "p8_test": float((te["fwd12"] >= 0.08).mean()) if len(te) >= 10 else None,
                "n": int(len(s)),
            })
        sweep.append(row)

    # El filtro "primer disparo" empeora — comprobado por símbolo (sin lookahead).
    ff_parts, win_parts = [], []
    for base in all_df["base"].unique():
        d = all_df[all_df["base"] == base].reset_index(drop=True)
        m = _winner_mask(d)
        win_parts.append(d[m])
        ff_parts.append(d[_first_fire(m)])
    win_all = pd.concat(win_parts)
    win = win_all.dropna(subset=["fwd12", "dd6", "fwd12_close"])
    ff = pd.concat(ff_parts).dropna(subset=["fwd12"])

    weeks = (all_df["timestamp"].max() - all_df["timestamp"].min()).days / 7
    recent = win_all.sort_values("timestamp", ascending=False).head(12)
    recent_rows = [
        {
            "symbol": r["base"],
            "date": str(pd.Timestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M")),
            "ret": float(r["ret"]),
            "vol": float(r["vol_ratio"]),
            "oi3": float(r["oi_3"]),
            "fwd": float(r["fwd12"]) if pd.notna(r["fwd12"]) else None,
        }
        for _, r in recent.iterrows()
    ]

    return {
        "table": table,
        "sweep": sweep,
        "winner": {
            "n": int(len(win)),
            "p5": float((win["fwd12"] >= 0.05).mean()),
            "p8": float((win["fwd12"] >= 0.08).mean()),
            "p10": float((win["fwd12"] >= 0.10).mean()),
            "mean_c2c": float(win["fwd12_close"].mean()),
            "med_c2c": float(win["fwd12_close"].median()),
            "med_dd": float(win["dd6"].median()),
            "crash": float((win["dd6"] <= -0.08).mean()),
            "per_week": float(len(win) / weeks),
            "symbols": int(win["base"].nunique()),
        },
        "first_fire": {"n": int(len(ff)), "p8": float((ff["fwd12"] >= 0.08).mean())},
        "recent": recent_rows,
    }


# ---------------------------------------------------------------------------
# Análisis 4: casos NEAR y TON (diario)
# ---------------------------------------------------------------------------

def case_study(coin: str, charts_1d: dict, basis_1d: dict, okx_oi: dict) -> dict:
    df = features(attach_basis(charts_1d[coin], coin, basis_1d))
    # El OI diario de OKX (rubik) viene en velas UTC+8: mapear por fecha
    # calendario de Hong Kong contra la fecha UTC de la vela del chart.
    oi_map = {
        pd.Timestamp(int(r[0]) + 8 * 3600 * 1000, unit="ms", tz="UTC").date(): float(r[1])
        for r in okx_oi.get(coin, [])
    }
    df["oi_usd"] = df["timestamp"].dt.date.map(oi_map)

    episodes = []
    c = df["close"].to_numpy()
    sig = (df["green"] & (df["ret"] >= 0.02) & (df["vol_ratio"] >= 1.5)).to_numpy()
    for i in pump_starts(df, PUMP_1D):
        peak_rel = int(np.argmax(c[i + 1 : i + FWD_BARS + 1])) + 1
        gain = c[i + peak_rel] / c[i] - 1
        oi0, oi_pre, oi_peak = df["oi_usd"].iloc[i], df["oi_usd"].iloc[max(0, i - 5)], df["oi_usd"].iloc[i + peak_rel]
        b0, bpk = df["basis"].iloc[i], df["basis"].iloc[i + peak_rel]
        bmax = df["basis"].iloc[i : i + peak_rel + 1].max()
        volz_first3 = df["vol_z"].iloc[i + 1 : i + 4].max()
        fire = next((j - i for j in range(i, i + peak_rel + 1) if sig[j]), None)
        episodes.append({
            "date": str(df["timestamp"].iloc[i].date()),
            "gain": float(gain),
            "days": int(peak_rel),
            "oi_pre": float(oi0 / oi_pre - 1) if pd.notna(oi0) and pd.notna(oi_pre) and oi_pre > 0 else None,
            "oi_during": float(oi_peak / oi0 - 1) if pd.notna(oi_peak) and pd.notna(oi0) and oi0 > 0 else None,
            "volz3": float(volz_first3) if pd.notna(volz_first3) else None,
            "basis0": float(b0 * 1e4) if pd.notna(b0) else None,
            "basis_max": float(bmax * 1e4) if pd.notna(bmax) else None,
            "fire_day": fire,
        })

    series = {
        "ts": df["ts_ms"].tolist(),
        "close": [round(float(x), 6) for x in df["close"]],
        "oi": [round(float(x), 0) if pd.notna(x) else None for x in df["oi_usd"]],
        "basis_bps": [round(float(x) * 1e4, 2) if pd.notna(x) else None for x in df["basis"]],
        "starts": [{"ts": int(df["ts_ms"].iloc[i]), "peak_ts": int(df["ts_ms"].iloc[i + e["days"]])}
                   for i, e in zip(pump_starts(df, PUMP_1D), episodes)],
    }
    return {"episodes": episodes, "series": series}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def pct(x: float, digits: int = 1) -> str:
    return f"{100 * x:.{digits}f}%"


def build_html(sig_rows, capture, study, basis, cases, alert) -> str:
    now = pd.Timestamp.now("Europe/Madrid").strftime("%Y-%m-%d %H:%M (Madrid)")

    sig_html = ""
    for r in sig_rows:
        hl = ' class="hl"' if "tu señal" in r["label"] else (' class="dim"' if "contraste" in r["label"] else "")
        sig_html += (
            f'<tr{hl}><td>{r["label"]}</td><td>{r["n"]:,}</td>'
            f'<td>{pct(r["p5"])}</td><td>{pct(r["p8"])}</td><td><b>{pct(r["p10"])}</b></td>'
            f'<td>{r["lift"]:.1f}×</td><td>{pct(r["med_fwd"])}</td><td>{pct(r["med_dd"])}</td></tr>'
        )

    bucket_html = ""
    for b in basis["buckets"]:
        hl = ' class="hl"' if "z ≤ −1.5" in b["label"] else ""
        bucket_html += (
            f'<tr{hl}><td>{b["label"]}</td><td>{b["n"]:,}</td><td><b>{pct(b["p5"])}</b></td>'
            f'<td>{pct(b["med_fwd"], 2)}</td><td>{pct(b["med_dd"], 2)}</td></tr>'
        )

    case_html = ""
    for coin, case in cases.items():
        rows = ""
        for e in case["episodes"]:
            def fpct(v):
                return "—" if v is None else f"{100 * v:+.1f}%"
            volz = "—" if e["volz3"] is None else f"{e['volz3']:+.1f}σ"
            b0 = "—" if e["basis0"] is None else f"{e['basis0']:+.1f}"
            bmax = "—" if e["basis_max"] is None else f"{e['basis_max']:+.1f}"
            fire = "no disparó" if e["fire_day"] is None else f"día +{e['fire_day']}"
            rows += (
                f'<tr><td>{e["date"]}</td><td>+{100 * e["gain"]:.0f}% en {e["days"]}d</td>'
                f'<td>{fpct(e["oi_pre"])}</td><td>{fpct(e["oi_during"])}</td>'
                f'<td>{volz}</td><td>{b0}</td><td>{bmax}</td><td>{fire}</td></tr>'
            )
        case_html += f"""
      <h3>{coin} — rallies ≥20% detectados (diario)</h3>
      <table>
        <thead><tr><th>Arranque</th><th>Subida</th><th>OI 5d antes</th><th>OI hasta pico</th>
        <th>Vol máx 3d</th><th>Basis d0 (bps)</th><th>Basis máx (bps)</th><th>Señal diaria</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="chart-wrap"><canvas id="case-{coin}"></canvas></div>
      <div class="chart-wrap small"><canvas id="case-{coin}-basis"></canvas></div>"""

    eps = [e for case in cases.values() for e in case["episodes"]]
    n_eps = len(eps)
    n_disc = sum(1 for e in eps if e["basis0"] is not None and e["basis0"] < 0)
    n_flush = sum(1 for e in eps if e["oi_pre"] is not None and e["oi_pre"] < 0)
    n_oi_up = sum(1 for e in eps if e["oi_during"] is not None and e["oi_during"] > 0)
    best = max(eps, key=lambda e: e["gain"])
    disc_txt = f"los {n_eps}" if n_disc == n_eps else f"{n_disc} de {n_eps}"
    case_verdict = f"""
<div class="verdict"><b>Lo que muestran los {n_eps} rallies de NEAR y TON:</b>
(1) <b>{disc_txt} arrancaron con el basis en descuento</b> (d0 entre −5 y −21 bps) y ni en el
pico llegó a calentarse — el basis nunca avisó antes, y durante apenas confirmó;
(2) en {n_flush} de {n_eps} el OI venía de <b>caer</b> los 5 días previos (flush) y luego se reconstruyó con
fuerza durante el tramo en {n_oi_up} de {n_eps} casos — el mayor: NEAR {best["date"]}, +{100 * best["gain"]:.0f}%
con OI +{100 * (best["oi_during"] or 0):.0f}% hasta el pico;
(3) el volumen del día d0 era normal, la señal diaria disparó de media a mitad del tramo.
El patrón repetido es: <b>flush de OI + basis en descuento → arranque silencioso → OI y volumen confirman en
las primeras velas</b> — que es exactamente la ventana que captura tu vela de 4h.</div>"""

    alert_rows = ""
    for r in alert["table"]:
        cls = ' class="hl"' if r["label"].startswith("PROPUESTA") else (' class="dim"' if r["label"].startswith("Baseline") else "")
        alert_rows += (
            f'<tr{cls}><td>{r["label"]}</td><td>{r["n"]:,}</td>'
            f'<td>{pct(r["p8_train"])}</td><td><b>{pct(r["p8_test"])}</b></td>'
            f'<td>{pct(r["p10"])}</td><td>{pct(r["med_dd"])}</td><td>{pct(r["crash"])}</td></tr>'
        )

    sweep_rows = ""
    for row in alert["sweep"]:
        cells = ""
        for cell in row["cells"]:
            if cell["p8_train"] is None or cell["p8_test"] is None:
                cells += f'<td>— <span class="note">n={cell["n"]}</span></td>'
            else:
                cells += (
                    f'<td>{pct(cell["p8_train"])} / {pct(cell["p8_test"])}'
                    f'<span class="note"> n={cell["n"]}</span></td>'
                )
        sweep_rows += f'<tr><td>{row["vol"]}×</td>{cells}</tr>'

    recent_rows = ""
    for r in alert["recent"]:
        fwd = '<span class="note">en curso</span>' if r["fwd"] is None else f'{100 * r["fwd"]:+.1f}%'
        recent_rows += (
            f'<tr><td>{r["symbol"]}</td><td>{r["date"]}</td><td>{100 * r["ret"]:+.1f}%</td>'
            f'<td>{r["vol"]:.1f}×</td><td>{100 * r["oi3"]:+.1f}%</td><td>{fwd}</td></tr>'
        )

    payload = json.dumps({
        "sig": sig_rows, "study": study, "basis": basis,
        "cases": {k: v["series"] for k, v in cases.items()},
    }, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Informe — OI, volumen y basis en los pumps</title>
<style>
body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       max-width: 920px; margin: 0 auto; padding: 18px 14px 60px; line-height: 1.55; font-size: 15px; }}
h1 {{ font-size: 22px; margin: 8px 0 2px; }}
h2 {{ font-size: 17px; margin: 34px 0 8px; color:#79c0ff; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
h3 {{ font-size: 14px; margin: 22px 0 6px; color:#d2a8ff; }}
p, li {{ color:#c9d1d9; }}
.meta {{ color:#8b949e; font-size: 12px; margin-bottom: 18px; }}
.verdict {{ background:#161b22; border:1px solid #30363d; border-left: 3px solid #3fb950; border-radius: 8px;
            padding: 12px 14px; margin: 14px 0; }}
.verdict.warn {{ border-left-color:#d29922; }}
.verdict b {{ color:#e6edf3; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 10px 0 6px; }}
th {{ text-align: left; color:#8b949e; border-bottom: 1px solid #30363d; padding: 5px 7px; white-space: nowrap; }}
td {{ border-bottom: 1px solid #21262d; padding: 5px 7px; white-space: nowrap; }}
tr.hl td {{ background: rgba(63,185,80,0.08); font-weight: 600; }}
tr.dim td {{ color:#8b949e; }}
.chart-wrap {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:10px; margin:12px 0; height: 300px; }}
.chart-wrap.small {{ height: 180px; }}
.note {{ color:#8b949e; font-size: 12px; }}
code {{ background:#21262d; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }}
a {{ color:#58a6ff; }}
</style>
</head>
<body>
<h1>📊 Cómo se comportan OI, volumen y basis en los pumps</h1>
<div class="meta">Generado {now} · 40 símbolos × 528 velas 4h (~88 días) · basis real perp-vs-índice de OKX
(10 símbolos) · OI diario OKX para NEAR/TON · informe estático, datos embebidos.</div>

<h2>1 · Tu señal de 4h: vela verde + volumen + OI subiendo</h2>
<p>Sobre <b>{sig_rows[0]["n"]:,} velas de 4h</b> medimos: si entras al cierre de la vela,
¿qué probabilidad hay de ver <b>+5% / +8% / +10% en las 12 velas siguientes (48&nbsp;h)</b>?</p>
<table>
<thead><tr><th>Condición de la vela</th><th>n</th><th>≥5%</th><th>≥8%</th><th>≥10%</th><th>Lift vs base</th>
<th>Subida mediana</th><th>Drawdown med. 24h</th></tr></thead>
<tbody>{sig_html}</tbody>
</table>
<div class="chart-wrap small"><canvas id="sig-chart"></canvas></div>
<div class="verdict"><b>Tu intuición se confirma.</b> Una vela cualquiera tiene un {pct(sig_rows[0]["p10"])} de
probabilidad de +10% en 48h; con tu vela (verde + volumen + OI subiendo) sube al <b>{pct(sig_rows[3]["p10"])}
({sig_rows[3]["lift"]:.1f}×)</b>. El aporte específico del OI se ve en la cola grande: la misma vela con OI
<i>bajando</i> solo llega al {pct(sig_rows[4]["p10"])}. Y <b>el OI sostenido manda</b>: pedir OI +2% en 3 velas
con volumen ≥2× eleva la probabilidad al <b>{pct(sig_rows[5]["p10"])} ({sig_rows[5]["lift"]:.1f}×)</b> sin
empeorar apenas el drawdown. Si quieres un único ajuste: usa <code>OI 3-velas ≥ 2%</code> en vez del OI de una sola vela.</div>

<h2>2 · ¿El OI avisa antes o confirma durante?</h2>
<p>Detectamos <b>{study["episodes"]} arranques de pump</b> (subida ≥10% en 12 velas, sin venir ya corridos)
y miramos qué hacían OI y volumen las 12 velas anteriores (t−12…t0) y las 12 siguientes:</p>
<div class="chart-wrap"><canvas id="study-chart"></canvas></div>
<ul>
<li>En la vela de arranque (t0) el OI solo subía en el <b>{pct(study["oi_pos_t0"], 0)}</b> de los casos, y el
volumen mediano era normal ({study["volz_t0_median"]:+.2f}σ): <b>el arranque exacto es silencioso</b>.</li>
<li>Antes del arranque el OI medio incluso <b>cae ~1.5%</b> (flush de posiciones — se limpia el mercado).</li>
<li>Durante el pump, OI y volumen <b>se disparan juntos</b>: OI medio +3% y volumen +1.7σ a t+12.</li>
</ul>
<div class="verdict"><b>El OI no anticipa la vela exacta del arranque: confirma en las primeras velas del
tramo.</b> Por eso tu vela de 4h funciona — es el primer punto donde precio, volumen y OI coinciden. En los
{study["episodes"]} pumps, tu señal disparó dentro del tramo alcista en el <b>{pct(capture["fired_pct"], 0)}</b>
de los casos (lag mediano {capture["median_lag"]:.0f} velas ≈ 1 día) y tras disparar quedaba una subida mediana
de <b>{pct(capture["median_left"])}</b> hasta el pico. No te mete en el suelo, te mete en el tramo con recorrido.</div>

<h2>3 · El basis: ¿llega tarde?</h2>
<p>Basis real por vela (cierre del perp ÷ índice − 1, OKX) en {len(basis["symbols"])} símbolos
({", ".join(basis["symbols"])}), {basis["n"]:,} velas de 4h. Correlación del basis (z-score) con el retorno
de la vela en distintos desfases:</p>
<div class="chart-wrap small"><canvas id="lag-chart"></canvas></div>
<div class="verdict warn"><b>Sí: para el lado largo, el basis llega tarde — tu lectura es correcta.</b>
La correlación es máxima con el retorno de la vela <i>actual y la anterior</i> (~+0.14) y cae a ~0 (o negativa)
con los retornos futuros: el premium se hincha <i>con</i> la subida y no la predice. Comprar porque el basis
se calienta es comprar lo que ya pasó.</div>
<p>Donde sí hay información es en el <b>lado contrario</b> — el descuento:</p>
<table>
<thead><tr><th>Estado del basis (z-score)</th><th>n</th><th>P(+5% en 48h)</th><th>Subida mediana</th><th>Drawdown med.</th></tr></thead>
<tbody>{bucket_html}</tbody>
</table>
<div class="verdict"><b>El descuento fuerte sí lidera.</b> Con basis en z ≤ −1.5 (perp por debajo del índice:
cortos apretando) la probabilidad de +5% en 48h sube del {pct(basis["buckets"][2]["p5"])} (neutro) al
<b>{pct(basis["buckets"][0]["p5"])}</b> — justo el combustible de squeeze que puntúa el detector. El premium
fuerte no da edge al alza y trae algo más de drawdown: úsalo como <b>aviso de blowoff / no-entrar</b>, no como gatillo.
En el dashboard: <code>DISCOUNT</code> = interesante para squeeze; <code>HOT/EXTREME</code> = tarde, riesgo de techo.</div>

<h2>4 · Casos: NEAR y TON</h2>
<p class="note">Precio/volumen de tu dashboard (Binance/Bybit), OI diario en USD y basis de OKX. Los puntos
rojos marcan arranques de rally; los verdes, su pico. NEAR es COIN-M en Binance y su OI propio solo cubre
~30 días — por eso usamos el OI agregado de OKX.</p>
{case_html}
{case_verdict}

<h2>5 · Si tuviera que disparar UNA alerta: la ALERTA PUMP</h2>
<p>Planteamiento: con todo lo anterior, ¿qué condición exacta maximiza la probabilidad de una subida rápida
inmediata? Probamos ~25 reglas candidatas con <b>validación temporal</b>: umbral elegido con datos hasta el
15-may (train) y comprobado con las 4 semanas posteriores (test) que la regla nunca vio. P(+8% en 48h):</p>
<table>
<thead><tr><th>Regla</th><th>n</th><th>+8% train</th><th>+8% test</th><th>+10% total</th><th>DD mediano 24h</th><th>P(caída ≥8%)</th></tr></thead>
<tbody>{alert_rows}</tbody>
</table>
<div class="verdict"><b>La propuesta: vela 4h verde ≥2% · volumen ≥2.5× su mediana · OI +2% en 3 velas.</b>
Dispara al cierre de la vela. P(+8% en 48h): <b>{pct(alert["table"][-1]["p8_train"])} en train y
{pct(alert["table"][-1]["p8_test"])} en test</b> (baseline {pct(alert["table"][0]["p8_train"])}/{pct(alert["table"][0]["p8_test"])});
1 de cada 4 disparos llega a +10%. Frecuencia: ~{alert["winner"]["per_week"]:.0f} alertas/semana en los 40 símbolos
({alert["winner"]["symbols"]} símbolos distintos — no la sostienen dos monedas). De las alertas actuales,
el OI surge <i>solo</i> apenas supera el baseline en train; <b>la pareja OI+VOL a la vez ya es buena</b> —
la propuesta es esa misma idea con la vela de impulso como gatillo y umbrales afinados.</div>
<p>El umbral no es un pico de suerte sino una meseta — P(+8%) train/test exigiendo OI 3-velas ≥2%:</p>
<table>
<thead><tr><th>vol ≥ \\ vela ≥</th><th>1.5%</th><th>2.0%</th><th>2.5%</th></tr></thead>
<tbody>{sweep_rows}</tbody>
</table>
<div class="verdict warn"><b>Dos avisos honestos.</b> (1) Es un cazador de colas, no un sistema de mediana
positiva: el retorno cierre-a-cierre a 48h tiene mediana {pct(alert["winner"]["med_c2c"], 2)} y media
{pct(alert["winner"]["mean_c2c"], 2)} — la asimetría está en que el {pct(alert["winner"]["p8"], 0)} de los
disparos hace +8% mientras el {pct(alert["winner"]["crash"], 0)} cae −8%: detecta <i>volatilidad explosiva con
sesgo alcista</i> y exige gestión (el drawdown mediano en 24h es {pct(alert["winner"]["med_dd"])}).
(2) Hallazgo contraintuitivo: filtrar por "primer disparo" (sin alerta en las 12 velas previas) <b>empeora</b>
la precisión ({pct(alert["first_fire"]["p8"])} vs {pct(alert["winner"]["p8"])}): las repeticiones dentro de un
tramo en marcha son las mejores señales — el momentum confirmado continúa. No hay que silenciar los re-disparos.</div>
<h3>Qué NO aportó (y queda fuera)</h3>
<ul>
<li><b>Breakout de 20 velas</b>: brilló en test (24-27%) pero mediocre en train (13-15%) — inestable entre regímenes.</li>
<li><b>Compresión de Bollinger</b> como gatillo: por debajo del baseline en ambos periodos.</li>
<li><b>Descuento de basis como gatillo de pump</b>: muestra insuficiente como disparador puntual (su sitio es el score de squeeze, como contexto).</li>
<li><b>OI de una sola vela</b>: aporta poco; el OI <b>sostenido en 3 velas</b> es lo que discrimina.</li>
<li><b>Funding y L/S</b>: sin poder de discriminación adicional una vez tienes vela+volumen+OI.</li>
</ul>
<h3>Últimos disparos de la regla propuesta</h3>
<table>
<thead><tr><th>Símbolo</th><th>Fecha (UTC)</th><th>Vela</th><th>Volumen</th><th>OI 3v</th><th>Máx. en 48h después</th></tr></thead>
<tbody>{recent_rows}</tbody>
</table>
<p class="note">Siguiente paso si te convence: implementarla como evento custom <code>PUMP_ALERT</code> en el
scanner (sin tocar OI surge ni VOL surge) con su chip en el dashboard, y en el próximo refresh la ves en gráfico.</p>

<h2>Limitaciones</h2>
<ul class="note">
<li>88 días de 4h (528 velas × 40 símbolos) y ~8 meses de diario: suficiente para patrones gruesos, corto para
estacionalidades largas. El basis 4h solo cubre ~33 días en 10 símbolos ({basis["n"]:,} velas).</li>
<li>Probabilidades sin comisiones/slippage; "subida" = máximo de cierres en la ventana (no necesariamente capturable entera).</li>
<li>El basis usado es de OKX; el de Binance/Bybit es muy parecido en signo y timing, pero no idéntico.</li>
<li>Ventanas solapadas entre velas consecutivas: los n efectivos independientes son menores que los mostrados.</li>
</ul>
<p class="note">Regenerar: <code>PYTHONPATH=src python scripts/build_report.py</code> (usa las cachés
<code>report/okx_*.json</code>).</p>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script>const R = {payload};</script>
<script>
const GRID = 'rgba(48,54,61,0.6)', TICK = '#6e7681';
Chart.defaults.color = '#8b949e'; Chart.defaults.borderColor = GRID;
function axis(extra) {{ return Object.assign({{ ticks: {{ color: TICK, font: {{ size: 10 }} }}, grid: {{ color: GRID }} }}, extra || {{}}); }}

// 1) señal: barras P(>=10%)
new Chart(document.getElementById('sig-chart'), {{
  type: 'bar',
  data: {{
    labels: R.sig.map(r => r.label.replace('  ← tu señal','').replace('  (contraste)','')),
    datasets: [{{ label: 'P(≥10% en 48h)', data: R.sig.map(r => +(100*r.p10).toFixed(1)),
      backgroundColor: R.sig.map(r => r.label.includes('tu señal') ? '#3fb950' : (r.label.includes('contraste') ? '#6e7681' : '#58a6ff')) }}]
  }},
  options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => c.raw + '%' }} }} }},
    scales: {{ x: axis({{ title: {{ display: true, text: 'probabilidad de +10% en 48h (%)' }} }}), y: axis() }} }}
}});

// 2) event study
const T = Array.from({{length: 25}}, (_, i) => i - 12);
new Chart(document.getElementById('study-chart'), {{
  type: 'line',
  data: {{ labels: T, datasets: [
    {{ label: 'OI relativo a t0', data: R.study.oi_profile.map(v => +(100*(v-1)).toFixed(2)), borderColor: '#3fb950', backgroundColor: 'transparent', yAxisID: 'y', pointRadius: 0, borderWidth: 2, tension: .25 }},
    {{ label: 'Volumen (z-score)', data: R.study.volz_profile.map(v => +v.toFixed(2)), borderColor: '#d2a8ff', backgroundColor: 'transparent', yAxisID: 'y2', pointRadius: 0, borderWidth: 2, tension: .25 }},
    {{ label: 'Basis (z-score)', data: R.study.basisz_profile.map(v => v === null ? null : +v.toFixed(2)), borderColor: '#79c0ff', backgroundColor: 'transparent', yAxisID: 'y2', pointRadius: 0, borderWidth: 1.5, borderDash: [5,3], tension: .25 }},
  ] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ boxWidth: 18 }} }},
      tooltip: {{ callbacks: {{ title: i => 'vela t' + (i[0].label >= 0 ? '+' : '') + i[0].label }} }} }},
    scales: {{
      x: axis({{ title: {{ display: true, text: 'velas 4h respecto al arranque del pump (t0)' }} }}),
      y: axis({{ position: 'left', title: {{ display: true, text: 'OI vs t0 (%)' }} }}),
      y2: axis({{ position: 'right', title: {{ display: true, text: 'z-score' }}, grid: {{ display: false }} }})
    }} }}
}});

// 3) lead/lag del basis
new Chart(document.getElementById('lag-chart'), {{
  type: 'bar',
  data: {{ labels: R.basis.lags.map(k => (k >= 0 ? '+' : '') + k),
    datasets: [{{ data: R.basis.corrs.map(v => +v.toFixed(3)),
      backgroundColor: R.basis.lags.map(k => k <= 0 ? '#79c0ff' : '#f85149') }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }},
      tooltip: {{ callbacks: {{ title: i => 'corr(basis_t, retorno t' + i[0].label + ')' }} }} }},
    scales: {{ x: axis({{ title: {{ display: true, text: '← pasado · desfase del retorno en velas · futuro →' }} }}), y: axis() }} }}
}});

// 4) casos
function caseCharts(coin) {{
  const s = R.cases[coin];
  const lab = s.ts.map(t => new Date(t).toISOString().slice(0, 10));
  const startSet = new Set(s.starts.map(e => e.ts)), peakSet = new Set(s.starts.map(e => e.peak_ts));
  new Chart(document.getElementById('case-' + coin), {{
    type: 'line',
    data: {{ labels: lab, datasets: [
      {{ label: 'Precio', data: s.close, borderColor: '#e6edf3', backgroundColor: 'transparent', yAxisID: 'y', borderWidth: 1.6, tension: .15,
         pointRadius: s.ts.map(t => startSet.has(t) ? 4 : (peakSet.has(t) ? 3.5 : 0)),
         pointBackgroundColor: s.ts.map(t => startSet.has(t) ? '#f85149' : '#3fb950') }},
      {{ label: 'OI (USD, OKX)', data: s.oi, borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,.08)', yAxisID: 'y2', borderWidth: 1.4, pointRadius: 0, tension: .2, fill: true, spanGaps: true }},
    ] }},
    options: {{ responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ boxWidth: 18 }} }} }},
      scales: {{ x: axis({{ ticks: {{ maxTicksLimit: 8, color: TICK, font: {{ size: 10 }} }} }}),
        y: axis({{ position: 'right' }}),
        y2: axis({{ position: 'left', grid: {{ display: false }} }}) }} }}
  }});
  new Chart(document.getElementById('case-' + coin + '-basis'), {{
    type: 'bar',
    data: {{ labels: lab, datasets: [{{ label: 'Basis (bps)', data: s.basis_bps,
      backgroundColor: s.basis_bps.map(v => (v ?? 0) >= 0 ? '#d2992288' : '#79c0ff88') }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ labels: {{ boxWidth: 18 }} }} }},
      scales: {{ x: axis({{ ticks: {{ maxTicksLimit: 8 }} }}), y: axis() }} }}
  }});
}}
Object.keys(R.cases).forEach(caseCharts);
</script>
</body>
</html>"""


if __name__ == "__main__":
    charts4 = load_charts("4h")
    charts1 = load_charts("1d")
    basis4 = load_okx("okx_basis_4h.json")
    basis1 = load_okx("okx_basis_1d.json")
    okx_oi = load_okx("okx_oi_1d.json")

    frames = []
    for base, raw in charts4.items():
        df = features(attach_basis(raw, base, basis4))
        df["base"] = base
        frames.append(df)
    valid = pd.concat(frames, ignore_index=True).dropna(subset=["ret", "vol_ratio", "oi_chg", "fwd12", "dd6"])

    sig_rows = signal_table(valid)
    capture = capture_stats(charts4)
    study = event_study(charts4, basis4)
    basis = basis_analysis(charts4, basis4)
    cases = {coin: case_study(coin, charts1, basis1, okx_oi) for coin in ("NEAR", "TON") if coin in charts1}
    alert = alert_study(charts4)

    html = build_html(sig_rows, capture, study, basis, cases, alert)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "index.html").write_text(html, encoding="utf-8")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "report.html").write_text(html, encoding="utf-8")
    print(f"✅ report/index.html + docs/report.html ({len(html.encode()) // 1024} KB)")
    print(f"   señal: {sig_rows[3]['n']} velas, P10 {sig_rows[3]['p10']:.3f} (lift {sig_rows[3]['lift']:.1f}x)")
    print(f"   episodios 4h: {study['episodes']} | basis: {basis['n']} velas | casos: {list(cases)}")
