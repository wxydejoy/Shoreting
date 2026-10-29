> ⚠️ **本组件已移除**（20261003-131027）。Mac 上不再运行 Hermes，Hub 也不再做分流。
> 归档、回滚步骤见 [`disabled/README.md`](disabled/README.md)。以下内容仅作历史参考。

# 泰缇斯（Tethys）

守岸人背后的 **Hermes Agent**。同一颗大脑、两张脸：书桌走守岸人口语，微信走 Hermes 原文；**会话不合并**，只同步任务结果。

```
K20 → Windows Hub
         ├─ 简单 → llm（守岸人 / Flash）→ 流式 TTS
         └─ 复杂 → tethys（Mini :8642 Hermes，session tethys:wanderer）
                      ├─ polish（Flash）→ TTS（书桌）
                      └─ 全文 → Mini :8643 → 微信 home

微信 iLink → 同一份 Hermes gateway
         ├─ 普通回复只在微信
         └─ 家居/书桌相关 → POST Hub /v1/companion/announce → 守岸人短播
```

微信 gateway 和 API server **必须是同一个进程**（`ai.hermes.gateway`）。不要再装第二份 `hermes gateway`，会抢 iLink token。

## Mini 安装

微信已经配对后再跑：

```bash
cd companion/tethys
chmod +x install-launchd.sh
./install-launchd.sh
```

会：在现有 `~/.hermes/.env` 打开 `API_SERVER_*`（`:8642`）、安装 `cn.weiekko.tethys-notify`（`:8643`）、把 `shorekeeper-announce` skill 拷到 `~/.hermes/skills/`。

探活：

```bash
curl -s http://127.0.0.1:8642/health
curl -s http://127.0.0.1:8643/health
```

脚本还会给 Hermes 装上 **DuckDuckGo 搜索**（`ddgs`，不需要 API key）和 **play-mini-music** skill。缺这两样时，天气/股市只能瞎编，放歌会空转终端。`~/.hermes/config.yaml` 里应有：

```yaml
web:
  backend: ddgs
  search_backend: ddgs
```

微信→书桌出声：把 Windows `hub.yaml` 的 token 写进 `~/.hermes/.env` 的 `DOCK_HUB_TOKEN`（`DOCK_HUB_URL` 默认 `http://10.83.22.31:17890`）。

## Windows hub.yaml

在现有 `companion` 下增加（与 `llm`、`tts` 并列）：

```yaml
companion:
  enabled: true
  llm:
    base_url: "https://…/compatible-mode/v1"
    model: qwen3.8-flash
    api_key: "sk-…"
    timeout_sec: 45
  tethys:
    enabled: true
    base_url: "http://10.83.22.121:8642/v1"
    model: hermes-agent
    api_key: "与 ~/.hermes/tethys.env 的 API_SERVER_KEY 相同"
    timeout_sec: 120
    polish: true
    speak: true
    ack: "好。"
    weixin_notify: true
    session_key: tethys:wanderer
  tts:
    base_url: "http://10.83.22.121:18100"
```

`api_key` 只写本机 yaml。改 `hub/dock_hub/companion.py` 后须 **Windows 重打 exe**。

## 路由规则

**守岸人（`companion.llm`）先判断**简单还是复杂：向 Flash 发一条极短调度 prompt，只回复 `ROUTE: shore` 或 `ROUTE: tethys`。解析失败、超时或守岸人说「不懂」，都交泰缇斯。

不经模型的短路：明显家居/启动口语（「关灯」「开抖音」等）直接 `shore`；「天气 / 帮我查 / 股市 / 放一首音乐 / 交给大模型」直接 `tethys`。

| 路径 | 谁 | 例子 |
|---|---|---|
| 守岸人 | 调度为 `shore` 或短路 | 「晚上好」「关灯」 |
| 泰缇斯 | 调度为 `tethys` 或短路 | 「明天天气」「放一首音乐」「查资料…」 |

日志里可见 `route_decide` / `route_bypass` / `weixin_notify_ok`。

## snapshot

`companion.tethys.ready`：Hermes `/v1/models` 探活（仅当 `tethys.enabled`）。
