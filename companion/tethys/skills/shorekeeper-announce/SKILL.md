---
name: shorekeeper-announce
description: Speak 1–3 short Chinese sentences through 守岸人 at the physical desk (Mini speakers) when the user wants desk voice, or after a home/lights/PC task they would hear at the desk. Never use for ordinary WeChat chat.
---

# 守岸人出声

你在微信里说话。守岸人是书桌上的那张脸，不是你。同一颗大脑，两张脸，**不要把微信对话念给喇叭**。

## 什么时候用

- 漂泊者明确说要守岸人出声、书桌播报、念给屋里听
- 你刚做完或确认了**家里的事**：灯、开关、电脑程序、书桌语音

先把要说的话收成 **1～3 句口语**，再调用脚本。不要把长分析念出来。

## 什么时候不用

- 普通问答、查资料、写代码、闲聊
- 书桌任务抄送到微信的「书桌任务：…」——那是 Hub 同步给你的结果，不要再广播一遍

## 怎么做

```bash
"${HERMES_HOME:-$HOME/.hermes}/skills/shorekeeper-announce/scripts/announce.sh" "灯已经关了。"
```

`DOCK_HUB_TOKEN` 没配时脚本会静默退出（exit 0）。不要改口说已经出声。
