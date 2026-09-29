"""Supervised desktop backend. stdout is a small, non-sensitive JSON protocol.

The web socket is bound to port zero once and passed directly to Waitress,
so there is no check-then-bind race or fixed web port. The Windows job contains
the database and background tasks; a crashed parent cannot leave orphans.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def send(kind, **payload):
    print(json.dumps({"type": kind, **payload}, ensure_ascii=True), flush=True)


def reserve_web_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    return listener


def acquire_lock(data):
    import msvcrt

    handle = (data / "desktop-backend.lock").open("a+b")
    handle.seek(0)
    if not handle.read(1):
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]  # Windows API
    except OSError:
        handle.close()
        raise RuntimeError("Another desktop backend owns this data directory") from None
    return handle


def own_windows_job():
    # Embedded Python does not process pywin32's post-install DLL relocation.
    dlls = Path(sys.executable).parent.parent / "site-packages/pywin32_system32"
    dll_directory = os.add_dll_directory(str(dlls)) if dlls.is_dir() else None  # type: ignore[attr-defined]  # Windows API
    import win32api
    import win32job

    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    if dll_directory is not None:
        dll_directory.close()
    return job


def load_environment(data):
    from dotenv import dotenv_values

    for name, value in dotenv_values(data / "config.env").items():
        if value is not None:
            os.environ[name] = value
    os.environ["POSTGRES_PORT"] = (data / "database-port.txt").read_text().strip()
    os.environ["FISH_MANAGER_DATA"] = str(data)
    os.environ["DJANGO_SETTINGS_MODULE"] = "app.config.settings.windows_release"
    os.environ["PYTHONUTF8"] = "1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    # Do not resolve junctions: PostgreSQL on Windows needs an ASCII executable path.
    root = args.app_root.absolute()
    data = args.data_dir.absolute()
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("FISH_DESKTOP_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("Desktop session token is missing")
    lock = acquire_lock(data)
    job = own_windows_job()
    stopping = threading.Event()
    children = {}
    files = []
    server = None
    web_thread = None
    listener = None
    prepared = False
    flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]  # Windows API

    def command(arguments, log_name, timeout=600):
        with (logs / log_name).open("ab") as log:
            return subprocess.run(
                arguments,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                timeout=timeout,
                check=False,
            ).returncode

    def control():
        try:
            for line in sys.stdin:
                if json.loads(line).get("action") == "stop":
                    break
        except (ValueError, OSError):
            pass
        stopping.set()

    threading.Thread(target=control, daemon=True).start()
    launcher = [
        str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(root / "scripts/windows_release.ps1"),
        "-AppRoot",
        str(root),
        "-DataDir",
        str(data),
        "-NoBrowser",
        "-NoErrorDialog",
    ]
    try:
        send(
            "progress", stage="正在检查数据并准备数据库", detail="首次启动可能需要几分钟，请稍候。"
        )
        prepared = True
        if command([*launcher, "-Action", "Prepare"], "desktop-prepare.log"):
            raise RuntimeError("Database preparation failed")
        if stopping.is_set():
            return
        load_environment(data)
        os.environ["FISH_DESKTOP_TOKEN"] = token
        metadata = root / "resources/app/package.json"
        if metadata.is_file():
            os.environ["RELEASE_VERSION"] = json.loads(metadata.read_text(encoding="utf-8"))[
                "version"
            ]
        listener = reserve_web_socket()
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        os.environ["PUBLIC_BASE_URL"] = address
        send("progress", stage="正在打开工作台", detail="数据已就绪，正在启动本机服务。")
        from django.core.wsgi import get_wsgi_application
        from waitress import create_server  # type: ignore[import-untyped]

        server = create_server(get_wsgi_application(), sockets=[listener], threads=6)
        web_thread = threading.Thread(target=server.run, daemon=True)
        web_thread.start()
        specs = {
            "desktop-worker": ["worker", "--pool=solo", "--concurrency=1", "--loglevel=INFO"],
            "desktop-beat": [
                "beat",
                "--schedule=" + str(data / "celerybeat-schedule"),
                "--loglevel=INFO",
            ],
        }

        def start(name):
            log = (logs / f"{name}.log").open("ab")
            files.append(log)
            children[name] = subprocess.Popen(
                [sys.executable, "-m", "celery", "-A", "app.config.celery", *specs[name]],
                cwd=data,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )

        for name in specs:
            start(name)
        send("ready", url=address)
        failures: dict[str, list[float]] = {name: [] for name in specs}
        while not stopping.wait(1):
            if not web_thread.is_alive():
                raise RuntimeError("The desktop web service exited")
            for name, child in list(children.items()):
                if child.poll() is not None:
                    now = time.monotonic()
                    failures[name] = [t for t in failures[name] if now - t < 60] + [now]
                    if len(failures[name]) > 3:
                        raise RuntimeError("A background service failed repeatedly")
                    send("progress", stage="正在恢复后台服务", detail="工作数据保留在本机。")
                    start(name)
                    send("ready", url=address)
    except Exception as exc:
        # Details stay in local diagnostics, never in the window or protocol.
        import traceback

        with (logs / "desktop-runtime.log").open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        send(
            "error",
            stage="启动暂未完成",
            detail="数据已保留。可以重试，或打开诊断文件夹。",
            code=type(exc).__name__,
        )
        raise SystemExit(1) from None
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for child in children.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        if server is not None:
            server.close()
        elif listener is not None:
            listener.close()
        if prepared:
            command([*launcher, "-Action", "Stop"], "desktop-stop.log", timeout=200)
        for log in files:
            log.close()
        lock.close()
        # The OS closes the job handle when this process exits, terminating any
        # remaining descendants. Keep it alive until all graceful cleanup ends.
        assert job is not None


if __name__ == "__main__":
    main()
