#!/usr/bin/env python3
"""
Klipper Moonraker / Fluidd / Mainsail Live Agent Control & Auto-Remediation Bridge
Allows any AI agent (Gemini, Claude Code, Cursor, Windsurf, Cline, OpenClaw, or local automation)
to connect directly to a live Klipper 3D printer through the Moonraker HTTP API (which powers Fluidd and Mainsail).

Capabilities:
1. `status`: Query live printer telemetry (state, temperatures, toolhead position, homed axes, active speed/accel,
   Input Shaper, Pressure Advance, Bed Mesh profile, and recent console output).
2. `pull-and-audit`: Automatically download `printer.cfg`, `moonraker.conf`, and all `[include ...]` config files
   from the live printer into a local directory and run `klipper_config_auditor.py` on the complete bundle.
3. `gcode`: Execute safe diagnostic or calibration G-code commands (`DUMP_TMC`, `BED_MESH_OUTPUT`, `MEASURE_AXES_NOISE`,
   `SET_INPUT_SHAPER`, `SET_PRESSURE_ADVANCE`, `SET_VELOCITY_LIMIT`, `PID_CALIBRATE`, `SHAPER_CALIBRATE`) and return
   the console output from Klipper. Blocks destructive motion/restart commands if a print is actively running.
4. `patch-config`: Automatically create a timestamped backup of `printer.cfg` on the printer, apply section/key
   updates to fix identified issues, upload the patched config back to Moonraker, and optionally run `FIRMWARE_RESTART`
   when the printer is idle.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple


BLOCKED_WHILE_PRINTING = {
    "G28",
    "FIRMWARE_RESTART",
    "RESTART",
    "SAVE_CONFIG",
    "SHAPER_CALIBRATE",
    "TEST_RESONANCES",
    "PID_CALIBRATE",
    "BED_MESH_CALIBRATE",
    "Z_TILT_ADJUST",
    "QUAD_GANTRY_LEVEL",
    "SCREWS_TILT_CALCULATE",
    "FORCE_MOVE",
    "STEPPER_BUZZ",
}


class MoonrakerClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 15):
        url = base_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        self.base_url = url
        self.api_key = api_key or os.environ.get("MOONRAKER_API_KEY")
        self.timeout = timeout

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        if extra:
            h.update(extra)
        return h

    def get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def post_json(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def download_config_file(self, filename: str) -> str:
        quoted = urllib.parse.quote(filename.lstrip("/"))
        url = f"{self.base_url}/server/files/config/{quoted}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    def upload_config_file(self, filename: str, content: str, path_prefix: str = "") -> Dict[str, Any]:
        boundary = f"----KlipperEngineeringBoundary{uuid.uuid4().hex}"
        lines: List[bytes] = []

        def add_field(name: str, val: str):
            lines.append(f"--{boundary}\r\n".encode("utf-8"))
            lines.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            lines.append(f"{val}\r\n".encode("utf-8"))

        add_field("root", "config")
        if path_prefix:
            add_field("path", path_prefix.strip("/"))

        lines.append(f"--{boundary}\r\n".encode("utf-8"))
        lines.append(
            f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(filename)}"\r\n'.encode("utf-8")
        )
        lines.append(b"Content-Type: text/plain\r\n\r\n")
        lines.append(content.encode("utf-8"))
        lines.append(b"\r\n")
        lines.append(f"--{boundary}--\r\n".encode("utf-8"))

        body = b"".join(lines)
        url = f"{self.base_url}/server/files/upload"
        req = urllib.request.Request(
            url,
            data=body,
            headers=self._headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def get_printer_status(self) -> Dict[str, Any]:
        server_info = self.get_json("/server/info").get("result", {})
        obj_query = (
            "/printer/objects/query?"
            "print_stats&toolhead&extruder&heater_bed&bed_mesh&input_shaper&gcode_move&system_stats"
        )
        objects = self.get_json(obj_query).get("result", {}).get("status", {})
        gcode_store = self.get_json("/server/gcode_store", {"count": "15"}).get("result", {}).get("gcode_store", [])
        return {
            "moonraker_server_info": {
                "klippy_connected": server_info.get("klippy_connected"),
                "klippy_state": server_info.get("klippy_state"),
                "moonraker_version": server_info.get("moonraker_version"),
            },
            "print_stats": objects.get("print_stats", {}),
            "toolhead": {
                k: objects.get("toolhead", {}).get(k)
                for k in (
                    "homed_axes",
                    "position",
                    "max_velocity",
                    "max_accel",
                    "minimum_cruise_ratio",
                    "max_accel_to_decel",
                    "square_corner_velocity",
                )
            },
            "extruder": {
                k: objects.get("extruder", {}).get(k)
                for k in ("temperature", "target", "power", "pressure_advance", "smooth_time", "can_extrude")
            },
            "heater_bed": {
                k: objects.get("heater_bed", {}).get(k)
                for k in ("temperature", "target", "power")
            },
            "input_shaper": objects.get("input_shaper", {}),
            "bed_mesh_profile": objects.get("bed_mesh", {}).get("profile_name"),
            "recent_console_messages": [m.get("message") for m in gcode_store[-10:]],
        }

    def run_gcode(self, script: str, force_while_printing: bool = False) -> Dict[str, Any]:
        status = self.get_printer_status()
        state = status.get("print_stats", {}).get("state", "standby")
        upper_tokens = {line.strip().split()[0].upper() for line in script.splitlines() if line.strip()}
        blocked = upper_tokens.intersection(BLOCKED_WHILE_PRINTING)

        if state == "printing" and blocked and not force_while_printing:
            raise RuntimeError(
                f"SAFETY BLOCK: Printer is currently 'printing'. Refusing to execute destructive/calibration "
                f"commands {sorted(blocked)} during an active print."
            )

        self.post_json("/printer/gcode/script", {"script": script})
        time.sleep(0.6)
        store = self.get_json("/server/gcode_store", {"count": "20"}).get("result", {}).get("gcode_store", [])
        return {
            "executed_script": script,
            "printer_state": state,
            "console_response": [m.get("message") for m in store[-15:]],
        }

    def pull_all_configs(self, dest_dir: str) -> List[str]:
        os.makedirs(dest_dir, exist_ok=True)
        files_resp = self.get_json("/server/files/list", {"root": "config"}).get("result", [])
        downloaded: List[str] = []
        for item in files_resp:
            rel_path = item.get("path", "")
            if not rel_path or not rel_path.lower().endswith((".cfg", ".conf")):
                continue
            local_path = os.path.join(dest_dir, rel_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            content = self.download_config_file(rel_path)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(content)
            downloaded.append(local_path)
        return downloaded


def patch_ini_section_in_text(text: str, section: str, updates: Dict[str, str]) -> str:
    """Updates or appends key: value pairs inside [section] of a Klipper config file (above SAVE_CONFIG)."""
    save_marker = "#*# <---------------------- SAVE_CONFIG"
    if save_marker in text:
        main_part, save_part = text.split(save_marker, 1)
        save_part = save_marker + save_part
    else:
        main_part, save_part = text, ""

    lines = main_part.splitlines()
    sec_pattern = re.compile(r"^\[" + re.escape(section) + r"\]\s*$", re.IGNORECASE)

    sec_start = -1
    sec_end = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if sec_pattern.match(s):
            sec_start = i
            for j in range(i + 1, len(lines)):
                if re.match(r"^\[[^\]]+\]\s*$", lines[j].strip()):
                    sec_end = j
                    break
            break

    if sec_start == -1:
        # Append new section at the end of main_part
        new_block = [f"\n[{section}]"]
        for k, v in updates.items():
            if v is not None:
                new_block.append(f"{k}: {v}")
        lines.extend(new_block)
    else:
        remaining = dict(updates)
        new_sec_lines = [lines[sec_start]]
        for idx in range(sec_start + 1, sec_end):
            raw_line = lines[idx]
            stripped = re.sub(r"[#;].*$", "", raw_line).strip()
            kv_m = re.match(r"^([a-zA-Z0-9_]+)\s*[:=]\s*(.*)$", stripped)
            if kv_m:
                key_l = kv_m.group(1).lower()
                matched_k = next((uk for uk in remaining if uk.lower() == key_l), None)
                if matched_k is not None:
                    val = remaining.pop(matched_k)
                    if val is None or val == "__DELETE__":
                        new_sec_lines.append(f"# {raw_line} # Removed by klipper-engineering")
                    else:
                        new_sec_lines.append(f"{matched_k}: {val}")
                    continue
            new_sec_lines.append(raw_line)

        for k, v in remaining.items():
            if v is not None and v != "__DELETE__":
                new_sec_lines.append(f"{k}: {v}")

        lines = lines[:sec_start] + new_sec_lines + lines[sec_end:]

    rebuilt = "\n".join(lines).rstrip() + "\n\n" + save_part.lstrip() if save_part else "\n".join(lines) + "\n"
    return rebuilt


def main():
    parser = argparse.ArgumentParser(
        description="Moonraker / Fluidd / Mainsail Live Agent Control & Auto-Remediation Bridge"
    )
    parser.add_argument("--url", required=True, help="Printer Moonraker/Fluidd/Mainsail URL (e.g. http://192.168.1.50:7125)")
    parser.add_argument("--api-key", default=None, help="Optional Moonraker X-Api-Key")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Fetch live printer telemetry, temperatures, kinematics, and console logs")

    p_audit = sub.add_parser("pull-and-audit", help="Download all configs from printer and run klipper_config_auditor.py")
    p_audit.add_argument("--out-dir", default=None, help="Optional directory to save downloaded configs")

    p_gcode = sub.add_parser("gcode", help="Execute a safe G-code or extended Klipper command on the printer")
    p_gcode.add_argument("script", help="G-code command or newline-separated script (e.g. 'DUMP_TMC STEPPER=stepper_x')")
    p_gcode.add_argument("--force", action="store_true", help="Override active-print block (use with extreme caution)")

    p_patch = sub.add_parser(
        "patch-config",
        help="Back up a config file on the printer, patch [section] key=value pairs, upload, and optionally restart",
    )
    p_patch.add_argument("--file", default="printer.cfg", help="Config file to patch (default: printer.cfg)")
    p_patch.add_argument("--section", required=True, help="Config section name without brackets, e.g. 'bed_mesh'")
    p_patch.add_argument(
        "--set",
        nargs="+",
        required=True,
        help="One or more KEY=VALUE pairs (use KEY=__DELETE__ to comment out a parameter)",
    )
    p_patch.add_argument("--restart", action="store_true", help="Run FIRMWARE_RESTART after uploading if printer is idle")

    args = parser.parse_args()
    client = MoonrakerClient(args.url, api_key=args.api_key)

    if args.command == "status":
        print(json.dumps(client.get_printer_status(), indent=2))

    elif args.command == "pull-and-audit":
        target_dir = args.out_dir or tempfile.mkdtemp(prefix="klipper_live_cfg_")
        files = client.pull_all_configs(target_dir)
        auditor_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "klipper_config_auditor.py")
        cmd = [sys.executable, auditor_script, target_dir, "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        print(res.stdout or res.stderr)

    elif args.command == "gcode":
        out = client.run_gcode(args.script, force_while_printing=args.force)
        print(json.dumps(out, indent=2))

    elif args.command == "patch-config":
        status = client.get_printer_status()
        state = status.get("print_stats", {}).get("state", "standby")
        if state == "printing":
            raise RuntimeError("SAFETY BLOCK: Cannot patch config or restart firmware while printer is actively printing.")

        orig_text = client.download_config_file(args.file)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{args.file}.bak.{ts}"
        client.upload_config_file(backup_name, orig_text)

        updates = {}
        for item in args.set:
            if "=" not in item:
                raise ValueError(f"Invalid --set item '{item}', expected KEY=VALUE")
            k, v = item.split("=", 1)
            updates[k.strip()] = v.strip()

        patched_text = patch_ini_section_in_text(orig_text, args.section, updates)
        upload_res = client.upload_config_file(args.file, patched_text)

        restart_res = None
        if args.restart:
            restart_res = client.run_gcode("FIRMWARE_RESTART")

        print(
            json.dumps(
                {
                    "patched_file": args.file,
                    "backup_created_on_printer": backup_name,
                    "section_updated": args.section,
                    "applied_updates": updates,
                    "upload_result": upload_res,
                    "firmware_restart": restart_res,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
