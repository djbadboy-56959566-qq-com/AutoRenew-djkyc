# renew.py - StackRyze 域名自动续签（浏览器自动登录版）
#
# 取代 2026-11-19 的 cookie 版：不用再导出 cookie，不用管 cookie 过期。
# 真浏览器（Playwright Chromium）打开 dashboard → 点 SSO →
# 在 Zitadel 登录页填邮箱+密码 → 回到 dashboard 后，在已登录的
# 浏览器上下文里调前端同款接口查 / 续期域名。
#
# 为什么必须用真浏览器（用户 2026-11-19 原话：cookie 要自动更新麻烦，
# 网站有 CF 盾）：
#   - 站点前面有 Cloudflare 质询，curl / requests 直接请求拿 403
#     "Just a moment..."（2026-11-19 实测），cookie 里的 cf_clearance
#     过期就得重新进浏览器导一遍。
#   - 真浏览器能自己通过这层质询，会话 cookie 由浏览器自己维护，
#     脚本每次运行都重新登录，不依赖任何事先导出的东西。
#
# 登录说明（2026-11-19 已核实，见源码 + Zitadel 文档）：
#   - 本站登录页只有一个按钮“Continue with Stackryze SSO”，没有
#     用户名/密码框；点它跳到 Zitadel 登录页（auth.stackryze.com），
#     在那里用邮箱（登录名）+密码登录，也可能要第二因素/通行密钥。
#   - 邮箱+密码只从环境变量读，脚本绝不打印密码、不写进结果文件。
#     密码只放 GitHub Secrets，绝不在聊天里发。
#   - 如果账号开了第二因素（TOTP/短信码）或只剩通行密钥登录，
#     脚本无法自动过，会把账号记为 needs_second_factor 并继续下一个，
#     不会在那死等。配了 STACKRYZE_TOTP_N 的账号，脚本会现算验证码
#     自动填进二步验证页（最多试 2 次）；没配的就直接记 needs_second_factor。
#
# 配置（GitHub Secrets 里填，不要写进仓库）：
#   STACKRYZE_EMAILS     - 3 个账号邮箱，逗号分隔，
#     例：56959566@qq.com,adjlevis@qq.com,djbadboy.vip@gmail.com
#   STACKRYZE_PASSWORD_1 / _2 / _3
#     - 与邮箱按序号一一对应的 Zitadel 密码。
#   STACKRYZE_TOTP_1 / _2 / _3（可选，有 2FA 的账号才要）
#     - 每个账号的验证器种子（TOTP secret），脚本用它现算 6 位验证码
#       自动填进 Zitadel 的二步验证页。种子只放 Secrets，绝不发聊天。
#     怎么拿到种子：在 Zitadel（auth.stackryze.com）账号设置里找到
#       二步验证/验证器，重新设置一次会显示新的二维码和种子；
#       当初设置时保存过二维码或种子文本的，直接用那个。
#   STACKRYZE_DASHBOARD_URL - 可选，默认 https://domain.stackryze.com/dashboard
#   STACKRYZE_HEADLESS   - 可选，默认 1（无头）；本地调试可设 0 看浏览器。
#
# 续期规则（官方文档 docs.stackryze.com）：
#   - 到期前 60 天内才开续期，免费，续一次从原到期日往后延一年，
#     不是从续的那天算；每天最多续一次。
#   - 过期后 15 天宽限期内还能续；超过 15 天域名被删，脚本不再尝试。
#   - 剩余天数按前端同样算法算：ceil((到期 - 现在) / 1天)。
#
# 结果：只写结构化 stackryze_result.json，不发任何通知、不做任何消息样式，
# 通知文案全在 .github/workflows/stackryze-renew.yml 的 "Send notification" 步骤里。

import argparse
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

try:
    import pyotp  # 2FA 验证码现算用；没装时有 2FA 的账号会记 needs_second_factor
except ImportError:
    pyotp = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- 1. 从环境变量中读取配置 ---
DASHBOARD_URL = os.getenv(
    "STACKRYZE_DASHBOARD_URL", "https://domain.stackryze.com/dashboard"
).strip().rstrip("/")

EMAILS_RAW = os.getenv("STACKRYZE_EMAILS", "")
EMAILS = [e.strip() for e in EMAILS_RAW.split(",") if e.strip()]

HEADLESS = os.getenv("STACKRYZE_HEADLESS", "1") != "0"

# 结构化结果的输出路径，工作流的通知步骤会读这个文件。
RESULT_PATH = os.getenv("RENEW_RESULT_PATH", "stackryze_result.json")

# 到期前 60 天内才尝试续期；过期后 15 天宽限期内仍可续，超过不再尝试。
RENEW_WITHIN_DAYS = 60
GRACE_DAYS = 15

# 前端源码里的固定值（src/lib/api.js）
CSRF_COOKIE_NAME = "freedomains.csrf"

# 像正常 Chrome 一样访问，降低被质询拦截的概率。
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CF_WAIT_SECONDS = 120


class AccountError(Exception):
    """单个账号失败：kind 是给工作流/通知看的状态，detail 是给人看的原因。"""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def mask_email(email):
    """脱敏：ad***@qq.com 这种，只在结果/通知里显示。"""
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"


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
    try:
        exp_dt = datetime.fromisoformat(clean)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return math.ceil((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
    except ValueError:
        return None


def page_text_contains(page, patterns):
    """页面正文里是否出现任一关键词（登录报错、第二因素等判断用）。"""
    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return None
    for p in patterns:
        if p.lower() in text:
            return p
    return None


def wait_for_cf(page, timeout=CF_WAIT_SECONDS):
    """等 Cloudflare 质询过去：标题不再是 "Just a moment..."。超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if "Just a moment" not in page.title():
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def first_visible(page, selector, timeout=5000):
    """某个选择器在 timeout 内是否可见。"""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        return loc
    except Exception:
        return None


def click_button_by_text(page, pattern, timeout=5000):
    """按文字点按钮（Continue / Next / Sign in 等），点到返回 True。"""
    try:
        btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
        btn.wait_for(state="visible", timeout=timeout)
        btn.click(timeout=timeout)
        return True
    except Exception:
        return False


def wait_for_login_state(page, timeout=30):
    """等 SPA 的鉴权跳转稳定再判断：登录页/SSO 按钮 → 'login'，
    侧栏出现 My Domains → 'dashboard'（已登录），超时 → 'unknown'。
    2026-11-20 的教训：goto 刚返回时 SPA 还没跳完，URL 还是 /dashboard
    且没有 SSO 按钮，直接判断会误以为已登录，随后 goto /my-domains
    被站内的跳转打断。"""
    sso = page.get_by_role("button", name=re.compile(r"Continue with Stackryze SSO", re.I))
    domains_link = page.get_by_role("link", name=re.compile(r"My Domains", re.I))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "/login" in page.url:
            return "login"
        try:
            if sso.count() > 0:
                return "login"
        except Exception:
            pass
        try:
            if domains_link.count() > 0:
                return "dashboard"
        except Exception:
            pass
        time.sleep(1)
    return "unknown"


def goto_with_retry(page, url, timeout=60000):
    """goto 被站内另一次跳转打断时（SPA 登录后常有），等 3 秒再试一次。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as e:
        if "interrupted by another navigation" in str(e):
            logger.warning(f"goto {url} 被站内跳转打断，3 秒后重试。")
            time.sleep(3)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        else:
            raise


def fill_totp_code(page, code):
    """把 6 位验证码填进二步验证页：单个输入框、6 个单字框都支持。"""
    single = first_visible(page, 'input[autocomplete="one-time-code"]', timeout=3000)
    if single:
        single.fill(code)
        return True
    boxes = page.locator('input[maxlength="1"]')
    try:
        n = boxes.count()
    except Exception:
        n = 0
    if n >= 6:
        for i, ch in enumerate(code[:n]):
            boxes.nth(i).fill(ch)
        return True
    generic = first_visible(
        page, 'input[type="text"], input[inputmode="numeric"]', timeout=3000
    )
    if generic:
        generic.fill(code)
        return True
    return False


def zitadel_login(page, email, password, home_netloc, totp_secret=None):
    """在 Zitadel 登录页完成邮箱+密码登录，直到跳回本站 dashboard。"""
    # 第一步：等登录页出现任一输入框（登录名或密码）。
    deadline = time.time() + 30
    loginname = password_field = None
    while time.time() < deadline:
        loginname = first_visible(
            page,
            'input[type="email"], input[name="loginName"], input[name="loginname"], '
            'input[name="username"], input[autocomplete="username"]',
            timeout=2000,
        )
        password_field = first_visible(
            page, 'input[type="password"], input[autocomplete="current-password"]', timeout=2000
        )
        if loginname or password_field:
            break
        # 只有通行密钥/第三方登录，没有密码框：先找“用密码登录”的退路。
        if page_text_contains(page, ["passkey"]):
            if click_button_by_text(page, r"password", timeout=3000):
                continue
        if first_visible(page, 'button:has-text("GitHub")', timeout=2000) and not loginname:
            raise AccountError(
                "no_password_login",
                "Zitadel 登录页没有邮箱密码框，只有第三方登录按钮，无法自动登录",
            )
        time.sleep(1)
    if not loginname and not password_field:
        raise AccountError(
            "login_failed",
            "Zitadel 登录页 30 秒内没出现邮箱/密码输入框，页面结构可能变了",
        )

    # 第二步：如果只有登录名框，先填邮箱点继续，等密码框出现。
    if loginname and not password_field:
        loginname.fill(email)
        if not click_button_by_text(page, r"continue|next", timeout=5000):
            loginname.press("Enter")
        password_field = first_visible(
            page, 'input[type="password"], input[autocomplete="current-password"]', timeout=20000
        )
        if not password_field:
            hit = page_text_contains(
                page, ["incorrect", "invalid", "not found", "locked", "too many"]
            )
            raise AccountError(
                "login_failed",
                f"填完邮箱后没出现密码框"
                + (f"（页面提示含“{hit}”，邮箱可能不对）" if hit else "（可能要第二因素或页面结构变了）"),
            )

    # 第三步：填密码提交。注意：密码只填进输入框，绝不打印到任何地方。
    password_field.fill(password)
    if not click_button_by_text(page, r"continue|sign in|log in", timeout=5000):
        password_field.press("Enter")

    # 第四步：等跳回本站（dashboard 或 my-domains），同时盯着报错和第二因素。
    # 有 TOTP 种子时，二步验证页出现就现算验证码填进去（最多试 2 次，
    # 错了 Zitadel 会留在验证页并提示，可再试一次）；没种子则直接记
    # needs_second_factor，不死等。
    deadline = time.time() + 90
    totp_attempts = 0
    twofa_patterns = [
        "second factor", "two-factor", "two factor", "authenticator",
        "verification code", "enter the code", "one-time code", "one time code",
    ]
    while time.time() < deadline:
        url = page.url
        if urlparse(url).netloc == home_netloc and (
            "/dashboard" in url or "/my-domains" in url or "/overview" in url
        ):
            return True
        code_field = first_visible(
            page, 'input[autocomplete="one-time-code"], input[maxlength="1"]', timeout=1000
        )
        twofa_text = page_text_contains(page, twofa_patterns)
        if (code_field or twofa_text) and totp_secret and pyotp and totp_attempts < 2:
            totp_attempts += 1
            code = pyotp.TOTP(totp_secret).now()
            logger.info("检测到二步验证，用 TOTP 种子现算验证码填入。")
            if fill_totp_code(page, code):
                if not click_button_by_text(
                    page, r"continue|verify|sign in|confirm", timeout=5000
                ):
                    page.keyboard.press("Enter")
                deadline = time.time() + 60
                time.sleep(2)
                continue
        if code_field or twofa_text:
            raise AccountError(
                "needs_second_factor",
                "Zitadel 要求第二因素验证码，脚本无法自动过："
                "请给这个账号配 STACKRYZE_TOTP_N（验证器种子，放 Secrets 里），"
                "或先在账号里关掉第二因素再跑",
            )
        hit = page_text_contains(
            page, ["incorrect", "invalid password", "wrong password", "locked", "too many attempts"]
        )
        if hit:
            raise AccountError("login_failed", f"登录被拒（页面提示含“{hit}”，邮箱或密码不对）")
        time.sleep(2)
    raise AccountError(
        "login_failed",
        "提交密码后 90 秒没跳回 dashboard（可能要第二因素/通行密钥，或 Zitadel 页面结构变了）",
    )


def fetch_domains_in_browser(page, origin, captured):
    """拿域名列表：先用 dashboard 自己发的那条 /subdomains 请求的地址
    （从而知道后端接口在哪），再在浏览器里按这个地址取一次列表；
    没抓到就直接按同域取一次。返回 (api_base, domains)。"""
    if captured:
        api_url = captured[0]
        api_base = api_url.split("/subdomains")[0]
        url_to_fetch = api_url
    else:
        api_base = origin
        url_to_fetch = origin + "/subdomains"

    data = page.evaluate(
        """async (url) => {
            const r = await fetch(url, {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                credentials: 'include'
            });
            const t = await r.text();
            return {status: r.status, body: t.slice(0, 20000)};
        }""",
        url_to_fetch,
    )
    if data["status"] != 200:
        raise AccountError(
            "api_error",
            f"取域名列表失败（状态 {data['status']}）：{data['body'][:200]}",
        )
    try:
        parsed = json.loads(data["body"])
    except Exception as e:
        raise AccountError("api_error", f"/subdomains 响应解析失败：{e}")

    if isinstance(parsed, dict):
        domains = parsed.get("data") or parsed.get("subdomains") or parsed.get("domains") or []
    else:
        domains = parsed
    return api_base, domains


def api_renew(page, api_base, csrf, domain_id):
    """在已登录的浏览器里调前端同款续期接口，返回 (status, body)。"""
    result = page.evaluate(
        """async ({base, id, csrf}) => {
            const headers = {'Content-Type': 'application/json',
                             'X-Requested-With': 'XMLHttpRequest'};
            if (csrf) headers['x-csrf-token'] = csrf;
            const r = await fetch(base + '/subdomains/' + id + '/renew', {
                method: 'POST', headers, credentials: 'include'
            });
            const t = await r.text();
            return {status: r.status, body: t.slice(0, 500)};
        }""",
        {"base": api_base, "id": domain_id, "csrf": csrf or ""},
    )
    return result["status"], result["body"]


def process_account(browser, email, password, index, totp_secret=None, probe=False):
    """处理单个账号：登录 → 拿列表 → 续 60 天内的域名。返回结构化 dict。"""
    account_label = f"{index}. {mask_email(email)}"
    logger.info(f"开始处理账号：{account_label}")

    if not password:
        return {
            "index": index,
            "email": mask_email(email),
            "status": "no_password",
            "error": f"未配置对应的 STACKRYZE_PASSWORD_{index}",
            "domains": [],
        }

    # TOTP 种子先验一次格式：错了直接报出来，别等进了二步验证页才崩。
    if totp_secret and pyotp:
        try:
            pyotp.TOTP(totp_secret).now()
        except Exception:
            return {
                "index": index,
                "email": mask_email(email),
                "status": "error",
                "error": f"STACKRYZE_TOTP_{index} 种子格式不对（应为验证器给的 Base32 种子），"
                         "请检查 Secrets 里是不是多粘了空格或缺了字符",
                "domains": [],
            }

    home = urlparse(DASHBOARD_URL)
    home_netloc = home.netloc
    origin = f"{home.scheme}://{home.netloc}"

    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        user_agent=CHROME_UA,
    )
    page = context.new_page()
    captured = []

    def on_response(resp):
        # 只记 URL，不在这里读响应体：在 handler 里调 resp.body()/resp.json()
        # 会失败。列表后面用浏览器里的 fetch 再取一次。
        try:
            if "/subdomains" in resp.url and resp.request.method == "GET" and resp.status == 200:
                captured.append(resp.url)
        except Exception:
            pass

    page.on("response", on_response)
    try:
        goto_with_retry(page, DASHBOARD_URL)
        if not wait_for_cf(page):
            raise AccountError(
                "cf_challenge",
                "Cloudflare 质询 120 秒没过去（真浏览器也没通过），本次无法登录",
            )

        # 先等 SPA 的鉴权跳转稳定，再判断是登录页还是已登录的仪表盘。
        state = wait_for_login_state(page)
        if state == "unknown":
            raise AccountError(
                "login_failed",
                "打开 dashboard 后 30 秒没出现登录按钮也没出现仪表盘菜单，"
                "页面可能卡住了或结构变了",
            )
        if state == "login":
            logger.info(f"账号 {account_label} 未登录，点 SSO 去 Zitadel 登录。")
            sso = page.get_by_role(
                "button", name=re.compile(r"Continue with Stackryze SSO", re.I)
            )
            sso.first.click(timeout=15000)
            # 等离开登录页：跳到 Zitadel，或直接回到 dashboard。
            deadline = time.time() + 60
            while time.time() < deadline:
                if "/dashboard" in page.url or "/my-domains" in page.url:
                    break
                if first_visible(
                    page,
                    'input[type="email"], input[type="password"], '
                    'input[name="loginName"], input[name="username"]',
                    timeout=2000,
                ):
                    break
                time.sleep(1)
            if "/dashboard" not in page.url and "/my-domains" not in page.url:
                zitadel_login(page, email, password, home_netloc, totp_secret)
                if not wait_for_cf(page):
                    raise AccountError("cf_challenge", "登录后 Cloudflare 质询 120 秒没过去")
            logger.info(f"账号 {account_label} 登录成功。")

        # 进 My Domains，强制拉一次域名列表，顺便抓到后端接口地址。
        goto_with_retry(page, origin + "/my-domains")
        if not wait_for_cf(page):
            raise AccountError("cf_challenge", "进 My Domains 时 Cloudflare 质询 120 秒没过去")
        deadline = time.time() + 30
        while time.time() < deadline and not captured:
            time.sleep(1)

        api_base, domains = fetch_domains_in_browser(page, origin, captured)
        logger.info(f"账号 {account_label} 后端接口：{api_base}，域名 {len(domains)} 个。")

        if not domains:
            return {
                "index": index,
                "email": mask_email(email),
                "status": "empty",
                "error": None,
                "domains": [],
            }

        # CSRF：从浏览器 cookie 里取 freedomains.csrf（前端同款做法）。
        csrf = None
        for c in context.cookies():
            if c.get("name") == CSRF_COOKIE_NAME:
                csrf = c.get("value")
                break

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
            logger.info(
                f"检查域名：{full_name}（到期：{expiry_raw}，剩余：{remaining_days} 天，状态：{status}）"
            )

            if remaining_days is not None and remaining_days > RENEW_WITHIN_DAYS:
                domain_results.append({
                    "name": full_name, "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days, "result": "skipped", "detail": "未到期",
                })
                continue
            if remaining_days is not None and remaining_days < -GRACE_DAYS:
                domain_results.append({
                    "name": full_name, "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days, "result": "expired",
                    "detail": "已过 15 天宽限期，域名已被删，无法再续",
                })
                continue
            if status == "Pending Deletion":
                domain_results.append({
                    "name": full_name, "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days, "result": "failed",
                    "detail": "待删除状态，前端 Renew 按钮禁用，无法续期",
                })
                continue

            if probe:
                domain_results.append({
                    "name": full_name, "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days, "result": "would_renew",
                    "detail": "探查模式：可续，未执行",
                })
                continue

            http_status, body = api_renew(page, api_base, csrf, domain_id)
            if http_status in (200, 201):
                logger.info(f"域名 {full_name} 续期成功！")
                domain_results.append({
                    "name": full_name, "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days, "result": "renewed", "detail": "+1年",
                })
            else:
                logger.warning(f"域名 {full_name} 续期失败：状态 {http_status}，响应：{body[:200]}")
                domain_results.append({
                    "name": full_name, "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days, "result": "failed", "detail": body[:200],
                })

        return {
            "index": index,
            "email": mask_email(email),
            "status": "ok",
            "error": None,
            "domains": domain_results,
        }
    except AccountError as e:
        logger.error(f"账号 {account_label} 出错：{e.detail}")
        return {
            "index": index,
            "email": mask_email(email),
            "status": e.kind,
            "error": e.detail,
            "domains": [],
        }
    except Exception as e:
        logger.error(f"账号 {account_label} 异常：{e}")
        return {
            "index": index,
            "email": mask_email(email),
            "status": "error",
            "error": f"浏览器操作异常：{str(e)[:200]}",
            "domains": [],
        }
    finally:
        context.close()


def write_result(result):
    """把结构化结果写文件，供工作流的通知步骤读取。"""
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已写入 {RESULT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="StackRyze 域名自动续签（浏览器自动登录版）")
    parser.add_argument("--probe", action="store_true",
                        help="只探查：登录第一个账号，列出域名和剩余天数，不续期")
    args = parser.parse_args()

    try:
        if not EMAILS:
            raise ValueError("缺少必需的环境变量 STACKRYZE_EMAILS（3 个账号邮箱，逗号分隔）。")

        accounts = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            try:
                for idx, email in enumerate(EMAILS, 1):
                    # 密码和 TOTP 种子只从环境变量读进内存，绝不打印。
                    password = os.getenv(f"STACKRYZE_PASSWORD_{idx}", "")
                    totp_secret = os.getenv(f"STACKRYZE_TOTP_{idx}", "")
                    accounts.append(
                        process_account(
                            browser, email, password, idx,
                            totp_secret=totp_secret, probe=args.probe,
                        )
                    )
                    if args.probe:
                        break
            finally:
                browser.close()

        if args.probe:
            for acc in accounts:
                print(f"账号{acc['index']} · {acc['email']}：{acc.get('status')}"
                      + (f"（{acc.get('error')}）" if acc.get("error") else ""))
                for d in acc["domains"]:
                    print(f"  {d['name']} 到期 {d['expiry']}，剩 {d['remaining_days']} 天：{d['detail']}")
            sys.exit(0 if all(a["status"] == "ok" for a in accounts) else 1)

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
