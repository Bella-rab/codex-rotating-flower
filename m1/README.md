# Bridge: link Codex to the motor

[简体中文](#中文说明)

The bridge watches your **local** Codex tasks on one computer. When a task starts, it sends a speed command to the ESP32 over USB. When all tasks finish, it sends `STOP`. If several tasks are active, it uses the fastest setting. It can reconnect if the USB port name changes.

It runs on macOS, Linux, and Windows with only the Python standard library. macOS and Linux use `stty` and `select`; Windows uses the Win32 serial API through `ctypes`.

Upload [the ESP32 firmware](../m0/motor_control/motor_control.ino) and test `PING`, `RUN LOW`, and `STOP` in Arduino Serial Monitor before running the bridge. Close Serial Monitor afterward; it cannot share the port with the bridge.

## Try it in Terminal

From the repository root:

```sh
python3 m1/global_bridge.py --once
python3 m1/global_bridge.py
```

On Windows, use `python` instead of `python3`:

```bat
python m1\global_bridge.py --once
python m1\global_bridge.py
```

`--once` prints the task state it sees without opening USB. The second command keeps running and searches for an ESP32 that answers `PING`. If you have several serial devices, add `--port` with the **actual** port name: `/dev/cu.usbmodemXXXX` on macOS or `COM3` on Windows. Press Ctrl+C to send `STOP` and exit.

## Start it automatically when you log in

Once the manual test works, install the login service for your system:

```sh
python3 m1/install_macos.py install   # macOS LaunchAgent
python m1\install_windows.py install  # Windows sign-in task
```

The macOS installer copies the bridge to `~/Library/Application Support/RotatingFlower/` and creates a LaunchAgent; its log is `~/Library/Logs/RotatingFlower/bridge.log`. The Windows installer copies the bridge to `%LOCALAPPDATA%\RotatingFlower\` and creates the `RotatingFlowerCodexMotor` sign-in task.

To update or remove it:

```sh
python3 m1/install_macos.py install --replace
python3 m1/install_macos.py uninstall

python m1\install_windows.py install --replace
python m1\install_windows.py uninstall
```

Both installers use your current Python executable, so keep that Python installation available. Uninstalling keeps the logs. The bridge looks for sessions in your `CODEX_HOME` folder (default `~/.codex`). You can pass `--sessions-root` if yours is elsewhere.

## Check that it works

1. Start a local Codex task at LOW. The log should show `Codex active: 1` and `Motor: RUN LOW`.
2. While it is still running, start another local task at HIGH. The motor should switch to HIGH.
3. When HIGH ends, the motor should return to LOW. It should stop after both tasks finish.

The bridge reads Codex's local task files. That file format can change, so retest after Codex updates. It handles local and worktree tasks; it does not handle cloud tasks. If a task record disappears, the bridge stops treating it as active after 10 minutes without updates. The ESP32 also stops the motor after 30 seconds without a `RUN` command.

## 中文说明

这个程序读取同一台电脑上的**本地** Codex 任务状态，通过 USB 向 ESP32 发送速度命令。多个任务同时运行时取最高档；全部结束后发送 `STOP`。USB 串口名称变化时，程序会尝试重新连接。

它可以在 macOS、Linux 和 Windows 上运行，只用 Python 标准库。macOS 和 Linux 用 `stty` 与 `select`；Windows 通过 `ctypes` 调用 Win32 串口 API。

先上传[电机固件](../m0/motor_control/motor_control.ino)，在 Arduino 串口监视器里手动试通 `PING`、`RUN LOW` 和 `STOP`，然后**关闭串口监视器**。

在仓库根目录运行：

```sh
python3 m1/global_bridge.py --once
python3 m1/global_bridge.py
```

Windows 上把 `python3` 换成 `python`：

```bat
python m1\global_bridge.py --once
python m1\global_bridge.py
```

`--once` 只显示识别到的任务状态，不连接电机。第二条命令会持续运行。如果连了多个串口设备，可以用 `--port` 指定实际端口：macOS 形如 `/dev/cu.usbmodemXXXX`，Windows 形如 `COM3`。按 Ctrl+C 会发送 `STOP` 并退出。

确认手动运行正常后，可以安装为登录后自动运行的用户服务：

```sh
python3 m1/install_macos.py install   # macOS LaunchAgent
python m1\install_windows.py install  # Windows 登录任务
```

macOS 安装器会把程序复制到 `~/Library/Application Support/RotatingFlower/`，日志在 `~/Library/Logs/RotatingFlower/bridge.log`。Windows 安装器会复制到 `%LOCALAPPDATA%\RotatingFlower\`，并创建名为 `RotatingFlowerCodexMotor` 的登录任务。

更新或卸载：

```sh
python3 m1/install_macos.py install --replace
python3 m1/install_macos.py uninstall

python m1\install_windows.py install --replace
python m1\install_windows.py uninstall
```

两个安装器都会使用你当前的 Python 路径，之后不要移除该 Python 安装。卸载会保留日志。程序按当前用户的 `CODEX_HOME`（默认 `~/.codex`）寻找任务记录；如果你的路径不同，可以传 `--sessions-root`。

测试跨窗口时，可以先启动 LOW 任务，再启动 HIGH 任务：电机应切到 HIGH；HIGH 结束而 LOW 仍在运行时，应回到 LOW；全部结束后停转。

程序依赖 Codex 的本地任务文件格式，Codex 更新后可能需要调整。它支持 Mac 和 Windows 上的本地与 worktree 任务，不支持云端任务。任务记录如果消失，10 分钟无更新后会失效；ESP32 收不到新的 `RUN` 命令满 30 秒也会停机。
