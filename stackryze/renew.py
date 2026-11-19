# renew.py - StackRyze 域名自动续签（cookie + CSRF 会话版）
#
# 职责只有一件事：用浏览器里已登录的会话去查 / 续期域名，
# 把结构化结果写到 renew_result.json。
#
# 本脚本不发任何通知、不做任何消息样式：
#   - 没有 Telegram / 微信代码，没有 TG_BOT_TOKEN / WX_PUSH_URL 等环境变量
#   - 没有 emoji、没有 Markdown、没有 HTML，返回的只是纯数据
#   - 通知文案（TG 用 HTML、微信用纯文本）全部在
#     .github/workflows/renew.yml 的 "Send notification" 步骤里，
#     以后想改推送样式只改工作流文件，不用动这个脚本。
#
# 为什么不用 API Key（跟 DigitalPlat 不一样）：
#   - StackRyze 前端源码（github.com/stackryze/FreeDomains，src/lib/api.js）
#     没有 Bearer token：登录是 Zitadel SSO，续期就是
#     POST {API_BASE}/subdomains/{_id}/renew，靠浏览器会话 cookie，
#     写操作还要带 CSRF：cookie 里的 freedomains.csrf 值，
#     同时放到 x-csrf-token 请求头里，另带 X-Requested-With 头。
#   - 续期本身不要人机验证：Turnstile 只在注册页和重发验证邮件时出现，
#     Domains.jsx 里 handleRenew 就是直接调 renew，不带 captchaToken。
#     用户说的“CF 验证”是边缘层的 Cloudflare 质询：curl 直接请求
#     domain.stackryze.com 会拿到 403 "Just a moment..."（2026-11-19 实测），
#     所以纯 requests 脚本必须复用浏览器里已通过质询的 cookie，
#     包括 cf_clearance，否则一律 403，脚本会把这种情况单独记为
#     cf_challenge，而不是误报成“cookie 失效”。
#
# 配置（GitHub Secrets 里填，不要写进仓库）：
#   STACKRYZE_API_URL  - 必填，后端 origin。前端构建时由 VITE_API_URL
#     注入，仓库里只写了 http://localhost:5000，线上值不在仓库里。
#     取法：已登录的浏览器打开 dashboard，按 F12 → Network → 刷新，
#     找到 /subdomains 那条请求，复制它的 Request URL 的 origin 部分。
#     已排除：api.stackryze.com（2026-11-19 实测返回 Cloudflare 1016
#     Origin DNS error，不是后端）。站点的 CSP 是 connect-src 'self'
#     https://*.stackryze.com，说明后端是 *.stackryze.com 下某个子域，
#     也可能是同域；先用 --probe 试 https://domain.stackryze.com，
#     不通再按上面 F12 的办法拿。
#   STACKRYZE_EMAILS   - 3 个账号邮箱，逗号分隔，与 cookie 按序号一一对应，
#     例：56959566@qq.com,adjlevis@qq.com,djbadboy.vip@gmail.com
#     （只用来在结果里分组和认账号；通知里显示的是脱敏版）
#   STACKRYZE_COOKIE_1 / _2 / _3
#     - 每个账号在已登录浏览器里导出的完整 Cookie 字符串（原样粘贴，
#       形如 "cf_clearance=...; freedomains.csrf=...; <会话>=..."）。
#       导出方法见 renew.yml 旁的说明：要在 API 那个 origin 下导出，
#       不能只导 domain.stackryze.com 的；会话过期或 CF 质询过期后
#       要在浏览器里重新登录并重新导出替换 Secret。
#
# 续期规则（官方文档 docs.stackryze.com）：
#   - 到期前 60 天内才开续期，免费，续一次从原到期日往后延一年，
#     不是从续的那天算；每天最多续一次。
#   - 过期后 15 天宽限期内还能续；超过 15 天域名被删，脚本不再尝试。
#   - 剩余天数按前端同样算法算：ceil((到期 - 现在) / 1天)。

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- 1. 从环境变量中读取配置 ---
API_BASE = os.getenv("STACKRYZE_API_URL", "").strip().rstrip("/")

EMAILS_RAW = os.getenv("STACKRYZE_EMAILS", "")
EMAILS = [e.strip() for e in EMAILS_RAW.split(",") if e.strip()]

# 结构化结果的输出路径，工作流的通知步骤会读这个文件。
RESULT_PATH = os.getenv("RENEW_RESULT_PATH", "renew_result.json")

# 到期前 60 天内才尝试续期；过期后 15 天宽限期内仍可续，超过不再尝试。
RENEW_WITHIN_DAYS = 60
GRACE_DAYS = 15

# 前端源码里的固定值（src/lib/api.js）
CSRF_COOKIE_NAME = "freedomains.csrf"
CSRF_HEADER_NAME = "x-csrf-token"


def mask_email(email):
    """脱敏：ad***@qq.com 这种，只在结果/通知里显示，不存完整邮箱以外的东西。"""
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"


def get_cookie_value(cookie_header, name):
    """从完整 Cookie 字符串里取某一个 cookie 的值（CSRF 用）。"""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return None


def get_headers(cookie_header, with_csrf=False):
    """构建跟前端 apiRequest 一致的请求头。"""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie_header,
    }
    if with_csrf:
        csrf = get_cookie_value(cookie_header, CSRF_COOKIE_NAME)
        if csrf:
            headers[CSRF_HEADER_NAME] = csrf
    return headers


def parse_expiry_days(expiry_str):
    """解析到期时间并算剩余天数（向上取整，跟前端 Math.ceil 一致），失败返回 None。"""
    if not expiry_str:
        return None
    expiry_str = str(expiry_str).strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    clean = expiry_str.replace("Z", "+00:00") if expiry_str.endswith("Z") else expiry_str
    for fmt in formats:
        try:
            exp_dt = datetime.strptime(clean, fmt)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            delta_seconds = (exp_dt - datetime.now(timezone.utc)).total_seconds()
            return math.ceil(delta_seconds / 86400)
        except ValueError:
            continue
    # 最后兜底：fromisoformat
    try:
        exp_dt = datetime.fromisoformat(clean)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return math.ceil((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
    except ValueError:
        return None


def classify_http_error(status, body):
    """按响应体给失败定性：只看状态码会误诊，先看体（调试规则第一条）。"""
    text = body or ""
    if status == 401:
        return "session_expired", "会话已过期（401），需在浏览器里重新登录并重新导出 cookie"
    if status == 403 and ("Just a moment" in text or "challenges.cloudflare.com" in text
                          or "cf-challenge" in text.lower() or "Attention Required" in text):
        return "cf_challenge", "被 Cloudflare 质询拦下（403 Just a moment），cookie 里的 cf_clearance 已过期，需在浏览器里重新打开一次 dashboard 再导出 cookie"
    if status == 403:
        return "forbidden", f"被拒绝（403），响应：{text[:200]}"
    if status == 429:
        return "rate_limited", "触发限流（429），下次运行再试"
    return "http_error", f"状态码：{status}，响应：{text[:200]}"


def fetch_domains(cookie_header):
    """GET /subdomains 拿域名列表，返回 (domains, error_dict)。"""
    url = f"{API_BASE}/subdomains"
    try:
        response = requests.get(url, headers=get_headers(cookie_header), timeout=30)
        if response.status_code != 200:
            kind, detail = classify_http_error(response.status_code, response.text)
            logger.error(f"获取域名列表失败：{detail}")
            return None, {"kind": kind, "detail": detail}
        data = response.json()
        if isinstance(data, dict):
            domains = data.get("data") or data.get("subdomains") or data.get("domains") or []
        else:
            domains = data
        return domains, None
    except Exception as e:
        return None, {"kind": "exception", "detail": f"获取域名列表异常：{str(e)}"}


def renew_domain(cookie_header, domain_id):
    """POST /subdomains/{id}/renew，返回 {"status": ..., "body": ...}。"""
    url = f"{API_BASE}/subdomains/{domain_id}/renew"
    try:
        response = requests.post(url, headers=get_headers(cookie_header, with_csrf=True),
                                 timeout=30)
        return {"status": response.status_code, "body": response.text}
    except Exception as e:
        return {"status": 500, "body": str(e)}


def process_account(cookie_header, email, index):
    """处理单个账号下的域名检查与续期，返回结构化 dict（不含任何样式）。"""
    account_label = f"{index}. {mask_email(email)}"
    logger.info(f"开始处理账号：{account_label}")

    if not cookie_header:
        logger.warning(f"账号 {account_label} 未配置对应的 STACKRYZE_COOKIE_{index}，已跳过。")
        return {
            "index": index,
            "email": mask_email(email),
            "status": "no_cookie",
            "error": f"未配置对应的 STACKRYZE_COOKIE_{index}",
            "domains": [],
        }

    domains, err = fetch_domains(cookie_header)
    if err:
        logger.error(f"账号 {account_label} 出错：{err['detail']}")
        return {
            "index": index,
            "email": mask_email(email),
            "status": err["kind"],
            "error": err["detail"],
            "domains": [],
        }

    if not domains:
        logger.info(f"账号 {account_label} 未找到任何域名或获取列表为空。")
        return {
            "index": index,
            "email": mask_email(email),
            "status": "empty",
            "error": None,
            "domains": [],
        }

    domain_results = []
    for d in domains:
        name = d.get("name")
        suffix = d.get("domain") or "indevs.in"
        full_name = f"{name}.{suffix}" if name else (d.get("domain") or d.get("id") or "")
        domain_id = d.get("_id") or d.get("id") or full_name
        status = d.get("status")
        if not full_name:
            continue

        expiry_raw = d.get("expiresAt") or d.get("expires_at") or d.get("expiry_date") or d.get("expires")
        remaining_days = parse_expiry_days(expiry_raw)

        logger.info(f"检查域名：{full_name}（到期：{expiry_raw}，剩余：{remaining_days} 天，状态：{status}）")

        if remaining_days is not None and remaining_days > RENEW_WITHIN_DAYS:
            domain_results.append({
                "name": full_name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "skipped",
                "detail": "未到期",
            })
            continue

        if remaining_days is not None and remaining_days < -GRACE_DAYS:
            domain_results.append({
                "name": full_name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "expired",
                "detail": "已过 15 天宽限期，域名已被删，无法再续",
            })
            continue

        if status == "Pending Deletion":
            domain_results.append({
                "name": full_name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "failed",
                "detail": "待删除状态，前端 Renew 按钮禁用，无法续期",
            })
            continue

        result = renew_domain(cookie_header, domain_id)
        if result["status"] in (200, 201):
            logger.info(f"域名 {full_name} 续期成功！")
            domain_results.append({
                "name": full_name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "renewed",
                "detail": "+1年",
            })
        else:
            msg = result["body"] or ""
            logger.warning(f"域名 {full_name} 续期失败：状态 {result['status']}，响应：{msg[:200]}")
            domain_results.append({
                "name": full_name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "failed",
                "detail": msg[:200],
            })

    return {
        "index": index,
        "email": mask_email(email),
        "status": "ok",
        "error": None,
        "domains": domain_results,
    }


def write_result(result):
    """把结构化结果写文件，供工作流的通知步骤读取。"""
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已写入 {RESULT_PATH}")


def probe(cookie_header):
    """--probe：只测会话通不通。GET /auth/me，打印状态码和响应体原文，不写结果文件。"""
    url = f"{API_BASE}/auth/me"
    try:
        response = requests.get(url, headers=get_headers(cookie_header), timeout=30)
        print(f"GET {url}\nstatus: {response.status_code}\nbody: {response.text[:500]}")
        return 0 if response.status_code == 200 else 1
    except Exception as e:
        print(f"GET {url}\nerror: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="StackRyze 域名自动续签")
    parser.add_argument("--probe", action="store_true",
                        help="只测第一个账号的会话：GET /auth/me，打印状态和响应体")
    args = parser.parse_args()

    try:
        if not API_BASE:
            raise ValueError(
                "缺少必需的环境变量 STACKRYZE_API_URL（后端 origin）。"
                "取法：已登录的浏览器打开 dashboard，F12 → Network → 刷新，"
                "找到 /subdomains 请求，复制 Request URL 的 origin 部分。"
            )

        if args.probe:
            cookie1 = os.getenv("STACKRYZE_COOKIE_1", "").strip()
            if not cookie1:
                raise ValueError("探查需要 STACKRYZE_COOKIE_1，请先配置第一个账号的 cookie。")
            sys.exit(probe(cookie1))

        if not EMAILS:
            raise ValueError("缺少必需的环境变量 STACKRYZE_EMAILS（3 个账号邮箱，逗号分隔）。")

        accounts = []
        for idx, email in enumerate(EMAILS, 1):
            cookie = os.getenv(f"STACKRYZE_COOKIE_{idx}", "").strip()
            accounts.append(process_account(cookie, email, idx))

        summary = {
            "accounts": len(accounts),
            "domains": sum(len(a["domains"]) for a in accounts),
            "renewed": sum(1 for a in accounts for d in a["domains"] if d["result"] == "renewed"),
            "skipped": sum(1 for a in accounts for d in a["domains"] if d["result"] == "skipped"),
            "failed": sum(1 for a in accounts for d in a["domains"] if d["result"] == "failed"),
            "expired": sum(1 for a in accounts for d in a["domains"] if d["result"] == "expired"),
        }
        status = "ok" if summary["failed"] == 0 else "partial"

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status,
            "summary": summary,
            "accounts": accounts,
        }
        write_result(result)
        logger.info(
            "所有账号任务执行完毕：共 %d 个账号 / %d 个域名，续期成功 %d，跳过 %d，失败 %d，过期 %d。",
            summary["accounts"], summary["domains"],
            summary["renewed"], summary["skipped"], summary["failed"], summary["expired"],
        )

    except Exception as e:
        error_msg = f"脚本运行发生严重错误：{str(e)}"
        logger.error(error_msg)
        # 致命错误也要写一份结构化结果，让工作流的通知步骤（if: always()）
        # 能照常发一条报错通知，而不是静默失败。
        write_result({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "error",
            "summary": {"accounts": 0, "domains": 0, "renewed": 0, "skipped": 0, "failed": 0, "expired": 0},
            "accounts": [],
            "error": error_msg,
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
