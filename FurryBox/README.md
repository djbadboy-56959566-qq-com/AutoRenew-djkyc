# FurryBox 每日签到（简单版）

每天北京时间 08:00 自动签到，每个账号 +5 boxes。

## 1. 先设密码

如果你的账号是 GitHub / NodeLoc 等第三方登录的，现在没有密码，要先设一个：

1. 去 https://id.furry.box/ 点「忘记密码」
2. 填你的 FurryBox 账号邮箱，去邮箱收链接，设一个新密码（至少 8 位）
3. 在网页上用邮箱 + 新密码登录一次，确认能进

这个密码只是 FurryBox 邮箱登录用的，不影响第三方登录。

## 2. 放文件

- `furrybox/checkin.py`
- `.github/workflows/furrybox-checkin.yml`

## 3. 设 Secrets

仓库 → Settings → Secrets and variables → Actions → Repository secrets：

| Secret | 值 |
|---|---|
| `FURRYBOX_EMAIL` | 你的 FurryBox 账号邮箱 |
| `FURRYBOX_PASSWORD` | 刚设的新密码 |
| `FURRYBOX_EMAIL_2` | 第二个账号的邮箱（多账号才填） |
| `FURRYBOX_PASSWORD_2` | 第二个账号的新密码（多账号才填） |
| `TG_BOT_TOKEN` | 可选，Telegram 机器人 token |
| `TG_CHAT_ID` | 可选，你的 Telegram ID |

一个 Secret 只放一个值，不要把多个挤在一起。密码不要发到聊天里，直接贴进 Secrets。

多账号：一个账号一组，`FURRYBOX_EMAIL_3` / `FURRYBOX_PASSWORD_3`……到 `_10`。

## 4. 测试

Actions → FurryBox Auto Checkin → Run workflow，成功会显示：

```
密码登录，共 2 个账号，开始签到...
✅ 账号1(你的邮箱1) FurryBox 签到成功: +5 boxes，余额 15
✅ 账号2(你的邮箱2) FurryBox 签到成功: +5 boxes，余额 10
完成: 2/2 个账号签到成功
```

当天已签到过也算成功，会显示“今天已经签到过了”。

## 5. 通知

一次只发一条，样式在工作流里，脚本只写结果：

```
👤 你的 GitHub 用户名 · 📁 你的仓库名
🌐 签到的平台：请点击
🔗 仓库：请点击
✅ 账号1(你的邮箱1) FurryBox 签到成功: +5 boxes，余额 15
✅ 账号2(你的邮箱2) FurryBox 签到成功: +5 boxes，余额 10
```

失败的那行显示 ❌，原因写在后面。

## 常见问题

- **邮箱或密码不正确**：先去网页上用邮箱 + 密码登录一次，能进再跑。多个账号的密码别搞混。
- **缺少凭证**：Secrets 建错仓库了，或者名字打错了，或者建成 Variables 不是 Secrets。
- **不要用 refresh_token**：它跑一次就轮换作废，现在改密码登录了，不用再取它。
