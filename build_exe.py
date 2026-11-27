# -*- coding: utf-8 -*-
"""把外壳 + 原项目代码打成一个可双击运行的发行目录。

资源布局与上游 tools/build_exe.py 完全一致（_MEIPASS 根放网页文件、tools/ 放脚本），
因为 serve.py 的 frozen 分支硬编码了这套约定：它会从 _MEIPASS 释放网页到
%LOCALAPPDATA%\\ReplayRadio\\www，并从 _MEIPASS/tools/ 动态加载分段模块。

与上游唯一的区别：PyInstaller 入口从 serve.py 换成 launcher.py，serve.py 作为数据文件
由 launcher 用 runpy 执行。这样才有原生窗口，而不是拉起系统浏览器。

用法：
    python build_exe.py
产物：
    dist/ReplayRadio/ReplayRadio.exe
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
BUILD = ROOT / "build"

WEB = ["index.html", "favicon.ico", "favicon.png", "assets", "data"]
TOOLS = ["serve.py", "auto_segments.py", "seg_refine.py", "songcut.py", "collect.py"]


def check():
    missing = [n for n in WEB if not (APP / n).exists()]
    missing += [f"tools/{n}" for n in TOOLS if not (APP / "tools" / n).is_file()]
    if missing:
        raise SystemExit("缺少文件，先确认 app/ 已就位：\n  - " + "\n  - ".join(missing))


def stage_version() -> Path:
    BUILD.mkdir(exist_ok=True)
    p = BUILD / "www_version.txt"
    p.write_text(time.strftime("%Y%m%d%H%M%S"), "utf-8")
    return p


def find_ffmpeg() -> str:
    try:
        sys.path.insert(0, str(APP / "tools"))
        from auto_segments import find_ffmpeg  # noqa: E402
        return find_ffmpeg() or ""
    except Exception:
        return ""


def main():
    check()
    ver = stage_version()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",                      # 必须 onedir：onefile 会让 --serve 子进程再解压一次
        "--noconsole",
        "--name", "ReplayRadio",
        "--distpath", str(BUILD / "dist"),
        "--workpath", str(BUILD / "pyi"),
        "--specpath", str(BUILD),
        "--hidden-import", "clr",
        "--hidden-import", "pythonnet",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "webview.platforms.winforms",
    ]

    for item in WEB:
        src = APP / item
        dest = "." if src.is_file() else item
        args += ["--add-data", f"{src}{os.pathsep}{dest}"]

    for name in TOOLS:
        args += ["--add-data", f"{APP / 'tools' / name}{os.pathsep}tools"]

    args += ["--add-data", f"{ver}{os.pathsep}."]

    icon = APP / "favicon.ico"
    if icon.is_file():
        args += ["--icon", str(icon)]

    ff = find_ffmpeg()
    if ff:
        args += ["--add-data", f"{ff}{os.pathsep}ffmpeg"]
        print(f"随包 FFmpeg：{ff}")
    else:
        print("！未找到 ffmpeg —— 自动分段/跳过空白不可用（其余功能正常）")

    args.append(str(ROOT / "launcher.py"))

    subprocess.run(args, cwd=ROOT, check=True)

    exe = BUILD / "dist" / "ReplayRadio" / "ReplayRadio.exe"
    print(f"\n完成：{exe}")
    print(f"目录大小：{sum(f.stat().st_size for f in exe.parent.rglob('*') if f.is_file()) / 1048576:.0f} MB")


if __name__ == "__main__":
    main()
