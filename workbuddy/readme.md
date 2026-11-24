# WorkBuddy 每日积分签到（GitHub Actions 云端版）

电脑关机也能自动签到领积分：GitHub Actions 每天定时运行原版签到脚本，
结果通过 **Telegram + 自建 wxsend 微信接口** 双通道推送，凭据由本机自动化**持续自动续期**。

```
本机 WorkBuddy 桌面端（使用中，token 自动刷新）
        ↓ 每 3 天自动同步（本机自动化 + push_secret.py）
GitHub Secrets（WB_CREDS_ADJLEVIS / WB_CREDS_56959566，始终最新）
        ↓ 每天定时触发（.github/workflows/workbuddy.yml）
GitHub Actions 云端运行 workbuddy/workbuddy.py（签到 + 成长中心）
        ↓
Telegram（HTML 卡片）+ 微信（wxsend 纯文本卡片）
```

## 文件说明

| 文件 | 作用 |
|------|------|
| `workbuddy/workbuddy.py` | 签到主脚本（原版 [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)，MIT，未改动） |
| `.github/workflows/workbuddy.yml` | 定时签到 + 结果汇总 + 双通道通知，通知样式改这一个文件即可 |
| 本机 `gh-checkin/push_secret.py` | 自动续期：桌面端凭据 → 解密 → GitHub API 更新 Secret |

## 定时计划

- **每天 00:05**（北京时间）签到；**04:35** 自动补签一次——GitHub cron 常延迟，两档保底，接口幂等不会重复计分
- Actions 页可随时手动 Run workflow 验证

## 通知样式

```
📋 WorkBuddy 每日签到
🔗 平台：请点击
📂 仓库：请点击

✅ adjlevis(da80654a…) 签到成功: +108 积分，累计 308
✅ 56959566(f03a5ab6…) 今日已签，累计 200
```

- Telegram：HTML 超链接，「请点击」可点开真实地址；已禁用链接预览
- 微信（wxsend）：纯文本版；JSON 体 `{"token", "title", "content"}` + Bearer 头 + 浏览器 UA（过 CF）
- 状态图标：✅ 成功/已签 · ⚠️ 未开启/超时 · ❌ 异常（附原因，标"需处理"）

## Secrets 一览（Settings → Secrets and variables → Actions）

| Secret | 说明 |
|--------|------|
| `WB_CREDS_ADJLEVIS` | adjlevis 账号凭据（本机自动化每 3 天自动更新，无需手动管） |
| `WB_CREDS_56959566` | 56959566 账号凭据（同上，登录该账号时自动更新） |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Telegram 通知 |
| `WX_PUSH_URL` / `WX_PUSH_TOKEN` | 自建 wxsend 微信通知（与本仓库其他工作流共用） |

## 凭据维护（重要）

1. **token 有效期约 30 天**，但桌面端登录使用时会自动刷新：
   - **adjlevis**：天天在用 → 零操作，全自动
   - **56959566**：**每 2~4 周退出当前账号、登录它一次**（用几下再切回来即可）；
     忘了也不怕——云端签到会 ❌ + 自动化汇报"需处理"
2. **本机自动化**（WorkBuddy 里配置）：每 3 天 09:30 运行 `push_secret.py` 自动续期并汇报；
   报 `NO_TOKEN` / 401 说明 GitHub PAT 失效，需重建
3. **GitHub PAT**（存本机 `~/.workbuddy/secrets/github-pat.txt`）：
   classic，`repo` + `workflow` 两个 scope，无过期；用于更新 Secrets 和提交工作流文件
4. **60 天无提交** GitHub 会停用定时任务 → 到 Actions 页重新启用即可

## 常见问题

| 现象 | 原因/处理 |
|------|----------|
| `No module named signin` / 找不到脚本 | `workbuddy.py` 必须是完整主脚本（约 2773 行），不是导出辅助脚本 |
| 签到 JSON `"AUTH_REJECTED"`（401） | 该账号凭据过期 → 桌面端登录该账号，等本机自动同步即可 |
| 微信收不到 | 检查工作流日志里"推送自建微信接口"步骤：HTTP 非 200 时看返回内容 |
| Telegram 推送失败 | 检查 `TG_BOT_TOKEN`/`TG_CHAT_ID` |
| 定时没跑 | GitHub cron 会延迟；或仓库 60 天无提交被停用，去 Actions 重新启用 |
