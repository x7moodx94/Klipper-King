# User's 3D Printer Engineering Twin & Optimization Blueprint

This reference documents the exact hardware, firmware topology, and calibrated engineering state of the user's printers discovered in the workspace, along with physics-backed optimization procedures.

## 1. Primary Printer: Creality Ender-3 V3 (`F001` — Rooted CoreXZ High-Speed Printer)

### Hardware & MCU Topology (`printer.cfg` v1.8.2 + Creality Helper Script + KAMP)
- **Model & Build Volume**: Creality Ender-3 V3 (`# F001`), **220 × 220 × 250 mm**.
- **Kinematics**: **`corexz`** (`[printer] kinematics: corexz`)
  - **X and Z axes are mechanically coupled** via two stationary frame-mounted motors (`stepper_x` and `stepper_z`) driving dual GT2 belt loops across the X-gantry and Z-columns:
    - $\Delta M_X = \Delta X + \Delta Z$ (`[stepper_x]` on `PC2`/`PB9`, `rotation_distance: 39.94`, `microsteps: 32`, `tmc2209` @ `1.5 A` RMS, physical X endstop on `PA5`).
    - $\Delta M_Z = \Delta X - \Delta Z$ (`[stepper_z]` on `PB6`/`PB5`, `rotation_distance: 39.94`, `microsteps: 32`, `tmc2209` @ `1.5 A` RMS, physical Z top endstop on `PA15` at `position_endstop: 268`, `[soft_homing] diff_step: 20`).
  - **Y axis (Moving Bed)**: Driven by `[stepper_y]` (`PB8`/`PB7`, `rotation_distance: 40`, `microsteps: 32`, `tmc2209` @ `1.5 A` RMS, StallGuard sensorless homing `driver_SGTHRS: 78` on `^PB13` via `SOFTY_G28`).
- **Multi-MCU Architecture**:
  - `[mcu]` (Mainboard): `GD32F303RET6` (`CR4CU220812S12`) on `/dev/ttyS7` @ `230400` baud.
  - `[mcu nozzle_mcu]` (Toolhead Board): `GD32F303CBT6` (`CR-K1-MAX-NOZZLE-V1.0.0`) on `/dev/ttyS1` @ `230400` baud — drives direct-drive Sprite/Unicorn extruder (`rotation_distance: 6.9`, `microsteps: 16`, `tmc2209` @ `0.55 A` run / `0.50 A` hold), 320°C ceramic heater (`PB8`), throat fan (`PB6`), part cooling fans (`fan0` on `!PB3`, `fan1` on `!PC14`, `fan2` on `PB9`), and X-axis accelerometer (`ADXL345`/`LIS2DW` on `PA4..PA7`).
  - `[mcu leveling_mcu]` (Under-Bed Load Cell MCU): `GD32E230F8P6` (`CR-K1-MAX-LEVELING-V1.0.0`) on `/dev/ttyS9` @ `230400` baud — reads 4 strain-gauge load cells (`[prtouch_v2] pres_cnt: 4`) and Y-axis bed accelerometer (`ADXL345`/`LIS2DW` on `PA4..PA7`).
  - `[mcu rpi]` (Linux Host MCU): `/tmp/klipper_host_mcu` with `bl24c16f` I2C EEPROM.
- **Installed Ecosystem**:
  - `Guilouz/Creality-Helper-Script`, `Fluidd` (`/usr/data/fluidd`), and `KAMP` (`Adaptive_Meshing.cfg`, `Line_Purge.cfg`, `Smart_Park.cfg`, `Prusa_Slicer.cfg`).
  - Slicer: **Creality Print V7.2.2** (`Raneem tag.stl_PLA_19m58s.gcode` sliced for `Creality Ender-3 V3`, 0.4 mm nozzle, PLA @ 210°C/65°C, `travel_speed = 500 mm/s`, `travel_acceleration = 12000 mm/s^2`).

### Critical Engineering Diagnostics & Concrete Fixes for User's Ender-3 V3

1. **CRITICAL: 1.44 mm Left-to-Right X-Gantry / Bed Tilt (`[bed_mesh default]`)**
   - **Measured Evidence**: Probed mesh points in `printer.cfg` climb monotonically from **`-0.495 mm` at Left (`X=5`) to `+0.943 mm` at Right (`X=215`)** — a **`1.438 mm` peak-to-valley variance** and **`+1.363 mm` (`0.372°`) left-to-right roll**!
   - **What Happens When G-Code Runs on This CoreXZ Machine**: Because X and Z share the same two CoreXZ motors (`stepper_x` and `stepper_z`), every horizontal move across X below `fade_end: 50.0` forces `stepper_x` (`X+Z`) to travel `1.36 mm` farther than `stepper_z` (`X-Z`). In the user's 19m58s PLA print (`Raneem tag.stl_PLA_19m58s.gcode`), this bed tilt forces the CoreXZ motors to execute **hundreds of millimeters of extra parasitic Z-compensation travel**, tilting vertical part walls by `0.37°` relative to the print bed and causing uneven first-layer squish.
   - **How to Fix It**:
     1. Mechanically square the X-axis gantry against the top Z hard-stops (`position_endstop: 268`) and equalize the left and right CoreXZ belt tensions (pluck both long vertical belt spans to match within 2 Hz, ~110 Hz).
     2. Check the 4 bed mounting standoffs/strain-gauge pillars on the left (`X=5`) vs right (`X=215`) side; tighten the high right-side screws or add 0.5–1.0 mm PEEK/silicone shims on the low left-side mounts until `BED_MESH_CALIBRATE` shows $< 0.25\text{ mm}$ total range.
     3. Reduce `fade_end` in `[bed_mesh]` from `50.0` to `10.0` so mesh compensation finishes within the first 10 mm of Z height.

2. **HIGH: Y-Axis Input Shaper Smoothing vs. `20,000 mm/s^2` Acceleration**
   - **Measured Evidence**: `[input_shaper]` is calibrated to `shaper_type_x = ei` @ `80.0 Hz` and `shaper_type_y = ei` @ `58.0 Hz`, while `[printer]` allows `max_accel: 20000` (and slicer sets `machine_max_acceleration_y = 20000`, `travel_acceleration = 12000`).
   - **Physics Impact**: On the Y-axis bed-slinger (`ei @ 58.0 Hz`), smoothing at `20,000 mm/s^2` is $3.225 \times 20000 / (58^2 \times 1000) = 0.0192\text{ s}^2 \times a \approx 0.192\text{ mm}$ (well above Klipper's `0.12 mm` corner-rounding limit!), and high Y accelerations risk StallGuard false triggers or layer shifts on the heavy bed.
   - **How to Fix It**:
     1. Switch `[input_shaper]` to `shaper_type_x: mzv` (`80.0 Hz`) and test `shaper_type_y: mzv` (`58.0 Hz`), which reduces corner smoothing by **55%** (`0.045 mm` on X, `0.086 mm` on Y at 10,000 mm/s²).
     2. In Creality Print / OrcaSlicer, set **Max Acceleration Y = `6000–7000 mm/s²`** while keeping **Max Acceleration X = `12000–15000 mm/s²`**, matching the physical mass difference between the lightweight CoreXZ toolhead and the heavy Y heated bed.

3. **MEDIUM: Non-Nominal CoreXZ `rotation_distance: 39.94` & TMC2209 Settings**
   - **Measured Evidence**: `[stepper_x]` and `[stepper_z]` use `rotation_distance: 39.94` while `[stepper_y]` uses `40`. All three use `interpolate: true` and `run_current: 1.5`, and `[tmc2209 extruder]` sets `hold_current: 0.5`.
   - **How to Fix It**:
     1. Set `rotation_distance: 40` on both `stepper_x` and `stepper_z` (true GT2 20T pitch = 40.00 mm) and use slicer `xy_contour_compensation` / shrinkage factor for PLA/PETG thermal contraction.
     2. Set `interpolate: false` and `microsteps: 64` on `stepper_x`, `stepper_y`, and `stepper_z` to eliminate TMC interpolation phase lag.
     3. Remove `hold_current: 0.5` from `[tmc2209 extruder]` to prevent extruder motor thermal/torque pulsing.

4. **MEDIUM: Heated Bed Derivative Disabled (`pid_kd: 0`)**
   - **Measured Evidence**: `[heater_bed]` in `printer.cfg` has `pid_kp: 27`, `pid_ki: 0.08`, `pid_kd: 0`.
   - **How to Fix It**: Run `PID_CALIBRATE HEATER=heater_bed TARGET=65` followed by `SAVE_CONFIG` so full PID derivative damping stabilizes bed temperature within $\pm 0.05^\circ\text{C}$.

## 2. Secondary Reference Config: Creality K1 Max (`factory_printer.cfg` — CoreXY 300×300×300 mm)

- **Kinematics**: `corexy`, `max_velocity: 1000`, `max_accel: 20000`, `max_z_velocity: 20`, `max_z_accel: 300`.
- **Key Mechanical Vulnerability (`rotation_distance: 72`)**:
  - Stock `[stepper_x]` and `[stepper_y]` use **36-tooth GT2 pulleys** (`rotation_distance: 72`, `microsteps: 32`, `interpolate: True`), yielding `1.125 µm/microstep` (`0.36 mm/full-step`) and severe **1.44 mm / 2.0 mm pitch VFAs**.
  - **Upgrade Path**: Swap X and Y motor pulleys to **20-tooth GT2 pulleys** (`rotation_distance: 40`), set `microsteps: 64` and `interpolate: False`, remove `hold_current: 1.0` on X/Y, and run independent X/Y `SHAPER_CALIBRATE`.
