#!/usr/bin/env python3
"""FurryBox 每日签到 — 修正版 v2 (2026-11-16)

为什么不用 Gemini 那版 (Playwright + FURRYBOX_COOKIE):
  1. FurryBox 登录根本不靠 Cookie。会话存在 localStorage `fbx-session`
     里 ({access_token, refresh_token})，签到接口只认
     `Authorization: Bearer <access_token>`。往 my.furry.box 注入 Cookie
     不会登录，页面会跳回 https://id.furry.box/，按钮永远找不到。
     my.furry.box 下唯一的 Cookie 是 fbx-ticket(有效期 300 秒，只给
     mail.furry.box 单点登录用)，复制它也没用。
  2. GitHub / NodeLoc 只是 id.furry.box 上的登录入口:
     GitHub -> Supabase /auth/v1/authorize?provider=github
     NodeLoc -> Supabase /auth/v1/authorize?provider=custom:nodeloc
     两者走完 OAuth(PKCE) 后都落到同一个 fbx-session，签到时已无区别。
     所以脚本不需要、也不应该存 GitHub/NodeLoc 的账号密码——
     OAuth 要跳浏览器、点授权、可能还要过 GitHub 二次验证，
     在 GitHub Actions 里根本跑不通。正确做法: 浏览器里用 GitHub
     或 NodeLoc 登录一次，取出 refresh_token 存进 Secrets，
     之后每天只用它换新的 access_token 再签到。
  3. 直接调官方签到接口，不用浏览器:
     POST https://pwfcgdrorwpdzatxavdt.supabase.co/functions/v1/auth
     {"action": "signin"}  -> {"amount": 5, "balance": ...}
     已签到会返回错误码 ALREADY_SIGNED_IN(当天北京时间 0 点重置)，
     这算成功，不是失败。
  4. 原版点完按钮不验证结果、is_visible() 不等待会误报、
     networkidle 可能一直挂起、截图也不上传，等于没法排错。

凭证 (三选一，优先顺序):
  FURRYBOX_REFRESH_TOKEN  — 推荐，长期有效，每天用它换 access_token
  FURRYBOX_SESSION         — 完整的 fbx-session JSON，取其中 refresh_token
  FURRYBOX_ACCESS_TOKEN    — 仅应急，access_token 只有约 1 小时有效

取 refresh_token 的办法 (浏览器里登录后做一次，不要发给任何人):
  打开 https://my.furry.box/wallet ，按 F12 -> Console，粘贴执行:
      copy(JSON.parse(localStorage.getItem('fbx-session')).refresh_token)
  然后去 GitHub 私有仓库 Settings -> Secrets and variables -> Actions，
  新建 FURRYBOX_REFRESH_TOKEN，把剪贴板内容贴进去。
  注意: GitHub 和 NodeLoc 邮箱不同就是两个不同的 FurryBox 账号，
  选定一个登录，签到只对那个账号生效。

退出码: 0 = 签到成功或今天已签到；1 = 凭证失效/网络/接口错误。
可选通知: 设 TG_BOT_TOKEN + TG_CHAT_ID，失败和成功都会推一条 Telegram。
"""

import json
import os
import sys
import urllib.request
import urllib.error

SUPABASE_URL = "https://pwfcgdrorwpdzatxavdt.supabase.co"
# Supabase publishable key，bundle.js 里公开的，不是秘密
PUBLISHABLE_KEY = "sb_publishable_KWmBOppH3DRoJf4-g9UCwQ_qLUZojRR"


def log(msg):
    print(msg, flush=True)


def notify(text):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"Telegram 通知发送失败(不影响签到结果): {e}")


def post_json(url, payload, headers=None, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"raw": body[:500]}
        return e.code, data
    except Exception as e:
        raise RuntimeError(f"请求失败 {url}: {e}")


def get_refresh_token():
    """只取 refresh_token，不打印、不记录它的值。"""
    rt = os.getenv("FURRYBOX_REFRESH_TOKEN", "").strip()
    if rt:
        return rt
    session = os.getenv("FURRYBOX_SESSION", "").strip()
    if session:
        try:
            return json.loads(session)["refresh_token"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise SystemExit("FURRYBOX_SESSION 不是合法的 fbx-session JSON，取不到 refresh_token")
    at = os.getenv("FURRYBOX_ACCESS_TOKEN", "").strip()
    if at:
        log("只给了 FURRYBOX_ACCESS_TOKEN(约 1 小时过期)，将直接用它签到一次，"
            "请尽快改存 FURRYBOX_REFRESH_TOKEN")
        return None  # 由调用方用 access_token 直签
    raise SystemExit(
        "缺少凭证: 请在 Secrets 里设置 FURRYBOX_REFRESH_TOKEN\n"
        "取法: 登录 https://my.furry.box/wallet 后 F12 Console 执行\n"
        "  copy(JSON.parse(localStorage.getItem('fbx-session')).refresh_token)"
    )


def refresh_access_token(refresh_token):
    try:
        status, data = post_json(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": refresh_token},
            headers={"apikey": PUBLISHABLE_KEY},
        )
    except RuntimeError as e:
        raise SystemExit(f"换 access_token 时网络失败: {e}，请稍后手动跑一次重试")
    if status != 200 or not data.get("access_token"):
        # 只打错误摘要，绝不打印 token
        log(f"换 access_token 失败: HTTP {status} {str(data)[:300]}")
        raise SystemExit(
            "refresh_token 已失效(退出/换号/被吊销都会这样)，"
            "请重新在浏览器登录一次，更新 Secrets 里的 FURRYBOX_REFRESH_TOKEN"
        )
    if data.get("refresh_token") and data["refresh_token"] != refresh_token:
        log("注意: 服务端轮换了新的 refresh_token，"
            "请把本次运行后轮换的值更新到 Secrets，否则下次可能失效")
    return data["access_token"]


def api_auth(access_token, action):
    """调 functions/v1/auth，返回 (status, data)。"""
    return post_json(
        f"{SUPABASE_URL}/functions/v1/auth",
        {"action": action},
        headers={"Authorization": f"Bearer {access_token}"},
    )


def main():
    access_token = os.getenv("FURRYBOX_ACCESS_TOKEN", "").strip() or None
    refresh_token = get_refresh_token()  # 无凭证时直接 SystemExit；只有 ACCESS_TOKEN 时返回 None
    if access_token is None and refresh_token is None:
        raise SystemExit("缺少凭证: 请设置 FURRYBOX_REFRESH_TOKEN")

    if refresh_token:
        log("正在用 refresh_token 换取 access_token...")
        access_token = refresh_access_token(refresh_token)

    # 先查一次余额，签到后可核对是否 +5
    try:
        w_status, before = api_auth(access_token, "wallet")
        status, data = api_auth(access_token, "signin")
    except RuntimeError as e:
        log(f"❌ 网络失败: {e}")
        notify(f"❌ FurryBox 签到失败: 网络失败 {e}")
        return 1
    balance_before = before.get("wallet", {}).get("balance") if w_status == 200 else None
    log(f"签到前余额: {balance_before} (查钱包 HTTP {w_status})")
    err_code = str(data.get("code") or data.get("error") or "")
    err_msg = str(data.get("message") or "")

    if status == 200:
        amount = data.get("amount", 5)
        balance = data.get("balance", balance_before)
        msg = f"FurryBox 签到成功: +{amount} boxes，余额 {balance}"
        log(f"✅ {msg}")
        notify(f"✅ {msg}")
        return 0

    if "ALREADY_SIGNED_IN" in err_code or "已经签到" in err_msg:
        msg = f"FurryBox 今天已经签到过了，余额 {balance_before}"
        log(f"✅ {msg}")
        notify(f"✅ {msg}")
        return 0

    log(f"❌ 签到失败: HTTP {status} code={err_code} message={err_msg[:300]}")
    notify(f"❌ FurryBox 签到失败: HTTP {status} {err_code} {err_msg[:200]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
