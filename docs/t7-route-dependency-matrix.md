# T7 阶段一 · server.py 路由依赖矩阵盘点

> 盘点日期：2026-11-02 · 对象：岸亭仓库 hub-slim 分支（@e03e312）`hub/dock_hub/server.py`（757 行）
> 方法：通读 server.py 全部路由分支；对 android/、console/、mini/、companion/、scripts/ 全量
> grep 路由字面量并逐条回读调用上下文（Kotlin/Swift/Python/TS/Vue/Shell）；
> 对 dock_hub 内部模块做 import 图扫描。
> 本文件只盘点不裁剪；「真机抓包确认」类结论见各行的 ⏸ 标注。

## 0. 一句话结论

server.py 共 **36 个路由面**（GET 15 + POST 19 + DELETE 2 组）。真正的「现役三支柱」是：

1. `/v1/snapshot`（含长轮询）——安卓 k20 主屏的唯一状态通道，**保留**（T2 k20 插件将重实现协议客户端，hub-lite 保留服务端）；
2. `/setup/*` + `/v1/companion/*` 的读面——console web 的全部页面数据源，**随 console web 冻结**，第三批退役；
3. `/v1/companion/chat|stop|read|books` 等——语音/对话/朗读链路，等 T3/T4/T5 替代件就位后**第二批退役**。

无人调用或仅测试调用的疑似遗留口：`/v1/telemetry`（POST）、`/v1/leave`、`/metrics`（仓库内无抓取方）。

## 1. 路由 × 调用方矩阵

调用方缩写：
- **安卓** = `android/app/src/main/java/cn/weiekko/dock/data/HubClient.kt` 及其调用链（k20 主屏 App）
- **console-server** = `console/dock_console/`（本地控制台服务，含 hub_proxy 透传 `/api/hub/*`）
- **console-web** = `console/web/`（Vite 页面，经 console-server 代理）
- **menubar** = `console/menubar/main.swift`（launchd 三件套启停，**不直连 hub HTTP**）
- **mini** = `mini/helm_mini/`（Mac 监控 :17891）
- **tts** = `companion/tts/server.py`（keeper-tts :18100，hub 是它的客户端）
- **外部** = 仓库外的机器人/脚本（微信机器人、自动化脚本），代码不在本仓库
- **测试** = 仅 hub/tests、console/tests 引用

### 1.1 GET

| 路由 | 用途 | 现役调用方 | 判定 | 退役批次 |
|---|---|---|---|---|
| `/setup/*` | 配置向导页 + JSON API（state/save/token/voice/phone/features/mijia-login/llm） | console-server 透传 ← console-web（setup 页全套）；本地浏览器 | 现役，console web 冻结后退役 | 三（setup_ui/features 随批） |
| `/health` | 探活 | 安卓（3 处心跳/探活）、console-server（三件套健康页）、scripts | **保留**（hub-lite 需要） | — |
| `/metrics` | Prometheus 文本 | 仓库内**无调用方**（疑外部抓取或遗留） | 退役候选；⏸ 实机确认无人抓 | 一（monitor 类） |
| `/v1/dispatch/status` | F40 派发任务状态机查询 | 测试；console 未接 | dsh 即外部 AI 本体，整条 dispatch 退役 | 二 |
| `/v1/companion/diary` | F29 日记列表 | console-web 日记页 | dsh-calendar-diary + migration 接管 | 二（存储类） |
| `/v1/companion/week` | 周报 | console-web | 周报降级为守岸人 skill（memory 数据源） | 二（存储类） |
| `/v1/companion/read/jobs` | 朗读任务列表 | console-web 听书页 | T5 reading 接管 | 二（等 T5） |
| `/v1/companion/books`（+`/books/{id}`、`/books/{id}/chapter/{n}`） | 书架/开书/章节 | console-web 书架页；scripts/check_protocol 仅对账 | T5 接管 | 二（等 T5） |
| `/v1/companion/story` | 听书状态 | console-web | T5 接管 | 二（等 T5） |
| `/v1/companion/chats` | 对话原文检索（q/day/with_days） | console-web 聊天页 | 记忆插件接管；chat_log.py 见 §3 | 二（等 T3） |
| `/v1/companion/memory` | F28 她记得的事 | console-web 设置页、**安卓** | T3 记忆插件接管 | 二（等 T3） |
| `/v1/snapshot` | **全量状态 + 长轮询**（since/wait） | **安卓 8 处**（主屏轮询核心）、mini 打印横幅 | **保留**：k20 网关协议核心（lan-protocol.md） | — |
| `/v1/photos`、`/v1/photos/{id}` | F37 回忆相框（ETag 缓存） | **安卓**（相框模块） | 屏面功能，去向随 T2 k20.layout 定；⏸ 等 T2 | 三（屏面类） |
| `/v1/weather` | 天气代取 + PM2.5 | **安卓**（WeatherClient）、scripts | dsh-weather 接管后由 dsh 推屏 | 二 |
| `/v1/companion/audio/{id}` | 对话轮 wav 回放/下载 | **安卓**（气泡回放） | T4 voice-mode 本地播报接管 | 二（等 T4） |

### 1.2 POST

| 路由 | 用途 | 现役调用方 | 判定 | 退役批次 |
|---|---|---|---|---|
| `/setup/*`（POST） | 配置写入（guard_write 同源校验） | console-web setup 页 | 同 GET /setup | 三 |
| `/v1/companion/chat` | **对话主入口**（限流+车道） | **安卓**（语音轮主链路 2 处） | dsh agent loop + voice-mode 接管 | 二（等 T3/T4） |
| `/v1/companion/stop` | 打断合成/朗读 | **安卓**、console-web | voice-mode barge-in 接管 | 二（等 T4） |
| `/v1/companion/announce` | 手机→书桌递话出声 | **安卓** | 通知类，dsh-mobile-notify / k20 字幕替代 | 二 |
| `/v1/dispatch` | F40 派任务给外部 AI CLI | 测试；配置段控制开关 | dsh 本体就是 AI，整条退役 | 二 |
| `/v1/notify/phone` | 脚本/电脑往手机递话（内存队列随 snapshot 下发） | 测试；**外部脚本**（token 持有者）⏸ 实机确认 | notify 队列是安卓通知链路一部分，随 snapshot 保留骨架，递话语义由 dsh-mobile-notify 替代 | 二 |
| `/v1/wechat` | 微信机器人转一句来念（F34） | **外部**机器人（companion/tethys/notify.py 是同机配套的发送侧 :8643，hermes→微信 home channel） | T6 wechat 插件接管 | 二（等 T6） |
| `/v1/mirror` | F35 安卓监听器通知镜像（白名单应用） | **安卓**（通知监听器） | T6 接管「重要消息摘要」；mirror.py 判定见 §3 | 二（等 T6） |
| `/v1/companion/read` | 守岸人念一段/听书入口 | console-web | T5 接管 | 二（等 T5） |
| `/v1/companion/books`（上传） | 书籍上传（≤32MB） | console-web | T5 接管 | 二（等 T5） |
| `/v1/companion/story/timer` | 听书定时 | console-web | T5 接管 | 二（等 T5） |
| `/v1/companion/books/{id}/{action}` | play/pause/skip/bookmark | console-web | T5 接管 | 二（等 T5） |
| `/v1/companion/read/jobs/{id}/{action}` | 朗读任务确认/取消 | console-web | T5 接管 | 二（等 T5） |
| `/v1/telemetry` | PC/进程快照入口 | **仓库内无调用方**（探针 `_report_loop` 走 reporter 直发 mini :17891，不进 hub） | 疑遗留；⏸ 实机抓包确认 | 一（monitor 类） |
| `/v1/leave` | 离家模式 | 仓库内无调用方 | dsh 场景接管 | 三 |
| `/v1/presence` | 到/离家上报 | **安卓**（PresenceReporter） | 感知类；dsh 接管前安卓仍依赖，⏸ 与安卓侧改造同批 | 三 |
| `/v1/at-desk` | 书桌在座上报 | **安卓** | 同上 | 三 |
| `/v1/scenes/{id}` | 场景引擎（actions.py）触发 | **安卓**（bedtime 按钮）、scripts | dsh 场景接管；actions.py 见 §3 | 二 |
| `/v1/devices/{id}/command` | 米家设备控制 + 媒体键 | **安卓**（lamp、media 两处）、scripts | dsh-mihome（灯）+ T2 k20.media（媒体键） | 二（等 T1 拍板） |

### 1.3 DELETE

| 路由 | 用途 | 现役调用方 | 判定 | 退役批次 |
|---|---|---|---|---|
| `/v1/companion/memory/{key}` | 逐条删记忆 | console-web、**安卓** | T3 记忆插件接管（写入确认/可撤销语义要在插件侧复刻） | 二（等 T3） |
| `/v1/companion/diary/{day}` | 删一天日记 | console-web | dsh-calendar-diary 接管 | 二（存储类） |

### 1.4 非 HTTP 面但同生共死的

- `serve()` 启动横幅、`GuardedHTTPServer`（连接封顶 503）、`AccessLog`、`lanes`、`ratelimit`：**骨架保留**，hub-lite 继续用。
- `hub/web/`（Vite 项目 dock-hub-rare）构建产物进 `dock_hub/assets/`，但 `assets.py` 已无任何 import 方，server.py 也不再出 HTML——**疑似死料**；⏸ 在真机上 grep 确认无路由引用后，第三批随 setup_ui 一起清。

## 2. 各组件调用面小结

| 组件 | 对 hub 的 HTTP 依赖 | 对其他组件的依赖 | T7 处置 |
|---|---|---|---|
| android（k20 主屏 App） | HubClient.kt 15 个调用点：snapshot 长轮询（核心）、chat/stop/announce（语音轮）、memory（读+删）、audio 回放、photos、weather、presence、at-desk、mirror 上报、scenes、devices 命令、health | 直连 tts 服务的设置/白噪音/音乐/weixin（:18100 与 :8643 面上另有调用） | 安卓定位改「显示屏+麦克风」；chat/stop 等大脑面改走 dsh（T2/T4 决策）；presence/at-desk 保留到 dsh 感知就位 |
| console-server | hub_proxy 透传 `/api/hub/*`（companion 全家 + setup API）+ `/api/services/*` launchd 启停 + phone_bg（直连 k20 的壁纸/布局，不走 hub） | menubar 与它共管三件套 | 菜单栏瘦身为三件套启停器（hub-lite/keeper-tts/dsh），web 面板冻结；phone_bg 的 k20 直连面与 T2 插件归并 |
| menubar（Swift） | 无 HTTP；launchd start/stop/restart，serviceOrder=["hub","mini","tts"] | — | 列表去 mini 增 dsh（实际裁剪期执行，⏸） |
| mini（:17891） | 无（它只收探针 POST /v1/telemetry） | 被 probe.py（reporter.py）作为上报目标；自身 GET /v1/snapshot、/v1/desk 仓库内无消费方 | **整件退役**（dsh-vitals 接管上报用途）；GET 两口疑遗留 ⏸ |
| keeper-tts（:18100） | 被 hub/dock_hub/tts.py 当客户端：/health、/v1/speak、/v1/stop、/v1/noise、/v1/music | — | **保留独立进程**（用户拍板）；noise/music 面被 @doitian/dsh-music 等替代后收窄为纯 TTS |
| tethys-notify（:8643） | 被 hub 的递话链路当作发送侧（hermes→微信 home channel），仓库内无直接 import（外部编排） | — | T6 wechat 插件接管后随微信链路一并退役 |
| scripts/ | check_protocol.py 对账 server.py↔openapi.yaml；bump_version/release 脚本 | — | 保留（对账脚本退役期继续有用） |

## 3. 未定模块逐个判定（refactor-map 补行依据）

判定口径：谁 import 它 + 它挂在哪条路由/链路上 + 替代件是谁。

| 模块 | 行数 | 干什么 | 依赖方 | 判定 | 批次 |
|---|---|---|---|---|---|
| mirror.py | 84 | F35 通知镜像缓冲 | service.py（/v1/mirror） | 退役（T6 接管摘要职责；镜像亮屏语义归 k20 字幕） | 二 |
| chat_log.py | 152 | 对话原文 jsonl | companion.py、service.py（/v1/companion/chats） | 退役（对话记忆归 T3 记忆插件；迁移脚本可选导出） | 二 |
| metrics.py | 155 | Prometheus 文本 | companion_support.py、service.py（/metrics） | 退役（dsh-vitals/pc-manager 接管监控面） | 一 |
| guest.py | 137 | F26 访客模式（记忆/日记/聊天 404） | companion_desk.py、config.py、service.py | 退役（隐私语义如有需要由 dsh 侧权限承担；无替代件则直接删） | 三 |
| photos.py | 71 | F37 回忆相框照片源 | service.py（/v1/photos） | 待 T2：k20.layout 若编排相框则归 k20 插件，否则退役 | 三 |
| proactive.py | 341 | 她主动开口的时机引擎 | service.py | 退役（主动性由 dsh agent + 仪式 skill 承担，T3 写进世界书） | 二 |
| alarm.py | 149 | F11 起床闹钟 | service.py | 退役（闹钟走系统/dsh 日程） | 三 |
| dusk.py | 148 | F16 日落联动开灯 | service.py | 退役（dsh-mihome 场景） | 二 |
| schedule.py | 94 | 到点开关设备/说一句话 | service.py | 退役（dsh 日程） | 二 |
| timers.py | 81 | F9 番茄钟状态板 | companion.py、companion_desk.py | 待 T2：番茄钟推屏若保留归 k20 插件，否则退役 | 三 |
| lanes.py | 80 | 慢请求车道（503 busy） | service.py、server.py | **保留**（hub-lite 骨架） | — |
| ratelimit.py | 121 | 令牌桶限流 | service.py、server.py | **保留**（骨架） | — |
| clarify.py | 149 | F3 追问澄清 | companion.py、companion_flow.py、companion_desk.py | 退役（澄清是 agent loop 天然行为，T3 角色卡校准） | 二 |
| speech.py | 197 | 分句切块 + wav 拼接 | books.py、companion_flow.py、companion_llm.py、companion.py、reads.py | **部分保留**：hub-lite 若不再合成则整块退役；keeper-tts（T4）自带分句后退役 | 二（等 T4 拍板） |
| dispatch.py | 210 | F40 外部 AI CLI 派发 | config.py、service.py | 退役（dsh 本体取代外部 CLI） | 二 |
| llm.py | 224 | LLM 提示词/响应两头 | companion.py、companion_llm.py、companion_flow.py | 退役（大脑归 dsh） | 二 |
| pc.py | 243 | PC 快照模型（无 docstring） | media.py、probe.py、service.py | 随探针/监控退役；media 的音量面另判 | 一 |
| reporter.py | 89 | 书桌状态定时发 mini | probe.py、service.py | 随 mini 整件退役 | 一 |
| tray.py | 121 | Windows 托盘 | \_\_main\_\_.py、setup_ui.py | 保留（Windows 侧探针还要用，直到探针退役） | 一（探针退役时一并） |
| launch.py | 269 | 白名单程序启动（/v1/launch 面） | probe.py、service.py | **保留迁移**：方案 1.2 归 k20 网关或 dsh-ops；迁移完成前不退役 | ⏸ 等 T2 |
| logs.py | 37 | 异常留痕 | \_\_main\_\_.py、companion_flow.py、reporter.py、pc.py、probe.py | **保留**（基础设施） | — |
| ledstrip.py | 68 | WS2812 灯带（ESP32 HTTP） | service.py | 待定：T1 实测若米家/HA 灯带可替代则退役，否则并入 k20/dsh-ops | ⏸ 等 T1 |
| wallpaper.py | 51 | F39 一句话壁纸 | service.py | 归 T2 `k20.wallpaper`（方案已定） | 二（等 T2） |
| system_volume.py | 56 | Mac 输出音量 | service.py、setup_ui.py | 待 T1/T2：音量控制归 k20.media 面或生态件 | ⏸ 等 T1/T2 |
| theme.py | 95 | F36 节日皮肤 | service.py | 退役（皮肤语义进 k20 插件壁纸/世界书即可） | 三 |
| ambience.py | 162 | F17 灯光氛围联动 | service.py | 退役（dsh-mihome 场景接管） | 二 |
| accesslog.py | 136 | 访问日志 | probe.py、server.py | **保留**（运维面） | — |
| assets.py | 42 | 静态资源读取（进程内缓存） | **无 import 方** | 死料确认后退役 | 三 |
| actions.py | 334 | 模型 ACTION 行→设备动作 | companion_desk.py | 退役（工具调用由 dsh 原生） | 二 |

## 4. 与 check_protocol.py 的关系

`scripts/check_protocol.py` 机械比对 server.py 实际路由 ↔ docs/openapi.yaml。**退役每批路由时必须同步改 openapi.yaml**，否则对账红。回滚开关翻转回来时同理。这条已列入退役清单的每批步骤。

## 5. 盘点遗留的不确定点（⏸ 待实机）

1. `/metrics` 是否有外部 Prometheus 在抓（⏸ 实机看 access 日志）。
2. `/v1/telemetry` 是否已无调用方（探针只发 mini；⏸ 实机抓包/看 access 日志）。
3. `/v1/notify/phone` 外部脚本的使用频率（⏸ access 日志）。
4. `dock_hub/assets/` 构建产物是否仍被任何端加载（⏸ 真机加载主屏观察）。
5. mini 的 GET `/v1/snapshot`、`/v1/desk` 是否仍被手机 App 旧版本调用（⏸ mini access 日志）。
