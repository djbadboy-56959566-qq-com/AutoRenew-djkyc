#!/usr/bin/env python3
"""FurryBox 每日签到 — v4 邮箱密码版 (2026-11-16)

为什么改成密码版:
  refresh_token 版跑一次就废——服务端每次换 token 都可能轮换出新的，
  旧的随即作废(实测: HTTP 400 Refresh Token Not Found)，
  脚本又不会自动把新 token 写回 Secrets，每天都得手动更新，
  自动化就没意义了。所以改成每天现登现签: 用邮箱+密码登录，
  现场换一个 access_token，签完就扔，下次再登。密码本身不会轮换。

登录接口(和 id.furry.box 登录页用的同一个，读 bundle.js 验证过):
  POST https://pwfcgdrorwpdzatxavdt.supabase.co/auth/v1/token?grant_type=password
  headers: Content-Type: application/json, apikey: <公开的 publishable key>
  body: {"email": "...", "password": "..."}
  -> {"access_token": "...", "refresh_token": "...", "user": {...}}

签到接口:
  POST https://pwfcgdrorwpdzatxavdt.supabase.co/functions/v1/auth
  {"action": "signin"} + Authorization: Bearer <access_token>
  -> {"amount": 5, "balance": ...}，已签到返回 ALREADY_SIGNED_IN(算成功)。

先设密码(你的账号现在没有密码):
  你的账号是 NodeLoc 第三方登录的，account 页写着“没有设置过密码”。
  去 https://id.furry.box/ 点登录页的「忘记密码」，填邮箱
  56959566@qq.com，收 QQ 邮箱里的重置链接，设一个新密码(至少 8 位，
  建议字母+数字)。这个密码只是 FurryBox 邮箱登录用的，
  不影响你 NodeLoc 论坛的密码，NodeLoc 登录照样能用。
  设好后先在网页上用邮箱+密码登录一次，确认能进，再往下走。

Secrets(一个账号一组，一个 Secret 只放一个值，不要挤):
  FURRYBOX_EMAIL     = 56959566@qq.com
  FURRYBOX_PASSWORD  = 刚设的新密码(不要发到聊天里，直接贴进 Secrets)
  多账号: FURRYBOX_EMAIL_2 / FURRYBOX_PASSWORD_2，
          FURRYBOX_EMAIL_3 / FURRYBOX_PASSWORD_3，……到 _10
  密码版优先: 只要设了 EMAIL+PASSWORD，就走密码登录，
  不用再设 FURRYBOX_REFRESH_TOKEN。
  没设密码时才回退到旧的 refresh_token 方式(不推荐，会轮换作废)。

退出码: 0 = 全部账号签到成功或今天已签到；1 = 至少一个账号失败。
通知: 脚本本身不发通知，只把结果行写到 furrybox/checkin_result.txt，
消息样式在工作流里，工作流读这个文件再发 Telegram。
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error

SUPABASE_URL = "https://pwfcgdrorwpdzatxavdt.supabase.co"
# Supabase publishable key，bundle.js 里公开的，不是秘密
PUBLISHABLE_KEY = "sb_publishable_KWmBOppH3DRoJf4-g9UCwQ_qLUZojRR"
# 邮箱 -> 显示名，结果行里显示名字，不只显示邮箱
DISPLAY_NAMES = {
    "56959566@qq.com": "dj56959566",
    "adjlevis@qq.com": "adjlevis",
}
# 结果文件：脚本只把每个账号的一行结果写到这里，不发通知。
# 消息样式、表头、链接都在工作流 .github/workflows/furrybox-checkin.yml 里，
# 工作流读这个文件再发 Telegram。
RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkin_result.txt")


def display_name(email):
    return DISPLAY_NAMES.get(email, email.split("@")[0])


def log(msg):
    print(msg, flush=True)


def write_result(lines):
    """把结果行写到 checkin_result.txt，供工作流发通知用。"""
    try:
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log(f"写结果文件失败(不影响签到结果): {e}")


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


class CheckinError(Exception):
    pass


def login_with_password(email, password):
    """邮箱+密码登录，现换 access_token。只返回 access_token，不存 refresh_token。"""
    try:
        status, data = post_json(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            {"email": email, "password": password},
            headers={"apikey": PUBLISHABLE_KEY},
        )
    except RuntimeError as e:
        raise CheckinError(f"登录时网络失败: {e}")
    if status != 200 or not data.get("access_token"):
        code = str(data.get("error_code") or data.get("code") or data.get("error") or "")
        msg = str(data.get("msg") or data.get("message") or data.get("error_description") or "")
        if "invalid_credentials" in code or "invalid_credentials" in msg:
            raise CheckinError(
                f"邮箱或密码不正确(HTTP {status})，请确认密码是在 id.furry.box "
                "用「忘记密码」设的那个，且先在网页上用邮箱+密码登录一次"
            )
        raise CheckinError(f"登录失败: HTTP {status} {code} {msg[:300]}")
    return data["access_token"]


def refresh_access_token(refresh_token):
    """旧方式回退: refresh_token 换 access_token(会轮换作废，不推荐)。"""
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
            "请改用邮箱密码方式(见脚本开头)，或重新登录更新 refresh_token"
        )
    if data.get("refresh_token") and data["refresh_token"] != refresh_token:
        log("  注意: 服务端轮换了新的 refresh_token，旧的已作废，"
            "请把新的值更新回 Secrets，否则下次会报 400")
    return data["access_token"]


def api_auth(access_token, action):
    return post_json(
        f"{SUPABASE_URL}/functions/v1/auth",
        {"action": action},
        headers={"Authorization": f"Bearer {access_token}"},
    )


def account_label(access_token, fallback="?"):
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("email") or data.get("user_name") or data.get("sub") or fallback
    except Exception:
        return fallback


def signin_with_access_token(label, access_token):
    """拿着 access_token 查余额+签到，返回通知里用的那一行（不直接发通知，
    由 main() 把所有账号攒成一条发，表头只出现一次）。"""
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
        msg = f"✅ {label} FurryBox 签到成功: +{data.get('amount', 5)} boxes，余额 {data.get('balance', balance_before)}"
        log(msg)
        return msg
    if "ALREADY_SIGNED_IN" in err_code or "已经签到" in err_msg:
        msg = f"✅ {label} FurryBox 今天已经签到过了，余额 {balance_before}"
        log(msg)
        return msg
    raise CheckinError(f"签到失败: HTTP {status} code={err_code} message={err_msg[:300]}")


def get_password_accounts():
    """收集邮箱密码账号: EMAIL(+_2.._10) 与 PASSWORD(+_2.._10) 成对出现才算一个。"""
    accounts = []
    for i in [""] + [f"_{n}" for n in range(2, 11)]:
        email = os.getenv(f"FURRYBOX_EMAIL{i}", "").strip()
        password = os.getenv(f"FURRYBOX_PASSWORD{i}", "")
        if email and password:
            accounts.append((email, password))
        elif email or password:
            log(f"警告: FURRYBOX_EMAIL{i} / FURRYBOX_PASSWORD{i} 只填了一个，"
                "这一组会被跳过，邮箱和密码要成对填")
    return accounts


def get_refresh_tokens():
    tokens = []
    for i in [""] + [f"_{n}" for n in range(2, 11)]:
        value = os.getenv(f"FURRYBOX_REFRESH_TOKEN{i}", "").strip()
        if value:
            tokens.append(value)
    return list(dict.fromkeys(tokens))


def main():
    pw_accounts = get_password_accounts()
    if pw_accounts:
        log(f"密码登录，共 {len(pw_accounts)} 个账号，开始签到...")
        results = []
        lines = []
        for idx, (email, password) in enumerate(pw_accounts, 1):
            label = f"{display_name(email)}({email})"
            try:
                log(f"[{label}] 正在用邮箱密码登录...")
                access_token = login_with_password(email, password)
                lines.append(signin_with_access_token(label, access_token))
                results.append(True)
            except CheckinError as e:
                msg = f"❌ {label} {e}"
                log(msg)
                lines.append(msg)
                results.append(False)
        # 一次只发一条通知：表头一行，下面每个账号一行
        write_result(lines)
        log(f"完成: {sum(results)}/{len(results)} 个账号签到成功")
        return 0 if all(results) else 1

    # 没设密码才回退到旧的 refresh_token 方式
    tokens = get_refresh_tokens()
    if tokens:
        log("未设置邮箱密码，回退到 refresh_token 方式(会轮换作废，不推荐)...")
        log(f"共 {len(tokens)} 个账号，开始签到...")
        results = []
        lines = []
        for idx, rt in enumerate(tokens, 1):
            label = f"账号{idx}"
            try:
                log(f"[{label}] 正在用 refresh_token 换取 access_token...")
                access_token = refresh_access_token(rt)
                label = f"账号{idx}({account_label(access_token)})"
                lines.append(signin_with_access_token(label, access_token))
                results.append(True)
            except CheckinError as e:
                msg = f"❌ {label} {e}"
                log(msg)
                lines.append(msg)
                results.append(False)
        write_result(lines)
        log(f"完成: {sum(results)}/{len(results)} 个账号签到成功")
        return 0 if all(results) else 1

    msg = (
        "缺少凭证: 请设置邮箱密码\n"
        "  FURRYBOX_EMAIL / FURRYBOX_PASSWORD\n"
        "  多账号: FURRYBOX_EMAIL_2 / FURRYBOX_PASSWORD_2，依此类推\n"
        "先去 https://id.furry.box/ 点「忘记密码」给 56959566@qq.com 设一个密码\n"
        "(至少 8 位)，设好后先在网页上用邮箱+密码登录一次确认能进。"
    )
    write_result(["❌ " + msg.split("\n")[0]])
    raise SystemExit(msg)


if __name__ == "__main__":
    sys.exit(main())
