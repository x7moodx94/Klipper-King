# Klipper Engineering Skill & Live Moonraker Agent Bridge

**Author:** Mohammad Kamal ([@x7moodx94](https://github.com/x7moodx94))  
**Inspired by:** [`bpuhnk/mcp-klipper-docs`](https://github.com/bpuhnk/mcp-klipper-docs) by Ben Phillips & the official [Klipper Firmware Documentation](https://www.klipper3d.org/Overview.html)  
**License:** MIT  

An engineering-grade **AI Agent Skill** (`SKILL.md` + deterministic Python tools) that upgrades static Klipper documentation lookup into a full **3D Printer Firmware Auditor, Kinematic & Physical G-Code Simulator, and Live Fluidd / Mainsail (Moonraker API) Autonomous Remediation Agent**.

Works with **any Klipper 3D printer** (Voron 2.4/Trident/Switchwire, RatRig, Prusa, Creality Ender-3 V3 / K1 / K1 Max, Sovol, Elegoo, Qidi, Flsun Delta, CoPrint MMU, or custom CoreXY / CoreXZ / Cartesian / Hybrid / Delta / IDEX machines) and **any AI Agent platform** (Google Gemini Skills, Claude Code, Claude Desktop, Cursor, Windsurf, Cline, and OpenClaw).

## Core Capabilities

| Module | Script / Reference | What It Does |
| :--- | :--- | :--- |
| **1. Universal Multi-File & Firmware-Gap Auditor** | `scripts/klipper_config_auditor.py` | Accepts any combination of `printer.cfg`, included `.cfg` macro files, `moonraker.conf`, `klippy.log`, or `.zip` Mainsail/Fluidd backups. Fingerprints kinematics, MCUs, TMC drivers, Input Shaper, and 3D Bed Mesh tilt, then reports every issue using a strict **4-part engineering chain**: (1) Problem / Firmware Gap, (2) What It Physically Causes, (3) How to Verify & Acknowledge It, and (4) Copy-Pasteable Solution. |
| **2. Physical & Kinematic G-Code Simulator** | `scripts/gcode_machine_simulator.py` | Simulates how Klipper's `klippy` host and MCUs physically execute a `.gcode` file: lookahead cornering (`square_corner_velocity`), trapezoidal velocity profiles, inverse kinematic stepper motor travel/speed/step-rates (`CoreXZ`, `CoreXY`, `Cartesian`, `Delta`), Bed Mesh Z-compensation workload, Poiseuille volumetric flow (`mm^3/s`), Pressure Advance velocity spikes, and Input Shaper corner rounding. |
| **3. Live Fluidd / Mainsail (Moonraker) Agent Bridge** | `scripts/moonraker_agent_bridge.py` | Connects any AI agent directly to a running Klipper printer through Moonraker (`port 7125` behind Fluidd and Mainsail) over LAN, Tailscale, or `ngrok`. Supports live status telemetry, automatic multi-file config pull & audit, safe G-code diagnostic execution (with active-print safety interlocks), automatic timestamped config backup, section patching, and `FIRMWARE_RESTART`. |
| **4. Engineering Reference Library** | `references/*.md` | Complete offline compendium of Klipper's 11 documentation categories, kinematic transformation matrices, GT2 belt & pulley VFA resonance physics, TMC2209/2240/5160 chopper rules, and Moonraker API integration. |

## Quick Start

### 1. Install as an AI Agent Skill (Claude Code / Cursor / Windsurf / Gemini)

```bash
# Install via npx skills CLI directly from GitHub:
npx skills add x7moodx94/klipper-engineering-skill

# Or clone directly into your agent's skills directory:
git clone https://github.com/x7moodx94/klipper-engineering-skill.git ~/.claude/skills/klipper-engineering
```

### 2. Audit Any Printer Configuration Bundle Offline

Drop any `.cfg` files, `moonraker.conf`, `klippy.log`, or `.zip` backup exported from Mainsail or Fluidd:

```bash
python3 scripts/klipper_config_auditor.py printer.cfg gcode_macro.cfg sensorless.cfg moonraker.conf
```

### 3. Simulate Physical Machine Execution of Any `.gcode` File

```bash
python3 scripts/gcode_machine_simulator.py part.gcode --config printer.cfg
```

### 4. Connect an Agent Live to Fluidd / Mainsail (Moonraker API)

```bash
# 1. Check live printer status, temperatures, kinematics, and console output:
python3 scripts/moonraker_agent_bridge.py --url http://192.168.1.50:7125 status

# 2. Pull all live config files from the printer and run a full engineering audit:
python3 scripts/moonraker_agent_bridge.py --url http://192.168.1.50:7125 pull-and-audit --out-dir ./printer_backup

# 3. Run live diagnostic commands (automatically blocked if printer is actively printing):
python3 scripts/moonraker_agent_bridge.py --url http://192.168.1.50:7125 gcode "DUMP_TMC STEPPER=stepper_x"
python3 scripts/moonraker_agent_bridge.py --url http://192.168.1.50:7125 gcode "BED_MESH_OUTPUT"

# 4. Auto-backup printer.cfg on the printer, patch a setting, and restart firmware safely:
python3 scripts/moonraker_agent_bridge.py --url http://192.168.1.50:7125 \
  patch-config --file printer.cfg --section bed_mesh --set fade_start=1.0 fade_end=10.0 --restart
```

## Safety Interlocks for Autonomous Agent Control

When connected to a live printer via `moonraker_agent_bridge.py`:

- **Active-Print Protection**: Before executing any G-code or config patch, the bridge queries `/printer/objects/query?print_stats`. If `state == "printing"`, it blocks `G28`, `FIRMWARE_RESTART`, `RESTART`, `SAVE_CONFIG`, `SHAPER_CALIBRATE`, `PID_CALIBRATE`, `BED_MESH_CALIBRATE`, `Z_TILT_ADJUST`, `QUAD_GANTRY_LEVEL`, and `FORCE_MOVE`.
- **Automatic Rollback Backups**: Every `patch-config` operation uploads a timestamped backup (`printer.cfg.bak.YYYYMMDD_HHMMSS`) directly to your printer's `config/` folder in Fluidd/Mainsail before modifying any line.
- **SAVE_CONFIG Preservation**: Config edits modify only the user section above `#*# <---------------------- SAVE_CONFIG ---------------------->`, preserving calibrated meshes, PID values, and Z-offsets.
