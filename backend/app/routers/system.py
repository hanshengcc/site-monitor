"""System diagnostic and specification router."""
import os
import platform
import shutil
import subprocess
from pathlib import Path
from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["System"])


def get_mem_info():
    mem_info = {}
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            with open(meminfo_path, "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        if key in ("MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree"):
                            mem_info[key + "_MB"] = round(int(val) / 1024, 1)
        except Exception:
            pass
    return mem_info


def get_cpu_info():
    model = "Unknown"
    cpuinfo_path = Path("/proc/cpuinfo")
    if cpuinfo_path.exists():
        try:
            with open(cpuinfo_path, "r") as f:
                for line in f:
                    if "model name" in line:
                        model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    return {
        "cores": os.cpu_count() or 1,
        "model": model,
        "arch": platform.machine(),
    }


def get_os_release():
    os_name = platform.platform()
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            with open(os_release, "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        os_name = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            pass
    return os_name


def get_docker_info():
    res = {"installed": False, "version": None, "containers": []}
    try:
        ver_proc = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=2)
        if ver_proc.returncode == 0:
            res["installed"] = True
            res["version"] = ver_proc.stdout.strip()

        ps_proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} | {{.Image}} | {{.Status}}"],
            capture_output=True, text=True, timeout=3
        )
        if ps_proc.returncode == 0:
            res["containers"] = [c.strip() for c in ps_proc.stdout.strip().split("\n") if c.strip()]
    except Exception as e:
        res["error"] = str(e)
    return res


@router.get("/specs")
async def system_specs():
    """Retrieve detailed hardware, OS, and resource utilization metrics."""
    disk = shutil.disk_usage("/")
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)

    return {
        "os": get_os_release(),
        "arch": platform.machine(),
        "cpu": get_cpu_info(),
        "memory": get_mem_info(),
        "disk": {
            "total_GB": round(disk.total / (1024**3), 2),
            "used_GB": round(disk.used / (1024**3), 2),
            "free_GB": round(disk.free / (1024**3), 2),
            "used_percent": round((disk.used / disk.total) * 100, 1),
        },
        "load_avg": [round(x, 2) for x in load],
        "docker": get_docker_info(),
    }
