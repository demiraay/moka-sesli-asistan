from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    """Proje kokundeki .env dosyasini os.environ'a yukler (mevcut degerleri ezmez).
    Boylece Node botu gibi dotenv kullanmayan cocuk surecler de ayni degerleri gorur."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as error:
        print(f"[server] .env okunamadi: {error}")


PYTHON_IMPORT_CHECK = (
    "import flask, dotenv; "
    "print('ok')"
)


def _python_supports_project(python_executable: str) -> bool:
    try:
        result = subprocess.run(
            [python_executable, "-c", PYTHON_IMPORT_CHECK],
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and "ok" in result.stdout


def _resolve_service_python() -> str:
    candidates: list[str] = []

    conda_prefix = os.getenv("CONDA_PREFIX", "").strip()
    if conda_prefix:
        candidates.extend(
            [
                str(Path(conda_prefix) / "bin" / "python"),
                str(Path(conda_prefix) / "bin" / "python3"),
            ]
        )

    candidates.extend(
        [
            sys.executable,
            "python",
            "python3",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if _python_supports_project(candidate):
            return candidate

    return sys.executable


SERVICE_PYTHON = _resolve_service_python()


SERVICES = [
    {
        "name": "admin",
        "command": [SERVICE_PYTHON, "scripts/run_admin_panel.py"],
        "cwd": BASE_DIR,
        "env": {
            "ADMIN_DEBUG": "false",
        },
        "ready_url": "http://127.0.0.1:5050/admin",
    },
    {
        "name": "bridge",
        "command": [SERVICE_PYTHON, "scripts/run_whatsapp_bridge.py"],
        "cwd": BASE_DIR,
        "env": {
            "WHATSAPP_BRIDGE_DEBUG": "false",
        },
        "ready_url": "http://127.0.0.1:5051/whatsapp/health",
    },
    {
        "name": "whatsapp-bot",
        "command": ["npm", "start"],
        "cwd": BASE_DIR / "whatsapp_mesaj_bot",
        "env": {},
    },
]


def _stream_output(service_name: str, pipe) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            print(f"[{service_name}] {line.rstrip()}")
    finally:
        pipe.close()


def _terminate_process(process: subprocess.Popen[str], service_name: str) -> None:
    if process.poll() is not None:
        return

    print(f"[server] {service_name} durduruluyor...")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print(f"[server] {service_name} zorla kapatiliyor...")
        process.kill()
        process.wait(timeout=5)


def _wait_for_http(url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.5)
    return False


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


def _preflight_node_modules_check() -> bool:
    bot_dir = BASE_DIR / "whatsapp_mesaj_bot"
    if (bot_dir / "node_modules").is_dir():
        return True
    print("[server] whatsapp_mesaj_bot/node_modules bulunamadi.")
    print("[server] Once su komutu calistirin: cd whatsapp_mesaj_bot && npm install")
    return False


def _preflight_port_check() -> bool:
    required_ports = [("admin", "127.0.0.1", 5050), ("bridge", "127.0.0.1", 5051)]
    blocked = [(name, host, port) for name, host, port in required_ports if _port_is_open(host, port)]
    if not blocked:
        return True

    for name, host, port in blocked:
        print(f"[server] {name} portu dolu: {host}:{port}")
    print("[server] Once eski processleri kapatip tekrar deneyin.")
    return False


def main() -> int:
    processes: list[tuple[dict, subprocess.Popen[str]]] = []

    _load_env_file()
    print(f"[server] Python executable: {sys.executable}")
    print(f"[server] Service Python: {SERVICE_PYTHON}")

    # WhatsApp ikincil kanal: Node kurulmamissa bot atlanir, sesli demo etkilenmez.
    services = list(SERVICES)
    if not _preflight_node_modules_check():
        print("[server] WhatsApp botu ATLANIYOR — sesli asistan ve panel tam calisir.")
        services = [s for s in services if s["name"] != "whatsapp-bot"]
    if not _preflight_port_check():
        return 1

    for service in services:
        env = os.environ.copy()
        env.update(service["env"])

        try:
            process = subprocess.Popen(
                service["command"],
                cwd=service["cwd"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as error:
            print(f"[server] {service['name']} baslatilamadi: {error}")
            for started_service, started_process in reversed(processes):
                _terminate_process(started_process, started_service["name"])
            return 1

        processes.append((service, process))
        thread = threading.Thread(
            target=_stream_output,
            args=(service["name"], process.stdout),
            daemon=True,
        )
        thread.start()
        print(f"[server] {service['name']} baslatildi.")

        ready_url = service.get("ready_url")
        if ready_url:
            print(f"[server] {service['name']} hazir olmasi bekleniyor: {ready_url}")
            if not _wait_for_http(ready_url):
                print(f"[server] {service['name']} health-check gecemedi.")
                for started_service, started_process in reversed(processes):
                    _terminate_process(started_process, started_service["name"])
                return 1
            print(f"[server] {service['name']} hazir.")

    stop_requested = False

    def shutdown_handler(signum, frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        print(f"\n[server] Sinyal alindi ({signum}). Tum servisler kapatilacak...")

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        while not stop_requested:
            for service, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"[server] {service['name']} cikti (code={return_code}). Tum servisler kapatilacak...")
                    stop_requested = True
                    break
            time.sleep(0.5)
    finally:
        for service, process in reversed(processes):
            _terminate_process(process, service["name"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
