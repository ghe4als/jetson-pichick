"""Parse ``tegrastats`` output into structured samples.

Sample input line (Jetson Orin Nano, JetPack 6):

    06-13-2026 06:50:20 RAM 874/7485MB (lfb 2x2MB) CPU [1%@729,...]
    GR3D_FREQ 0%@[305] cpu@46.687C/46.687C gpu@46.375C/46.375C
    VDD_IN 4857mW/...

We extract: ram_used_mb, ram_total_mb, gpu_pct, cpu_avg_pct, cpu_temp_c,
gpu_temp_c, vdd_in_mw. All best-effort; missing fields just don't appear
in the dict.

We don't shell tegrastats ourselves — caller is expected to spawn it as
a subprocess and feed lines in. That keeps this module pure and
testable.
"""
from __future__ import annotations

import re
from typing import Any

_RAM_RE = re.compile(r"RAM (\d+)/(\d+)MB")
_GPU_PCT_RE = re.compile(r"GR3D_FREQ (\d+)%")
_CPU_PCT_RE = re.compile(r"CPU \[([^\]]+)\]")
_CPU_TEMP_RE = re.compile(r"cpu@([\d.]+)C")
_GPU_TEMP_RE = re.compile(r"gpu@([\d.]+)C")
_VDD_IN_RE = re.compile(r"VDD_IN (\d+)mW")


def parse_line(line: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    if m := _RAM_RE.search(line):
        out["ram_used_mb"] = int(m.group(1))
        out["ram_total_mb"] = int(m.group(2))
        if out["ram_total_mb"] > 0:
            out["ram_pct"] = round(100 * out["ram_used_mb"] / out["ram_total_mb"], 1)

    if m := _GPU_PCT_RE.search(line):
        out["gpu_pct"] = int(m.group(1))

    if m := _CPU_PCT_RE.search(line):
        cores = []
        for chunk in m.group(1).split(","):
            try:
                cores.append(int(chunk.split("%", 1)[0]))
            except ValueError:
                continue
        if cores:
            out["cpu_pct_per_core"] = cores
            out["cpu_pct_avg"] = round(sum(cores) / len(cores), 1)

    if m := _CPU_TEMP_RE.search(line):
        out["cpu_temp_c"] = float(m.group(1))
    if m := _GPU_TEMP_RE.search(line):
        out["gpu_temp_c"] = float(m.group(1))
    if m := _VDD_IN_RE.search(line):
        out["vdd_in_mw"] = int(m.group(1))

    return out
