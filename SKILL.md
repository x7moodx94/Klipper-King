---
name: klipper-engineering
description: Universal Klipper firmware auditor, G-code machine physics simulator, live Fluidd/Mainsail (Moonraker API) agent controller, and 3D printer mechanical optimization skill (upgraded from bpuhnk/mcp-klipper-docs). Use whenever any user uploads Klipper configuration files (printer.cfg, macros, sensorless.cfg, moonraker.conf, klippy.log, or .zip backups) from ANY Klipper 3D printer (Voron, RatRig, Prusa, Creality, Sovol, Elegoo, Qidi, Flsun, CoPrint MMU, custom Cartesian, CoreXY, CoreXZ, Hybrid-CoreXY/XZ, Delta, or IDEX), wants an AI agent to connect directly to Fluidd/Mainsail via Moonraker to diagnose and fix problems, asks about Klipper documentation, or wants to simulate how G-code physically executes on a printer.
---
# Klipper Engineering

Provide universal Klipper firmware documentation intelligence (converted and expanded from `bpuhnk/mcp-klipper-docs`), multi-file printer configuration and firmware-gap auditing for **any** Klipper 3D printer, deterministic G-code physical machine simulation, and live printer control/remediation through Fluidd and Mainsail (via the Moonraker HTTP/JSON-RPC API).

## When to Use

- Any user uploads one or more Klipper files (`printer.cfg`, included `.cfg` macro/hardware files, `moonraker.conf`, `klippy.log`, or a `.zip` config backup) from **any** Klipper-powered 3D printer and wants a full machine analysis, troubleshooting audit, or upgrade plan.
- The user wants this skill or another AI agent (Claude Code, Cursor, Windsurf, Cline, OpenClaw, or local automation) to connect live to their Klipper printer through **Fluidd**, **Mainsail**, or **Moonraker** (`port 7125`) to pull configs, run diagnostic G-codes, back up files, apply configuration patches, and restart firmware.
- The user asks about any Klipper configuration section (`[printer]`, `[stepper_*]`, `[tmc2209]`, `[tmc5160]`, `[tmc2240]`, `[extruder]`, `[input_shaper]`, `[bed_mesh]`, `[z_tilt]`, `[quad_gantry_level]`, `[resonance_tester]`, `[skew_correction]`, `[firmware_retraction]`), extended G-code command, or Jinja2 macro.
- The user provides or references a `.gcode` file and wants to understand what physically happens inside the machine when the G-code executes.
- The user asks to audit or improve their own printer (defaulting to the pre-profiled **Creality Ender-3 V3 (`F001`, CoreXZ)** or **Creality K1 Max (CoreXY)** when no new files are uploaded).

## Steps

### Step 1: Ingest Uploaded Files & Fingerprint the Machine Architecture

1. Collect all files uploaded or referenced by the user (`printer.cfg`, `gcode_macro.cfg`, `sensorless.cfg`, `moonraker.conf`, `klippy.log`, `.zip` backups, or `.gcode` files). If the user has not uploaded new files and asks about their printer, consult `references/user_printer_profile.md`.
2. Read `references/klipper_docs_compendium.md` and `references/kinematics_and_mechanics.md` for Klipper's execution model, kinematic matrices, belt/pulley VFA physics, TMC chopper rules, and hotend rheology.
3. Run the universal multi-file configuration, log, and firmware-gap auditor across **all** uploaded files at once:

```bash
python3 <skill_dir>/scripts/klipper_config_auditor.py /path/to/printer.cfg /path/to/macros.cfg /path/to/moonraker.conf /path/to/backup.zip
```

4. Summarize the **Machine Fingerprint** first:
   - **Kinematics & Build Volume**: `cartesian` | `corexy` | `corexz` | `hybrid_corexy` | `hybrid_corexz` | `delta` | `generic_cartesian`, plus XYZ travel bounds (`position_min` to `position_max`).
   - **MCU & Bus Topology**: Mainboard MCU, toolhead CANBUS/serial MCU (`nozzle_mcu`, `EBBCan`, `cp_Head`), bed/leveling MCU, MMU/driver MCUs (`cp_Driver`), and Linux host MCU (`[mcu rpi]`).
   - **Motion & Stepper Hardware**: Pulley tooth counts / leadscrew pitch (`rotation_distance`), microsteps, step resolution ($\mu\text{m/step}$), TMC driver models (`TMC2209`, `TMC2240`, `TMC5160`), RMS currents, and homing method (mechanical switch, StallGuard `virtual_endstop`, BLTouch, inductive/Eddy probe, or load-cell).
   - **Included Files & Ecosystem**: Resolved `[include ...]` files, missing includes, and active macros.

### Step 2: Present Every Finding Using the Mandatory 4-Part Engineering Chain

For every problem, misconfiguration, mechanical misalignment, or firmware/safety gap detected across the uploaded files, present it clearly using this exact 4-stage structure:

1. **Problem / Issue / Firmware Gap**:
   - State the exact component, parameter, or missing firmware module (`MECHANICAL_ISSUE`, `CONFIG_ISSUE`, `FIRMWARE_GAP`, `SAFETY_GAP`, `MACRO_ISSUE`, `ECOSYSTEM_GAP`, or `LOG_ERROR`) with exact numbers from their uploaded files.
   - Check all 7 universal audit domains:
     - **Mechanical & Bed/Gantry Alignment**: 3D `[bed_mesh]` peak-to-valley variance and X/Y tilt angles; missing `[z_tilt]` or `[quad_gantry_level]` on multi-Z printers; missing `[screws_tilt_adjust]`; excessive `fade_end`.
     - **Kinematics, Belts & Pulleys**: Coupled motor mismatches (`CoreXY` A/B or `CoreXZ` X/Z); non-nominal `rotation_distance` hacks; oversized 36T pulleys (`rotation_distance: 72`) vs 20T (`rotation_distance: 40`); legacy `max_accel_to_decel` vs `minimum_cruise_ratio`.
     - **TMC Stepper Drivers**: `interpolate: True` phase lag; `hold_current` thermal/phase pulsing on motion/extruder steppers; mid-band `stealthchop_threshold` mode-switching jerks; StallGuard `homing_retract_dist != 0`.
     - **Resonance Compensation & Input Shaper**: Missing `[input_shaper]` or `[resonance_tester]`; Y-axis corner smoothing $\Delta_s = C_s \cdot a_{\text{max}} / f_0^2 > 0.12\text{ mm}$ when global `max_accel` is too high for a heavy bed-slinger Y axis.
     - **Thermal Control & Safety Gaps**: Disabled PID derivative (`pid_kd: 0`); missing `[verify_heater]` or disabled thermal runaway (`max_error > 500`); disabled cold-extrusion lock (`min_extrude_temp: 0`); extreme thermistor bounds (`min_temp: -273` or `max_temp: 999` masking open/shorted thermistor wires); disabled idle timeout (`[idle_timeout] timeout > 7200`).
     - **Firmware Feature Gaps**: Missing `[exclude_object]` (and Moonraker `enable_object_processing: True`), missing `[firmware_retraction]`, missing `[skew_correction]`, missing `[gcode_arcs]`, or missing `[axis_twist_compensation]`.
     - **Runtime Log Faults (`klippy.log`)**: `Timer too close`, `Rescheduled timer in the past`, high `bytes_retransmit` / `bytes_invalid`, `Heater not heating at expected rate`, or `Move out of range`.

2. **What It Causes (Physical & Print Impact)**:
   - Explain the exact physical cause-and-effect chain on the machine and printed part (e.g., differential motor speeds on coupled CoreXZ/CoreXY belts, rotor torque angle shift, Poiseuille nozzle pressure lag, corner rounding/bulging, 1.44 mm / 2.0 mm VFA surface ripples, skewed vertical walls, or unmitigated thermal runaway risk).

3. **How to Acknowledge & Verify It on the Machine**:
   - Give the user a concrete, fast way to verify and observe the issue themselves (e.g., an exact Klipper console command like `DUMP_TMC STEPPER=stepper_x`, `BED_MESH_OUTPUT`, `MEASURE_AXES_NOISE`, a Fluidd/Mainsail UI check, or a physical caliper/belt-pluck test).

4. **Actionable Solution (Config, Slicer & Hardware Fix)**:
   - Provide the exact copy-pasteable `printer.cfg` / `moonraker.conf` block, G-code calibration sequence, slicer setting, and mechanical adjustment procedure to fix it permanently, grounded with the official `klipper3d.org` documentation link.

### Step 3: Simulate Physical Machine Execution When G-Code Is Provided

Whenever the user uploads or references a `.gcode` file (or asks how a print job executes on their machine):

1. Run the bundled G-code physical and kinematic simulator:

```bash
python3 <skill_dir>/scripts/gcode_machine_simulator.py /path/to/file.gcode --config /path/to/printer.cfg
```

2. Report the 5-stage physical execution breakdown (Macro/Homing Sequence, Lookahead & Trapezoidal Move Timing, Inverse Kinematic Stepper Workload, Extruder Rheology & Pressure Advance, and Input Shaper Smoothing & VFA Resonance Speeds).

### Step 4: Live Printer Control & Automated Remediation via Fluidd / Mainsail (Moonraker API)

When the user wants an agent to connect directly to their running printer through Fluidd, Mainsail, or Moonraker (`http://<printer_ip>:7125` or an `ngrok` / Tailscale tunnel):

1. Read `references/moonraker_live_control.md` for Moonraker authentication (`trusted_clients` vs `X-Api-Key`), safety interlocks, and multi-agent integration.
2. Use `scripts/moonraker_agent_bridge.py` to query status, pull live configs, execute safe diagnostic G-codes, or automatically back up and patch `printer.cfg`:

```bash
# 1. Query live telemetry and console logs:
python3 <skill_dir>/scripts/moonraker_agent_bridge.py --url http://<printer_ip>:7125 status

# 2. Pull all live configs from Fluidd/Mainsail and run the full engineering audit:
python3 <skill_dir>/scripts/moonraker_agent_bridge.py --url http://<printer_ip>:7125 pull-and-audit

# 3. Execute a safe diagnostic command (blocked automatically if printer is actively printing):
python3 <skill_dir>/scripts/moonraker_agent_bridge.py --url http://<printer_ip>:7125 gcode "DUMP_TMC STEPPER=stepper_x"

# 4. Back up printer.cfg on the printer, apply a config fix, and trigger FIRMWARE_RESTART:
python3 <skill_dir>/scripts/moonraker_agent_bridge.py --url http://<printer_ip>:7125 \
  patch-config --file printer.cfg --section bed_mesh --set fade_start=1.0 fade_end=10.0 --restart
```

## Gotchas

- **Active-Print Safety Interlock**: Never execute `FIRMWARE_RESTART`, `G28`, `SHAPER_CALIBRATE`, `PID_CALIBRATE`, or `BED_MESH_CALIBRATE` when `print_stats.state == "printing"`. `moonraker_agent_bridge.py` enforces this check automatically.
- **CoreXZ vs. CoreXY Motor Coupling**: Never treat `stepper_x` and `stepper_z` on a CoreXZ printer (such as the Creality Ender-3 V3 or Voron Switchwire) as independent axes; both motors move simultaneously for pure X or pure Z moves ($\Delta M_X = \Delta X + \Delta Z$, $\Delta M_Z = \Delta X - \Delta Z$).
- **Vendor Fork Compatibility**: On rooted vendor OS machines (Creality OS, Qidi, Elegoo, Sovol), preserve custom C/Python extra sections (`[prtouch_v2]`, `[accel_chip_proxy]`, `[soft_homing]`) when patching `printer.cfg`.
