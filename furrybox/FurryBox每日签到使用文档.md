# FurryBox 每日签到使用文档

版本：v3 多账号版（2026-11-16）
脚本：`furrybox/checkin.py`　工作流：`.github/workflows/furrybox-checkin.yml`

## 1. 这是什么

每天北京时间 08:00 自动给 FurryBox 账号签到一次，每个账号 +5 boxes。
签到每天北京时间 0 点重置，钱包页按钮文字是“签到领 5 boxes”，
已签到会变成“今日已签到”。

放在 `DigitalPlat-renew-djkyc` 仓库里，和 DigitalPlat 域名续期共用一个仓库、
共用 Telegram / 微信通知的 Secret，两个工作流互不影响。

仓库结构：

```
DigitalPlat-renew-djkyc/
  furrybox/
    checkin.py          # 签到脚本（本文档的脚本）
    README.md           # 简版说明
  .github/workflows/
    furrybox-checkin.yml   # 每天 08:00 跑签到
    renew.yml             # DigitalPlat 续期，每月 15 日跑，别动它
  DigitalPlat/
    renew.py
    README.md
    requirements.txt
```

## 2. 原理：为什么不用 Cookie、不用账号密码

先说结论，Gemini 生成的 Playwright + `FURRYBOX_COOKIE` 那版是错的，
跑起来一定失败。本文档的脚本已经按下面这个原理重写过，
结论是直接读 `my.furry.box` 和 `id.furry.box` 的 `bundle.js` 验证过的。

| 项目 | 实际情况 |
|---|---|
| 登录凭据 | 不是 Cookie，是浏览器 `localStorage` 里的 `fbx-session`：`{access_token, refresh_token}` |
| 签到接口 | `POST https://pwfcgdrorwpdzatxavdt.supabase.co/functions/v1/auth`，`{"action": "signin"}`，只认 `Authorization: Bearer <access_token>` |
| 换 token 接口 | `POST https://pwfcgdrorwpdzatxavdt.supabase.co/auth/v1/token?grant_type=refresh_token`，带公开的 Supabase publishable key |
| GitHub / NodeLoc | 只是 `id.furry.box` 上的登录入口。GitHub 是 `provider=github`，NodeLoc 是 `provider=custom:nodeloc`，走完 OAuth（PKCE，要跳浏览器、点授权，GitHub 还可能要二次验证）后都落到同一个 `fbx-session`，签到时已经没有区别 |
| Cookie | `my.furry.box` 下唯一的 Cookie 是 `fbx-ticket`，有效期 300 秒，只给 `mail.furry.box` 单点登录用，复制它也没用。往页面注入 Cookie 不会登录，页面会跳回 `id.furry.box` |
| 账号 | 当前账号（2026-11-16 实测）：显示名 `dj56959566`，邮箱 `56959566@qq.com`，登录方式 `custom:nodeloc`，2026-11-16 注册，没有设置过密码 |

所以脚本里**不需要、也不应该存 GitHub / NodeLoc 的账号密码**。
正确做法是一次性关联：浏览器里用 GitHub **或** NodeLoc 登录一次，
取出 `refresh_token` 存进 GitHub Secrets，
之后每天只用它换新的 `access_token` 再签到。
`refresh_token` 本身就代表了那个账号。

实测数据（2026-11-16）：

- `refresh_token` 只有 12 个字符，看着很短，但能正常换 token，
  不要因为短就以为拿错了。
- `access_token` 是 1231 个字符的 JWT，开头是 `eyJ`。
- 换 token 时服务端**可能轮换出新的 `refresh_token`，旧的随即作废**。
  所以不要在浏览器 Console 里反复跑手动换 token 的测试，测一次旧的就废一次。

## 3. 准备：取 refresh_token（每个账号做一次）

1. 浏览器打开 https://my.furry.box/wallet（钱包页，不是 account 页）。
2. 按 F12，点 **Console（控制台）**。
3. 粘贴下面这行，回车：

```js
copy(JSON.parse(localStorage.getItem('fbx-session')).refresh_token)
```

4. 回车后显示 `undefined` 是**正常的**，`copy()` 本来就不返回值，
   token 已经复制到剪贴板了。
5. 直接去 GitHub Secrets 里粘贴，不要贴到聊天、记事本里发给任何人。

验证有没有复制成功（只看长度，不打印 token）：

```js
JSON.parse(localStorage.getItem('fbx-session')).refresh_token.length
// 应为 12

JSON.parse(localStorage.getItem('fbx-session')).access_token.length
// 应为 1231 左右

JSON.parse(localStorage.getItem('fbx-session')).access_token.slice(0, 20)
// 开头应为 eyJ
```

如果 `fbx-session` 是 `null`，说明这个浏览器在 `my.furry.box` 上还没登录会话：
先去 https://id.furry.box/ 点 NodeLoc（或 GitHub）登录一次，
再回钱包页重复第 2、3 步。

**不要去 account 页找 token，那个页面本来就没有 token。**
那个页面下面的“API 密钥”是调 `api.pet` 管域名的（增删 DNS、续期、永久化），
跟签到是两套东西，签到脚本根本不用它。
点“新建密钥”报 `permission denied for function create_api_key` 是
FurryBox 服务端数据库函数的权限问题，不是你的操作错了，不影响签到，
不用管它。

## 4. 部署

### 4.1 放文件

- `checkin.py` 放到仓库的 `furrybox/checkin.py`（不在仓库根目录）。
- 工作流放到 `.github/workflows/furrybox-checkin.yml`。
- 工作流里运行那行已经是带路径的，不要改回根目录：

```yaml
run: python3 furrybox/checkin.py
```

工作流全文（每天北京时间 08:00，`cron: '0 0 * * *'`，手动也能触发）：

```yaml
name: FurryBox Auto Checkin

on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:

jobs:
  checkin:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Run Checkin Script
        env:
          FURRYBOX_REFRESH_TOKEN: ${{ secrets.FURRYBOX_REFRESH_TOKEN }}
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
        run: python3 furrybox/checkin.py
```

脚本是纯 Python 标准库，不用装 Playwright、不用装浏览器、不用 `pip install`。

### 4.2 设 Secrets

去 `DigitalPlat-renew-djkyc` → Settings → Secrets and variables → Actions →
Repository secrets，新建。**Secrets 是按仓库分的**，
在别的仓库建了，这个仓库还得再建一次。

| Secret | 是否必须 | 说明 |
|---|---|---|
| `FURRYBOX_REFRESH_TOKEN` | 必须 | 第 3 节取到的 `refresh_token`（12 位）。多账号见第 5 节 |
| `TG_BOT_TOKEN` | 可选 | Telegram 机器人 token，设了成功/失败会推消息，不设只看 Actions 日志 |
| `TG_CHAT_ID` | 可选 | 你的 Telegram ID，和上面一对用 |

**不用设的**：`FURRYBOX_COOKIE`（Gemini 那版错的）、
GitHub 密码、NodeLoc 密码、FurryBox 的 API 密钥，脚本都不用。

### 4.3 手动测试

Actions → 左边点 FurryBox Auto Checkin → Run workflow → Run workflow，
等一分钟看日志。成功长这样：

```
共 1 个账号，开始签到...
[账号1(56959566@qq.com)] 正在用 refresh_token 换取 access_token...
[账号1(56959566@qq.com)] 签到前余额: 10
✅ [账号1(56959566@qq.com)] FurryBox 签到成功: +5 boxes，余额 15
完成: 1/1 个账号签到成功
```

当天已经签到过也会显示成功：

```
✅ [账号1(56959566@qq.com)] FurryBox 今天已经签到过了，余额 15
```

跑通之后就不用管了，每天北京时间 08:00 自动跑一次。
退出码：0 = 全部账号成功或已签到；1 = 至少一个账号失败。

## 5. 多账号

可以。一个账号一个 `refresh_token`，一个 Secret 只放一个 token，
不要把多个 token 挤在一个 Secret 里（换行/逗号分隔也不行），
GitHub 一个名字也只能建一个 Secret。

多账号就分开建多个 Secret：

```
FURRYBOX_REFRESH_TOKEN    = 账号1的 token
FURRYBOX_REFRESH_TOKEN_2  = 账号2的 token
FURRYBOX_REFRESH_TOKEN_3  = 账号3的 token
……
（到 _10）
```

脚本逐个签到，一个失败不影响其他账号，日志里按邮箱区分账号。

每个账号都要在浏览器里单独登录一次再取 token
（切换账号登录，或换浏览器 / 无痕窗口），
不要把同一个 token 贴两遍——那是同一个账号，只会签一次。

## 6. 常见问题

### 6.1 日志报“缺少凭证：请在 Secrets 里设置 FURRYBOX_REFRESH_TOKEN”

工作流里 `FURRYBOX_REFRESH_TOKEN:` 后面是空的，根本没读到。
`TG_BOT_TOKEN` / `TG_CHAT_ID` 后面有 `***`，说明那两个设上了，就这个没设上。
检查三点：

1. 建在了别的仓库里，不是 `DigitalPlat-renew-djkyc` 这个；
2. 名字打错了，必须一模一样是 `FURRYBOX_REFRESH_TOKEN`，多一个空格、下划线都不行；
3. 建成了 Variables（变量）不是 Secrets，工作流读的是 Secrets。

建好之后回 Actions 重新跑一次，不用改代码。

### 6.2 日志报 `HTTP 400 Refresh token is not valid`

Secret 里那个 token 已经作废了。最常见的原因就是第 2 节说的轮换：
在浏览器 Console 里跑过一次手动换 token 的测试，
旧 token 就被作废了，但测试只打印了新 `access_token` 的长度，
新的 `refresh_token` 没存下来，本地存的还是旧的。

解决办法：重新拿一个新鲜的，**这次别再跑那个 fetch 测试**：

1. 去 https://id.furry.box/ 退出再用 NodeLoc 登录一次；
2. 到钱包页 Console 只跑复制那行：
   `copy(JSON.parse(localStorage.getItem('fbx-session')).refresh_token)`；
3. 去 Secrets 里把 `FURRYBOX_REFRESH_TOKEN` 更新成刚复制的这个；
4. 回 Actions 重新跑一次。

如果明天 08:00 自动跑又报同样的 400，说明这个 token 每次用完都会轮换，
Secret 里那个只能用一次，需要加一个“跑完自动把新 token 回写到 Secrets”的步骤，
到时再处理，不要每天手动更新。

### 6.3 Console 里 `copy(...)` 显示 `undefined`

正常。`copy()` 本来就不返回值，token 已经在剪贴板里了，
直接去 Secrets 里按 Ctrl+V 粘贴就行。

### 6.4 `refresh_token` 只有 12 位，是不是拿错了

不是。实测就是 12 位，而且能换到 1231 位的新 `access_token`
（2026-11-16 14:46 在 Console 里验证过：`能换吗 true，新token长度 1231`）。
不要因为短就扔掉，也不要去找更长的。

### 6.5 点“新建密钥”报 `permission denied for function create_api_key`

跟签到没关系，不用管。见第 3 节末尾。

### 6.6 脚本里说的“轮换了新的 refresh_token”

日志里出现这句，说明这次运行换 token 时服务端发了新的 `refresh_token`，
旧的已经作废。请把新的值更新回 Secrets，否则下次会报 400。
脚本本身现在只提示，不自动回写。

## 7. 文件清单

| 文件 | 位置 | 说明 |
|---|---|---|
| `checkin.py` | `furrybox/checkin.py` | 签到脚本 v3，多账号，纯标准库 |
| `furrybox-checkin.yml` | `.github/workflows/furrybox-checkin.yml` | 每天 08:00（北京时间）+ 手动触发 |
| `README.md` | `furrybox/README.md` | 简版说明 |
| 本文档 | `FurryBox每日签到使用文档.md` | 完整使用文档 |

## 8. 变更记录

- 2026-11-16 v1：Gemini 生成 Playwright + Cookie 版，经查 `bundle.js` 确认
  Cookie 登录是错的，会跳回 `id.furry.box`，按钮永远找不到。
- 2026-11-16 v2：改成纯标准库直接调 Supabase 签到接口，
  用 `refresh_token` 换 `access_token`，查余额、已签到判成功。
  部署位置定为 `DigitalPlat-renew-djkyc` 仓库的 `furrybox/` 文件夹。
- 2026-11-16 v3：多账号版。`FURRYBOX_REFRESH_TOKEN_2`……`_10`，
  逐个签到、逐个汇报。实测：单账号手动运行 #3 成功；
  `refresh_token` 12 位有效，手动换 token 测试会作废旧 token。
- 2026-11-16 v3.1：多账号改成一个 Secret 一个 token，
  不再支持一个 Secret 里换行/逗号挤多个 token，
  GitHub 一个名字只能建一个 Secret。
