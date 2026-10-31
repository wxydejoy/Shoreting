# 发版手册：从预演线到真机（0.9.0）

> 写在前面：0.5.0 – 0.9 的全部功能一直在预演分支上验证，**main 还停在 13cf86f，
> Hub 没重启过、App 没重新打包过**。真机要看到这一切，走的就是这一页。
> 全程约 30–40 分钟，其中「真机验收」占一半，别省。

## 0. 前置机械检查（5 分钟，全绿才继续）

```bash
uv run python scripts/check_rehearsal.py        # 分支覆盖：所有补丁都在预演线上
uv run python scripts/bump_version.py --check   # 版本号五处一致
cd hub && uv run python -m unittest discover -s tests      # hub 全量（~1006 条）
cd mini && uv run python -m unittest discover -s tests     # mini（10 条）
cd console && uv run python -m unittest discover -s tests  # console（21 条）
cd android && ./gradlew testDebugUnitTest                  # 安卓（163 条）
uv run python scripts/check_protocol.py       # 路由 ↔ 文档一致
```

安卓首次构建注意：`app/libs/sherpa-onnx-*.aar` 和 `app/src/main/assets/` 下的
模型不进 git，新机器先跑 `android/fetch-voiceprint.sh`、`fetch-wake-word.sh`，
ASR 模型从老机器拷或按 README 拉取。

## 1. 合并进 main（2 分钟）

```bash
git checkout main
git merge --ff-only rehearsal/integration-hub    # ff-only：预演线必须是 main 的直系后代
git push origin main
```

`--ff-only` 失败说明 main 上有预演线没有的东西——先停下来看，不要制造合并提交。

## 2. 重启 Hub（3 分钟）

1. Console 菜单栏停掉 Hub（或 `launchctl` 对应项 unload）。
2. 确认进程没了：`lsof -i :17890` 应为空。
3. 重新起 Hub（同一份代码现在就是 main 上的了）。
4. 冒烟：

```bash
curl -s http://127.0.0.1:17890/v1/snapshot | head -c 300   # 有 JSON 就活
curl -s http://127.0.0.1:17890/metrics | head -5           # O7 的指标页
```

TTS 旁边如果挂了白噪音服务（:18101），同样重启并 `curl` 一下。

## 3. 打包 App（5 分钟）

```bash
cd android && JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  ./gradlew assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk   # 或手动传到 K20 安装
```

装上后先看三眼：主屏出时钟、`settings` 能连上 Hub、版本号显示 0.9.0。

## 4. 真机验收（15 分钟，按 features.md 的三个日常走）

**早上那条**：改 `morning.at` 到两分钟后等她说简报；说「我出门了」看离家总控汇报。

**晚上那条**：
- 说「开工」→ 工作区程序起来、灯带切 40%（F19）
- 说「二十五分钟」→ 屏幕右上角环走起来（F9），跑起来灯带进专注色（F17 亮度半包）
- 朗读开始 → 灯暗下去；说「不用」→ 她当天不再提久坐（F12 的被拒即闭嘴）

**夜里那条**：
- 说「睡前」→ 大灯灭、灯带 8%、她问书还读吗（F7 场景，主屏右下角也有按钮可点）
- 「放点雨声」→ 白噪音起、30 分钟淡出（F31）
- 防烧屏：静置 30 分钟看时钟漂移和夜间变暗（F38）

**布局编辑（O5 重构过的地方，重点回归）**：长按时钟进编辑态 → 拖动、角点缩放、
去底/加底、隐藏各试一遍 → 设置页开关同步 → 「恢复默认布局」复原 → 退出再进，
布局还是你调过的样子。磁贴该在的位置都在、该空的槽位显示灰色占位。

**她记得事**：说一句「记住我不喝冰的」→ 设置页「她记得的事」出现一条，可删（F28）；
周日看 `/week` 周报页（F27）。

**在场与访客**：离座 5 分钟熄屏、回来她说一句（F24/F25）；开访客模式，聊天记录藏起来（F26）。

**声纹**：设置页注册第二个人（三句话），让 TA 说「小岸」→ 她用注册的称呼开口（F5）。

任何一条不对：先看 Hub 的 access log 和 `ConnectionLogger`，别急着回滚——单功能问题
记 issue，全屏不可用才走回滚。

## 5. 发版收尾（3 分钟）

```bash
scripts/release.sh 0.9.0 docs/releases/0.9.0.md   # 改版本 → 跑测试 → 打包 → GitHub Release
```

Windows 的 Hub exe 只能在 Windows 上打，Mac 会自动跳过并提醒——补打一次。
发完把预演线留在原地不动：它是下一轮的工作基线。

## 回滚

- **Hub**：`git checkout main~1` 重启进程即回上一版；配置 yaml 不动。
- **App**：K20 重装上一个 APK（每次发版前把旧 APK 留一份在 `dist/`）。
- 配置向前兼容是新版本的红线：回滚旧版读不起新配置时，手工把 yaml 里新增的段删掉。

## 已知的发版坑（本轮真机踩过的）

- **TTS 起不来，反复退出**：0.9.0 起 TTS 拒绝在 `0.0.0.0` 上无口令监听。Hub 只从本机
  调它，把 LaunchAgent 里的 `--host` 改成 `127.0.0.1`（或给 `--token` 并在 hub.yaml
  `companion.tts_token` 里配同一个值），然后 `launchctl bootout` + `bootstrap` 重载。
- **场景的 on/off 解析成空**：裸写 `off: [lamp]` 会被 PyYAML 当布尔键吃掉——hub 侧已
  兜底（两种键都认），自己写配置时给键加引号 `"off":` 更稳。
- **repo 里残留 hub/hub.yaml 会顶掉真配置**：搜索顺序是 cwd → hub/ → ~/.config，
  仓库工作树里那个占位 hub.yaml 必须挪走（已改名 hub.yaml.pre-0.9.0-bak 留档）。
