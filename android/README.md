# 安卓客户端（岸亭）

横屏全屏摆件，包名 `cn.weiekko.dock`。当前 debug：**0.4.6**（`versionCode` 27）。设备方块铺满桌面，可重叠，长按拖动。

未配置 Hub 时是预览模式（本地演示数据）。联机后只通过局域网 HTTP 跟 **Mac Mini Hub** 说话，不直连米家、Ollama、TTS。

协议：[docs/lan-protocol.md](../docs/lan-protocol.md)。守岸人：[docs/companion.md](../docs/companion.md)。

## 设置里两栏不要填反

| 栏 | 填谁 | 本宅默认 |
|---|---|---|
| **Hub** | Mac Mini 门口 | `10.83.22.121` 端口 `17890` + Hub token |
| **Mini CPU** | 主屏 CPU 第二行 | `10.83.22.121` 端口 `17891` |

Hub 健康检查 `service=dock-hub` 且 `name=study`。`name=mini` 会被拒绝（那是旧的 Mini 自测服务）。

## 构建

需要 JDK 17（Android Studio 自带 JBR 即可）和一次网络，用来拉唤醒词 / ASR 模型（不进 git）。

```bash
cd android
./fetch-wake-word.sh
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  ./gradlew :app:assembleDebug
./gradlew :app:testDebugUnitTest
```

APK：`android/app/build/outputs/apk/debug/app-debug.apk`。装到横屏设备（本宅是 K20，adb `10.83.22.150:5555`）。

首次打开为预览；设置页填 Hub IP、`17890`、token，点测试连接。

主屏背景页面由岸亭自己提供，不经过 Hub。设置页显示 `http://<平板地址>:17920`。浏览器打开后用 Hub Token 登录，上传的图片和视频会自动进入索引，并成为当前主屏背景。

## 语音

- 唤醒词「小岸」（sherpa-onnx zipformer KWS）
- 唤醒词先上线，ASR 模型在后台线程装：开机就能叫醒，装好之前叫醒的那一句会等模型（最多 1.2s）再识别
- 听写：流式 zipformer transducer（`2025-06-30` int8，encoder 约 154MB）+ `modified_beam_search` + 热词（词表里有的字才会加权，如台灯/空调；小岸等靠模型本身）
- 听完由 `UtteranceGate`：结束静音 1s、最短句 1.5s
- 字幕等 `POST /v1/companion/chat` 全文；声音由 Mini TTS 出，本机不播守岸人 wav
- 她正在说时再喊「小岸」：`POST /v1/companion/stop` 后重新听
- 免唤醒连续对话（`ConversationWindow`）：她每说完一句开一个窗口——问句 15 秒、
  陈述句 8 秒（按字数等 TTS 说完才起算）；窗口内不用喊小岸，持续人声 300ms 直接进听写，
  不放提示音也不亮屏；连着两轮没听出人话就关窗，屏幕边缘有一圈淡呼吸光表示在听
- 她说话的那几秒不开窗（`ConversationBus.suppress`），免得麦克风听到的是她自己
- 插话打断：她正在说时不用喊小岸，直接说话就抢——阈值比窗口内更响、要持续 800ms，
  避免她自己的声音从 Mini 喇叭飘过来把自己打断；VM 送新对话前先 `POST /v1/companion/stop`
- 她问了一句（字幕带问号）：约 2.4 秒后自动再听，不必再喊小岸

## 防烧屏

K20 常亮，同一画面放久了会留影。`ui/BurnGuard.kt` 三件事，全是本地行为，不走 Hub：

- 时钟每 5 分钟在 ±2px 内挪一步，绕中心一圈后归零；
- 画面静止 30 分钟后，整个模块层挪 2–3px，再过 30 分钟挪回，四角循环；
- 23:00–07:00 整屏压到 40% 亮度（叠黑色遮罩）。

长按进编辑态时漂移与降亮都暂停，退出后恢复。

## 番茄钟

「二十五分钟」「开始正计时」她接了以后，snapshot 的 `companion.timer` 会带着状态回来，
主屏右上角画一个细环：倒计时环随剩余时间走短，正计时满环，中间是 `mm:ss`。
两次快照之间本地每秒续走，不依赖轮询频率。到点她说「到时间了。」环就消失。

## 在场上报（F25）

手机连上家里 Wi-Fi，她说一句「回来了」。`data/PresenceReporter.kt` 两条路喂状态：

- 系统的默认网络回调（`ConnectivityManager.registerDefaultNetworkCallback`）直接记
  「Wi-Fi 从哪一刻断的」，不吃轮询节奏、Doze 也兜得住；
- 轮询循环每轮顺手补一次采样，并在 Hub 真的答上话的那一轮发请求。

- **在家**：轮询拿得到 snapshot 且当前网络走 Wi-Fi → 边沿时 `POST /v1/presence {"state":"home"}`；
  之后每 15 分钟补一次心跳，Hub 重启或哪次上报丢了能自己长回来。
- **离家**：Wi-Fi 断开（掉了轮蜂窝 / 别家的网）满 10 分钟才算 away。人不在家时 Hub 大概率
  连不上、这条发不出去，不硬补——回家连上后先补发 `away` 再报 `home`，Hub 那边照样有
  `away → home` 的边沿，她该问候就问候。
- **Hub 挂了不算离家**：Wi-Fi 一直在、只是 snapshot 连不上（Hub 重启 / 断电），上一次
  看到 Wi-Fi 的时间戳一直在刷新，回来只补 `home`，不会凭空问候一句。

去抖与「Hub 连不上 ≠ 离家」的判断都在客户端做，Hub 只认报上来的状态（见 lan-protocol §6.12）。

## 在场检测（F24）

摄像头本地跑人脸检测（MLKit，前置、约 5fps），只取「有人 / 没人」布尔值——**画面不出设备、不存储**，
`power/CameraPresence.kt` 里没有任何落盘路径，协议面（`/v1/at-desk`）也没有图像字段。
设置页独立开关，默认关；开了才要 CAMERA 权限，拒绝就回落到关。

`power/PresenceWatch.kt` 是纯逻辑判定（单测覆盖）：

- 一帧有人就算坐下（亮屏要跟手），连续没人满 5 分钟才算离座（门口取快递不熄屏）；
- 离座 → 压暗（复用 hubSleep 的亮度路子，activity 活着、相机继续跑，人才有「回来」的那一刻）；
- 回座（离座满 5 分钟）→ 恢复亮度 + `POST /v1/at-desk {"present":true,"away_sec":N}`，
  Hub 复算离座时长后决定要不要问候；离开沿报 `{"present":false}` 只记状态不说话；
- 人出声（唤醒词）或手动解锁也算在场，但不发问候边沿——她多半正在说话。

按电源键锁屏后相机随生命周期停走，那之后就等手动唤醒，不强求。

