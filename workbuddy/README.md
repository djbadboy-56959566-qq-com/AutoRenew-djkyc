# WorkBuddy 积分签到 · GitHub Actions 版

用 GitHub Actions 云端定时执行 [workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)，  
电脑关机也能每天签到领积分。

## 原理

凭据在本机由 WorkBuddy 桌面端运行时解密为明文 token，放入 GitHub Secrets（加密存储）。  
云端没有桌面端运行时，但 signin.py 遇到明文 token 会直接使用、无需解密，  
因此云端可运行完整的原版脚本（签到 + 成长中心）。

## 部署步骤

1. **导出凭据**（在本机运行，必须能访问 WorkBuddy 桌面端）：
   ```
   python export_tokens.py
   ```
   生成 `export/WB_CREDS_ADJLEVIS.json` 和 `export/WB_CREDS_56959566.json`。
2. **建私有仓库**：在 GitHub 新建一个 **Private** 仓库（切勿公开，凭据虽在 Secrets 不在代码里，  
   但私有仓库能避免流程细节暴露），把本目录（signin.py、workflow、.gitignore）push 上去。  
   **不要提交 export/ 目录**（.gitignore 已排除）。
3. **配置 Secrets**：仓库 Settings → Secrets and variables → Actions → New repository secret：
   - `WB_CREDS_ADJLEVIS` ← export/WB_CREDS_ADJLEVIS.json 的完整内容
   - `WB_CREDS_56959566` ← export/WB_CREDS_56959566.json 的完整内容
4. **验证**：仓库 Actions 页 → 选 "WorkBuddy daily checkin" → Run workflow，  
   查看日志中每个账号输出的 JSON（report 字段即结果）。

## 定时计划

- 每天北京时间 00:05 签到；04:35 自动补签一次（GitHub cron 常延迟，两次机会，幂等不重复计分）
- 手动随时可用 workflow_dispatch 触发

## 维护事项（重要）

- **凭据有效期约 30 天**（当前两个账号到 2026-12-21）。过期前在本机重新登录 WorkBuddy 桌面端  
  并重新绑定（`signin.py bind <名> --force`），再跑一次 `python export_tokens.py` 更新两个 Secrets。
- **60 天无活动会被停用**：GitHub 会自动禁用 60 天没有代码提交的仓库的定时任务，  
  到时去 Actions 页重新启用，或偶尔提交点什么保持活跃。
- GitHub cron 可能延迟十几分钟到一小时，属正常现象。
- 私有仓库每月 2000 分钟免费额度，签到每天约 2 分钟，绰绰有余。
