# Serv00 / CT8 自动登录保号（精简版）

每月 1 号自动登录一次 DevilWEB 面板，保住账号。
官网规则：连续 90 天不登录面板或 SSH，账号自动删除，数据无法恢复。

原版：18686187636/Serv00_CT8_Auto_Login，用 Selenium + Chrome，689 行。
精简版：纯 Python 标准库，不用浏览器、不用装依赖。
登录就是一个 Django 表单 POST：先 GET `/login/` 拿 `csrfmiddlewaretoken`，
再 POST `username` / `password` / `csrfmiddlewaretoken` / `next=/`。

## 文件

- `login.py`：登录脚本，只写结果到 `login_result.json`，不发通知
- `.github/workflows/serv00-ct8-login.yml`：每月 1 号跑，样式在工作流里，读结果再发 Telegram

## 设 Secrets

仓库 → Settings → Secrets and variables → Actions：

| Secret | 值 |
|---|---|
| `ACCOUNTS_JSON` | 所有账号，一个 Secret 放全部，格式如下 |
| `TELEGRAM_BOT_TOKEN` | 可选，Telegram 机器人 token |
| `TELEGRAM_CHAT_ID` | 可选，你的 Telegram ID |

`ACCOUNTS_JSON` 格式（panel 只填域名）：

```json
[
  {"username": "你的 Serv00 账号", "password": "密码", "panel": "panel13.serv00.com"},
  {"username": "你的 CT8 账号", "password": "密码", "panel": "panel.ct8.pl"}
]
```

你的账号：Serv00 是 `djbadboy`（panel13）、`cntools`（panel16）、`robert912`（panel15）；
CT8 是 `djkyc`（panel.ct8.pl）。密码不要发到聊天里，直接贴进 Secrets。

## 测试

Actions → Serv00 & CT8 Auto Login → Run workflow。

## 通知

一次只发一条，样式在工作流里：

```
👤 你的 GitHub 用户名 · 📁 你的仓库名
🌐 Serv00 平台：请点击
🌐 CT8 平台：请点击
🔗 仓库：请点击
✅ Serv00 djbadboy：登录成功
✅ CT8 djkyc：登录成功
完成：2/2 个账号登录成功
```

失败的那行显示 ❌，原因写在后面。

## 注意

- 每月 1 号跑一次就够了，90 天才删号，不用每天跑。
- 自动登录保活有被封号的风险，官方条款没说允许自动登录，自己评估。
- GitHub 仓库 60 天没动静，定时任务可能被暂停，手动跑一次就恢复。
