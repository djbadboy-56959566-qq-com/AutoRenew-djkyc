# WorkBuddy 每日积分签到（GitHub Actions 云端版）

电脑关机也能自动签到领积分：GitHub Actions 每天定时运行原版签到脚本，
结果通过 **Telegram + 自建 wxsend 微信接口** 双通道推送，凭据由本机自动化**持续自动续期**，接近"永久"免维护。

```
本机 WorkBuddy 桌面端（使用中，token 自动刷新）
        ↓ 每 3 天自动同步（本机自动化 + push_secret.py）
GitHub Secrets（WB_CREDS_ADJLEVIS / WB_CREDS_56959566，始终最新）
        ↓ 每天定时触发（.github/workflows/workbuddy.yml）
GitHub Actions 云端运行 workbuddy/workbuddy.py（签到 + 成长中心全功能）
        ↓
Telegram（HTML 卡片）+ 微信（wxsend 纯文本卡片）
```

## 文件说明

| 文件 | 作用 |
|------|------|
| `workbuddy/workbuddy.py` | 签到主脚本（原版 [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)，MIT，未改动，约 2773 行） |
| `.github/workflows/workbuddy.yml` | 定时签到 + 结果汇总 + 双通道通知；改通知样式/增删账号只动这一个文件 |
| 本机 `gh-checkin/push_secret.py` | 自动续期：读桌面端当前登录账号凭据 → 解密 → GitHub API 更新对应 Secret |

## 定时计划

- **每天 00:05**（北京时间）签到；**04:35** 自动补签一次
  （GitHub cron 经常延迟十几分钟到一小时，两档保底；签到接口幂等，重复跑不会重复计分）
- Actions 页可随时手动 **Run workflow** 验证全链路

## 通知样式

```
📋 WorkBuddy 每日签到
🔗 平台：请点击
📂 仓库：请点击

✅ adjlevis(da80654a…) 签到成功: +108 积分，累计 308
✅ 56959566(f03a5ab6…) 今日已签，累计 200
```

- **Telegram**：HTML 超链接模式，「请点击」可点开真实地址（平台/仓库地址不直接显示）；已禁用链接预览（不会出现链接卡片）
- **微信（wxsend）**：单独生成纯文本版推送；请求体 `{"token", "title", "content"}` + `Authorization: Bearer` 头 + 浏览器 UA（过 Cloudflare），与本仓库 FurryBox 等工作流共用同一接口
- 状态图标：✅ 成功/已签 · ⚠️ 活动未开启/网络超时 · ❌ 异常（附原因，标"需处理"）

## Secrets 一览（仓库 Settings → Secrets and variables → Actions）

| Secret | 说明 |
|--------|------|
| `WB_CREDS_ADJLEVIS` | adjlevis 账号凭据（本机自动化每 3 天自动更新，无需手动管） |
| `WB_CREDS_56959566` | 56959566 账号凭据（同上，桌面端登录该账号时自动更新） |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Telegram Bot 通知 |
| `WX_PUSH_URL` / `WX_PUSH_TOKEN` | 自建 wxsend 微信通知（与其他工作流共用） |

> 通知类 Secret 未配置时对应推送自动跳过，不影响签到本身。

## 凭据维护（重要）

1. **签到凭据有效期约 30 天**，桌面端登录使用时会自动刷新：
   - **adjlevis**：天天在用 → 零操作，全自动
   - **56959566**：**每 2~4 周退出当前账号、登录它一次**（随便用几下再切回来）；
     忘了也不怕——云端签到会 ❌ + 自动化汇报"需处理"，看到提醒再登即可
2. **本机自动化**：每 3 天 09:30 自动运行续期脚本并一句话汇报；
   报 `NO_TOKEN` / 401 说明 GitHub PAT 失效，需重建
3. **GitHub PAT**（classic，存本机 `~/.workbuddy/secrets/github-pat.txt`）：
   需 **`repo` + `workflow` 两个 scope**、无过期——前者更新 Secrets，后者用于提交 `.github/workflows/` 下的文件
4. **60 天无提交** GitHub 会自动停用定时任务 → 到 Actions 页重新启用即可

## 修改工作流的两种方式

- **GitHub API / git push**：需要 PAT 带 `workflow` 权限（本机 PAT 已具备）
- **网页手动上传/编辑**：无权限要求，直接在 GitHub 页面操作即可

## 常见问题

| 现象 | 原因/处理 |
|------|----------|
| `No module named signin` / 找不到脚本 | `workbuddy.py` 必须是完整主脚本（约 2773 行），不是导出辅助脚本 export_tokens.py（57 行） |
| 签到 JSON `"AUTH_REJECTED"`（401） | 该账号凭据过期 → 桌面端登录该账号用一下，等本机每 3 天自动同步即可；急用可手动跑 push_secret.py |
| 微信收不到 | 看工作流日志"推送自建微信接口"步骤：Secret 未配置会打印"跳过"；HTTP 非 200 时按返回内容排查 |
| Telegram 推送失败 | 检查 `TG_BOT_TOKEN` / `TG_CHAT_ID`；消息含特殊字符时已做 HTML 转义，一般不会崩 |
| 消息里出现链接预览卡片 | 已通过 `disable_web_page_preview`（TG）+ 纯文本版（微信）规避；再出现说明推送服务自身附加 |
| 定时没跑 | GitHub cron 会延迟属正常；连续多天没跑检查仓库是否 60 天无提交被停用 |
