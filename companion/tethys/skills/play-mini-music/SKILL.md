---
name: play-mini-music
description: Play a song on this Mac Mini. Use when 漂泊者 asks to play music, a song, or 随便放一首 on Mini / Mac. Opens Music.app or YouTube Music search; do not download archives or install players.
---

# Mini 放歌

这台机器是 Mac Mini。用脚本打开本机 Music 或 YouTube Music，不要 `curl` 下安装包、不要 `tar`、不要空转终端。

## 怎么做

指定歌名或歌手：

```bash
"${HERMES_HOME:-$HOME/.hermes}/skills/play-mini-music/scripts/play.sh" "周杰伦 晴天"
```

随便放一首（打开 Music）：

```bash
"${HERMES_HOME:-$HOME/.hermes}/skills/play-mini-music/scripts/play.sh"
```

成功后用一两句中文告诉漂泊者已经在 Mini 上打开了。Mini 要接音箱才听得到。
