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
