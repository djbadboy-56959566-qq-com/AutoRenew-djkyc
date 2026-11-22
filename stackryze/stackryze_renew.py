# stackryze_renew.py - StackRyze 域名自动续签（纯接口版，不用浏览器）
#
# 取代 2026-11-20 的浏览器版：浏览器版在 GitHub Actions 机房 IP 上走不通，
# 调试证明 CF 给机房 IP 的是无交互 "Performing security verification" 页，
# 0 个 iframe，没有勾选框可点；有头真 Chrome + Stealth 也过不去。
# 纯接口版全程只用标准库 urllib，不装浏览器、不装任何 pip 包。
#
# 为什么能绕开 CF（2026-11-20 实测）：
#   - 盾只在前端 domain.stackryze.com 前面；登录站 auth.stackryze.com
#     和接口站 api-domains.stackryze.com 都不走 CF，curl 直接能通
#     （API 未登录返回 FREEDOMAINS-SESSION-001，不是 CF 拦截）。
#   - 前端源码 stackryze/FreeDomains：登录是 Zitadel SSO，后端
#     api-domains.stackryze.com 是 OIDC 客户端；接口用 cookie 会话，
#     写操作带 freedomains.csrf（x-csrf-token 头）。
#
# 登录流程（证据：前端源码 + 登录页 JS + 2026-11-20 curl 实测）：
#   1. GET {API_BASE}/auth/zitadel → 302 到 Zitadel authorize（带 client_id、
#      redirect_uri={API_BASE}/auth/zitadel/callback、state、PKCE
#      code_challenge；后端把 state/verifier 存在自己的 cookie 里，全程
#      用同一个 cookie jar 跟着走）。
#   2. GET authorize → 307 到 /login?authRequest=V2_... → 307 到
#      /en/login?authRequest=V2_...（200，Zitadel 登录页）。
#   3. POST {AUTH_BASE}/api/login {username, password, authRequestId}
#      （登录页 JS 里的原样调用；实测假账号返回 400
#      {"code":"INVALID_DATA","message":"invalid credentials"}，
#      说明请求格式正确）。2FA 强制的账号返回 mfaRequired + userId + orgId。
#   4. POST {AUTH_BASE}/api/mfa/verify {userId, code, authRequestId,
#      rememberDevice:false} → {callbackUrl}（登录页 MFA 组件里的原样调用；
#      code 是 TOTP 种子现算的 6 位码，最多试 2 次）。
#   5. GET callbackUrl → 302 回后端 /auth/zitadel/callback?code=...&state=...
#      → 后端换 token、设会话 cookie。跟完跳转后用 GET {API_BASE}/auth/me
#      验一下 authenticated=true 再往下走。
#   6. GET {API_BASE}/subdomains 拿列表；POST
#      {API_BASE}/subdomains/{id}/renew 续期，带 X-Requested-With 和
#      x-csrf-token（freedomains.csrf cookie 的值）。
#
# 配置（GitHub Secrets 里填，不要写进仓库）：
#   STACKRYZE_EMAILS     - 3 个账号邮箱，逗号分隔，
#     例：56959566@qq.com,adjlevis@qq.com,djbadboy.vip@gmail.com
#   STACKRYZE_PASSWORD_1 / _2 / _3
#     - 与邮箱按序号一一对应的 Zitadel 密码。
#   STACKRYZE_TOTP_1 / _2 / _3（有 2FA 的账号必填，站里强制开了二步验证）
#     - 每个账号的验证器种子（TOTP secret），脚本现算 6 位验证码。
#       种子只放 Secrets，绝不发聊天；只取 secret= 后面那段，不要整个
#       otpauth:// 地址。
#   STACKRYZE_API_BASE   - 可选，默认 https://api-domains.stackryze.com
#   STACKRYZE_AUTH_BASE  - 可选，默认 https://auth.stackryze.com
#   （上面两个只在本地 mock 测试时改成 http://127.0.0.1:端口）
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
import base64
import hashlib
import hmac
import http.client
import http.cookiejar
import json
import logging
import math
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- 1. 从环境变量中读取配置 ---
API_BASE = os.getenv(
    "STACKRYZE_API_BASE", "https://api-domains.stackryze.com"
).strip().rstrip("/")
AUTH_BASE = os.getenv(
    "STACKRYZE_AUTH_BASE", "https://auth.stackryze.com"
).strip().rstrip("/")

EMAILS_RAW = os.getenv("STACKRYZE_EMAILS", "")
EMAILS = [e.strip() for e in EMAILS_RAW.split(",") if e.strip()]

# 结构化结果的输出路径，工作流的通知步骤会读这个文件。
RESULT_PATH = os.getenv("RENEW_RESULT_PATH", "stackryze_result.json")

# 到期前 60 天内才尝试续期；过期后 15 天宽限期内仍可续，超过不再尝试。
RENEW_WITHIN_DAYS = 60
GRACE_DAYS = 15

# 后端 CSRF：freedomains.csrf cookie 的值放到 x-csrf-token 头（双提交模式）。
CSRF_COOKIE_NAME = "freedomains.csrf"

# 每次 HTTP 请求的超时（秒）。
REQUEST_TIMEOUT = 30

# 像正常 Chrome 一样访问。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


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


def totp_now(secret):
    """标准 TOTP（RFC 6238：SHA1、30 秒、6 位），纯标准库实现，
    跟验证器 App / pyotp 算出来的一样。种子是 Base32 文本。"""
    clean = re.sub(r"\s+", "", secret).upper()
    # Base32 长度须是 8 的倍数，不足补 '='。
    clean += "=" * (-len(clean) % 8)
    key = base64.b32decode(clean)
    counter = int(time.time()) // 30
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


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


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """不自动跟跳转：返回 3xx 响应本身，让调用方读 Location 自己决定。
    urllib 默认跟跳转会把 POST 变 GET、还会丢 cookie 的归属判断，
    登录链每一步都要看清跳去哪，所以手动跟。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpClient:
    """带 cookie jar 的 HTTP 客户端（urllib 标准库）。"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirectHandler(),
        )

    def request(self, method, url, body=None, headers=None, timeout=REQUEST_TIMEOUT, retries=2):
        """发一次请求，不跟跳转。返回 (status, headers, body_bytes)。
        3xx 也正常返回，不抛错；4xx/5xx 也一样，调用方按状态码判断。
        网络层出问题（连接被断、响应被截断、超时）会自动重试几次；
        5xx（500/502/503/504）是服务端一时出问题，也会等一会儿再试
        ——2026-11-22 Zitadel 就偶尔返回 500，不重试整个账号就白跑了；
        续期 POST 不重试（retries=0：响应丢了也可能是服务端已续成功，
        重试会把成功算成失败）。"""
        last_exc = None
        for attempt in range(retries + 1):
            try:
                status, resp_headers, body_bytes = self._request_once(
                    method, url, body, headers, timeout
                )
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
                last_exc = e
                if attempt < retries:
                    logger.warning(
                        f"请求 {url} 网络出错（{type(e).__name__}：{e}），"
                        f"第 {attempt + 1}/{retries + 1} 次，稍后重试。"
                    )
                    time.sleep(1 + attempt)
                    continue
                raise AccountError(
                    "api_error",
                    f"请求 {url} 网络失败（已重试 {retries + 1} 次）：{last_exc}",
                )
            if status in (500, 502, 503, 504) and attempt < retries:
                logger.warning(
                    f"请求 {url} 返回 {status}，服务端可能一时出问题，"
                    f"第 {attempt + 1}/{retries + 1} 次，稍后重试。"
                )
                time.sleep(2 + attempt * 2)
                continue
            return status, resp_headers, body_bytes
        raise AccountError(
            "api_error",
            f"请求 {url} 多次返回 5xx（已重试 {retries + 1} 次），服务端一直没恢复",
        )

    def _request_once(self, method, url, body=None, headers=None, timeout=REQUEST_TIMEOUT):
        data = None
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            # HTTPError 自带响应体（4xx/5xx 的错误 JSON 在里面）。
            # 注意：HTTPError 是 URLError 的子类，这里先接住返回；
            # 其余 URLError（DNS、连接失败等）不接，抛给外层重试。
            return e.code, e.headers, e.read()

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post_json(self, url, body, **kwargs):
        return self.request("POST", url, body=body, **kwargs)

    def follow(self, url, max_hops=10):
        """手动跟跳转链（只跟 GET），返回 (final_status, final_url, body)。
        每跳都经过同一个 cookie jar，所以后端 callback 设的会话 cookie
        能留下来；终点哪怕是 CF 403 的前端页也不怕，cookie 已经到手。"""
        current = url
        for _ in range(max_hops):
            status, headers, body = self.get(current)
            if status in (301, 302, 303, 307, 308):
                loc = headers.get("Location")
                if not loc:
                    return status, current, body
                current = urllib.parse.urljoin(current, loc)
                continue
            return status, current, body
        return status, current, body

    def cookie_value(self, name):
        """从 cookie jar 里取指定名字的值（CSRF 用）。"""
        for c in self.jar:
            if c.name == name:
                return c.value
        return None

    @staticmethod
    def json_body(body_bytes):
        """解析 JSON 响应体，解析失败返回 None。"""
        try:
            return json.loads(body_bytes.decode("utf-8", "replace"))
        except Exception:
            return None

    @staticmethod
    def error_message(data, raw, max_len=200):
        """从错误响应里抠一句人能看懂的原因：优先 message/error/code。"""
        if isinstance(data, dict):
            for key in ("message", "error", "msg", "detail"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()[:max_len]
                if isinstance(val, dict):
                    for sub in ("message", "error"):
                        if isinstance(val.get(sub), str) and val[sub].strip():
                            return val[sub].strip()[:max_len]
            if data.get("code"):
                return str(data["code"])[:max_len]
        text = raw.decode("utf-8", "replace").strip()
        return text[:max_len] if text else "（无错误信息）"


def zitadel_login(client, email, password, totp_secret=None):
    """走 Zitadel 接口完成登录，拿到后端会话。成功返回 True。
    步骤见文件头注释；每一步的失败都按类型报出来，不猜。"""
    # 第 1 步：后端 /auth/zitadel → 302 到 Zitadel authorize。
    status, headers, _ = client.get(f"{API_BASE}/auth/zitadel")
    if status not in (301, 302, 303, 307, 308):
        raise AccountError(
            "login_failed",
            f"后端 /auth/zitadel 没跳去 Zitadel（状态 {status}），"
            "后端登录入口可能变了",
        )
    authorize_url = urllib.parse.urljoin(
        f"{API_BASE}/auth/zitadel", headers.get("Location", "")
    )
    if "auth.stackryze.com" not in authorize_url and "/oauth/v2/authorize" not in authorize_url:
        raise AccountError(
            "login_failed",
            f"后端 /auth/zitadel 跳去了一个意外的地址：{authorize_url[:120]}",
        )

    # 第 2 步：authorize → 307 到登录页，取 authRequestId。
    status, headers, _ = client.get(authorize_url)
    if status not in (301, 302, 303, 307, 308):
        hint = "Zitadel 那边一时出问题，稍后重跑试试" if status >= 500 else "授权请求可能被拒了"
        raise AccountError(
            "login_failed",
            f"Zitadel authorize 没跳去登录页（状态 {status}），{hint}",
        )
    login_url = urllib.parse.urljoin(authorize_url, headers.get("Location", ""))
    # 登录页本身还会 307 到 /en/login，手动跟到 200 为止。
    status, final_url, _ = client.follow(login_url)
    if status != 200:
        raise AccountError(
            "login_failed",
            f"打开 Zitadel 登录页失败（状态 {status}），页面可能变了",
        )
    m = re.search(r"[?&]authRequest=([^&]+)", final_url)
    if not m:
        raise AccountError(
            "login_failed",
            f"登录页 URL 里没有 authRequestId（{final_url[:120]}），"
            "登录页结构可能变了",
        )
    auth_request_id = urllib.parse.unquote(m.group(1))

    # 第 3 步：POST /api/login 交邮箱密码（登录页 JS 的原样调用）。
    status, _, raw = client.post_json(
        f"{AUTH_BASE}/api/login",
        {"username": email, "password": password, "authRequestId": auth_request_id},
    )
    data = client.json_body(raw)
    if status == 200 and isinstance(data, dict):
        if data.get("callbackUrl"):
            return _finish_login(client, data["callbackUrl"])
        if data.get("verifyRequired"):
            raise AccountError(
                "login_failed",
                "这个账号要先验证邮箱才能登录，请先在浏览器里完成邮箱验证再跑",
            )
        if data.get("changeRequired"):
            raise AccountError(
                "login_failed",
                "这个账号的密码要求更换，请先在浏览器里改好密码再跑",
            )
        if data.get("mfaRequired") and data.get("userId"):
            return _mfa_verify(
                client, data["userId"], auth_request_id, totp_secret
            )
        raise AccountError(
            "login_failed",
            f"/api/login 返回了看不懂的结果：{str(data)[:200]}",
        )
    raise AccountError(
        "login_failed",
        f"邮箱或密码不对（/api/login 状态 {status}："
        f"{client.error_message(data, raw)}）",
    )


def _mfa_verify(client, user_id, auth_request_id, totp_secret):
    """二步验证：用 TOTP 种子现算验证码交 /api/mfa/verify，最多试 2 次。"""
    if not totp_secret:
        raise AccountError(
            "needs_second_factor",
            "Zitadel 要求第二因素验证码，但这个账号没配 STACKRYZE_TOTP_N"
            "（验证器种子，放 Secrets 里），无法自动过",
        )
    last_error = ""
    for attempt in range(2):
        code = totp_now(totp_secret)
        logger.info("检测到二步验证，用 TOTP 种子现算验证码提交。")
        status, _, raw = client.post_json(
            f"{AUTH_BASE}/api/mfa/verify",
            {
                "userId": user_id,
                "code": code,
                "authRequestId": auth_request_id,
                "rememberDevice": False,
            },
        )
        data = client.json_body(raw)
        if status == 200 and isinstance(data, dict) and data.get("callbackUrl"):
            return _finish_login(client, data["callbackUrl"])
        last_error = client.error_message(data, raw)
        logger.warning(f"二步验证第 {attempt + 1} 次没通过（状态 {status}：{last_error}），再试一次。")
        time.sleep(2)
    raise AccountError(
        "login_failed",
        f"二步验证码两次都没通过（{last_error}），"
        "可能是种子不对或手机时间不准，请检查 STACKRYZE_TOTP_N",
    )


def _finish_login(client, callback_url):
    """拿 callbackUrl 回后端换会话，再用 /auth/me 验一下。"""
    status, final_url, _ = client.follow(callback_url)
    logger.info(f"登录回调跟完，最终地址：{final_url[:100]}（状态 {status}）。")
    status, _, raw = client.get(f"{API_BASE}/auth/me")
    data = client.json_body(raw)
    if status == 200 and isinstance(data, dict) and data.get("authenticated"):
        return True
    raise AccountError(
        "login_failed",
        f"回调后 /auth/me 没认出登录态（状态 {status}："
        f"{client.error_message(data, raw)}），会话可能没建起来",
    )


def fetch_domains(client):
    """拿域名列表。返回 (domains,)。"""
    status, _, raw = client.get(
        f"{API_BASE}/subdomains",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    data = client.json_body(raw)
    if status != 200:
        raise AccountError(
            "api_error",
            f"取域名列表失败（状态 {status}：{client.error_message(data, raw)}）",
        )
    if isinstance(data, dict):
        domains = data.get("data") or data.get("subdomains") or data.get("domains") or []
    else:
        domains = data
    if not isinstance(domains, list):
        raise AccountError("api_error", f"/subdomains 响应格式不对：{str(data)[:200]}")
    return domains


def api_renew(client, domain_id):
    """调续期接口，返回 (status, body_text)。
    续期不重试：响应丢了也可能是服务端已续成功，重试会把成功算成失败；
    真丢了下次定时跑会看到新到期日自动跳过，不会重复续。"""
    headers = {"X-Requested-With": "XMLHttpRequest"}
    csrf = client.cookie_value(CSRF_COOKIE_NAME)
    if csrf:
        headers["x-csrf-token"] = csrf
    status, _, raw = client.post_json(
        f"{API_BASE}/subdomains/{domain_id}/renew", {}, headers=headers, retries=0
    )
    return status, raw.decode("utf-8", "replace")


def process_account(email, password, index, totp_secret=None, probe=False):
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

    # TOTP 种子先验一次格式：错了直接报出来，别等进了二步验证才崩。
    if totp_secret:
        try:
            totp_now(totp_secret)
        except Exception:
            return {
                "index": index,
                "email": mask_email(email),
                "status": "error",
                "error": f"STACKRYZE_TOTP_{index} 种子格式不对（应为验证器给的 Base32 种子），"
                         "请检查 Secrets 里是不是多粘了空格或缺了字符",
                "domains": [],
            }

    client = HttpClient()
    try:
        zitadel_login(client, email, password, totp_secret)
        logger.info(f"账号 {account_label} 登录成功。")

        domains = fetch_domains(client)
        logger.info(f"账号 {account_label} 域名 {len(domains)} 个。")

        if not domains:
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

            http_status, body = api_renew(client, domain_id)
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
            "error": f"请求异常：{str(e)[:200]}",
            "domains": [],
        }


def write_result(result):
    """把结构化结果写文件，供工作流的通知步骤读取。"""
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已写入 {RESULT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="StackRyze 域名自动续签（纯接口版）")
    parser.add_argument("--probe", action="store_true",
                        help="只探查：登录第一个账号，列出域名和剩余天数，不续期")
    args = parser.parse_args()

    try:
        if not EMAILS:
            raise ValueError("缺少必需的环境变量 STACKRYZE_EMAILS（3 个账号邮箱，逗号分隔）。")

        accounts = []
        for idx, email in enumerate(EMAILS, 1):
            # 密码和 TOTP 种子只从环境变量读进内存，绝不打印。
            password = os.getenv(f"STACKRYZE_PASSWORD_{idx}", "")
            totp_secret = os.getenv(f"STACKRYZE_TOTP_{idx}", "")
            accounts.append(
                process_account(
                    email, password, idx,
                    totp_secret=totp_secret, probe=args.probe,
                )
            )
            if args.probe:
                break

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
