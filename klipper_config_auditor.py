#!/usr/bin/env python3
"""
Universal Klipper Multi-File Configuration, Log & Firmware Gap Auditor
Works with ANY Klipper 3D printer (Voron, RatRig, Prusa, Creality, Sovol, Elegoo, Qidi, Flsun, CoPrint MMU, custom CoreXY/CoreXZ/Cartesian/Delta/IDEX).
Accepts any combination of uploaded files:
  - Main config (`printer.cfg`) and any included configs (`gcode_macro.cfg`, `sensorless.cfg`, `KAMP_Settings.cfg`, `ebb36.cfg`, etc.)
  - `moonraker.conf`
  - `klippy.log`
  - `.zip` backup archives exported from Mainsail / Fluidd

For every detected issue, misconfiguration, or firmware gap, outputs a strict 4-part engineering chain:
  1. `1_problem_or_firmware_gap`: Exact problem, misconfiguration, or missing firmware capability.
  2. `2_what_it_causes`: Physical, mechanical, thermal, electrical, or print-quality consequence.
  3. `3_how_to_acknowledge_and_verify`: Concrete check, G-code command, or physical measurement to verify/acknowledge it.
  4. `4_solution_and_fix`: Copy-pasteable Klipper config/macro fix and mechanical adjustment steps.
"""

import argparse
import json
import math
import os
import re
import sys
import zipfile
from typing import Any, Dict, List, Tuple


SHAPER_SMOOTHING_COEFF = {
    "zv": 0.0085,
    "mzv": 0.0145,
    "zvd": 0.0250,
    "ei": 0.03225,
    "2hump_ei": 0.0525,
    "3hump_ei": 0.0825,
}


def parse_single_cfg_text(
    text: str, source_name: str, active: Dict[str, Dict[str, str]], saved: Dict[str, Dict[str, str]],
    headers: List[str], includes: List[str], section_sources: Dict[str, str]
) -> None:
    lines = text.splitlines()
    for line in lines[:15]:
        s = line.strip()
        if s.startswith("#") and not s.startswith("#*#"):
            clean_h = s.strip("# \t")
            if clean_h and clean_h not in headers:
                headers.append(f"{source_name}: {clean_h}")

    cur_sec = None
    cur_key = None
    in_save_config = False

    for raw in lines:
        if raw.startswith("#*# <---------------------- SAVE_CONFIG"):
            in_save_config = True
            cur_sec = None
            cur_key = None
            continue

        if in_save_config:
            if not raw.startswith("#*#"):
                continue
            line = raw[3:].rstrip("\r\n")
            if line.startswith(" "):
                line = line[1:]
        else:
            line = re.sub(r"[#;].*$", "", raw).rstrip("\r\n")

        stripped = line.strip()
        if not stripped:
            continue

        sec_m = re.match(r"^\[([^\]]+)\]$", stripped)
        if sec_m:
            sec_header = sec_m.group(1).strip()
            if sec_header.lower().startswith("include "):
                inc_target = sec_header[8:].strip()
                if inc_target not in includes:
                    includes.append(inc_target)
                cur_sec = None
                cur_key = None
                continue
            cur_sec = sec_header
            target = saved if in_save_config else active
            target.setdefault(cur_sec, {})
            section_sources[cur_sec] = source_name
            cur_key = None
            continue

        if cur_sec is None:
            continue

        target = saved if in_save_config else active
        kv_m = re.match(r"^([a-zA-Z0-9_]+)\s*[:=]\s*(.*)$", stripped)
        if kv_m and not line.startswith(("\t", "  ")):
            cur_key = kv_m.group(1).strip()
            target[cur_sec][cur_key] = kv_m.group(2).strip()
        elif cur_key is not None:
            target[cur_sec][cur_key] += "\n" + stripped


def parse_klippy_log(text: str, source_name: str) -> Dict[str, Any]:
    shutdowns = set()
    warnings = set()
    max_retransmit = 0
    max_invalid = 0

    for line in text.splitlines():
        if "Transition to shutdown state:" in line or "Once the underlying issue is corrected" in line:
            shutdowns.add(line.strip())
        elif "Timer too close" in line or "Rescheduled timer in the past" in line:
            shutdowns.add(line.strip())
        elif "Move out of range:" in line or "Must home axis first:" in line:
            warnings.add(line.strip())
        elif "Heater" in line and "not heating at expected rate" in line:
            shutdowns.add(line.strip())
        elif "bytes_retransmit=" in line:
            m_ret = re.search(r"bytes_retransmit=(\d+)", line)
            if m_ret:
                max_retransmit = max(max_retransmit, int(m_ret.group(1)))
            m_inv = re.search(r"bytes_invalid=(\d+)", line)
            if m_inv:
                max_invalid = max(max_invalid, int(m_inv.group(1)))

    return {
        "log_file": source_name,
        "mcu_shutdown_events": list(shutdowns)[:10],
        "motion_warnings": list(warnings)[:10],
        "max_bytes_retransmit": max_retransmit,
        "max_bytes_invalid": max_invalid,
    }


def load_bundle(paths: List[str]) -> Tuple[
    Dict[str, Dict[str, str]],
    Dict[str, Dict[str, str]],
    Dict[str, Dict[str, str]],
    List[str],
    List[str],
    List[str],
    Dict[str, str],
    List[Dict[str, Any]],
]:
    active: Dict[str, Dict[str, str]] = {}
    saved: Dict[str, Dict[str, str]] = {}
    moonraker: Dict[str, Dict[str, str]] = {}
    headers: List[str] = []
    includes: List[str] = []
    loaded_files: List[str] = []
    section_sources: Dict[str, str] = {}
    log_reports: List[Dict[str, Any]] = []

    def process_file(fpath: str, display_name: str):
        lname = display_name.lower()
        if lname.endswith(".zip"):
            with zipfile.ZipFile(fpath, "r") as zf:
                for member in zf.namelist():
                    if member.endswith("/") or "__MACOSX" in member:
                        continue
                    m_lower = member.lower()
                    if m_lower.endswith((".cfg", ".conf", ".log")):
                        raw_bytes = zf.read(member)
                        txt = raw_bytes.decode("utf-8", errors="ignore")
                        sub_name = f"{os.path.basename(display_name)}:{member}"
                        loaded_files.append(sub_name)
                        if m_lower.endswith("moonraker.conf"):
                            parse_single_cfg_text(txt, sub_name, moonraker, {}, [], [], {})
                        elif m_lower.endswith(".log"):
                            log_reports.append(parse_klippy_log(txt, sub_name))
                        else:
                            parse_single_cfg_text(txt, sub_name, active, saved, headers, includes, section_sources)
            return

        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        loaded_files.append(display_name)
        if lname.endswith("moonraker.conf"):
            parse_single_cfg_text(txt, display_name, moonraker, {}, [], [], {})
        elif lname.endswith(".log"):
            log_reports.append(parse_klippy_log(txt, display_name))
        else:
            parse_single_cfg_text(txt, display_name, active, saved, headers, includes, section_sources)

    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for fn in sorted(files):
                    if fn.lower().endswith((".cfg", ".conf", ".log", ".zip")):
                        full = os.path.join(root, fn)
                        process_file(full, os.path.relpath(full, p))
        elif os.path.exists(p):
            process_file(p, os.path.basename(p))

    return active, saved, moonraker, headers, includes, loaded_files, section_sources, log_reports


def analyze_bed_mesh(points_str: str, min_x: float, max_x: float, min_y: float, max_y: float) -> Dict[str, Any]:
    rows = []
    for line in points_str.splitlines():
        vals = [float(v.strip()) for v in line.split(",") if v.strip()]
        if vals:
            rows.append(vals)
    if not rows:
        return {}

    ny = len(rows)
    nx = len(rows[0])
    flat = [v for r in rows for v in r]
    z_min, z_max = min(flat), max(flat)
    z_span = z_max - z_min
    z_mean = sum(flat) / len(flat)
    z_std = math.sqrt(sum((v - z_mean) ** 2 for v in flat) / len(flat))

    x_span_mm = max(1.0, max_x - min_x)
    y_span_mm = max(1.0, max_y - min_y)

    mean_left_x = sum(r[0] for r in rows) / ny
    mean_right_x = sum(r[-1] for r in rows) / ny
    x_tilt_mm = mean_right_x - mean_left_x
    x_tilt_deg = math.degrees(math.atan2(x_tilt_mm, x_span_mm))

    mean_front_y = sum(rows[0]) / nx
    mean_back_y = sum(rows[-1]) / nx
    y_tilt_mm = mean_back_y - mean_front_y
    y_tilt_deg = math.degrees(math.atan2(y_tilt_mm, y_span_mm))

    return {
        "grid_size": f"{nx}x{ny}",
        "z_min_mm": round(z_min, 4),
        "z_max_mm": round(z_max, 4),
        "peak_to_valley_mm": round(z_span, 4),
        "std_dev_mm": round(z_std, 4),
        "left_to_right_x_tilt_mm": round(x_tilt_mm, 4),
        "left_to_right_x_tilt_deg": round(x_tilt_deg, 4),
        "front_to_back_y_tilt_mm": round(y_tilt_mm, 4),
        "front_to_back_y_tilt_deg": round(y_tilt_deg, 4),
    }


def make_finding(
    severity: str,
    finding_type: str,
    category: str,
    component: str,
    problem: str,
    causes: str,
    acknowledge_verify: str,
    solution: str,
    doc_ref: str,
) -> Dict[str, str]:
    return {
        "severity": severity,
        "finding_type": finding_type,
        "category": category,
        "component": component,
        "1_problem_or_firmware_gap": problem,
        "2_what_it_causes": causes,
        "3_how_to_acknowledge_and_verify": acknowledge_verify,
        "4_solution_and_fix": solution,
        "doc_ref": doc_ref,
    }


def audit_bundle(paths: List[str]) -> Dict[str, Any]:
    active, saved, moonraker, headers, includes, loaded_files, section_sources, log_reports = load_bundle(paths)
    findings: List[Dict[str, str]] = []

    printer_sec = active.get("printer", {})
    kinematics = printer_sec.get("kinematics", "cartesian").lower()
    max_vel = float(printer_sec.get("max_velocity", 300))
    max_accel = float(printer_sec.get("max_accel", 3000))
    max_z_vel = float(printer_sec.get("max_z_velocity", 10))
    scv = float(printer_sec.get("square_corner_velocity", 5.0))

    mcus = {sec: active[sec] for sec in active if sec == "mcu" or sec.startswith("mcu ")}
    macros_defined = [sec.split()[1] for sec in active if sec.lower().startswith("gcode_macro ") and len(sec.split()) > 1]

    loaded_basenames = {re.sub(r"\s*\(\d+\)", "", os.path.basename(f.split(":")[-1])).lower() for f in loaded_files}
    unresolved_includes = [inc for inc in includes if os.path.basename(inc).lower() not in loaded_basenames]

    # --- 1. MOTION PLANNER & KINEMATICS CHECKS ---
    if "max_accel_to_decel" in printer_sec and "minimum_cruise_ratio" not in printer_sec:
        matd = float(printer_sec["max_accel_to_decel"])
        findings.append(make_finding(
            severity="LOW",
            finding_type="FIRMWARE_GAP",
            category="Motion Planner / Lookahead Damping",
            component="[printer] max_accel_to_decel",
            problem=f"Uses deprecated `max_accel_to_decel: {matd}` instead of modern Klipper `minimum_cruise_ratio`.",
            causes=(
                "`max_accel_to_decel` forces an abrupt ceiling on short moves rather than scaling smoothly with move distance, "
                "which can cause higher frame vibration during dense gyroid/rectilinear infill."
            ),
            acknowledge_verify="Check your Klipper version in Mainsail/Fluidd (`M115`). Mainline Klipper >= v0.12.0 uses `minimum_cruise_ratio: 0.5`; older vendor forks (e.g. Creality OS) still expect `max_accel_to_decel`.",
            solution=(
                "If running mainline Klipper, replace `max_accel_to_decel` with `minimum_cruise_ratio: 0.5` under `[printer]`. "
                "If running a vendor Klipper fork that requires `max_accel_to_decel`, keep `max_accel_to_decel` set to 50% of `max_accel`."
            ),
            doc_ref="https://www.klipper3d.org/Config_Reference.html#printer",
        ))

    # --- 2. STEPPER, BELT PULLEY & TMC DRIVER CHECKS ---
    stepper_summary = {}
    stepper_names = [s for s in active if s.startswith("stepper_") or s == "extruder" or s.startswith("extruder_stepper ")]
    z_steppers = [s for s in stepper_names if s.startswith("stepper_z")]

    for axis in stepper_names:
        sec = active[axis]
        rot_dist = float(sec.get("rotation_distance", 40.0))
        microsteps = int(sec.get("microsteps", 16))
        full_steps = int(sec.get("full_steps_per_rotation", 200))
        gear_ratio = sec.get("gear_ratio", None)
        endstop_pin = sec.get("endstop_pin", "")
        homing_retract = float(sec.get("homing_retract_dist", 5.0)) if "homing_retract_dist" in sec else 5.0

        eff_ratio = 1.0
        if gear_ratio and ":" in gear_ratio:
            n, d = gear_ratio.split(":", 1)
            eff_ratio = float(n) / float(d)
        steps_per_mm = (full_steps * microsteps * eff_ratio) / rot_dist
        um_per_step = 1000.0 / steps_per_mm
        est_gt2_teeth = round(rot_dist / 2.0, 2) if not axis.startswith("extruder") and not gear_ratio and rot_dist >= 24 else None

        tmc_type = None
        tmc_sec = {}
        for driver in ("tmc2209", "tmc5160", "tmc2240", "tmc2130", "tmc2660", "tmc2208"):
            if f"{driver} {axis}" in active:
                tmc_type = driver
                tmc_sec = active[f"{driver} {axis}"]
                break

        interp = tmc_sec.get("interpolate", "True").lower() == "true" if tmc_sec else None
        run_cur = float(tmc_sec.get("run_current", 0.0)) if "run_current" in tmc_sec else None
        hold_cur = float(tmc_sec.get("hold_current", 0.0)) if "hold_current" in tmc_sec else None
        sc_thresh = float(tmc_sec.get("stealthchop_threshold", 0.0)) if "stealthchop_threshold" in tmc_sec else 0.0

        stepper_summary[axis] = {
            "rotation_distance_mm": rot_dist,
            "microsteps": microsteps,
            "gear_ratio": gear_ratio,
            "estimated_gt2_pulley_teeth": est_gt2_teeth,
            "resolution_um_per_microstep": round(um_per_step, 3),
            "driver_type": tmc_type,
            "tmc_run_current_A": run_cur,
            "tmc_hold_current_A": hold_cur,
            "tmc_interpolate": interp,
            "stealthchop_threshold": sc_thresh,
            "sensorless_homing": "virtual_endstop" in endstop_pin,
        }

        if rot_dist >= 64.0 and axis in ("stepper_x", "stepper_y"):
            findings.append(make_finding(
                severity="HIGH",
                finding_type="MECHANICAL_ISSUE",
                category="Belt & Pulley Kinematics / VFAs",
                component=f"[{axis}] rotation_distance: {rot_dist}",
                problem=f"`{axis}` uses an oversized {int(rot_dist/2)}-tooth GT2 pulley (`rotation_distance: {rot_dist} mm`), yielding coarse {round(um_per_step, 2)} um/microstep resolution.",
                causes=(
                    f"Each 1.8 deg full motor step moves the belt {round(rot_dist/200, 3)} mm ({round(rot_dist/50, 2)} mm per 4-step electrical cycle), "
                    "cutting linear belt force by 44% compared to a 20T pulley and causing pronounced Vertical Fine Artifacts (VFAs) on outer walls."
                ),
                acknowledge_verify="Print a thin-walled cylinder or VFA speed tower from 60 to 200 mm/s and inspect for uniform vertical ripples spaced every 1.44 mm or 2.0 mm.",
                solution="Mechanically swap the X/Y motor pulleys to 20-tooth GT2 pulleys, update `rotation_distance: 40` in `printer.cfg`, set `microsteps: 64`, and set `interpolate: False`.",
                doc_ref="https://www.klipper3d.org/Rotation_Distance.html",
            ))

        if kinematics == "corexz" and axis in ("stepper_x", "stepper_z") and rot_dist != 40.0:
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="CONFIG_ISSUE",
                category="CoreXZ Coupled Belt Calibration",
                component=f"[{axis}] rotation_distance: {rot_dist}",
                problem=f"CoreXZ coupled motor `{axis}` has a modified `rotation_distance: {rot_dist}` instead of the physical GT2 20T pitch (`40.0`).",
                causes=(
                    "In CoreXZ kinematics, `stepper_x` and `stepper_z` drive coupled diagonal X+Z and X-Z belts. "
                    "Changing `rotation_distance` from `40.0` scales both X and Z travel simultaneously and distorts kinematic geometry across tall prints."
                ),
                acknowledge_verify="Measure a 100 mm calibration cross in X, Y, and Z after allowing the part to cool to room temperature.",
                solution="Set `rotation_distance: 40` on both `[stepper_x]` and `[stepper_z]`, then compensate for material thermal shrinkage using slicer XY shrinkage compensation or Klipper `[skew_correction]`.",
                doc_ref="https://www.klipper3d.org/Rotation_Distance.html",
            ))

        if kinematics == "corexy" and axis in ("stepper_x", "stepper_y") and rot_dist not in (32.0, 36.0, 40.0, 48.0, 60.0, 72.0):
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="CONFIG_ISSUE",
                category="CoreXY Coupled Belt Calibration",
                component=f"[{axis}] rotation_distance: {rot_dist}",
                problem=f"CoreXY motor `{axis}` has a non-integer tooth pitch `rotation_distance: {rot_dist}`.",
                causes="In CoreXY, `stepper_x` (A) and `stepper_y` (B) move diagonally (X+Y and X-Y). Non-nominal `rotation_distance` introduces scaling errors and XY skew.",
                acknowledge_verify="Check the physical tooth count on your motor pulley (typically 20T x 2mm = 40.0 mm or 16T x 2mm = 32.0 mm).",
                solution="Set `rotation_distance` to the exact pulley pitch (`belt_pitch * teeth`) and calibrate filament shrinkage in the slicer.",
                doc_ref="https://www.klipper3d.org/Rotation_Distance.html",
            ))

        if tmc_sec and interp:
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="CONFIG_ISSUE",
                category="TMC Stepper Driver Precision",
                component=f"[{tmc_type} {axis}] interpolate: True",
                problem=f"TMC internal 256-microstep interpolation is enabled on `{axis}` (`microsteps: {microsteps}`).",
                causes="Klipper's official TMC documentation states that internal driver interpolation introduces a systemic phase lag of ~0.5 full steps during acceleration/direction changes, producing subtle surface ripples.",
                acknowledge_verify=f"Run `DUMP_TMC STEPPER={axis}` in the Klipper console and inspect `CHOPCONF: intpol=1`.",
                solution=f"In `[{tmc_type} {axis}]`, set `interpolate: False` and increase `microsteps: 64` in `[{axis}]`.",
                doc_ref="https://www.klipper3d.org/TMC_Drivers.html",
            ))

        if tmc_sec and hold_cur is not None and hold_cur > 0 and hold_cur != run_cur:
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="CONFIG_ISSUE",
                category="TMC Driver Current Stability",
                component=f"[{tmc_type} {axis}] hold_current: {hold_cur}",
                problem=f"`hold_current: {hold_cur}` is configured alongside `run_current: {run_cur}` on `{axis}`.",
                causes=(
                    "Switching between `run_current` and `hold_current` when the motor pauses or restarts alters the rotor's magnetic equilibrium angle "
                    "and causes thermal expansion cycles in the motor shaft, creating Z-banding or extrusion blobs."
                ),
                acknowledge_verify=f"Observe `{axis}` during brief non-moving intervals or layer changes where current drops after `TPOWERDOWN`.",
                solution=f"Delete or comment out `hold_current` inside `[{tmc_type} {axis}]` so phase current stays constant during printing.",
                doc_ref="https://www.klipper3d.org/TMC_Drivers.html#prefer-to-not-specify-a-hold_current",
            ))

        if tmc_sec and 0 < sc_thresh < max_vel and axis in ("stepper_x", "stepper_y"):
            findings.append(make_finding(
                severity="HIGH",
                finding_type="CONFIG_ISSUE",
                category="TMC Chopper Mode Switching",
                component=f"[{tmc_type} {axis}] stealthchop_threshold: {sc_thresh}",
                problem=f"`stealthchop_threshold: {sc_thresh}` is set between `0` and `max_velocity ({max_vel} mm/s)` on `{axis}`.",
                causes=(
                    f"Every time `{axis}` accelerates or decelerates across {sc_thresh} mm/s, the TMC driver dynamically switches between "
                    "StealthChop (voltage PWM) and SpreadCycle (current chopper). That transition causes a sudden back-EMF jerk, audible clunk, and skipped steps."
                ),
                acknowledge_verify=f"Command moves just below and above {sc_thresh} mm/s (`G1 X100 F{int(sc_thresh*60)}`) and listen for a sharp mode-switch click.",
                solution=f"Set `stealthchop_threshold: 0` (always SpreadCycle for maximum high-speed torque) or `stealthchop_threshold: 999999` (always StealthChop for low-speed silent setups) in `[{tmc_type} {axis}]`.",
                doc_ref="https://www.klipper3d.org/TMC_Drivers.html#setting-spreadcycle-vs-stealthchop-mode",
            ))

        if "virtual_endstop" in endstop_pin and homing_retract != 0.0:
            findings.append(make_finding(
                severity="HIGH",
                finding_type="CONFIG_ISSUE",
                category="Sensorless Homing / StallGuard",
                component=f"[{axis}] homing_retract_dist: {homing_retract}",
                problem=f"`{axis}` uses StallGuard sensorless homing (`virtual_endstop`) but `homing_retract_dist` is `{homing_retract}` instead of `0`.",
                causes="A second slow homing pass immediately after StallGuard impact fails to build enough back-EMF to trigger StallGuard reliably, causing axis grinding against the hard stop.",
                acknowledge_verify=f"Run `G28 {axis[-1].upper()}` and watch if the carriage rams the frame twice.",
                solution=f"Set `homing_retract_dist: 0` under `[{axis}]`.",
                doc_ref="https://www.klipper3d.org/TMC_Drivers.html#sensorless-homing",
            ))

    # --- 3. INPUT SHAPER & RESONANCE COMPENSATION CHECKS ---
    shaper_sec = saved.get("input_shaper", active.get("input_shaper", {}))
    shaper_summary = {}
    if not shaper_sec:
        findings.append(make_finding(
            severity="HIGH",
            finding_type="FIRMWARE_GAP",
            category="Resonance Compensation / Input Shaper",
            component="[input_shaper] (Missing)",
            problem="No `[input_shaper]` section is configured or saved in `printer.cfg`.",
            causes="Without Input Shaper impulse convolution, rapid direction changes at corners excite structural frame resonances (40-90 Hz), producing ghosting/ringing ripples after every corner.",
            acknowledge_verify="Run `SET_INPUT_SHAPER` in the Klipper console or print a ringing test tower at 3000+ mm/s^2.",
            solution="Add `[input_shaper]` to `printer.cfg` and calibrate resonant frequencies using an ADXL345/LIS2DW accelerometer (`SHAPER_CALIBRATE`) or a ringing tower (`TUNING_TOWER`).",
            doc_ref="https://www.klipper3d.org/Resonance_Compensation.html",
        ))
    else:
        st_x = shaper_sec.get("shaper_type_x", shaper_sec.get("shaper_type", "mzv")).lower()
        sf_x = float(shaper_sec.get("shaper_freq_x", 50.0))
        st_y = shaper_sec.get("shaper_type_y", shaper_sec.get("shaper_type", "mzv")).lower()
        sf_y = float(shaper_sec.get("shaper_freq_y", 50.0))

        max_rec_accel_x = round(0.10 * (sf_x ** 2) / SHAPER_SMOOTHING_COEFF.get(st_x, 0.03225), -2)
        max_rec_accel_y = round(0.10 * (sf_y ** 2) / SHAPER_SMOOTHING_COEFF.get(st_y, 0.03225), -2)
        smooth_x_at_max = round(SHAPER_SMOOTHING_COEFF.get(st_x, 0.03225) * max_accel / (sf_x ** 2), 3)
        smooth_y_at_max = round(SHAPER_SMOOTHING_COEFF.get(st_y, 0.03225) * max_accel / (sf_y ** 2), 3)

        shaper_summary = {
            "shaper_x": f"{st_x} @ {sf_x} Hz",
            "shaper_y": f"{st_y} @ {sf_y} Hz",
            "smoothing_at_configured_max_accel_mm": {"x": smooth_x_at_max, "y": smooth_y_at_max},
            "max_recommended_accel_for_0.10mm_smoothing_mm_s2": {"x": int(max_rec_accel_x), "y": int(max_rec_accel_y)},
        }

        if max_accel > max_rec_accel_y * 1.15:
            findings.append(make_finding(
                severity="HIGH",
                finding_type="CONFIG_ISSUE",
                category="Resonance Compensation / Input Shaper Smoothing",
                component=f"[input_shaper] Y: {st_y} @ {sf_y} Hz vs max_accel: {int(max_accel)}",
                problem=(
                    f"Global `max_accel: {int(max_accel)} mm/s^2` exceeds the Y-axis `{st_y} @ {sf_y} Hz` "
                    f"smoothing ceiling (~{int(max_rec_accel_y)} mm/s^2), generating `{smooth_y_at_max} mm` of corner rounding."
                ),
                causes=(
                    f"An `{st_y}` shaper at {sf_y} Hz has an impulse duration of {round(1000.0/sf_y, 1)} ms. "
                    f"At {int(max_accel)} mm/s^2, the Y-axis experiences excessive spatial smoothing ({smooth_y_at_max} mm > 0.12 mm limit), "
                    "rounding sharp 90-degree corners, causing gaps between infill and walls, and risking Y-axis stepper skips."
                ),
                acknowledge_verify="Run `SHAPER_CALIBRATE AXIS=Y` or inspect 90-degree corners on a test cube printed at >10,000 mm/s^2 for bulging and rounding.",
                solution=(
                    f"1. In your slicer (OrcaSlicer / PrusaSlicer / Creality Print), cap **Max Acceleration Y** to `{int(max_rec_accel_y)} mm/s^2` while allowing X up to `{int(max_rec_accel_x)} mm/s^2`.\n"
                    f"2. Test switching `shaper_type_y` (and `shaper_type_x`) from `ei` to `mzv`, which reduces corner smoothing by 55%."
                ),
                doc_ref="https://www.klipper3d.org/Resonance_Compensation.html",
            ))

    # --- 4. BED LEVELING, MESH & GANTRY TRAMMING CHECKS ---
    mesh_sec = saved.get("bed_mesh default", saved.get("bed_mesh adaptive", {}))
    mesh_cfg = active.get("bed_mesh", {})
    mesh_summary = {}
    if mesh_sec and "points" in mesh_sec:
        min_x = float(mesh_sec.get("min_x", 5.0))
        max_x = float(mesh_sec.get("max_x", 215.0))
        min_y = float(mesh_sec.get("min_y", 5.0))
        max_y = float(mesh_sec.get("max_y", 215.0))
        mesh_summary = analyze_bed_mesh(mesh_sec["points"], min_x, max_x, min_y, max_y)

        if mesh_summary.get("peak_to_valley_mm", 0) > 0.40:
            findings.append(make_finding(
                severity="CRITICAL" if mesh_summary["peak_to_valley_mm"] > 0.80 else "HIGH",
                finding_type="MECHANICAL_ISSUE",
                category="Mechanical Alignment / Bed & Gantry Leveling",
                component="[bed_mesh default]",
                problem=(
                    f"Saved bed mesh has a peak-to-valley variance of `{mesh_summary['peak_to_valley_mm']} mm` "
                    f"(Left-to-Right X tilt: `{mesh_summary['left_to_right_x_tilt_mm']} mm` / `{mesh_summary['left_to_right_x_tilt_deg']} deg`; "
                    f"Front-to-Back Y tilt: `{mesh_summary['front_to_back_y_tilt_mm']} mm`)."
                ),
                causes=(
                    "Software bed mesh is meant for minor surface non-flatness (< 0.25 mm), not a >0.4 mm mechanical tilt. "
                    "As the nozzle moves across XY, Klipper must continuously step the Z axis (or both CoreXZ motors) up and down across every layer, "
                    "producing out-of-square vertical walls, uneven first-layer squish, and Z-banding."
                ),
                acknowledge_verify="Open the Heightmap tab in Fluidd/Mainsail or run `BED_MESH_OUTPUT` in the console to inspect the Z delta between `X_min` and `X_max`.",
                solution=(
                    "1. Mechanically square the X-gantry to the frame/bed (on CoreXZ, align left/right Z top stops and equalize Belt A/B tension; on multi-Z, run `Z_TILT_ADJUST` or `QUAD_GANTRY_LEVEL`).\n"
                    "2. Shim the low side of the bed standoffs (or adjust bed leveling knobs with `SCREWS_TILT_CALCULATE`) until total mesh range is `< 0.25 mm`, then re-run `BED_MESH_CALIBRATE`."
                ),
                doc_ref="https://www.klipper3d.org/Bed_Mesh.html",
            ))

    if mesh_cfg:
        fade_end = float(mesh_cfg.get("fade_end", 0.0))
        if fade_end > 20.0:
            findings.append(make_finding(
                severity="LOW",
                finding_type="CONFIG_ISSUE",
                category="Bed Mesh Z-Fade",
                component=f"[bed_mesh] fade_end: {fade_end}",
                problem=f"`fade_end: {fade_end}` stretches bed-mesh Z compensation across {fade_end} mm of part height.",
                causes="Fading a tilted mesh over 50 mm compresses or stretches layer heights across the bottom 50 mm of the part instead of completing compensation within the first 10 mm.",
                acknowledge_verify="Inspect `[bed_mesh] fade_end` in `printer.cfg`.",
                solution="Set `fade_start: 1.0` and `fade_end: 10.0` under `[bed_mesh]`.",
                doc_ref="https://www.klipper3d.org/Bed_Mesh.html#mesh-fade",
            ))

    if len(z_steppers) >= 2 and "z_tilt" not in active and "quad_gantry_level" not in active:
        findings.append(make_finding(
            severity="HIGH",
            finding_type="FIRMWARE_GAP",
            category="Multi-Z Gantry Tramming",
            component=f"Multi-Z Steppers ({', '.join(z_steppers)}) without [z_tilt] / [quad_gantry_level]",
            problem=f"Printer defines {len(z_steppers)} independent Z stepper drivers ({', '.join(z_steppers)}) but lacks `[z_tilt]` or `[quad_gantry_level]`.",
            causes="Independent Z motors slowly lose sync when de-energized (`M84`), causing gantry sag/tilt that is never automatically leveled before printing.",
            acknowledge_verify="Disable motors (`M84`), gently push one side of the Z gantry/bed, and observe that `G28` alone does not level the independent Z leadscrews/belts.",
            solution="Add `[z_tilt]` (for 2 or 3 Z motors) or `[quad_gantry_level]` (for 4 Z motors) with `stepper_z` pivot coordinates and probe points, and call `Z_TILT_ADJUST` / `QUAD_GANTRY_LEVEL` in `START_PRINT`.",
            doc_ref="https://www.klipper3d.org/Config_Reference.html#z_tilt",
        ))

    # --- 5. THERMAL SAFETY & PID CONTROL CHECKS ---
    for heater in ("extruder", "heater_bed"):
        if heater not in active:
            continue
        h_sec = active[heater]
        h_saved = saved.get(heater, {})
        ctrl = h_saved.get("control", h_sec.get("control", "")).lower()
        kd_str = h_saved.get("pid_kd", h_saved.get("pid_Kd", h_sec.get("pid_kd", h_sec.get("pid_Kd", "100"))))
        try:
            kd_val = float(kd_str)
        except ValueError:
            kd_val = 100.0

        if ctrl == "pid" and kd_val == 0.0:
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="CONFIG_ISSUE",
                category="Thermal Control / PID Derivative Damping",
                component=f"[{heater}] pid_kd: 0",
                problem=f"`[{heater}]` has derivative term `pid_kd: 0` (running as an undamped PI controller).",
                causes="Without derivative damping (`Kd`), thermal inertia causes temperature overshoot and slow cyclic oscillation, leading to periodic bed/nozzle expansion and subtle Z-banding.",
                acknowledge_verify=f"Watch the `{heater}` temperature graph in Fluidd/Mainsail after reaching target temperature for +-0.5C sinusoidal waves.",
                solution=f"Run `PID_CALIBRATE HEATER={heater} TARGET={'65' if heater == 'heater_bed' else '220'}` followed by `SAVE_CONFIG`.",
                doc_ref="https://www.klipper3d.org/Config_Checks.html#calibrate-pid-settings",
            ))

        if heater == "extruder" and "min_extrude_temp" in h_sec:
            try:
                met = float(h_sec["min_extrude_temp"])
                if met < 140.0:
                    findings.append(make_finding(
                        severity="HIGH",
                        finding_type="SAFETY_GAP",
                        category="Cold Extrusion Protection",
                        component=f"[extruder] min_extrude_temp: {met}",
                        problem=f"`min_extrude_temp` is set to `{met}C` (below polymer melting temperature).",
                        causes="Allows the extruder motor to push solid filament into a cold nozzle if a macro or user commands extrusion before heating, stripping filament or jamming the extruder gears.",
                        acknowledge_verify="Inspect `[extruder] min_extrude_temp` in your configuration.",
                        solution="Set `min_extrude_temp: 170` under `[extruder]`.",
                        doc_ref="https://www.klipper3d.org/Config_Reference.html#extruder",
                    ))
            except ValueError:
                pass

        vh_sec = active.get(f"verify_heater {heater}", None)
        if vh_sec is None:
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="SAFETY_GAP",
                category="Thermal Runaway Protection",
                component=f"[verify_heater {heater}] (Explicit section omitted)",
                problem=f"No explicit `[verify_heater {heater}]` block is defined (relying on stock defaults).",
                causes="Explicit `[verify_heater]` settings ensure fast fault detection if a heater cartridge falls out or a thermistor decouples.",
                acknowledge_verify="Inspect `printer.cfg` for `[verify_heater extruder]` and `[verify_heater heater_bed]`.",
                solution=f"Ensure `[verify_heater {heater}]` is present with safe `check_gain_time` (`20-30` for extruder, `60-120` for bed) and `hysteresis: 5`.",
                doc_ref="https://www.klipper3d.org/Config_Reference.html#verify_heater",
            ))
        elif "max_error" in vh_sec:
            try:
                me = float(vh_sec["max_error"])
                if me > 500.0:
                    findings.append(make_finding(
                        severity="CRITICAL",
                        finding_type="SAFETY_GAP",
                        category="Thermal Runaway Protection Disabled",
                        component=f"[verify_heater {heater}] max_error: {int(me)}",
                        problem=f"`[verify_heater {heater}]` sets `max_error: {int(me)}` (default is `120`), effectively disabling thermal runaway protection.",
                        causes="If the heater cartridge or thermistor dislodges while heating, Klipper will keep driving 100% PWM power into the heater without shutting down, creating a severe fire hazard.",
                        acknowledge_verify=f"Inspect `[verify_heater {heater}] max_error` in your config files.",
                        solution=f"Set `max_error: 120` under `[verify_heater {heater}]`.",
                        doc_ref="https://www.klipper3d.org/Config_Reference.html#verify_heater",
                    ))
            except ValueError:
                pass

    for sec_name, sec_data in active.items():
        if "min_temp" in sec_data or "max_temp" in sec_data:
            try:
                min_t = float(sec_data.get("min_temp", 0))
                max_t = float(sec_data.get("max_temp", 300))
                if min_t <= -100 or max_t >= 500:
                    findings.append(make_finding(
                        severity="HIGH",
                        finding_type="SAFETY_GAP",
                        category="Thermistor Fault Detection",
                        component=f"[{sec_name}] min_temp: {min_t}, max_temp: {max_t}",
                        problem=f"`[{sec_name}]` sets extreme temperature bounds (`min_temp: {min_t}`, `max_temp: {max_t}`).",
                        causes=(
                            "An open-circuit/broken NTC thermistor wire reads near -273C, and a shorted thermistor wire reads >500C. "
                            "Setting `min_temp: -273` or `max_temp: 999` masks broken or shorted thermistor wiring instead of triggering an immediate MCU safety shutdown."
                        ),
                        acknowledge_verify=f"Check the temperature reading of `{sec_name}` in Mainsail/Fluidd; if it reads below 0C at room temperature, the thermistor is unplugged or faulty.",
                        solution=f"Set realistic physical limits on `[{sec_name}]`: `min_temp: 0` (or `-10`) and `max_temp: 100` (for chamber/MCU sensors) or `320` (for hotends).",
                        doc_ref="https://www.klipper3d.org/Config_Reference.html#temperature_sensor",
                    ))
            except ValueError:
                pass

    # --- 6. IDLE TIMEOUT & FIRMWARE FEATURE GAPS ---
    idle_sec = active.get("idle_timeout", {})
    if "timeout" in idle_sec:
        try:
            idle_t = float(idle_sec["timeout"])
            if idle_t > 7200:
                findings.append(make_finding(
                    severity="HIGH",
                    finding_type="SAFETY_GAP",
                    category="Thermal & Motor Idle Safety Timeout",
                    component=f"[idle_timeout] timeout: {int(idle_t)}",
                    problem=f"`[idle_timeout]` is set to `{int(idle_t)}` seconds (~{round(idle_t/3600, 1)} hours), effectively disabling automatic idle heater/motor shutoff.",
                    causes=(
                        "If a print is paused indefinitely, a preheat command is forgotten, or a host macro hangs while heaters are active, "
                        "Klipper will never trigger `idle_timeout` to turn off the hotend and bed heaters, creating a fire/charring hazard and cooking nozzle filament."
                    ),
                    acknowledge_verify="Check `[idle_timeout] timeout` in `printer.cfg`.",
                    solution=(
                        "Set `timeout: 1800` (30 minutes) under `[idle_timeout]`, and use a custom `gcode:` block in `[idle_timeout]` that turns off only the extruder heater (`M104 S0`) if `printer.pause_resume.is_paused` is true."
                    ),
                    doc_ref="https://www.klipper3d.org/Config_Reference.html#idle_timeout",
                ))
        except ValueError:
            pass

    if "exclude_object" not in active:
        findings.append(make_finding(
            severity="MEDIUM",
            finding_type="FIRMWARE_GAP",
            category="Firmware Feature Gap / Object Cancellation & Adaptive Mesh",
            component="[exclude_object] (Missing)",
            problem="`[exclude_object]` is not enabled in `printer.cfg`.",
            causes="You cannot cancel an individual failed object mid-print in Mainsail/Fluidd, and KAMP / native Klipper `BED_MESH_CALIBRATE ADAPTIVE=1` cannot read object bounding boxes.",
            acknowledge_verify="Check if the Exclude Object icon appears in Fluidd/Mainsail during multi-part prints.",
            solution="Add `[exclude_object]` to `printer.cfg` and `enable_object_processing: True` under `[file_manager]` in `moonraker.conf`.",
            doc_ref="https://www.klipper3d.org/Exclude_Object.html",
        ))

    if "firmware_retraction" not in active:
        findings.append(make_finding(
            severity="LOW",
            finding_type="FIRMWARE_GAP",
            category="Firmware Feature Gap / Live Retraction Tuning",
            component="[firmware_retraction] (Missing)",
            problem="`[firmware_retraction]` is not configured in `printer.cfg`.",
            causes="Without `[firmware_retraction]`, you cannot adjust `retract_length` or `retract_speed` live during a print via `SET_RETRACTION` or run `TUNING_TOWER` retraction tests without reslicing.",
            acknowledge_verify="Run `GET_RETRACTION` in the Klipper console (returns Unknown command if missing).",
            solution=(
                "Add to `printer.cfg`:\n"
                "```ini\n[firmware_retraction]\nretract_length: 0.6\nretract_speed: 40\nunretract_extra_length: 0\nunretract_speed: 40\n```"
            ),
            doc_ref="https://www.klipper3d.org/Config_Reference.html#firmware_retraction",
        ))

    if "skew_correction" not in active:
        findings.append(make_finding(
            severity="LOW",
            finding_type="FIRMWARE_GAP",
            category="Firmware Feature Gap / Dimensional Orthogonality",
            component="[skew_correction] (Missing)",
            problem="`[skew_correction]` is not enabled in `printer.cfg`.",
            causes="Any slight frame or belt non-orthogonality between X, Y, and Z axes produces parallelograms instead of true 90.0-degree rectangles, preventing precision mating parts from fitting.",
            acknowledge_verify="Print a 100x100 mm diagonal calibration square and measure diagonals AC and BD with calipers; any difference > 0.15 mm indicates frame/belt skew.",
            solution="Add `[skew_correction]` to `printer.cfg`, measure diagonals AC, BD, and side AD, run `SET_SKEW XY=<AC>,<BD>,<AD>`, and save with `SKEW_PROFILE SAVE=my_skew_profile`.",
            doc_ref="https://www.klipper3d.org/Skew_Correction.html",
        ))

    # --- 7. MOONRAKER & LOG CHECKS ---
    if moonraker:
        fm = moonraker.get("file_manager", {})
        if fm.get("enable_object_processing", "False").lower() != "true":
            findings.append(make_finding(
                severity="MEDIUM",
                finding_type="ECOSYSTEM_GAP",
                category="Moonraker Pre-Processor",
                component="moonraker.conf [file_manager] enable_object_processing",
                problem="`enable_object_processing: True` is missing or disabled in `moonraker.conf`.",
                causes="Moonraker will not inject `EXCLUDE_OBJECT_DEFINE` polygons into uploaded G-code files unless the slicer explicitly does so.",
                acknowledge_verify="Check `[file_manager]` in `moonraker.conf`.",
                solution="Set `enable_object_processing: True` under `[file_manager]` in `moonraker.conf` and restart Moonraker.",
                doc_ref="https://moonraker.readthedocs.io/en/latest/configuration/#file_manager",
            ))

    for lr in log_reports:
        if lr["mcu_shutdown_events"]:
            findings.append(make_finding(
                severity="CRITICAL",
                finding_type="LOG_ERROR",
                category="Klippy Runtime Shutdown / Fault",
                component=f"{lr['log_file']} Shutdown Events",
                problem=f"Detected MCU shutdown/fault events in `{lr['log_file']}`: `{'; '.join(lr['mcu_shutdown_events'][:3])}`",
                causes="Hardware timer overload (`Timer too close`), thermal runaway (`not heating at expected rate`), or MCU communication drop halts active prints mid-job.",
                acknowledge_verify="Inspect the bottom of `klippy.log` right before `Transition to shutdown state`.",
                solution="Address the specific MCU or heater fault reported in the log, verify USB/UART/CANBUS cabling and 5V/24V power stability, and reduce `microsteps` if `Timer too close` occurs.",
                doc_ref="https://www.klipper3d.org/TMC_Drivers.html",
            ))

    return {
        "loaded_files": loaded_files,
        "included_files_referenced": includes,
        "unresolved_includes": unresolved_includes,
        "header_metadata": headers,
        "printer_architecture": {
            "kinematics": kinematics,
            "mcus": list(mcus.keys()),
            "z_stepper_count": len(z_steppers),
            "macros_count": len(macros_defined),
            "macros_sample": macros_defined[:25],
        },
        "motion_limits": {
            "max_velocity_mm_s": max_vel,
            "max_accel_mm_s2": max_accel,
            "max_z_velocity_mm_s": max_z_vel,
            "square_corner_velocity_mm_s": scv,
        },
        "steppers": stepper_summary,
        "input_shaper": shaper_summary,
        "bed_mesh_analysis": mesh_summary,
        "klippy_log_summary": log_reports,
        "total_findings": len(findings),
        "diagnostic_findings": findings,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Universal Klipper Multi-File Configuration, Log & Firmware Gap Auditor"
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="One or more paths to Klipper .cfg files, moonraker.conf, klippy.log, directories, or .zip backups",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    report = audit_bundle(args.paths)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("=== UNIVERSAL KLIPPER MULTI-FILE & FIRMWARE GAP AUDIT REPORT ===")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
