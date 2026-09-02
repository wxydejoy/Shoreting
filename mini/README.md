# 岸亭 Mini

Mac Mini 上的**本机监控**：CPU / 内存 / GPU，给岸亭主屏 CPU 块的**第二行**用。也接收 Windows Hub 的书桌上报，落盘到 `~/.config/helm-mini/`。

这不是 Hub，也不是 TTS / Ollama。

| 服务 | 端口 | `service` | 谁连 |
|---|---|---|---|
| 本仓库这个进程 | **17891** | `helm-mini` | 安卓「Mac Mini」栏 |
| Windows Hub | 17890 | `dock-hub` | 安卓「Hub」栏 |
| Ollama | 11434 | — | 只有 Hub 连 |
| 守岸人 TTS | 18100 | `helm-companion-tts` | 只有 Hub 连 |

**不要**在 Mini 上再开一份 `dock-hub:17890`。否则安卓把 Hub 栏填成 Mini IP 时，`/health` 会成功，米家和对话却打到错误的机器。

本宅默认：`http://10.83.22.121:17891`，Token `helm-mini-weiekko`（可用环境变量改掉）。

```bash
cd mini
uv sync
uv run helm-mini
```

探活：

```bash
curl -s http://127.0.0.1:17891/health
curl -s http://127.0.0.1:17891/v1/snapshot \
  -H "Authorization: Bearer helm-mini-weiekko"
curl -s http://127.0.0.1:17891/v1/desk \
  -H "Authorization: Bearer helm-mini-weiekko"
```

`/health` 应带 `"service":"helm-mini"`。`GET /v1/snapshot` 形状不变。Hub 用 `POST /v1/telemetry` 上报 Windows / 米家；最新一份在 `desk-state.json`，按日 jsonl 在 `log/`（留两天）。电脑约 1 秒一条，米家由 Hub 侧放慢到十几秒。

登录项（当前用户，开机自启）：

```bash
bash ./install-launchd.sh
```

可选环境变量：`HELM_MINI_TOKEN`、`HELM_MINI_PORT`（默认 17891）、`HELM_MINI_NAME`。

守岸人怎么接 Ollama / TTS：见 [docs/companion.md](../docs/companion.md)。

