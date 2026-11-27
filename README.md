# ReplayRadio Client

把 [DcC176/replay-radio](https://github.com/DcC176/replay-radio)（噼里啪啦）包成一个**真正的桌面客户端**：双击就出窗口，不起浏览器、不弹地址栏、不手动开端口。

原项目本身是「本地 Python 服务 + 浏览器网页」。这个仓库不改它的任何业务逻辑，只替换最外层：把浏览器换成 Windows 原生 WebView2 窗口，后端照旧。

## 与上游的差异

| | 上游 | 本仓库 |
|---|---|---|
| 启动 | 双击 EXE → 拉起默认浏览器 | 双击 EXE → 原生窗口（WebView2） |
| 界面 | 浏览器标签页 | 独立窗口，有任务栏图标 |
| 端口 | 固定 8765，占用顺延 | 外壳先探空闲端口再传给后端 |
| 单实例 | 二次启动会把浏览器指过去 | 具名互斥体，二次启动直接聚焦已有窗口 |
| 业务逻辑 | — | **完全不动**，`app/` 是原样代码 |

## 结构

```
launcher.py        外壳：找端口 → 拉起后端 → 开窗口 → 退出时收尾
build_exe.py       打包脚本（PyInstaller onedir）
app/               上游代码，保持原样，便于整体替换升级
```

`launcher.py` 打包后还是一个双角色程序：

- `ReplayRadio.exe` → 窗口宿主
- `ReplayRadio.exe --serve --port N --no-browser` → 后端宿主，用内嵌解释器 `runpy` 执行 `app/tools/serve.py`

这样分发时用户机器上不需要装 Python，也不需要两个 EXE。

## 构建

```bash
pip install -r requirements.txt
python build_exe.py
```

产物 `build/dist/ReplayRadio/ReplayRadio.exe`。

**必须用 onedir，不能 onefile**：onefile 每次启动要解压，而外壳会再启动一个自身进程跑后端，等于解压两次、占两份临时目录。

## 数据位置

与官方版一致，可继续用原有配置：

```
%LOCALAPPDATA%\ReplayRadio\
├── www\              网页文件（首次启动从 _internal 释放）
├── log.txt           后端日志
├── sessdata.txt      登录凭据
└── 使用说明.txt
```

## 已知限制

- **依赖 WebView2 Runtime**。Win11 自带；Win10 1809+ 一般有，缺了去 [微软官网](https://developer.microsoft.com/microsoft-edge/webview2/) 装 Evergreen Runtime。
- **不含 ffmpeg**，所以「自动分段 / 跳过空白」不可用。需要的话把 `ffmpeg.exe` 放到 `_internal/ffmpeg/` 后重新打包，或从上游 Releases 用带 ffmpeg 的官方包。
- 托盘图标由上游的「录播姬」逻辑按设置创建，和本窗口并存，属预期行为。
- 上游接口随 B 站变动，升级时把上游新代码整个替换掉 `app/` 即可，外壳无需改动。

## 许可

外壳代码 MIT。`app/` 下的代码与资源版权归上游作者，同为 MIT，见 `app/LICENSE`。内容版权归相应 UP 主，本工具不转存、不二次上传。
