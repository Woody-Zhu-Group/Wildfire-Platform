# Systemd units (Ubuntu backend box)

Install these on the backend instance (`ubuntu@`, repo at
`/home/ubuntu/Wildfire-Services`). Ollama on the current CPU model host is
managed separately.

Units: `wildfire-data-query` (:8000), `wildfire-visualization` (:8002),
`wildfire-comparison` (:8003), `wildfire-agent` (:8004), and
`wildfire-frontend` (:8765). The legacy `wildfire-gpu-control` (:8005) unit is
optional and should remain disabled unless a new EC2/Ollama resource is
explicitly configured. Services start in parallel after `network-online.target`.

Each unit runs as `ubuntu` from `/home/ubuntu/Wildfire-Services` with the
repo `.venv`, `PYTHONPATH` set to the repo root and `PYTHONIOENCODING=utf-8`.
The uvicorn units bind `--host 0.0.0.0`, and the frontend unit runs
`frontend/serve.py --bind 0.0.0.0 --port 8765`. That unit serves the older
local `frontend/` app, not the Pages website built from `website/`.

**No unit for historical risk.** There is no `wildfire-risk-forecasting`
unit, so nothing here starts `services.risk_forecasting` on `:8001`. The
agent's risk tool (default `RISK_FORECASTING_BASE_URL=http://127.0.0.1:8001`)
and the website's risk surface and residual map panels need it. Start it
separately if they are in use:

```bash
uvicorn services.risk_forecasting.app:app --port 8001 --app-dir .
```

It also needs the untracked covariate files and `grid_W.pkl`; see
[`services/risk_forecasting/README.md`](../../services/risk_forecasting/README.md).

## Stop screen sessions first

`enable --now` will fail to bind if a screen-launched uvicorn still owns the
port. Detach and quit those sessions before installing.

## EnvironmentFile

Each unit sets `EnvironmentFile=/home/ubuntu/Wildfire-Services/.env`. systemd
syntax is stricter than `python-dotenv`: no `export`, no `$VAR` expansion, no
unquoted spaces, and an unquoted `#` starts a comment (quote passwords that
contain `#`). Services also load the repo `.env` themselves (`shared/db.py`
for the database-backed services, `services/agent/config.py` and
`services/gpu_control/config.py` for those two) without overriding values
systemd has already set.

On the box, flag problem lines without printing values:

```bash
grep -nE '^(export[[:space:]]|[A-Za-z_][A-Za-z0-9_]*=.*[[:space:]]|[A-Za-z_][A-Za-z0-9_]*=.*\$)' \
  /home/ubuntu/Wildfire-Services/.env || echo "no EnvironmentFile syntax flags"
```

## Install and enable

From the repo on the instance (after `git pull` so these files exist). The
`cp` glob also copies `wildfire-gpu-control.service`; it is not enabled below.

```bash
sudo cp /home/ubuntu/Wildfire-Services/deploy/systemd/wildfire-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
  wildfire-data-query \
  wildfire-visualization \
  wildfire-comparison \
  wildfire-agent \
  wildfire-frontend
sudo systemctl --no-pager --full status \
  wildfire-data-query \
  wildfire-visualization \
  wildfire-comparison \
  wildfire-agent \
  wildfire-frontend
```

## Logs

```bash
journalctl -u wildfire-data-query -f
journalctl -u wildfire-visualization -f
journalctl -u wildfire-comparison -f
journalctl -u wildfire-agent -f
journalctl -u wildfire-frontend -f
```

## Agent /health lag after reboot

`Type=simple` plus default `TimeoutStartSec` is correct: the agent process is
considered started as soon as systemd forks uvicorn, and a missing model no
longer blocks the unit.

If the configured model backend is up, lifespan still awaits remote warmup before `:8004`
accepts connections. After any reboot, `systemctl is-active wildfire-agent`
can be `active` for several minutes while `GET :8004/health` still fails.
That is warmup, not a crashed unit. Do not manually restart the agent in
that window; watch `journalctl -u wildfire-agent -f` until the startup
line appears or the “model unavailable at startup” catch binds the port.

Deterministic Ask (counts, maps, rankings) works once the port is listening,
even if the model is still down.
