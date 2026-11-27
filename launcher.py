# -*- coding: utf-8 -*-
"""ReplayRadio 独立客户端外壳。

一个原生窗口 + 一个内嵌后端进程，对外只有一个可执行文件：
  - 以 `--serve` 参数启动时，本程序退化为后端宿主，用内嵌解释器执行 app/tools/serve.py；
  - 不带参数启动时，本程序是窗口宿主，负责找端口、拉起后端、开 WebView2 窗口。

这样打包后不需要用户单独装 Python，也不需要 `python tools/serve.py`。
"""

import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "ReplayRadio"
WINDOW_TITLE = "噼里啪啦"
DEFAULT_PORT = 8765
PORT_SCAN = 30
BOOT_TIMEOUT = 40
MUTEX_NAME = "Local\\ReplayRadioClient.SingleInstance"
STATE_FILE = "client_state.json"
CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

def app_root() -> Path:
    """打包后是解包目录（onedir 下即 _internal），开发时是脚本所在目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def backend_dir() -> Path:
    """原项目根目录（tools/serve.py 所在处）。

    开发态在 ./app；打包后按上游约定铺在 _MEIPASS 根，所以两种都探一下。
    """
    for candidate in (app_root() / "app", app_root()):
        if (candidate / "tools" / "serve.py").is_file():
            return candidate
    return app_root() / "app"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------
# 后端：以子进程身份重新启动自己，走 --serve 分支
# --------------------------------------------------------------------------

def run_backend(argv) -> None:
    """子进程入口：在当前进程内执行原项目的 serve.py。"""
    root = backend_dir()
    serve_py = root / "tools" / "serve.py"
    if not serve_py.is_file():
        raise SystemExit(f"找不到后端脚本: {serve_py}")

    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import runpy

    sys.argv = [str(serve_py)] + list(argv)
    runpy.run_path(str(serve_py), run_name="__main__")


def backend_command(port: int):
    # --no-browser 必须带：不带的话 serve.py 会自己再开一个系统浏览器
    tail = ["--port", str(port), "--no-browser"]
    if getattr(sys, "frozen", False):
        # 打包后：同一个 exe，带 --serve 再启动一次
        return [sys.executable, "--serve"] + tail
    # 开发态：用当前解释器直接跑 serve.py
    serve_py = backend_dir() / "tools" / "serve.py"
    return [sys.executable, str(serve_py)] + tail


def spawn_backend(port: int) -> subprocess.Popen:
    log_path = data_dir() / "backend.log"
    log = open(log_path, "ab", buffering=0)
    kwargs = {
        "cwd": str(backend_dir()),
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    proc = subprocess.Popen(backend_command(port), **kwargs)
    proc._log = log  # 保活，避免被回收
    return proc


# --------------------------------------------------------------------------
# 端口与就绪探测
# --------------------------------------------------------------------------

def pick_port() -> int:
    """自己先占一个空闲端口再交给后端，避免后端顺延后窗口猜不到端口。"""
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"{DEFAULT_PORT}~{DEFAULT_PORT + PORT_SCAN} 全部被占用")


def probe(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def scan_ports(start: int, span: int = 6) -> int:
    for port in range(start, start + span):
        if probe(port):
            return port
    return 0


def wait_ready(proc: subprocess.Popen, port: int) -> int:
    """返回后端真正监听的端口；0 表示没起来。

    后端自己发现端口被占时会顺延（官方逻辑），所以这里要跟着扫一段，
    不能死认传进去的那个。
    """
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        hit = scan_ports(port)
        if hit:
            return hit
        if proc.poll() is not None:
            # 子进程提前退出：可能是已有实例在跑（serve.py 会直接 return 0）
            return scan_ports(DEFAULT_PORT, PORT_SCAN)
        time.sleep(0.3)
    return 0


def port_from_log(log_path: Path) -> int:
    """兜底：后端若自己顺延端口，从日志里捞出来。"""
    try:
        text = log_path.read_text("utf-8", errors="ignore")[-4000:]
    except OSError:
        return 0
    hits = re.findall(r"127\.0\.0\.1:(\d{4,5})", text)
    return int(hits[-1]) if hits else 0


# --------------------------------------------------------------------------
# 单实例
# --------------------------------------------------------------------------

def acquire_mutex():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    already = kernel32.GetLastError() == 183
    return handle, already


def read_state() -> dict:
    try:
        return json.loads((data_dir() / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(payload: dict) -> None:
    try:
        (data_dir() / STATE_FILE).write_text(json.dumps(payload), "utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------
# 窗口
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    try:
        with open(data_dir() / "launcher.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def show_error(text: str) -> None:
    log("ERROR " + text.replace("\n", " | "))
    ctypes.windll.user32.MessageBoxW(None, text, WINDOW_TITLE, 0x10)


def open_window(url: str) -> None:
    import webview

    # WebView2 默认把用户数据放在 exe 旁边，装到 Program Files 就不可写。
    # 官方支持用这个环境变量改位置，必须在 create 之前设置。
    os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(data_dir() / "WebView2"))

    webview.create_window(
        WINDOW_TITLE,
        url,
        width=1440,
        height=900,
        min_size=(1024, 640),
        confirm_close=False,
    )
    # private_mode=False 才会保留 localStorage（主题、看的板块等设置）
    webview.start(private_mode=False, debug=bool(os.environ.get("RR_DEBUG")))


def main() -> int:
    mutex, already = acquire_mutex()
    if already:
        state = read_state()
        port = int(state.get("port") or 0)
        log(f"已有实例，复用端口 {port}")
        if port and probe(port):
            open_window(f"http://127.0.0.1:{port}/")
            return 0
        show_error("检测到本程序已在运行，但它的本地服务没有响应。\n请先在任务管理器结束 ReplayRadio 进程后重试。")
        return 1

    log_path = data_dir() / "backend.log"
    proc = None
    try:
        port = pick_port()
        proc = spawn_backend(port)
        real = wait_ready(proc, port) or port_from_log(log_path)
        if not real:
            show_error(
                "本地服务启动失败。\n\n"
                f"日志：{log_path}\n"
                "常见原因：端口被占用、防火墙拦截，或 app\\tools\\serve.py 缺失。"
            )
            return 2
        port = real

        write_state({"port": port, "pid": proc.pid})
        log(f"后端就绪 http://127.0.0.1:{port}/ (pid={proc.pid})，打开窗口")
        open_window(f"http://127.0.0.1:{port}/")
        log("窗口已关闭，清理后端")
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        write_state({})
        ctypes.windll.kernel32.ReleaseMutex(mutex)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        run_backend([a for a in sys.argv[1:] if a != "--serve"])
    else:
        import traceback
        try:
            sys.exit(main())
        except Exception:
            detail = traceback.format_exc()
            log(detail)
            show_error("启动失败：\n\n" + detail.strip().splitlines()[-1] +
                       "\n\n详细日志：" + str(data_dir() / "launcher.log"))
            sys.exit(3)
