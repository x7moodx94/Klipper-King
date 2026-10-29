# Klipper Documentation Compendium (Converted from `bpuhnk/mcp-klipper-docs`)

This reference consolidates the 11 documentation categories, 56 Klipper documentation modules (`klipper://docs/{document-id}`), configuration reference schemas (`get_config_option`), and extended G-code command specifications indexed by `bpuhnk/mcp-klipper-docs` from the official [Klipper documentation](https://www.klipper3d.org/Overview.html).

## 1. Klipper Architecture & Execution Pipeline (`Kinematics.md`, `Protocol.md`, `MCU_Commands.md`)

Unlike traditional 3D printer firmwares (Marlin/RepRap) that parse G-code directly on an 8-bit or 32-bit microcontroller, Klipper splits execution across two tiers:

1. **Host Process (`klippy` on Linux/SoC)**:
   - Reads G-code from `virtual_sdcard` or Moonraker Unix domain socket (`/tmp/klippy_uds`).
   - Evaluates Jinja2 command templates (`[gcode_macro]`) and status variables (`printer.*`).
   - Buffers moves in the **Lookahead Move Queue** to compute cornering junction speeds (`square_corner_velocity`) and **Trapezoidal Velocity Profiles** ($t_{\text{accel}}, t_{\text{cruise}}, t_{\text{decel}}$).
   - Evaluates **Inverse Kinematics** (`cartesian`, `corexy`, `corexz`, `hybrid_corexy`, `hybrid_corexz`, `delta`) in continuous time.
   - Applies **Input Shaper** time-domain impulse convolution to suppress structural frame resonances (`[input_shaper]`) and **Pressure Advance** velocity feedforward (`[extruder]`).
   - Applies **Bed Mesh** bilinear/bicubic/lagrange Z-coordinate offsets (`[bed_mesh]`) and **Skew Correction** (`[skew_correction]`).
   - Solves exact step times using an iterative root-finding solver (`itersolve`) and compresses step intervals into deterministic `queue_step` commands (`interval`, `count`, `add`).
2. **Microcontroller Firmware (`mcu`, `nozzle_mcu`, `leveling_mcu`, `rpi`)**:
   - Synchronizes internal hardware clocks across multiple MCUs within microseconds via Klipper's clock-drift estimation protocol.
   - Executes hardware timer interrupts to toggle `step_pin` and `dir_pin` at exact clock ticks, polls ADC thermistors, drives PWM heaters/fans, and monitors StallGuard/endstop pins.

## 2. Core Configuration Sections (`Config_Reference.md`)

### `[printer]` - Global Kinematics & Motion Limits
- `kinematics`: `cartesian` | `corexy` | `corexz` | `hybrid_corexy` | `hybrid_corexz` | `delta` | `deltesian` | `polar` | `rotary_delta` | `winch` | `none`.
- `max_velocity` (mm/s): Peak toolhead vector speed in XY/XYZ space.
- `max_accel` (mm/s^2): Peak toolhead vector acceleration.
- `minimum_cruise_ratio` (default `0.5`, replaces legacy `max_accel_to_decel`): Enforces a minimum cruising distance fraction on standalone short zigzag moves to damp high-frequency frame vibration. If using legacy `max_accel_to_decel`, set to `0.5 * max_accel`.
- `square_corner_velocity` (default `5.0` mm/s): Maximum instantaneous velocity through a 90-degree junction. Scales centripetal cornering acceleration ($a_{\text{centripetal}} \propto v_{\text{scv}}^2$).
- `max_z_velocity` (mm/s) & `max_z_accel` (mm/s^2): Limits Z-component speed and acceleration. Note: On `corexz` kinematics, `max_z_velocity` can be high (e.g. 200-500 mm/s) because Z is belt-driven alongside X, whereas leadscrew Z axes typically require 5-30 mm/s.

### `[stepper_x]`, `[stepper_y]`, `[stepper_z]` - Stepper & Endstop Definitions
- `step_pin`, `dir_pin` (prefix `!` to invert direction), `enable_pin` (prefix `!` for active-low).
- `microsteps`: Microstep subdivision per full step (typically `16`, `32`, `64`, or `128`).
- `rotation_distance` (mm): Linear travel per full 360-degree rotation of the stepper output shaft:
  - **Belt drives**: $\text{rotation\_distance} = \text{belt\_pitch} \times \text{pulley\_teeth}$ (e.g., GT2 2mm pitch x 20T = `40.0` mm; GT2 2mm x 36T = `72.0` mm).
  - **Leadscrews**: $\text{rotation\_distance} = \text{screw\_pitch} \times \text{number\_of\_starts}$ (e.g., T8x8 4-start = `8.0` mm; T8x2 1-start = `2.0` mm).
- `gear_ratio`: Optional reduction ratio (e.g. `64:20` or `80:16`).
- `full_steps_per_rotation`: `200` for 1.8-degree motors, `400` for 0.9-degree motors.
- `endstop_pin`: Hardware pin (with `^` pullup, `!` invert) or `tmc2209_stepper_x:virtual_endstop` for StallGuard sensorless homing.
- `homing_retract_dist`: Set to `0` for single-pass sensorless homing; `2.0-5.0` mm for mechanical/optical switches.

### `[tmc2209 ...]`, `[tmc2240 ...]`, `[tmc5160 ...]` - Trinamic Driver Configuration (`TMC_Drivers.md`)
- `run_current` (Amps RMS): Motor phase RMS current during motion. Peak current is $I_{\text{peak}} = I_{\text{rms}} \times \sqrt{2}$.
- `hold_current`: **Omit in almost all configurations.** Klipper documentation explicitly warns that changing current between `run_current` and `hold_current` causes motor rotor phase shift and thermal pulsing, introducing Z-banding and extrusion artifacts.
- `interpolate` (`True` | `False`): Controls TMC internal 256-microstep interpolation. **Klipper recommends `interpolate: False`** with higher `microsteps` (`64` or `128`) because interpolation introduces a small positional lag (~0.5 full step).
- `stealthchop_threshold`: Velocity (mm/s) below which StealthChop (voltage PWM) is used instead of SpreadCycle (current chopper). Set to `0` (always SpreadCycle) for high-acceleration XY/Z/E axes to maximize torque and positional accuracy; set to `999999` only when acoustic silence is prioritized over dynamic torque.
- `driver_SGTHRS` (TMC2209, `0..255`, higher = more sensitive) or `driver_SGT` (TMC2130/2240/5160, `-64..63`, lower = more sensitive): StallGuard threshold for sensorless homing.

### `[extruder]` - Hotend, Feeder & Pressure Advance (`Pressure_Advance.md`)
- `rotation_distance`: Calibrated via extruding 100mm at slow speed ($F=60$, 1 mm/s):
  $$\text{new\_rotation\_distance} = \text{old\_rotation\_distance} \times \frac{\text{actual\_extruded\_mm}}{100}$$
- `nozzle_diameter`, `filament_diameter` (typically `0.400` and `1.750`).
- `max_extrude_cross_section`: Safety limit against massive blobs (default $4.0 \times \text{nozzle\_diameter}^2$; often raised to `5`–`80` when using KAMP adaptive purge lines).
- `pressure_advance` (seconds, typically `0.02`–`0.06` for direct drive, `0.20`–`0.80` for Bowden): Modulates extruder velocity proportionally to desired extrusion acceleration:
  $$v_e(t) = v_{\text{nominal}}(t) + K_{\text{PA}} \cdot \frac{dv_{\text{nominal}}(t)}{dt}$$
- `pressure_advance_smooth_time` (default `0.040` s, range `0.010`–`0.200` s): Weighted averaging window applied to extruder velocity to prevent extruder stepper skipping during instantaneous corner velocity changes.

### `[input_shaper]` & `[resonance_tester]` (`Resonance_Compensation.md`, `Measuring_Resonances.md`)
- `shaper_freq_x`, `shaper_freq_y` (Hz): Dominant resonant frequencies measured via ADXL345 / LIS2DW / MPU9250 accelerometers or ringing tower.
- `shaper_type_x`, `shaper_type_y`:
  | Shaper | Impulse Duration | Smoothing Coefficient $C_s$ | Vibration Tol. ($\pm\%$) | Best Engineering Use Case |
  | :--- | :--- | :--- | :--- | :--- |
  | `zv` | $0.5 / f_0$ | $0.850$ | $\pm 5\%$ | Ultra-rigid CoreXY gantries with single clean peak and minimal smoothing |
  | `mzv` | $0.75 / f_0$ | $1.450$ | $\pm 4\%$ | **Default recommended** for most well-built CoreXY / CoreXZ / Cartesian axes |
  | `zvd` | $1.0 / f_0$ | $2.500$ | $\pm 15\%$ | Broad single resonance peaks |
  | `ei` | $1.0 / f_0$ | $3.225$ | $\pm 20\%$ | Bed-slingers where resonant frequency shifts as part mass accumulates on the bed |
  | `2hump_ei` | $1.5 / f_0$ | $5.250$ | Dual-peak | Axes with coupled dual resonances; heavy corner smoothing |
  | `3hump_ei` | $2.0 / f_0$ | $8.250$ | Triple-peak | Last resort for mechanically loose frames; severe corner rounding |
- **Smoothing Metric Formula**:
  $$\text{Smoothing} \approx C_s \cdot \frac{a_{\text{max}}}{f_0^2 \cdot 1000} \quad (\text{keep } \le 0.12\text{ mm for sharp corners})$$

### `[bed_mesh]` (`Bed_Mesh.md`)
- `mesh_min`, `mesh_max`: Probe coordinates `(X, Y)` in bed space (accounting for probe `x_offset`, `y_offset`).
- `probe_count`: Grid dimensions (e.g. `5,5` or `7,7`).
- `algorithm`: `lagrange` (required for $\le 6\times 6$ grids if not overridden, fits polynomial directly through points) vs `bicubic` (recommended for $\ge 5\times 5$ grids to smooth local probe noise; `bicubic_tension` default `0.2`).
- `fade_start` (default `1.0` mm), `fade_end` (default `0.0` disabled; typically `10.0` mm): Gradually phases out Z bed mesh compensation between `fade_start` and `fade_end`. Setting `fade_end: 50.0` smears bed tilt across 50mm of part height.
- `zero_reference_position`: Coordinates `(X, Y)` where mesh Z adjustment is pinned to `0.0`.

## 3. Extended Klipper G-Code Commands (`G-Codes.md`)

- **Motion & Kinematics**:
  - `SET_VELOCITY_LIMIT [VELOCITY=<v>] [ACCEL=<a>] [MINIMUM_CRUISE_RATIO=<r>] [SQUARE_CORNER_VELOCITY=<scv>]`
  - `SET_KINEMATIC_POSITION [X=<x>] [Y=<y>] [Z=<z>] [SET_HOMED=<axes>] [CLEAR_HOMED=<axes>]`
  - `FORCE_MOVE STEPPER=<name> DISTANCE=<mm> VELOCITY=<mm/s> [ACCEL=<mm/s^2>]`
  - `STEPPER_BUZZ STEPPER=<name>` (moves motor $\pm 1$ mm to verify wiring and direction).
- **Extrusion & Tuning**:
  - `SET_PRESSURE_ADVANCE [EXTRUDER=<name>] [ADVANCE=<k>] [SMOOTH_TIME=<t>]`
  - `TUNING_TOWER COMMAND=<cmd> PARAMETER=<name> START=<val> FACTOR=<val> [BAND=<mm>]`
- **Resonance & Input Shaper**:
  - `ACCELEROMETER_QUERY [CHIP=<name>]`
  - `MEASURE_AXES_NOISE` (healthy values are $< 100$, ideally $1$–$20$).
  - `TEST_RESONANCES AXIS=X|Y`
  - `SHAPER_CALIBRATE [AXIS=X|Y] [MAX_SMOOTHING=<val>]`
  - `SET_INPUT_SHAPER [SHAPER_FREQ_X=<f>] [SHAPER_FREQ_Y=<f>] [SHAPER_TYPE_X=<t>] [SHAPER_TYPE_Y=<t>]`
- **Bed Leveling & Geometry**:
  - `BED_MESH_CALIBRATE [PROFILE=<name>] [ADAPTIVE=1] [ADAPTIVE_MARGIN=<mm>]`
  - `BED_MESH_PROFILE LOAD=<name>|SAVE=<name>|REMOVE=<name>`
  - `BED_MESH_OUTPUT [PGP=0|1]`
  - `SCREWS_TILT_CALCULATE` / `Z_TILT_ADJUST` / `QUAD_GANTRY_LEVEL`
  - `SET_SKEW [XY=<ac,bd,ad>] [XZ=<...>] [YZ=<...>] [CLEAR=0|1]`
  - `CALC_MEASURED_SKEW AC=<len> BD=<len> AD=<len>`
- **TMC Diagnostics & Thermal**:
  - `DUMP_TMC STEPPER=<name>` (inspects `DRV_STATUS`, `GSTAT`, `IOIN`, `MSCNT`, over-temperature `ot`/`otpw`, open-load `ola`/`olb`, short-to-ground `s2ga`/`s2gb`).
  - `SET_TMC_CURRENT STEPPER=<name> CURRENT=<amps>`
  - `SET_TMC_FIELD STEPPER=<name> FIELD=<field> VALUE=<val>`
  - `PID_CALIBRATE HEATER=extruder|heater_bed TARGET=<temp>`
  - `SAVE_CONFIG`
