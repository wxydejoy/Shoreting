# Mini TTS（守岸人）

独立进程，跑在 **Mac Mini**。不要 `import dock_hub`。Hub 当客户端打 `POST /v1/speak`、`POST /v1/stop`。安卓不直连本服务。

默认：本地 `models/Qwen3-TTS-12Hz-1.7B-Base-8bit`（Qwen3-TTS 12Hz 1.7B Base 8bit / MLX）+ 官方包片段克隆（`voices/shorekeeper/clone.wav` + 同名 `.lab`）。0.6B 6bit 仍可用 `--model mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit` 加 `HF_HOME=.cache-base` 退回。

Mac Mini 没有内置喇叭，`--play` 需要耳机或外接音箱。

## 启动

日常用 LaunchAgent（开机自启，不占 Cursor 终端）：

```bash
cd companion/tts
uv sync   # 首次
bash ./install-launchd.sh
```

探活：`curl -s http://127.0.0.1:18100/health`。需要 `ready: true`、`play: true`、`clone: true`。日志：`companion/tts/tts.log`。

临时前台调试才用下面这组环境变量；平时不要在 Cursor 里挂着跑。

```bash
cd companion/tts
export HF_HOME=.cache-1.7b-8bit
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_XET=1
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
.venv/bin/python server.py --host 0.0.0.0 --port 18100 \
  --model models/Qwen3-TTS-12Hz-1.7B-Base-8bit \
  --ref-audio voices/shorekeeper/clone.wav --voice shorekeeper --play
```

探活：`curl -s http://127.0.0.1:18100/health`。需要 `ready: true`、`play: true`、`clone: true`。

克隆参考文案来自同目录 `clone.lab`。换参考音频时两边一起换。Serena / CustomVoice：`--no-clone` 并改 `--model`。

## 接口

无鉴权（只应听局域网）。请求体 JSON，最大约 16KB。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | `ok` / `ready` / `play` / `clone` / `voice` / `error` |
| `POST` | `/v1/speak` | `text`（必填）、`voice`、`language`、`turn_id`。`play_only: true` 时立刻 **202**，合成在后台线程里播。`play: false` 时只合成 wav、不走喇叭（Hub 生成文件用） |
| `POST` | `/v1/stop` | 立刻停播、丢掉这一轮还没播完的缓冲；正在合成的那轮会中断 |
| `GET`/`POST` | `/v1/noise` | F31 白噪音：`{"name":"rain","minutes":30}`，`name:"off"` 停；到点自动淡出 |
| `GET`/`POST` | `/v1/music` | F32 本地电台：`{"minutes":60}` 放、`{"action":"off"}` 停；目录没歌回 `ok:false, error:"empty"` |

## 本地电台（F32）

往 `companion/tts/music/`（或 `--music-dir` 指的目录）里丢 mp3/flac/ogg，
对她说「放点音乐」就随机洗牌播放，一轮放完再洗一轮；默认一小时后自动淡出。
音乐和白噪音共用喇叭，谁后到谁说了算；她说话时音乐只是底，音量压在 0.32。

`play_only: false`（或不带）时同步合成，**200** `audio/wav`。Hub 默认 `tts.deliver: false`，走 `play_only`，不把 wav 交给手机。

## 边写边念（播放队列）

Hub 会按句多次 `/v1/speak`（同一 `turn_id`）。同轮各句接到**同一条播放队列**：下一句开始合成时**不要** `flush` 上一句还没播完的尾巴，否则听起来会一顿一顿。

只有 `/v1/stop`（再喊「小岸」）才清空缓冲并加代次，旧轮合成看到代次变了就停。

## 本机自测

```bash
curl -s http://127.0.0.1:18100/health
curl -s -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"text":"岸边很安静。","turn_id":"cue-1","play_only":true}' \
  http://127.0.0.1:18100/v1/speak
# 应为 202，Mini 喇叭出声
curl -s -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:18100/v1/stop
```

## 和岸亭的关系

完整拓扑、Hub 怎么 cue、安卓为什么不播 wav：见 [docs/companion.md](../../docs/companion.md)。
