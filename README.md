# Crypto Pump Detector

MVP local para escanear perpetuos cripto y detectar setups tipo leveraged pump / blow-off.

## Setup

En WSL/Ubuntu instala primero soporte de venv para el Python del sistema:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv python3-pip
```

Este proyecto fuerza `pyenv` a usar `system` con `.python-version`, porque un Python `pyenv` mal compilado puede romper `ctypes`/`pandas`.

```bash
cd /home/alb/projects/crypto-pump-detector
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

## Watchlist

La watchlist activa de TradingView quedó guardada en:

```text
config/watchlist.yaml
```

## Scanner CLI

```bash
PYTHONPATH=src python scripts/scan.py
```

Esto genera:

```text
data/latest_scan.csv
data/signals.sqlite
data/alerts.csv
```

## Dashboard

```bash
PYTHONPATH=src streamlit run app.py
```

La señal principal exige:

- impulso de precio,
- impulso de Open Interest,
- primera vela del impulso.

Funding se clasifica como `NEGATIVE`, `NEUTRAL`, `POSITIVE`, `HOT`, `EXTREME` o `UNKNOWN`.
# crypto-pump-detector
