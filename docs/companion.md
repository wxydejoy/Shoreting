# 守岸人 Companion

桌上的小人：K20 听你说话并出字幕，Mac Mini Hub 当门口，LLM 写字，Mini 在本机喇叭出声。

只做**在家**。手机只连 Hub（局域网 IP + token），不直连 LLM / TTS / 米家 / 在线 API。

改 HTTP 接口先改 [lan-protocol.md](./lan-protocol.md) 和 [openapi.yaml](./openapi.yaml)。空闲卸模型见 [companion-power.md](./companion-power.md)。

## 三台机器

下面 IP 是**本宅当前地址**。换网络请改成自己的，安卓默认值也在 `HubPreferences` / `MiniConnection` 里。

```
K20 岸亭（脸+耳+字幕）
   Bearer ──►  Mac Mini Hub :17890（门口）
                  ├─ 米家 / companion / 配置向导
                  ├─ 收 Windows 探针 CPU；开程序转发到 Windows :17892
                  ├─ chat → 配置的 LLM（本机 Ollama 或 OpenAI 兼容）
                  ├─ 天气 → Hub 代取（Open‑Meteo），手机无需外网
                  └─ cue  → Mini TTS :18100（本机喇叭；不把 wav 回给手机）

Windows 探针 :17892
                  ├─ POST Mini /v1/telemetry（CPU / 是否开机 / 正在运行）
                  └─ 接收 {run:true} 启动本机程序
```

| 谁 | 地址 | 干什么 | 不干什么 |
|---|---|---|---|
| **K20 安卓** | `10.83.22.150`，包名 `cn.weiekko.dock` | 主屏、点灯、开程序、听你说话、显示字幕 | 不播守岸人声音；不直连 LLM / TTS / 在线 API |
| **Mac Mini Hub** | **`10.83.22.121:17890`** `service=dock-hub` `name=study` | 米家、伴侣、配置；转发开程序 | 不跑 Windows 程序路径 |
| **Windows 探针** | **`10.83.22.31:17892`** `service=dock-probe` | CPU、开机探测、开程序 | 不上米家、不当 Hub |
| **Mac Mini 监控** | `10.83.22.121:17891` `service=helm-mini` | 主屏 CPU 块第二行 | **不是 Hub** |
| **Mac Mini 嘴** | TTS **`:18100 --play`** | 本机出声 | 不当 Hub；安卓不连它 |

**安卓设置：** Hub 栏填 Mini `10.83.22.121:17890`，Mini CPU 填 `10.83.22.121:17891`。健康检查若 `name=mini` 会拒绝。

Hub 在 Mini 上，`hub.yaml` 里 llm / tts 用 `127.0.0.1`。

## 启动顺序（本宅）

1. **Mini Hub**：`hub/install-launchd.sh`。探活：`curl -s http://10.83.22.121:17890/health` 应为 `service=dock-hub`、`name=study`。
2. **Windows 探针**：`uv run dock-hub --probe`。探活：`curl -s http://10.83.22.31:17892/health` 应为 `service=dock-probe`。停掉旧的 `HelmDockHub`。
3. **大脑**：默认走 Hub 本机 yaml 里的 OpenAI 兼容地址（如阿里云 MaaS `qwen3.8-flash`）。`api_key` 只写本机 yaml，不要写进 git / 安卓 / 文档示例。也仍可改回 Mini Ollama `:11434`。
4. **Mini 嘴**：见下方 TTS。TTS **必须**仍指向 Mini 本机喇叭。
5. **Mini 监控**（主屏第二行 CPU）：`mini/` 的 `helm-mini`，`:17891`。
6. **K20**：设置里 Hub 填 Mini IP / `17890` / token；Mini CPU 填 `10.83.22.121:17891`。

对话原文记在 Mini `~/.config/dock-hub/companion-chats.jsonl`。本机打开 `http://127.0.0.1:17890/chats`，局域网开 `http://10.83.22.121:17890/chats` 并填同一枚 Hub Token。朗读页：`http://127.0.0.1:17890/read`。

## 安卓

当前 debug 包 **versionName `0.4.6`**（`versionCode` 27）。主屏方块数量跟 Hub `devices[]` 一致，室内温湿度是传感器方块。

- 设置只填 Hub 的 host / port / token。不要填 LLM、TTS、在线 API Key
- Hub 占位默认 Mini `10.83.22.121`；粘贴 `IP:端口` 时会丢掉端口，避免拼成双端口
- `companion != null && ready`：主屏可点人物（时钟区域）说话；说「小岸」同样开听
- `null` 或 `ready == false`：不打开对话。`ready` 表示 LLM 探活成功（Ollama `/api/tags` 或 OpenAI 兼容 `/models`）
- `POST /v1/companion/chat`，超时 90s；可带 `turn_id`
- 默认 `tts.deliver: false`：Hub **边写边 cue** Mini TTS，HTTP 仍等全文再 `200` 给字幕
- 她正在说话时再喊「小岸」：立刻 `POST /v1/companion/stop`，Mini 喇叭停，旧轮不再开口，然后重新听
- 字幕显示 `text`。声音从 Mini 喇叭出，安卓不要播 wav
- 只有 `companion.tts.deliver: true` 时才整段合成、chat 才带 `audio_id`，手机才去拉音频

听写用流式 zipformer transducer（`sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`）+ `modified_beam_search`。热词按字空格；词表没有的字（岸、畏、契 等）整词会丢掉，避免 sherpa 整表失败。台灯/空调/开灯 这类常用字会加权。APK 会大很多（encoder 约 154MB）。听完仍由 `UtteranceGate` 决定，不是 sherpa 端点：

| 参数 | 当前 | 说明 |
|---|---:|---|
| 结束静音 | 1s | 太短会把停顿切成两句 |
| 最短句 | 1.5s | 太短会把「嗯」直接送走 |
| 空听超时 | 6.5s | 一直没出字 |
| 上限 | 16s | 再说也截 |

Hub 和 TTS 都跑在 Mini 上，`companion.tts.base_url` 填 `http://127.0.0.1:18100`（同一台机器，不用绕网络）；只有把 Hub 临时挪到别的机器调试时才写 `http://10.83.22.121:18100`。TTS 进程不要打进 `dock_hub` 包。`companion.llm` 可填 OpenAI 兼容网关，或仍填 Mini Ollama `http://10.83.22.121:11434`。

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
- `POST /v1/stop` → 立刻清缓冲并丢掉这一轮合成（再喊「小岸」走这条）
- `POST /v1/noise` `{name: rain|waves|fan|brown|off, minutes}` → 白噪音（F31）：四种声音全靠合成，循环播放，起播 2 秒淡入、到点前 45 秒淡出，默认 30 分钟；`name: "off"` 是停。音量压在 0.32，她说话时雨声当底
- `GET /v1/noise` → 现在放没放、放的什么、还剩几秒
- 安卓不要直连 `:18100`

## 一轮说话

```
喊「小岸」
  → K20 切 ASR，听完一句（约 2.5s）
  → POST Hub /v1/companion/chat
  → Hub 先 /v1/stop 清上一轮
  → LLM stream:true，按 。！？ 切句
  → 每句 POST Mini /v1/speak play_only（第一句就能开口）
  → chat HTTP 等全文 200，K20 出字幕
```

典型：喊「小岸」→ **Mini 开口约 4～5s**，字幕稍晚（等全文）。

| 阶段 | 大约 | 说明 |
|---|---:|---|
| 切 ASR | 0.5s | 跳过提示音 |
| 听完一句 | ~2.5s | 识别约 0.7s 出字；静音 1s + 最短 1.5s |
| 第一句开口 | 大脑写出第一句后约 0.5s | 按。！？切句 cue TTS；chat **不**先拉米家 |
| 字幕 | 全文写完 | chat HTTP 仍等 LLM 结束 |

`tts.deliver: true` 时仍整段合成，不边写边念，并把 wav 交给手机。本宅默认 false。

再喊「小岸」走 stop，不走完整这一轮。Hub 用序号丢掉旧轮还没 cue 的句子。

## 人设与技能

默认人设在 `hub/dock_hub/companion.py` 的 `DEFAULT_PERSONA`：慢、短、轻；1–3 句；可称「漂泊者」。

**对话不在开口时现拉米家。** Windows 约 1 秒、米家约 15 秒报到 Mini 落盘；Hub 把**已经在内存里的**书桌状态塞进守岸人提示词，避免模型再等一轮 HTTP。规则写在提示词里：对方没问灯、温度、占用、内存、显卡、某台机器时，一句都不要提。「电脑」默认指 Windows。问了才答室温、忙不忙、灯开了没、在播什么；报时用 Hub 本地时钟，不必等模型。口头「暂停 / 下一首 / 声音大一点 / 十分钟后叫我」走系统媒体键、音量键和进程内倒计时。最近 3～4 轮会进下一轮提示词，截断要狠。登录米家后，账号里**所有带开关属性的设备**都可以口头操作，不只是主屏那两块磁贴。主屏仍只显示 `hub.yaml` 里的灯/开关/启动项/灯带。书桌 **WS2812 灯带**（`type: ws2812`）走 ESP32 HTTP，口头「开灯带 / 关灯带」；Windows Hub 启动时跟着亮、关机或退出 Hub 时关掉。

1. 模型单独一行 `ACTION: lamp.off`、`ACTION: desk-led.off`、`ACTION: lamp.bri.40`、`ACTION: wegame.run`、`ACTION: media.next`、`ACTION: volume.up` 或 `ACTION: timer.10`（主屏设备用 yaml 的 id；其它米家设备是 `m` + did）
2. 4B 漏写 ACTION 时，Hub 从「关灯 / 开灯带 / 开空调 / 关掉加湿器 / 启动无畏契约 / 亮一点 / 下一首 / 十分钟后叫我」这类话里按**设备名或书桌动词**兜底

真正执行走 `POST /v1/devices/{id}/command`。窗帘、温湿度计等没有 on 类属性的仍不能口头开关。启动项仍要写进 `hub.yaml`。闲聊里的 ACTION 会丢掉。失败时不要假装「好。」。

情绪由 Hub 持有（`companion-mood.yaml`）：漂泊者此刻疲倦/烦/轻快，守岸人安静/担心/乏。只注入两三行中文，不写分数。叹气、打招呼不当成关灯。上一轮问过，这一轮不要再问。

守岸人默认不主动开口。倒计时是你定的，到点说「到时间了。」另有一个**白名单式的主动播报**（`proactive:` 段，默认整段关着）：到点说一句早间问候（问好 + 室温），一天一次；免打扰时段（默认 23:00–07:00）不说、每日配额（默认 6 次）用完不说、说之前还要过三道闸（手机在不在、在打游戏吗、伴侣开没开）——被拦下不算配额，下个到点的时刻还会再试。

另有**异常提醒**（`proactive.alerts`，跟主动播报共用每日配额）：Windows CPU 温度超过 `temp_celsius`（默认 85，热满 3 分钟才说）、内存占用超过 `memory_percent`（默认 90，满 3 分钟才说，「内存用到 94% 了，后台该收一收」）、米家设备离线超过 `offline_minutes`（默认 10 分钟）、连续用电脑超过 `sedentary_minutes`（默认 90，键鼠空闲 5 分钟就算休息过了，探针要在 Windows 上跑才报得了键鼠）各说一句，一天一件事最多一句；启动项、灯带、探针电脑不提醒——电脑关机是常态。阈值写 0 或 `alerts: false` 关掉。配置示例：

**天气突变**（`alerts.weather`，默认开）也走同一条路：每小时看一次逐小时预报，只在「变化」上开口——现在没下雨才提醒两小时内有雨/雪、气温要降 5 度以上、要起大风，一次最多挑一条（雨 > 雪 > 降温 > 大风），一天最多一句，只在 07:00–21:00 之间看。城市用手机最近一次查过天气的那个；手机从没查过天气就不提醒。预报拿不到就闭嘴，不重试到烦人。

**任务完成**（`alerts.finished`）是「她真的在看着你的电脑」的那句：探针每轮上报进程列表，谁不见了就记一笔「跑完了」，名单里的说一句「Blender 跑完了，用了 4 分钟」。名单 = 启动项里的进程（她本来就在盯着）+ `alerts.finished` 里额外点的名。免打扰、每日配额、一天一个进程最多一句照旧；进程名只在内存里路过，不落盘。

**回家问候**（`proactive.presence`，默认「回来了。」）：手机连上家里 WiFi 的那一刻（`POST /v1/presence`，安卓去抖后上报）说一句，只有 away → home 那个边沿说，一天来回几趟就说几句，但没有「欢迎回家」式的第二句。Hub 刚重启的第一报不问候。免打扰、每日配额、三道闸照走，被拦下不补发；`presence: false` 或把 `home_text` 置空就彻底不开口。

**回座问候**（同 `proactive.presence` 的 `desk_text`，默认「回来了。」）：摄像头看到人回到桌前、离座满 5 分钟（`POST /v1/at-desk`，安卓去抖后上报）说一句。画面不出设备、不存储，Hub 只收一个布尔值。`desk_text` 置空就只亮屏不说话——不想让「回家」和「坐下」各来一句，就把这处置空。

```yaml
proactive:
  daily_limit: 6
  quiet: ["23:00", "07:00"]
  morning:
    at: "07:30"
    # text: "早，记得喝水。"   # 不写就现拼：问好 + 室温
```

可以说「以后我说开黑就启动无畏契约」：Hub 把口令绑到**已有** id，写在 `%USERPROFILE%\.config\dock-hub\companion-aliases.yaml`（不要写进带 token 的 `hub.yaml`）。不能让模型自己填 exe 路径。

点灯仍可用主屏开关。启动项仍可点 logo。

场景（`scenes:`）配好后，除了喊口令和手机点磁贴，还能**专注联动**（`focus:`）：启动项里的程序一跑起来就自动切一个场景（写场景 id 或名字），退出来再切回另一个；检测跟守岸人判断「在打游戏」是同一套，所以游戏期间她照样不主动开口，`on` 场景里配的 `say` 会被不打扰闸拦下——想让她说，把那句话放进 `off` 场景。启动时游戏已经开着不算「刚开起来」，不会触发。

说「**我出门了**」不用配：她把所有能开关的设备（灯 / 插座 / 灯带）一台一台关掉，然后报告没关掉的——「台灯没关上，其他的都关了。」全关好就是「都关好了，3 个，路上小心。」启动项是「开」的东西、传感器没有开关，都不碰。这条也有 `POST /v1/leave` 接口，安卓想放个出门按钮直接打它。

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

配了 `api_key`、或 URL 含 `compatible-mode` / path 含 `/v1` 时走 OpenAI 兼容：`GET {base}/models` 探活（超时 ≥2s）、`POST {base}/chat/completions`。请求顶层带 `enable_thinking: false`（推理型模型默认开思考，不关则额度会被思考内容吃光、第一句 TTS 会极慢）。流式读 SSE `choices[0].delta.content`，忽略 `reasoning_content`。

未配密钥且 base 是 Ollama 端口（如 `:11434`）时仍走 `/api/tags` 与 `/api/chat`，`think: false`。TTS 始终 Mini，不要改成在线语音。

## 泰缇斯（已移除）

早期版本把「复杂任务」交给 Mini 上的 Hermes Agent（`:8642`），再由守岸人润色转述。
**现已整体移除**：Mac 上不再运行 Hermes，Hub 也不再做任何分流。

- 所有对话**直接**交给 `companion.llm`
- 少掉一次「简单还是复杂」的分类请求，首句更快
- 历史配置里残留的 `companion.tethys` 会被**安全忽略**
- 原服务、plist 与配置备份见 [companion/tethys/disabled/](../companion/tethys/disabled/README.md)

天气不再依赖泰缇斯：Hub 提供 `GET /v1/weather?city=…` 代取（见 [lan-protocol.md](lan-protocol.md)），手机在**纯局域网**也能显示天气。

## 常见问题

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 测试 Hub 成功，灯和对话失败 | Hub 栏填的是 Windows 探针（`:17892`）或 Mini 监控（`:17891`）的地址 | 改成 Mini `10.83.22.121:17890`，`/health` 要回 `service=dock-hub` |
| 有字幕没声音 | TTS 没 `--play`、没接音箱、或 Hub 没填 `tts.base_url` | Mini 上看 `/health` 的 `play` / `ready` |
| 说话一顿一顿 | 旧 TTS 在下一句合成时 `flush` 了上一句 | 用现在的 `server.py`：同轮只排队，只有 `/v1/stop` 才清空 |
| 开口总是「不知道」 | 伴侣模型没配好或探活失败；人设又禁止编造 | 在配置向导「伴侣大模型」里点**测试连接**确认可用 |
| 手机看不到天气 | 手机所在 Wi‑Fi 无外网（本宅 `P-5G` 就是纯局域网） | 天气默认由 **Hub 代取**；只要跑 Hub 的机器能上网即可。Hub 不可用才回落手机直连 |
| 问天气卡住、手机报超时 | 旧 Hub 先念「好。」时锁死；旧 APK 只等 30s | 重打现在的 Hub；K20 装 0.3.16（对话超时 90s） |
| 改了 companion.py 没变化 | 跑的是 exe | `build-exe.ps1` 后重启 `HelmDockHub` |
| TTS 502 `no attribute '_gate'` | 旧进程 | 停掉 `:18100` 再按上面的命令启动 |
| 开口很慢 | 听完静音仍偏长，或思考模式没关、LLM 整段才 cue | 确认 `enable_thinking: false`（OpenAI 兼容）或 `think: false`（Ollama），Hub 走 `stream: true` |

## 不要做的

- 安卓直连 LLM / `:18100` / 在线 API
- 把 Hub token、LLM `api_key`、米家 `auth.json` 写进文档或提交进 git
- 用 DeepSeek Harness 替换 Hub 当门口（技能若要接，也是 Hub 转发任务，脸和嘴仍走现在这条）
