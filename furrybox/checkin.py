#!/usr/bin/env python3
"""FurryBox 每日签到 — 修正版 v3 多账号 (2026-11-16)

为什么不用 Gemini 那版 (Playwright + FURRYBOX_COOKIE):
  1. FurryBox 登录根本不靠 Cookie。会话存在 localStorage `fbx-session`
     里 ({access_token, refresh_token})，签到接口只认
     `Authorization: Bearer <access_token>`。往 my.furry.box 注入 Cookie
     不会登录，页面会跳回 https://id.furry.box/，按钮永远找不到。
  2. GitHub / NodeLoc 只是 id.furry.box 上的登录入口:
     GitHub -> Supabase /auth/v1/authorize?provider=github
     NodeLoc -> Supabase /auth/v1/authorize?provider=custom:nodeloc
     两者走完 OAuth(PKCE) 后都落到同一个 fbx-session，签到时已无区别。
     所以脚本不需要、也不应该存 GitHub/NodeLoc 的账号密码。
     正确做法: 浏览器里用 GitHub 或 NodeLoc 登录一次，取出 refresh_token
     存进 Secrets，之后每天只用它换新的 access_token 再签到。
  3. 直接调官方签到接口，不用浏览器:
     POST https://pwfcgdrorwpdzatxavdt.supabase.co/functions/v1/auth
     {"action": "signin"}  -> {"amount": 5, "balance": ...}
     已签到会返回错误码 ALREADY_SIGNED_IN(当天北京时间 0 点重置)，
     这算成功，不是失败。

多账号:
  一个账号一个 refresh_token，逐个签到，一个失败不影响其他账号。
  填法二选一:
  A) 一个 Secret 里放多个，用换行或逗号分隔:
       FURRYBOX_REFRESH_TOKEN = <账号1的token>
                                 <账号2的token>
  B) 分开建多个 Secret:
       FURRYBOX_REFRESH_TOKEN, FURRYBOX_REFRESH_TOKEN_2,
       FURRYBOX_REFRESH_TOKEN_3, ... (到 _10)
  两种可以混用，脚本会去重。每个账号都要在浏览器里单独登录一次
  再取 token(切换账号登录，或换浏览器/无痕窗口)，不要把同一个
  token 贴两遍——那是同一个账号，只会签一次。

  注意(实测 2026-11-16): refresh_token 只有 12 个字符，但能正常换
  token；换 token 时服务端可能轮换出新的 refresh_token，旧的随即
  作废——所以不要在浏览器 Console 里反复跑手动换 token 的测试，
  测一次旧的就废一次。脚本每次运行后如果轮换了，会在日志里提示，
  请把新的值更新回 Secrets，否则下次会报 400 Refresh token is not valid。

取 refresh_token 的办法 (每个账号做一次，不要发给任何人):
  打开 https://my.furry.box/wallet ，按 F12 -> Console，粘贴执行:
      copy(JSON.parse(localStorage.getItem('fbx-session')).refresh_token)
  然后去 GitHub 私有仓库 Settings -> Secrets and variables -> Actions，
  新建 FURRYBOX_REFRESH_TOKEN，把剪贴板内容贴进去。

退出码: 0 = 全部账号签到成功或今天已签到；1 = 至少一个账号失败。
可选通知: 设 TG_BOT_TOKEN + TG_CHAT_ID，失败和成功都会推一条 Telegram。
"""

import base64
import json
import os
import re
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


def split_tokens(value):
    return [t for t in re.split(r"[\s,;]+", value or "") if t]


def get_refresh_tokens():
    """收集全部 refresh_token，去重，不打印、不记录它们的值。"""
    tokens = []
    for name in ["FURRYBOX_REFRESH_TOKEN", "FURRYBOX_REFRESH_TOKENS"]:
        tokens += split_tokens(os.getenv(name, ""))
    for i in range(2, 11):
        tokens += split_tokens(os.getenv(f"FURRYBOX_REFRESH_TOKEN_{i}", ""))
    session = os.getenv("FURRYBOX_SESSION", "").strip()
    if session:
        try:
            tokens.append(json.loads(session)["refresh_token"])
        except (json.JSONDecodeError, KeyError, TypeError):
            raise SystemExit("FURRYBOX_SESSION 不是合法的 fbx-session JSON，取不到 refresh_token")
    seen, out = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def account_label(access_token):
    """从 JWT 里解出邮箱/用户名，只用来在日志里区分账号，不验签。"""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("email") or data.get("user_name") or data.get("sub") or "?"
    except Exception:
        return "?"


class CheckinError(Exception):
    pass


def refresh_access_token(refresh_token):
    try:
        status, data = post_json(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": refresh_token},
            headers={"apikey": PUBLISHABLE_KEY},
        )
    except RuntimeError as e:
        raise CheckinError(f"换 access_token 时网络失败: {e}")
    if status != 200 or not data.get("access_token"):
        raise CheckinError(
            f"refresh_token 已失效，换 access_token 失败: HTTP {status} {str(data)[:300]}，"
            "请重新在浏览器登录一次，更新 Secrets 里的 FURRYBOX_REFRESH_TOKEN"
        )
    new_rt = data.get("refresh_token")
    if new_rt and new_rt != refresh_token:
        log("  注意: 服务端轮换了新的 refresh_token，旧的已作废，"
            "请把新的值更新回 Secrets，否则下次会报 400")
    return data["access_token"]


def api_auth(access_token, action):
    """调 functions/v1/auth，返回 (status, data)。"""
    return post_json(
        f"{SUPABASE_URL}/functions/v1/auth",
        {"action": action},
        headers={"Authorization": f"Bearer {access_token}"},
    )


def checkin_one(idx, refresh_token):
    """签到一个账号，返回 True=成功/已签到，False=失败。"""
    label = f"账号{idx}"
    try:
        log(f"[{label}] 正在用 refresh_token 换取 access_token...")
        access_token = refresh_access_token(refresh_token)
        label = f"账号{idx}({account_label(access_token)})"
        try:
            w_status, before = api_auth(access_token, "wallet")
            status, data = api_auth(access_token, "signin")
        except RuntimeError as e:
            raise CheckinError(f"网络失败: {e}")
        balance_before = before.get("wallet", {}).get("balance") if w_status == 200 else None
        log(f"[{label}] 签到前余额: {balance_before}")
        err_code = str(data.get("code") or data.get("error") or "")
        err_msg = str(data.get("message") or "")
        if status == 200:
            msg = f"FurryBox 签到成功: +{data.get('amount', 5)} boxes，余额 {data.get('balance', balance_before)}"
            log(f"✅ [{label}] {msg}")
            notify(f"✅ [{label}] {msg}")
            return True
        if "ALREADY_SIGNED_IN" in err_code or "已经签到" in err_msg:
            msg = f"FurryBox 今天已经签到过了，余额 {balance_before}"
            log(f"✅ [{label}] {msg}")
            notify(f"✅ [{label}] {msg}")
            return True
        raise CheckinError(f"签到失败: HTTP {status} code={err_code} message={err_msg[:300]}")
    except CheckinError as e:
        log(f"❌ [{label}] {e}")
        notify(f"❌ [{label}] {e}")
        return False


def main():
    tokens = get_refresh_tokens()
    if not tokens:
        # 兼容只给了短效 access_token 的单账号应急用法
        at = os.getenv("FURRYBOX_ACCESS_TOKEN", "").strip()
        if at:
            log("只给了 FURRYBOX_ACCESS_TOKEN(约 1 小时过期)，将直接用它签到一次，"
                "请尽快改存 FURRYBOX_REFRESH_TOKEN")
            # 走单账号路径：把 access_token 当作已换好的 token 直接签到
            try:
                w_status, before = api_auth(at, "wallet")
                status, data = api_auth(at, "signin")
            except RuntimeError as e:
                log(f"❌ 网络失败: {e}")
                return 1
            if status == 200 or "ALREADY_SIGNED_IN" in str(data.get("code") or ""):
                log(f"✅ 签到成功/已签到，余额 {data.get('balance')}")
                return 0
            log(f"❌ 签到失败: HTTP {status} {str(data)[:300]}")
            return 1
        raise SystemExit(
            "缺少凭证: 请在 Secrets 里设置 FURRYBOX_REFRESH_TOKEN\n"
            "取法: 登录 https://my.furry.box/wallet 后 F12 Console 执行\n"
            "  copy(JSON.parse(localStorage.getItem('fbx-session')).refresh_token)\n"
            "多账号: 同一个 Secret 里换行/逗号放多个 token，或再建 "
            "FURRYBOX_REFRESH_TOKEN_2、_3……"
        )
    log(f"共 {len(tokens)} 个账号，开始签到...")
    results = [checkin_one(i + 1, t) for i, t in enumerate(tokens)]
    ok = sum(results)
    log(f"完成: {ok}/{len(tokens)} 个账号签到成功")
    return 0 if ok == len(tokens) else 1


if __name__ == "__main__":
    sys.exit(main())
