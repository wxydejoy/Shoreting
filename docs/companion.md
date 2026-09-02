# 守岸人 Companion

桌上的小人：K20 听你说话并出字幕，Windows Hub 当门口，LLM 写字，Mac Mini 在本机喇叭出声。

只做**在家**。手机只连 Hub（局域网 IP + token），不直连 LLM / TTS / 米家 / 在线 API。

改 HTTP 接口先改 [lan-protocol.md](./lan-protocol.md) 和 [openapi.yaml](./openapi.yaml)。空闲卸模型见 [companion-power.md](./companion-power.md)。

## 三台机器

下面 IP 是**本宅当前地址**。换网络请改成自己的，安卓默认值也在 `HubPreferences` / `MiniConnection` 里。

```
K20 岸亭（脸+耳+字幕）
   Bearer ──►  Windows Hub :17890（门口）
                  ├─ 米家 / 开程序 / pc / 媒体
                  ├─ chat → 配置的 LLM（Ollama 或 OpenAI 兼容，如阿里云 MaaS）
                  ├─ 复杂 → Mini Hermes API :8642（泰缇斯）→ 润色 → TTS
                  │            └─ 全文 → Mini :8643 → 微信
                  └─ cue  → Mini TTS :18100（本机喇叭；不把 wav 回给手机）
                  └─ stop → Mini TTS /v1/stop（再喊「岸宝」打断）

微信 iLink → Mini Hermes gateway（同一进程上的 :8642）
                  └─ 家居相关 → Hub /v1/companion/announce → 守岸人短播
```

| 谁 | 地址 | 干什么 | 不干什么 |
|---|---|---|---|
| **K20 安卓** | `10.83.22.150`，包名 `cn.weiekko.dock` | 主屏、点灯、开程序、听你说话、显示字幕 | 不播守岸人声音；不直连 LLM / TTS / 在线 API |
| **Windows Hub** | **`10.83.22.31:17890`** `service=dock-hub` `name=study` | 米家、Steam 等、电脑监控；把对话转给 LLM | 不跑模型、不合成语音 |
| **Mac Mini 监控** | `10.83.22.121:17891` `service=helm-mini` | 主屏 CPU 块第二行 | **不是 Hub** |
| **Mac Mini 嘴** | TTS **`:18100 --play`** | 本机出声 | 不当 Hub；安卓不连它。本地 Ollama 仅作可选备用大脑 |

**安卓设置不要填反：** Hub 栏填 Windows `10.83.22.31`，Mini 监控填 `10.83.22.121:17891`。Mini 上如果还开着一份 dock-hub `:17890`（`name=mini`），Hub 栏填错 IP 时健康检查会误报成功，真正拉米家 / 对话会失败。安卓若检测到 Hub 栏是 Mini 的 IP，测试连接会直接拒绝。

Hub 若改到 Mini 上跑，`hub.yaml` 里 llm / tts 才用 `127.0.0.1`。现在 Hub 在 Windows。

## 启动顺序（本宅）

1. **Windows**：管理员运行 `hub/dist/DockHub.exe`（计划任务名 `HelmDockHub`）。探活：`curl -s http://10.83.22.31:17890/health` 应为 `service=dock-hub`、`name=study`。
2. **大脑**：默认走 Hub 本机 yaml 里的 OpenAI 兼容地址（如阿里云 MaaS `qwen3.8-flash`）。`api_key` 只写本机 yaml，不要写进 git / 安卓 / 文档示例。也仍可改回 Mini Ollama `:11434`。
3. **Mini 嘴**：见下方 TTS。Mac Mini 没有内置喇叭，要接耳机或音箱。TTS **必须**仍指向 Mini `http://10.83.22.121:18100`。
4. **Mini 监控**（可选，主屏第二行 CPU）：`mini/` 的 `helm-mini`，`:17891`。
5. **K20**：设置里 Hub 填 Windows IP / `17890` / token；Mini 填 `10.83.22.121:17891`。主屏 `companion.ready` 后可点人物或喊「岸宝」。

对话原文从接上之后记在 Windows `%USERPROFILE%\.config\dock-hub\companion-chats.jsonl`。本机打开 `http://127.0.0.1:17890/chats`，局域网（Mac / 手机）开 `http://10.83.22.31:17890/chats` 并填同一枚 Hub Token。更早只记了字数的轮次不会出现在这里。

改 `hub/dock_hub/companion.py` 之后必须在 Windows **重新打 exe** 再启动。正在跑的是 `DockHub.exe`，只拷 `.py` 不会生效。

## 安卓

当前 debug 包 **versionName `0.3.15`**（`versionCode` 18）。横屏设置页两栏。

- 设置只填 Hub 的 host / port / token。不要填 LLM、TTS、在线 API Key
- Hub 占位默认 `10.83.22.31`；粘贴 `IP:端口` 时会丢掉端口，避免拼成双端口
- `companion != null && ready`：主屏可点人物（时钟区域）说话；说「岸宝」同样开听
- `null` 或 `ready == false`：不打开对话。`ready` 表示 LLM 探活成功（Ollama `/api/tags` 或 OpenAI 兼容 `/models`）
- `POST /v1/companion/chat`，超时 30s；可带 `turn_id`
- 默认 `tts.deliver: false`：Hub **边写边 cue** Mini TTS，HTTP 仍等全文再 `200` 给字幕
- 她正在说话时再喊「岸宝」：立刻 `POST /v1/companion/stop`，Mini 喇叭停，旧轮不再开口，然后重新听
- 字幕显示 `text`。声音从 Mini 喇叭出，安卓不要播 wav
- 只有 `companion.tts.deliver: true` 时才整段合成、chat 才带 `audio_id`，手机才去拉音频

听写用流式 zipformer transducer（`sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`）+ `modified_beam_search`。热词按字空格；词表没有的字（岸、畏、契 等）整词会丢掉，避免 sherpa 整表失败。台灯/空调/开灯 这类常用字会加权。APK 会大很多（encoder 约 154MB）。听完仍由 `UtteranceGate` 决定，不是 sherpa 端点：

| 参数 | 当前 | 说明 |
|---|---:|---|
| 结束静音 | 1s | 太短会把停顿切成两句 |
| 最短句 | 1.5s | 太短会把「嗯」直接送走 |
| 空听超时 | 6.5s | 一直没出字 |
| 上限 | 16s | 再说也截 |

Hub 在 Windows 时，`companion.tts.base_url` 填 Mini：`http://10.83.22.121:18100`。TTS 进程在 Mini 上跑，不要打进 `dock_hub` 包。`companion.llm` 可填 OpenAI 兼容网关，或仍填 Mini Ollama `http://10.83.22.121:11434`。

## Mini TTS

独立进程，见 [../companion/tts/README.md](../companion/tts/README.md)。

```bash
cd companion/tts
HF_HOME=.cache-base HF_HUB_OFFLINE=1 HF_HUB_DISABLE_XET=1 \
  .venv/bin/python server.py --host 0.0.0.0 --port 18100 \
  --ref-audio voices/shorekeeper/clone.wav --voice shorekeeper --play
```

- `POST /v1/speak` + `play_only: true` → **202**，本机边合成边播
- 同一轮可以多次 speak（边写边念）：音频接到**同一条播放队列**，下一句不要 `flush` 上一句
- `POST /v1/stop` → 立刻清缓冲并丢掉这一轮合成（再喊「岸宝」走这条）
- 安卓不要直连 `:18100`

## 一轮说话

```
喊「岸宝」
  → K20 切 ASR，听完一句（约 2.5s）
  → POST Hub /v1/companion/chat
  → Hub 先 /v1/stop 清上一轮
  → LLM stream:true，按 。！？ 切句
  → 每句 POST Mini /v1/speak play_only（第一句就能开口）
  → chat HTTP 等全文 200，K20 出字幕
```

典型：喊「岸宝」→ **Mini 开口约 4～5s**，字幕稍晚（等全文）。

| 阶段 | 大约 | 说明 |
|---|---:|---|
| 切 ASR | 0.5s | 跳过提示音 |
| 听完一句 | ~2.5s | 识别约 0.7s 出字；静音 1s + 最短 1.5s |
| 第一句开口 | 大脑写出第一句后约 0.5s | 按。！？切句 cue TTS；chat **不**先拉米家 |
| 字幕 | 全文写完 | chat HTTP 仍等 LLM 结束 |

`tts.deliver: true` 时仍整段合成，不边写边念，并把 wav 交给手机。本宅默认 false。

再喊「岸宝」走 stop，不走完整这一轮。Hub 用序号丢掉旧轮还没 cue 的句子。

## 人设与技能

默认人设在 `hub/dock_hub/companion.py` 的 `DEFAULT_PERSONA`：慢、短、轻；1–3 句；可称「漂泊者」。

**对话不在开口时现拉米家。** Windows 约 1 秒、米家约 15 秒报到 Mini 落盘；Hub 把**已经在内存里的**书桌状态塞进守岸人提示词，避免模型再等一轮 HTTP。规则写在提示词里：对方没问灯、温度、占用、内存、显卡、某台机器时，一句都不要提。「电脑」默认指 Windows。问了才答室温、忙不忙、灯开了没、在播什么；报时用 Hub 本地时钟，不必等模型。口头「暂停 / 下一首 / 声音大一点 / 十分钟后叫我」走系统媒体键、音量键和进程内倒计时。最近 3～4 轮会进下一轮提示词，截断要狠。登录米家后，账号里**所有带开关属性的设备**都可以口头操作，不只是主屏那两块磁贴。主屏仍只显示 `hub.yaml` 里的灯/开关/启动项。

1. 模型单独一行 `ACTION: lamp.off`、`ACTION: lamp.bri.40`、`ACTION: wegame.run`、`ACTION: media.next`、`ACTION: volume.up` 或 `ACTION: timer.10`（主屏设备用 yaml 的 id；其它米家设备是 `m` + did）
2. 4B 漏写 ACTION 时，Hub 从「关灯 / 开空调 / 关掉加湿器 / 启动无畏契约 / 亮一点 / 下一首 / 十分钟后叫我」这类话里按**设备名或书桌动词**兜底

真正执行走 `POST /v1/devices/{id}/command`。窗帘、温湿度计等没有 on 类属性的仍不能口头开关。启动项仍要写进 `hub.yaml`。闲聊里的 ACTION 会丢掉。失败时不要假装「好。」。

情绪由 Hub 持有（`companion-mood.yaml`）：漂泊者此刻疲倦/烦/轻快，守岸人安静/担心/乏。只注入两三行中文，不写分数。叹气、打招呼不当成关灯。上一轮问过，这一轮不要再问。

主动开口只由 **Windows Hub** 决定，K20 / Mini 不自己找人。电脑开没开只看 Hub 自己还在不在（进程在说话就是开机），**不用米家、也不用 `pc.online` 采样**。其余：K20 最近约 90 秒内打过认证 snapshot（或刚说过话）、启动项没在跑、本地时钟在 23:00–05:00。打游戏、手机没连上、Hub 没起来，永远不先开口。模板一句、不问句；倒计时是你定的，不受这道门限制。

可以说「以后我说开黑就启动无畏契约」：Hub 把口令绑到**已有** id，写在 `%USERPROFILE%\.config\dock-hub\companion-aliases.yaml`（不要写进带 token 的 `hub.yaml`）。不能让模型自己填 exe 路径。

点灯仍可用主屏开关。启动项仍可点 logo。

## Hub 配置要点

`%USERPROFILE%\.config\dock-hub\hub.yaml`（不要把 token 提交进仓库）：

```yaml
companion:
  enabled: true
  llm:
    # OpenAI 兼容（阿里云 MaaS 等）。api_key 只写本机 yaml，不要提交。
    base_url: "https://<endpoint>/compatible-mode/v1"
    model: qwen3.8-flash
    api_key: "sk-..."
    timeout_sec: 45
    # 本地 Ollama 备用：去掉 api_key，改成 http://10.83.22.121:11434 与 qwen3.5:4b
  tts:
    base_url: "http://10.83.22.121:18100"
    timeout_sec: 20
    # deliver: true   # 才把 wav 交给手机。默认 false
```

配了 `api_key`、或 URL 含 `compatible-mode` / path 含 `/v1` 时走 OpenAI 兼容：`GET {base}/models` 探活（超时 ≥2s）、`POST {base}/chat/completions`。请求顶层带 `enable_thinking: false`（qwen3.8-flash 默认开思考，不关则第一句 TTS 会极慢）。流式读 SSE `choices[0].delta.content`，忽略 `reasoning_content`。

未配密钥且 base 是 Ollama 端口（如 `:11434`）时仍走 `/api/tags` 与 `/api/chat`，`think: false`。TTS 始终 Mini，不要改成在线语音。

## 泰缇斯（Tethys / Hermes）

守岸人管快答与家居；**泰缇斯**是 Mini 上的 Hermes Agent（`:8642`），只管复杂推理。Hub 自动分流：

| 路径 | 谁 | 例子 |
|---|---|---|
| 守岸人 | `companion.llm`（Flash 等） | 「晚上好」「关灯」「开抖音」 |
| 泰缇斯 | 守岸人调度为复杂 → Hermes → 守岸人润色 | 需查资料、分析、长推理（由守岸人判断） |

**路由**：开启 `tethys` 时，每轮先由守岸人 LLM 回复一行 `ROUTE: shore|tethys`；家居/启动口语不经调度直接走守岸人。书桌复杂任务的 **Hermes 记忆**用 `tethys.session_key`（默认 `tethys:wanderer`），不要和微信 `weixin:dm:…` 合成一份 transcript。全文另发微信；微信普通聊天不会念到喇叭，只有家居/书桌相关才 `POST /v1/companion/announce`。

安装与 `hub.yaml` 片段见 [companion/tethys/README.md](../companion/tethys/README.md)。`tethys.speak: false` 时只出字幕不播报；`ack: false` 关闭复杂任务前的「好。」；`weixin_notify: false` 关闭同步微信。

## 常见问题

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 测试 Hub 成功，灯和对话失败 | Hub 栏填了 Mini 的 IP，连到 Mini 上另一份 dock-hub | 改成 Windows `10.83.22.31`；不要在 Mini 上再开 `:17890` 的 dock-hub |
| 有字幕没声音 | TTS 没 `--play`、没接音箱、或 Hub 没填 `tts.base_url` | Mini 上看 `/health` 的 `play` / `ready` |
| 说话一顿一顿 | 旧 TTS 在下一句合成时 `flush` 了上一句 | 用现在的 `server.py`：同轮只排队，只有 `/v1/stop` 才清空 |
| 每句都从「台灯」开头 | 模型没守「没问不提」 | 提示词已写明禁止主动提起缓存状态；需重打 Windows exe |
| 改了 companion.py 没变化 | 跑的是 exe | `build-exe.ps1` 后重启 `HelmDockHub` |
| TTS 502 `no attribute '_gate'` | 旧进程 | 停掉 `:18100` 再按上面的命令启动 |
| 开口很慢 | 听完静音仍偏长，或思考模式没关、LLM 整段才 cue | 确认 `enable_thinking: false`（OpenAI 兼容）或 `think: false`（Ollama），Hub 走 `stream: true` |

## 不要做的

- 安卓直连 LLM / `:18100` / 在线 API
- 把 Hub token、LLM `api_key`、米家 `auth.json` 写进文档或提交进 git
- 用 DeepSeek Harness 替换 Hub 当门口（技能若要接，也是 Hub 转发任务，脸和嘴仍走现在这条）
