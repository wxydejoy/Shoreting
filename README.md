# 岸亭（Shoreting）

守岸人在漂泊者书桌上值班。横屏安卓是亭子的脸和字幕，Mac Mini Hub 当门口，LLM 写字，Mini 在本机喇叭说话。亭里还能看天气与室内温湿度、电脑监控，控制米家设备和书桌灯带，一键启动电脑上的白名单程序。

当前安卓 **0.4.6**。权威仓库在 Mac：`/Users/weiekko/dock/`，remote `git@github.com:wxydejoy/Shoreting.git`，分支 `main`。

![岸亭主屏](docs/screenshots/home.gif)

手机只通过局域网 HTTP 与 Hub 通信，不直连米家、LLM 或 TTS。协议是两边的唯一契约：

- [docs/companion.md](docs/companion.md) — **先读这个**：三台怎么接、怎么启动、听完 / 边写边念 / 打断、常见填反
- [docs/lan-protocol.md](docs/lan-protocol.md) — HTTP 说明、时序、验收
- [docs/openapi.yaml](docs/openapi.yaml) — OpenAPI 3

下面 IP 是**本宅当前地址**，换家请改。Hub `http://10.83.22.121:17890`（Mac Mini，Bearer token）。Windows 只跑探针 `10.83.22.31:17892`（CPU 上报 + 开程序，不是 Hub）。Mini 监控另开 `http://10.83.22.121:17891`（不是 Hub）。嘴在 Mini：TTS `:18100 --play`；大脑走 Hub 配置的 LLM（OpenAI 兼容，或 Mini Ollama `:11434`）。

## 仓库结构

| 目录 | 说明 |
|------|------|
| [android/](android/README.md) | 安卓客户端（Kotlin / Jetpack Compose） |
| [hub/](hub/README.md) | Hub（Mac Mini：米家 + 伴侣 + 配置向导）与 Windows 探针（CPU + 开程序 + 托盘 exe） |
| [mini/](mini/README.md) | Mac Mini 本机 CPU / 内存 / GPU（`:17891`，不是 Hub、不是 TTS） |
| [companion/tts/](companion/tts/README.md) | Mini 守岸人 TTS（`:18100 --play`） |
| [console/](console/README.md) | Mac 控制台与菜单栏：启停 Hub / Mini / TTS，看 Windows 在不在线 |
| [docs/](docs/) | 局域网协议与守岸人说明（改接口先改文档） |

## 安卓（客户端）

横屏全屏摆件，未配置 Hub 时进入**预览模式**（本地演示数据）。构建见 [android/README.md](android/README.md)。

### 主屏

- 大时钟 + 中文日期（多种字体与字号）
- **PC 监控条**：第一行 Windows CPU / 内存 / GPU / FPS，第二行 Mac Mini CPU / 内存 / GPU（约 3s 刷新）— 点击进入设置
- **左侧**：最多 3 个 Windows 应用快捷启动（Remix 图标）
- **右侧**：米家灯/开关 + **手机媒体控制**（上一首 / 播放暂停 / 下一首）
- **底部**：室外天气 + 室内温湿度
- 可选**图片 / 视频背景**（视频循环静音）；浏览器开设置页显示的 `:17920` 背景页，用 Hub Token 登录即可上传，上传即成为当前背景；方块样式（磨砂 / 深色 / 实心 / 细线）与透明度可调
- **语音唤醒**：说「小岸」听你说话；听完约 2.5s 后发给 Hub。字幕显示全文；Mini 在第一句写出后就开始念。她正在说时再喊「小岸」会打断并停播；她反问了一句会自动再听
- **主屏编排**：长按模块可拖动、缩放；可隐藏模块或去掉背景边框

### 设置

- Hub：**地址 / 端口 / Token**。填 Mini `10.83.22.121`、`17890`，不要填探针或 Mini 监控的地址
- Mac Mini：**默认地址 `10.83.22.121:17891`，默认 Token 已填**
- 视频背景：选择 / 更换 / 清除本地视频；也可用背景页面上传图片 / 视频
- 方块样式与字体排版预览
- **电脑关机后熄屏**、Hub 恢复后自动亮屏（需设备管理员，仅用于 `lockNow()`；插拔电源不再熄屏）
- 媒体控制需开启**通知使用权**

### 构建

```bash
cd android
./fetch-wake-word.sh   # 首次：下载「小岸」唤醒词与中文流式识别模型
./gradlew :app:assembleDebug
```

安装后首次打开为预览；在设置页填入 Hub 的 IP、`17890`、token 即可联机。

单元测试：

```bash
./gradlew :app:testDebugUnitTest
```

## Hub（Mac Mini）与 Windows 探针

完整说明见 [hub/README.md](hub/README.md)。

**Mac Mini 是 Hub（米家 + 伴侣 + 配置向导），LaunchAgent 开机自启：**

```bash
cd hub
uv sync
./install-launchd.sh
```

配置向导：`http://127.0.0.1:17890/setup`（本机直接开）。米家过期在向导里**扫码登录米家**，或 `uv run dock-hub --login`。

**Windows 只跑探针（CPU 上报 + 开程序，端口 17892，托盘 exe）：**

```powershell
cd hub
uv sync
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
# 右键 dist\DockHub.exe → 以管理员身份运行（计划任务 HelmDockHub 开机拉起）
```

配置：`~/.config/dock-hub/hub.yaml`（Mini 上；token、设备白名单、本机程序路径）。防火墙放行：Mini 入站 TCP 17890，Windows 入站 TCP 17892。CPU 温度需 **PawnIO**（可选，见 [hub/README.md](hub/README.md#pawnio-cpu-温度可选)）+ 管理员权限。

Mac 上日常启停这几个服务，用 [console/](console/README.md) 的控制台或菜单栏。

## 联机速查

完整顺序和排错见 [docs/companion.md](docs/companion.md)。

1. Mini 上装 Hub：`cd hub && uv sync && ./install-launchd.sh`。探活 `curl -s http://10.83.22.121:17890/health` 应为 `service=dock-hub`、`name=study`
2. Windows 跑探针：`uv run dock-hub --probe`。探活 `http://10.83.22.31:17892/health` 应为 `service=dock-probe`
3. 大脑：Hub 本机 `hub.yaml` 里配 LLM（OpenAI 兼容地址，或 Mini Ollama `:11434`）。`api_key` 只写本机 yaml，不要写进 git / 安卓 / 文档示例
4. Mini 上启动 [companion/tts](companion/tts/README.md)（LaunchAgent 自启，或前台 `--play`）
5. 安卓设置页 **Hub** 填 Mini `10.83.22.121`、端口 `17890`、token，点测试连接；**Mini CPU** 填 `10.83.22.121:17891`
6. 米家设备名在 Mini 侧用 `uv run dock-hub --list-devices` 核对

## 测试

改完别只靠手点。四件事，加起来一分钟：

```bash
# Hub：190 条，含协议、朗读、场景、长轮询
cd hub && uv run python -m unittest discover -s tests -t .

# Mini 监控 / 桌面控制台
cd mini && PYTHONPATH=. uv run python -m unittest discover -s tests -p 'test_*.py'
cd console && PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py'

# 接口文档和 server.py 有没有漂（只用标准库）
python3 scripts/check_protocol.py
```

安卓：`cd android && ./gradlew testDebugUnitTest`（需要 `app/libs/sherpa-onnx-*.aar`，`./fetch-wake-word.sh` 会取）。

`.github/workflows/ci.yml` 把这四条挂到了 push / PR 上。CI 验不了的只有三样：真机上的唤醒词识别、米家云、LLM 联调——这些只能上机。

## 许可

- Hub 依赖 [mijia-api](https://github.com/Do1e/mijia-api)（GPL-3.0）；分发 Hub 需同样开源
- 安卓内置字体见 `android/app/src/main/assets/font-licenses/`（OFL）
