"""`hub doctor`: inspect the actual machine and recommend local settings. Read-only; no network except
localhost Ollama and the configured database."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess  # noqa: S404
from pathlib import Path


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_gb() -> float | None:
    try:
        if Path("/proc/meminfo").exists():
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
        if platform.system() == "Darwin":
            return int(_run(["sysctl", "-n", "hw.memsize"]) or 0) / 1024**3 or None
        if platform.system() == "Windows":
            out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
            return int(out) / 1024**3 if out.isdigit() else None
    except (ValueError, OSError):
        return None
    return None


def _gpu() -> tuple[str, float | None]:
    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if out:
            name, mem = out.splitlines()[0].rsplit(",", 1)
            return name.strip(), float(mem) / 1024
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Apple Silicon (unified memory)", None
    if Path("/dev/kfd").exists():
        return "AMD GPU (ROCm device present)", None
    return "none detected", None


def recommend(ram: float | None, vram: float | None, apple: bool) -> list[str]:
    tips = []
    budget = vram if vram else (ram * 0.5 if ram else None)
    if apple and ram:
        budget = ram * 0.6
    if budget is None:
        tips.append("Could not determine memory; start with qwen2.5:1.5b + all-minilm.")
    elif budget < 3:
        tips.append("Low memory: HUB_LOW_RESOURCE_MODE=true, model qwen2.5:0.5b or qwen2.5:1.5b, embeddings all-minilm.")
    elif budget < 6:
        tips.append("Model qwen2.5:1.5b / llama3.2:3b (Q4) and all-minilm; HUB_WORKER_CONCURRENCY=2.")
    elif budget < 12:
        tips.append("Model qwen2.5:7b / llama3.1:8b (Q4, ~5 GB) and nomic-embed-text; HUB_WORKER_CONCURRENCY=2.")
    else:
        tips.append("Model qwen2.5:14b (Q4, ~9 GB) or 7-8B for speed; nomic-embed-text; HUB_WORKER_CONCURRENCY=3.")
    tips.append("Always run Ollama with OLLAMA_NO_CLOUD=1 (disables paid cloud models / web search).")
    return tips


def run_doctor() -> int:
    print("== AI Intelligence Hub doctor ==")
    print(f"OS:      {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"CPU:     {platform.processor() or 'unknown'}; logical cores: {os.cpu_count()}")
    ram = _ram_gb()
    print(f"RAM:     {ram:.1f} GiB" if ram else "RAM:     unknown")
    gpu, vram = _gpu()
    print(f"GPU:     {gpu}" + (f" ({vram:.1f} GiB VRAM)" if vram else ""))
    du = shutil.disk_usage(Path.cwd())
    print(f"Disk:    {du.free / 1024**3:.1f} GiB free of {du.total / 1024**3:.1f} GiB")
    for tool in ("python3", "node", "pnpm", "psql", "pg_dump", "docker", "ollama"):
        path = shutil.which(tool)
        ver = _run([tool, "--version"]).splitlines()[0] if path else ""
        print(f"{tool:8} {'found: ' + ver if path else 'MISSING'}")
    # database + ollama
    try:
        from sqlalchemy import text

        from hub.db import session_scope

        with session_scope() as s:
            v = s.execute(text("SHOW server_version")).scalar()
            ext = s.execute(text("SELECT extname FROM pg_extension")).scalars().all()
        print(f"Postgres: OK (server {v}; extensions: {', '.join(ext)})")
    except Exception as e:  # noqa: BLE001
        print(f"Postgres: NOT reachable ({type(e).__name__}) — check HUB_DATABASE_URL / service")
    try:
        from hub.ai.providers import OllamaProvider
        from hub.config import get_settings

        h = OllamaProvider(get_settings().ollama_url).health()
        print(f"Ollama:  {'OK' if h.ok else 'unavailable'} — {h.detail}; local models: {', '.join(h.models) or 'none'}"
              + (f"; cloud models ignored: {', '.join(h.blocked_models)}" if h.blocked_models else ""))
    except Exception as e:  # noqa: BLE001
        print(f"Ollama:  blocked/unavailable ({e})")
    print("\nRecommendations:")
    for t in recommend(ram, vram, "Apple" in gpu):
        print(" -", t)
    print(" - The hub works without AI; summaries/embeddings simply queue until a local model is available.")
    return 0
