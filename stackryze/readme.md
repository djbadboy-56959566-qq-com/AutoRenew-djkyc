# StackRyze 域名自动续签 — 简单说明

每周日北京时间 09:00 自动跑一次，13 个域名里 60 天内到期的才续，免费续 1 年。
仓库：djbxxx-ccc-qq-com/AutoRenew-djkyc
脚本：`stackryze/stackryze_renew.py`　工作流：`.github/workflows/stackryze-renew.yml`

## 变量（GitHub 仓库 → Settings → Secrets，配一次就行）

| 变量 | 填什么 |
|---|---|
| STACKRYZE_EMAILS | 三个登录邮箱，逗号分隔：`56959566@qq.com,adjlevis@qq.com,djbadboy.vip@gmail.com` |
| STACKRYZE_PASSWORD_1 | 账号 1（ssss6@qq.com）的密码 |
| STACKRYZE_PASSWORD_2 | 账号 2（asssis@qq.com）的密码 |
| STACKRYZE_PASSWORD_3 | 账号 3（djsssvip@gmail.com）的密码 |
| STACKRYZE_TOTP_1 | 账号 1 的 2FA 种子，只填 `secret=` 后面那段 |
| STACKRYZE_TOTP_2 | 账号 2 的 2FA 种子，只填 `secret=` 后面那段 |
| STACKRYZE_TOTP_3 | 账号 3 的 2FA 种子，只填 `secret=` 后面那段 |
| TG_BOT_TOKEN / TG_CHAT_ID | Telegram 通知用的机器人 token 和聊天 ID |
| WX_PUSH_URL / WX_PUSH_TOKEN | 微信通知用的 wxsend 地址和 token |

邮箱顺序和 1/2/3 一一对应，别错位。密码和种子只放 Secrets 里，别发聊天里。

## 2FA 种子怎么弄

1. 手机浏览器打开 `auth.stackryze.com`，登录要配的账号。
2. 进安全设置，找到 2FA / 双重验证，点添加验证器 App（Add MFA）。
3. 页面出现一个二维码。
4. **用手机浏览器扫这个二维码**，扫出来是一串 `otpauth://` 开头的地址。
   微信扫不出来，别用微信扫。用手机浏览器扫via第三方的带扫描功能的，
5. 从这串地址里找到 `secret=` 后面那段（到下一个 `&` 为止），只把这一段填进对应的 `STACKRYZE_TOTP_N`。
   比如扫出来是 `otpauth://totp/StackRyze:xxx?secret=ABCD1234EFGH5678&issuer=Zitadel`，就只填 `ABCD1234EFGH5678`，前面的整串不要填。

注意两点：

- 账号已经开了 2FA 的，页面不会再显示二维码和种子，必须先把 2FA 关掉（Disable），再重新开一次，才会出新二维码。
- 重开之后，手机验证器也要重新扫新码，旧的验证码就作废了。

## 手动跑一次

仓库 → Actions → StackRyze Renew → Run workflow。结果看 TG / 微信通知，或 Actions 里的运行日志。
