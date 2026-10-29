# Connecting AI Agents to Fluidd & Mainsail via Moonraker for Live Printer Control

Fluidd (port 80/4408) and Mainsail (port 80/4409) are web frontends powered by **Moonraker** (`port 7125`), Klipper's HTTP and WebSocket JSON-RPC API server. Every action performed in Fluidd or Mainsail (reading temperatures, editing `printer.cfg`, running G-code macros, calibrating Input Shaper or Bed Mesh, and restarting Klipper) is a standard Moonraker HTTP/JSON-RPC request.

By bundling `scripts/moonraker_agent_bridge.py`, the **`klipper-engineering`** skill allows any AI agent with network access to your printer (or a secure tunnel) to autonomously inspect, diagnose, calibrate, back up, and fix your Klipper printer.

## 1. How Agents Connect to Your Printer (3 Network Architectures)

1. **Local Network (LAN) Agents (Claude Code, Cursor, Windsurf, Cline, OpenClaw, Local Python/Node Agents)**:
   - Because your `moonraker.conf` already trusts private LAN subnets (`192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12` under `[authorization] trusted_clients`), any agent running on your PC, Mac, or Raspberry Pi can talk directly to `http://<printer_lan_ip>:7125` (or through the Fluidd/Mainsail reverse proxy at `http://<printer_lan_ip>`) with zero additional setup.
2. **Cloud / Remote Agents via Secure Tunnel (`ngrok` / Tailscale / Cloudflare Tunnel)**:
   - To let remote or cloud-hosted agents connect safely to your printer's Moonraker API (`port 7125`):
     - Expose port `7125` via Tailscale Funnel or `ngrok http 7125` protected by a **Moonraker API Key** (`X-Api-Key` header) or OAuth policy.
     - Retrieve your Moonraker API key in Fluidd (**Settings -> Authentication**) or Mainsail (**Settings -> Authorization**), or run `curl http://<printer_ip>:7125/access/api_key` from a trusted LAN device.
3. **Browser-Based Agents (`web_browser_agent`)**:
   - Browser-capable agents can also interact directly with the Fluidd or Mainsail web UI (Console tab, Configuration file editor, and Heightmap viewer).

## 2. Live Agent Workflows with `moonraker_agent_bridge.py`

### A. Live Telemetry & Console Inspection (`status`)
```bash
python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  status
```
Returns live `print_stats`, `toolhead` kinematics and position, `extruder` & `heater_bed` temperatures/powers, active `input_shaper`, loaded `bed_mesh` profile, and the last 10 G-code console responses.

### B. One-Command Live Config Pull & Full Engineering Audit (`pull-and-audit`)
```bash
python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  pull-and-audit --out-dir ./live_printer_backup
```
Downloads `printer.cfg`, `moonraker.conf`, and all `[include ...]` files from `/server/files/config/*` and immediately runs `klipper_config_auditor.py` across the entire downloaded bundle.

### C. Safe Live Diagnostic & Calibration Execution (`gcode`)
```bash
# Inspect live TMC2209 driver registers for phase lag, overtemp (otpw), or open-load faults:
python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  gcode "DUMP_TMC STEPPER=stepper_x"

# Check accelerometer noise floor before running Input Shaper:
python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  gcode "MEASURE_AXES_NOISE"
```
**Built-In Safety Interlock**: `moonraker_agent_bridge.py` checks `print_stats.state` before sending any G-code. If the printer state is `"printing"`, it automatically blocks destructive or calibration commands (`G28`, `FIRMWARE_RESTART`, `SHAPER_CALIBRATE`, `PID_CALIBRATE`, `BED_MESH_CALIBRATE`, `FORCE_MOVE`, `SAVE_CONFIG`) so an agent can never ruin an active print.

### D. Automated Backup, Config Patching & Restart (`patch-config`)
```bash
# Example: Fix bed_mesh fade_end, disable TMC interpolation, and set safe idle_timeout
python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  patch-config --file printer.cfg --section bed_mesh --set fade_start=1.0 fade_end=10.0

python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  patch-config --file printer.cfg --section "tmc2209 stepper_x" --set interpolate=False

python3 <skill_dir>/scripts/moonraker_agent_bridge.py \
  --url http://192.168.1.50:7125 \
  patch-config --file printer.cfg --section "tmc2209 extruder" --set hold_current=__DELETE__ \
  --restart
```
Before modifying any file, `patch-config` automatically uploads a timestamped backup (`printer.cfg.bak.YYYYMMDD_HHMMSS`) to your printer's config directory so you can roll back with one click inside Fluidd or Mainsail.
