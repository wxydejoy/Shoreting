# 泰缇斯 / Hermes 已移除（20261003-130852）

按用户要求，Mac 上的 Hermes（泰缇斯）已停用并移除。

## 已停用的服务（plist 在本目录）
- `ai.hermes.gateway.plist` —— Hermes API 网关（原 `:8642`）
- `cn.weiekko.tethys-notify.plist` —— 微信通知插件

重新启用（如需回滚）：
```sh
mv ~/dock/companion/tethys/disabled/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.hermes.gateway.plist
launchctl load ~/Library/LaunchAgents/cn.weiekko.tethys-notify.plist
```
但**Hub 侧的泰缇斯代码已删除**，仅恢复服务不会恢复路由/转述能力，
需要在 git 中回滚 `hub/dock_hub/companion.py`（备份见同目录 `.bak-nohermes-*`）。

## 归档内容
- `tethys-src-*.tar.gz` —— tethys 源码（notify.py / install-launchd.sh / tethys.env / README / skills / 两个 plist）
- `hermes-config-*.tar.gz` —— `~/.hermes` 的配置要点（.env / config.yaml / SOUL.md / auth.json / 自建技能等）

## 已删除
- `~/dock/companion/tethys/.venv`（218 MB）
- `~/.hermes`（111 MB：bin / logs / cache / sessions / state.db 等）
