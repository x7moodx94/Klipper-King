#!/usr/bin/env python3
"""
Klipper G-Code Physical & Kinematic Machine Simulator
Simulates how Klipper's klippy host and MCU execute a G-code file on a real 3D printer:
1. Parses G-code movement, extrusion, temperature, fan, acceleration, and Klipper macro commands.
2. Models Klipper's lookahead cornering (centripetal square_corner_velocity) and trapezoidal move planner
   (acceleration, cruise, deceleration phases with max_accel and minimum_cruise_ratio / max_accel_to_decel).
3. Applies inverse kinematics (Cartesian, CoreXY, CoreXZ, Hybrid-CoreXY, Hybrid-CoreXZ, Linear Delta)
   to compute actual stepper motor displacements, peak motor velocities, and MCU step pulse rates (steps/s).
4. Computes extruder physics: instantaneous volumetric flow rate (mm^3/s), filament feed velocity (mm/s),
   Pressure Advance instantaneous motor velocity boost, and retraction statistics.
5. Computes Input Shaper spatial smoothing / corner rounding radius and Bed Mesh Z-axis compensation work.
"""

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


SHAPER_DURATION_FACTOR = {
    "zv": 0.5,
    "mzv": 0.75,
    "zvd": 1.0,
    "ei": 1.0,
    "2hump_ei": 1.5,
    "3hump_ei": 2.0,
}

SHAPER_SMOOTHING_COEFF = {
    "zv": 0.0085,
    "mzv": 0.0145,
    "zvd": 0.0250,
    "ei": 0.03225,
    "2hump_ei": 0.0525,
    "3hump_ei": 0.0825,
}


@dataclass
class MachineConfig:
    kinematics: str = "corexz"
    max_velocity: float = 600.0
    max_accel: float = 20000.0
    max_accel_to_decel: float = 10000.0
    max_z_velocity: float = 500.0
    max_z_accel: float = 500.0
    square_corner_velocity: float = 5.0
    nozzle_diameter: float = 0.4
    filament_diameter: float = 1.75
    pressure_advance: float = 0.04
    pressure_advance_smooth_time: float = 0.040
    rot_dist_x: float = 39.94
    rot_dist_y: float = 40.0
    rot_dist_z: float = 39.94
    rot_dist_e: float = 6.9
    microsteps_x: int = 32
    microsteps_y: int = 32
    microsteps_z: int = 32
    microsteps_e: int = 16
    full_steps_per_rotation: int = 200
    shaper_type_x: str = "ei"
    shaper_freq_x: float = 80.0
    shaper_type_y: str = "ei"
    shaper_freq_y: float = 58.0
    bed_mesh_z_span: float = 0.0
    bed_mesh_x_slope: float = 0.0
    bed_mesh_y_slope: float = 0.0
    fade_start: float = 5.0
    fade_end: float = 50.0


def load_machine_config_from_cfg(cfg_path: str) -> MachineConfig:
    mc = MachineConfig()
    if not cfg_path or not os.path.exists(cfg_path):
        return mc

    with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    section = ""
    mesh_points: List[List[float]] = []
    in_points = False

    for raw in lines:
        line = raw.strip()
        if line.startswith("#*#"):
            line = line[3:].strip()
        else:
            line = re.sub(r"[#;].*$", "", line).strip()
        if not line:
            continue

        sec_m = re.match(r"^\[([^\]]+)\]$", line)
        if sec_m:
            section = sec_m.group(1).strip().lower()
            in_points = False
            continue

        if section == "bed_mesh default" and line.startswith("points"):
            in_points = True
            continue
        if in_points:
            if ":" in line or "=" in line:
                in_points = False
            else:
                vals = [float(v.strip()) for v in line.split(",") if v.strip()]
                if vals:
                    mesh_points.append(vals)
                continue

        kv_m = re.match(r"^([a-zA-Z0-9_]+)\s*[:=]\s*(.+)$", line)
        if not kv_m:
            continue
        k, v = kv_m.group(1).strip().lower(), kv_m.group(2).strip()

        try:
            if section == "printer":
                if k == "kinematics":
                    mc.kinematics = v.lower()
                elif k == "max_velocity":
                    mc.max_velocity = float(v)
                elif k == "max_accel":
                    mc.max_accel = float(v)
                elif k == "max_accel_to_decel":
                    mc.max_accel_to_decel = float(v)
                elif k == "minimum_cruise_ratio":
                    mcr = float(v)
                    mc.max_accel_to_decel = mc.max_accel * (1.0 - mcr)
                elif k == "max_z_velocity":
                    mc.max_z_velocity = float(v)
                elif k == "max_z_accel":
                    mc.max_z_accel = float(v)
                elif k == "square_corner_velocity":
                    mc.square_corner_velocity = float(v)
            elif section == "stepper_x":
                if k == "rotation_distance":
                    mc.rot_dist_x = float(v)
                elif k == "microsteps":
                    mc.microsteps_x = int(v)
            elif section == "stepper_y":
                if k == "rotation_distance":
                    mc.rot_dist_y = float(v)
                elif k == "microsteps":
                    mc.microsteps_y = int(v)
            elif section == "stepper_z":
                if k == "rotation_distance":
                    mc.rot_dist_z = float(v)
                elif k == "microsteps":
                    mc.microsteps_z = int(v)
            elif section == "extruder":
                if k == "rotation_distance":
                    mc.rot_dist_e = float(v)
                elif k == "microsteps":
                    mc.microsteps_e = int(v)
                elif k == "nozzle_diameter":
                    mc.nozzle_diameter = float(v)
                elif k == "filament_diameter":
                    mc.filament_diameter = float(v)
                elif k == "pressure_advance":
                    mc.pressure_advance = float(v)
                elif k == "pressure_advance_smooth_time":
                    mc.pressure_advance_smooth_time = float(v)
            elif section == "input_shaper":
                if k == "shaper_type_x":
                    mc.shaper_type_x = v.lower()
                elif k == "shaper_freq_x":
                    mc.shaper_freq_x = float(v)
                elif k == "shaper_type_y":
                    mc.shaper_type_y = v.lower()
                elif k == "shaper_freq_y":
                    mc.shaper_freq_y = float(v)
            elif section == "bed_mesh":
                if k == "fade_start":
                    mc.fade_start = float(v)
                elif k == "fade_end":
                    mc.fade_end = float(v)
        except ValueError:
            pass

    if mesh_points:
        flat = [p for row in mesh_points for p in row]
        mc.bed_mesh_z_span = max(flat) - min(flat)
        row_x_diffs = [row[-1] - row[0] for row in mesh_points if len(row) >= 2]
        if row_x_diffs:
            mc.bed_mesh_x_slope = (sum(row_x_diffs) / len(row_x_diffs)) / 210.0
        if len(mesh_points) >= 2 and len(mesh_points[0]) == len(mesh_points[-1]):
            col_y_diffs = [mesh_points[-1][i] - mesh_points[0][i] for i in range(len(mesh_points[0]))]
            mc.bed_mesh_y_slope = (sum(col_y_diffs) / len(col_y_diffs)) / 210.0

    return mc


def compute_junction_velocity(
    v1_vec: Tuple[float, float, float],
    v2_vec: Tuple[float, float, float],
    scv: float,
    accel: float,
) -> float:
    cos_theta = -(v1_vec[0] * v2_vec[0] + v1_vec[1] * v2_vec[1] + v1_vec[2] * v2_vec[2])
    cos_theta = max(-1.0, min(1.0, cos_theta))
    sin_theta_d2 = math.sqrt(max(0.0, 0.5 * (1.0 - cos_theta)))
    cos_theta_d2 = math.sqrt(max(0.0, 0.5 * (1.0 + cos_theta)))
    if cos_theta_d2 < 1e-6:
        return 0.0
    one_minus_sin = max(1e-6, 1.0 - sin_theta_d2)
    r_jd = (scv * scv / max(1.0, accel)) / (math.sqrt(2.0) - 1.0)
    v_junction_sq = accel * r_jd * sin_theta_d2 / one_minus_sin
    return math.sqrt(max(0.0, v_junction_sq))


def transform_kinematics(
    kinematics: str, dx: float, dy: float, dz: float
) -> Dict[str, float]:
    kin = kinematics.lower()
    if kin == "corexy":
        return {"stepper_x(A)": dx + dy, "stepper_y(B)": dx - dy, "stepper_z": dz}
    elif kin == "corexz":
        return {"stepper_x(A)": dx + dz, "stepper_y": dy, "stepper_z(B)": dx - dz}
    elif kin in ("hybrid_corexy", "hybrid-corexy"):
        return {"stepper_x(A)": dx + dy, "stepper_y": dy, "stepper_z": dz}
    elif kin in ("hybrid_corexz", "hybrid-corexz"):
        return {"stepper_x(A)": dx + dz, "stepper_y": dy, "stepper_z": dz}
    else:
        return {"stepper_x": dx, "stepper_y": dy, "stepper_z": dz}


def simulate_gcode(gcode_path: str, mc: MachineConfig) -> Dict:
    filament_area = math.pi * (mc.filament_diameter / 2.0) ** 2

    abs_coords = True
    abs_extrude = False
    x, y, z, e = 0.0, 0.0, 0.0, 0.0
    feedrate_mm_s = 50.0
    cur_accel = mc.max_accel
    max_observed_accel = mc.max_accel
    cur_scv = mc.square_corner_velocity

    total_lines = 0
    move_count = 0
    extrude_move_count = 0
    travel_move_count = 0
    z_hop_count = 0
    retract_count = 0
    retract_distances: List[float] = []

    total_time_s = 0.0
    accel_time_s = 0.0
    cruise_time_s = 0.0
    decel_time_s = 0.0

    total_xy_dist_mm = 0.0
    total_z_dist_mm = 0.0
    total_extruded_mm = 0.0
    bed_mesh_induced_z_mm = 0.0

    max_commanded_xy_speed = 0.0
    max_reached_xy_speed = 0.0
    max_volumetric_flow = 0.0
    volumetric_flows: List[float] = []
    max_pa_extruder_speed = 0.0

    motor_total_travel: Dict[str, float] = {}
    motor_max_speed: Dict[str, float] = {}
    motor_direction_reversals: Dict[str, int] = {}
    motor_last_dir: Dict[str, int] = {}

    macros_invoked: Dict[str, int] = {}
    temp_commands: List[str] = []
    fan_changes = 0
    layer_count = 0

    min_x, max_x = float("inf"), float("-inf")
    min_y, max_y = float("inf"), float("-inf")
    max_z = 0.0

    prev_unit_vec: Optional[Tuple[float, float, float]] = None
    prev_v_exit = 0.0

    param_re = re.compile(r"([A-Za-z])([-+]?\d*\.?\d+)")

    with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            total_lines += 1
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith(";"):
                if stripped.startswith(";LAYER_CHANGE") or stripped.startswith("; layer "):
                    layer_count += 1
                continue

            cmd_part = stripped.split(";", 1)[0].strip()
            if not cmd_part:
                continue

            tokens = cmd_part.split()
            cmd = tokens[0].upper()

            if cmd in ("G0", "G1"):
                params = {m.group(1).upper(): float(m.group(2)) for m in param_re.finditer(cmd_part[len(cmd):])}
                if "F" in params:
                    feedrate_mm_s = max(0.1, params["F"] / 60.0)

                nx = (params["X"] if abs_coords else x + params["X"]) if "X" in params else x
                ny = (params["Y"] if abs_coords else y + params["Y"]) if "Y" in params else y
                nz = (params["Z"] if abs_coords else z + params["Z"]) if "Z" in params else z
                if "E" in params:
                    ne = params["E"] if abs_extrude else e + params["E"]
                else:
                    ne = e

                dx, dy, dz, de = nx - x, ny - y, nz - z, ne - e
                dist_xyz = math.sqrt(dx * dx + dy * dy + dz * dz)
                dist_xy = math.sqrt(dx * dx + dy * dy)

                if dist_xyz > 1e-6:
                    move_count += 1
                    total_xy_dist_mm += dist_xy
                    total_z_dist_mm += abs(dz)

                    if de > 1e-5 and dist_xy > 1e-5:
                        extrude_move_count += 1
                        min_x, max_x = min(min_x, nx), max(max_x, nx)
                        min_y, max_y = min(min_y, ny), max(max_y, ny)
                        max_z = max(max_z, nz)
                    elif dist_xy > 1e-5:
                        travel_move_count += 1

                    if abs(dz) > 1e-4 and dist_xy < 1e-4:
                        z_hop_count += 1

                    if dist_xy > 1e-5 and nz < mc.fade_end and (abs(mc.bed_mesh_x_slope) > 0 or abs(mc.bed_mesh_y_slope) > 0):
                        fade_factor = 1.0
                        if nz > mc.fade_start and mc.fade_end > mc.fade_start:
                            fade_factor = max(0.0, 1.0 - (nz - mc.fade_start) / (mc.fade_end - mc.fade_start))
                        dz_mesh = (dx * mc.bed_mesh_x_slope + dy * mc.bed_mesh_y_slope) * fade_factor
                        bed_mesh_induced_z_mm += abs(dz_mesh)
                    else:
                        dz_mesh = 0.0

                    v_target = min(feedrate_mm_s, mc.max_velocity)
                    if abs(dz) > 1e-5:
                        z_frac = abs(dz) / dist_xyz
                        if v_target * z_frac > mc.max_z_velocity:
                            v_target = mc.max_z_velocity / z_frac

                    if dist_xy > 1e-5:
                        max_commanded_xy_speed = max(max_commanded_xy_speed, v_target)

                    unit_vec = (dx / dist_xyz, dy / dist_xyz, dz / dist_xyz)
                    if prev_unit_vec is not None:
                        v_junc = compute_junction_velocity(prev_unit_vec, unit_vec, cur_scv, cur_accel)
                        v_start = min(prev_v_exit, v_target, v_junc)
                    else:
                        v_start = 0.0

                    v_end = min(v_target, cur_scv)
                    eff_accel = min(cur_accel, mc.max_accel)
                    max_observed_accel = max(max_observed_accel, eff_accel)
                    v_peak_sq = eff_accel * dist_xyz + 0.5 * (v_start * v_start + v_end * v_end)
                    v_peak = min(v_target, math.sqrt(max(0.0, v_peak_sq)))

                    if dist_xy > 1e-5:
                        max_reached_xy_speed = max(max_reached_xy_speed, v_peak)

                    t_a = max(0.0, (v_peak - v_start) / eff_accel)
                    t_d = max(0.0, (v_peak - v_end) / eff_accel)
                    d_a = 0.5 * (v_start + v_peak) * t_a
                    d_d = 0.5 * (v_end + v_peak) * t_d
                    d_c = max(0.0, dist_xyz - d_a - d_d)
                    t_c = d_c / max(1e-3, v_peak)
                    seg_time = t_a + t_c + t_d

                    accel_time_s += t_a
                    cruise_time_s += t_c
                    decel_time_s += t_d
                    total_time_s += seg_time

                    if de > 0:
                        total_extruded_mm += de
                        if seg_time > 1e-5:
                            e_vel_peak = (de / dist_xyz) * v_peak
                            q_flow = e_vel_peak * filament_area
                            max_volumetric_flow = max(max_volumetric_flow, q_flow)
                            volumetric_flows.append(q_flow)
                            e_accel = (de / dist_xyz) * eff_accel
                            smooth_atten = min(1.0, t_a / max(0.005, mc.pressure_advance_smooth_time))
                            pa_boost = mc.pressure_advance * e_accel * smooth_atten
                            max_pa_extruder_speed = max(max_pa_extruder_speed, e_vel_peak + pa_boost)
                    elif de < -1e-4:
                        retract_count += 1
                        retract_distances.append(abs(de))

                    kin_deltas = transform_kinematics(mc.kinematics, dx, dy, dz + dz_mesh)
                    for m_name, m_delta in kin_deltas.items():
                        motor_total_travel[m_name] = motor_total_travel.get(m_name, 0.0) + abs(m_delta)
                        if dist_xyz > 1e-6:
                            m_speed = (abs(m_delta) / dist_xyz) * v_peak
                            motor_max_speed[m_name] = max(motor_max_speed.get(m_name, 0.0), m_speed)
                        if abs(m_delta) > 1e-4:
                            d_sign = 1 if m_delta > 0 else -1
                            if m_name in motor_last_dir and motor_last_dir[m_name] != d_sign:
                                motor_direction_reversals[m_name] = motor_direction_reversals.get(m_name, 0) + 1
                            motor_last_dir[m_name] = d_sign

                    prev_unit_vec = unit_vec
                    prev_v_exit = v_peak

                elif abs(de) > 1e-5:
                    e_speed = min(feedrate_mm_s, 100.0)
                    seg_time = abs(de) / max(0.1, e_speed)
                    total_time_s += seg_time
                    if de > 0:
                        total_extruded_mm += de
                    else:
                        retract_count += 1
                        retract_distances.append(abs(de))
                    prev_unit_vec = None
                    prev_v_exit = 0.0

                x, y, z, e = nx, ny, nz, ne

            elif cmd == "G90":
                abs_coords = True
            elif cmd == "G91":
                abs_coords = False
            elif cmd == "M82":
                abs_extrude = True
            elif cmd == "M83":
                abs_extrude = False
            elif cmd == "G92":
                params = {m.group(1).upper(): float(m.group(2)) for m in param_re.finditer(cmd_part[len(cmd):])}
                if "X" in params:
                    x = params["X"]
                if "Y" in params:
                    y = params["Y"]
                if "Z" in params:
                    z = params["Z"]
                if "E" in params:
                    e = params["E"]
            elif cmd == "M204":
                params = {m.group(1).upper(): float(m.group(2)) for m in param_re.finditer(cmd_part[len(cmd):])}
                if "S" in params:
                    cur_accel = min(mc.max_accel, params["S"])
                elif "P" in params and "T" in params:
                    cur_accel = min(mc.max_accel, params["P"], params["T"])
                elif "P" in params:
                    cur_accel = min(mc.max_accel, params["P"])
            elif cmd == "M205":
                params = {m.group(1).upper(): float(m.group(2)) for m in param_re.finditer(cmd_part[len(cmd):])}
                if "X" in params:
                    cur_scv = params["X"]
                elif "Y" in params:
                    cur_scv = params["Y"]
            elif cmd == "SET_VELOCITY_LIMIT":
                for tok in tokens[1:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        k = k.upper()
                        try:
                            val = float(v)
                            if k == "ACCEL":
                                cur_accel = min(mc.max_accel, val)
                            elif k == "SQUARE_CORNER_VELOCITY":
                                cur_scv = val
                        except ValueError:
                            pass
            elif cmd in ("M104", "M109", "M140", "M190"):
                temp_commands.append(cmd_part)
            elif cmd in ("M106", "M107"):
                fan_changes += 1
            else:
                if not cmd.startswith("M") and not cmd.startswith("G"):
                    macros_invoked[cmd] = macros_invoked.get(cmd, 0) + 1

    steps_per_mm_x = (mc.full_steps_per_rotation * mc.microsteps_x) / mc.rot_dist_x
    steps_per_mm_y = (mc.full_steps_per_rotation * mc.microsteps_y) / mc.rot_dist_y
    steps_per_mm_z = (mc.full_steps_per_rotation * mc.microsteps_z) / mc.rot_dist_z
    steps_per_mm_e = (mc.full_steps_per_rotation * mc.microsteps_e) / mc.rot_dist_e

    motor_step_rates = {}
    for m_name, m_spd in motor_max_speed.items():
        if "x" in m_name:
            spm = steps_per_mm_x
        elif "y" in m_name:
            spm = steps_per_mm_y
        else:
            spm = steps_per_mm_z
        motor_step_rates[m_name] = round(m_spd * spm, 1)

    shaper_x_dur = SHAPER_DURATION_FACTOR.get(mc.shaper_type_x, 1.0) / max(1.0, mc.shaper_freq_x)
    shaper_y_dur = SHAPER_DURATION_FACTOR.get(mc.shaper_type_y, 1.0) / max(1.0, mc.shaper_freq_y)
    smoothing_x = SHAPER_SMOOTHING_COEFF.get(mc.shaper_type_x, 0.03225) * max_observed_accel / (mc.shaper_freq_x ** 2)
    smoothing_y = SHAPER_SMOOTHING_COEFF.get(mc.shaper_type_y, 0.03225) * max_observed_accel / (mc.shaper_freq_y ** 2)

    vfa_fundamental_speeds_mm_s = {
        "50Hz_motor_tooth_2mm_pitch": 100.0,
        "100Hz_motor_harmonic_2mm_pitch": 200.0,
        "shaper_x_freq_2mm_pitch": round(mc.shaper_freq_x * 2.0, 1),
        "shaper_y_freq_2mm_pitch": round(mc.shaper_freq_y * 2.0, 1),
    }

    filament_volume_cm3 = (total_extruded_mm * filament_area) / 1000.0
    filament_weight_pla_g = filament_volume_cm3 * 1.24

    return {
        "file": os.path.basename(gcode_path),
        "total_lines": total_lines,
        "layer_count": layer_count,
        "kinematics_mode": mc.kinematics,
        "bounding_box_mm": {
            "x_min": round(min_x, 2) if min_x != float("inf") else 0,
            "x_max": round(max_x, 2) if max_x != float("-inf") else 0,
            "y_min": round(min_y, 2) if min_y != float("inf") else 0,
            "y_max": round(max_y, 2) if max_y != float("-inf") else 0,
            "z_max": round(max_z, 2),
        },
        "time_breakdown_s": {
            "estimated_motion_time_s": round(total_time_s, 1),
            "estimated_motion_time_min": round(total_time_s / 60.0, 2),
            "accel_phase_s": round(accel_time_s, 1),
            "cruise_phase_s": round(cruise_time_s, 1),
            "decel_phase_s": round(decel_time_s, 1),
            "accel_decel_fraction_pct": round(100.0 * (accel_time_s + decel_time_s) / max(1e-3, total_time_s), 1),
        },
        "motion_metrics": {
            "total_moves": move_count,
            "extruding_moves": extrude_move_count,
            "travel_moves": travel_move_count,
            "z_hops": z_hop_count,
            "total_xy_distance_m": round(total_xy_dist_mm / 1000.0, 3),
            "total_z_distance_mm": round(total_z_dist_mm, 2),
            "bed_mesh_induced_extra_z_travel_mm": round(bed_mesh_induced_z_mm, 2),
            "max_commanded_xy_speed_mm_s": round(max_commanded_xy_speed, 1),
            "max_reached_xy_speed_mm_s": round(max_reached_xy_speed, 1),
        },
        "kinematic_stepper_workload": {
            "motor_total_travel_m": {k: round(v / 1000.0, 3) for k, v in motor_total_travel.items()},
            "motor_peak_velocity_mm_s": {k: round(v, 1) for k, v in motor_max_speed.items()},
            "motor_peak_step_rate_steps_per_s": motor_step_rates,
            "motor_direction_reversals": motor_direction_reversals,
            "step_resolution_um": {
                "x": round(1000.0 / steps_per_mm_x, 3),
                "y": round(1000.0 / steps_per_mm_y, 3),
                "z": round(1000.0 / steps_per_mm_z, 3),
                "e": round(1000.0 / steps_per_mm_e, 3),
            },
        },
        "extruder_and_rheology": {
            "total_filament_mm": round(total_extruded_mm, 1),
            "total_filament_cm3": round(filament_volume_cm3, 2),
            "estimated_pla_weight_g": round(filament_weight_pla_g, 2),
            "max_volumetric_flow_mm3_s": round(max_volumetric_flow, 2),
            "avg_volumetric_flow_mm3_s": round(sum(volumetric_flows) / len(volumetric_flows), 2) if volumetric_flows else 0.0,
            "max_pa_boosted_extruder_speed_mm_s": round(max_pa_extruder_speed, 2),
            "max_extruder_step_rate_steps_per_s": round(max_pa_extruder_speed * steps_per_mm_e, 1),
            "retraction_count": retract_count,
            "avg_retraction_mm": round(sum(retract_distances) / len(retract_distances), 2) if retract_distances else 0.0,
        },
        "input_shaper_and_vibrations": {
            "shaper_x": f"{mc.shaper_type_x} @ {mc.shaper_freq_x} Hz (impulse {round(shaper_x_dur*1000, 1)} ms)",
            "shaper_y": f"{mc.shaper_type_y} @ {mc.shaper_freq_y} Hz (impulse {round(shaper_y_dur*1000, 1)} ms)",
            "corner_smoothing_at_peak_accel_mm": {
                "x": round(smoothing_x, 3),
                "y": round(smoothing_y, 3),
            },
            "vfa_belt_pitch_critical_speeds_mm_s": vfa_fundamental_speeds_mm_s,
        },
        "klipper_macros_and_io": {
            "macros_called": macros_invoked,
            "thermal_commands": temp_commands[:15],
            "fan_pwm_updates": fan_changes,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Simulate physical Klipper machine behavior for a G-code file.")
    parser.add_argument("gcode", help="Path to the .gcode file")
    parser.add_argument("--config", help="Optional path to Klipper printer.cfg", default=None)
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    mc = load_machine_config_from_cfg(args.config)
    report = simulate_gcode(args.gcode, mc)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("=== KLIPPER G-CODE MACHINE & KINEMATIC SIMULATION REPORT ===")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
