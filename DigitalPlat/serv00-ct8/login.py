#!/usr/bin/env python3
"""Serv00 / CT8 自动登录保号 — 精简版 (2026-11-16)

原版 18686187636/Serv00_CT8_Auto_Login 用 Selenium + Chrome，
689 行，要装浏览器、装依赖、跑起来慢。
精简后只用 Python 标准库，不用浏览器、不用 pip install：

登录表单是 Django 的（实测 2026-11-16）：
  GET https://<panel>/login/
    -> 取 hidden 字段 csrfmiddlewaretoken + csrftoken Cookie
  POST https://<panel>/login/
    csrfmiddlewaretoken=<token>&username=<账号>&password=<密码>
    成功会跳出 /login/，页面带登出（logout / Wyloguj）字样；
    失败还停在 /login/。

保活规则（官网条款）：
  Serv00 Terms 第 13 条、CT8 Regulamin 第 12 条：
  连续 90 天未通过 DevilWEB 面板或 SSH 正确登录，账号自动删除，
  数据无法恢复。所以每月登录一次就够了。

账号来源：环境变量 ACCOUNTS_JSON，一个 Secret 放所有账号：
  [
    {"username": "你的 Serv00 账号", "password": "密码", "panel": "panel13.serv00.com"},
    {"username": "你的 CT8 账号", "password": "密码", "panel": "panel.ct8.pl"}
  ]
  panel 只填域名就行，不用写 https://，也不用写 /login。

脚本本身不发通知、不拼样式，只把结果写到 login_result.json：
  [{"provider": "Serv00", "username": "...", "panel": "...",
    "ok": true, "message": "登录成功"}, ...]
消息样式、表头、链接都在工作流 .github/workflows/serv00-ct8-login.yml 里，
工作流读这个文件再发 Telegram。

退出码：0 = 全部登录成功；1 = 至少一个失败 / 没有账号。
"""

import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login_result.json")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def log(msg):
    print(msg, flush=True)


def provider_of(panel):
    return "CT8" if "ct8" in panel.lower() else "Serv00"


def login_url(panel):
    panel = panel.strip().rstrip("/")
    if panel.startswith("http://") or panel.startswith("https://"):
        base = panel
    else:
        base = "https://" + panel
    return base.rstrip("/") + "/login/"


def login_one(account):
    username = (account.get("username") or account.get("user") or "").strip()
    password = account.get("password") or account.get("pass") or ""
    panel = (account.get("panel") or account.get("url") or account.get("host") or "").strip()
    provider = provider_of(panel)
    if not username or not password or not panel:
        return {"provider": provider, "username": username or "?",
                "panel": panel, "ok": False, "message": "账号信息不完整（需要 username、password、panel）"}

    url = login_url(panel)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": USER_AGENT}

    try:
        # 1. GET 登录页，拿 CSRF token
        req = urllib.request.Request(url, headers=headers, method="GET")
        with opener.open(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        if not m:
            return {"provider": provider, "username": username, "panel": panel,
                    "ok": False, "message": "登录页没有找到 csrfmiddlewaretoken"}
        csrf = m.group(1)

        # 2. POST 登录（表单里还要带 next=/，跟浏览器提交的一样）
        data = urllib.parse.urlencode({
            "csrfmiddlewaretoken": csrf,
            "username": username,
            "password": password,
            "next": "/",
        }).encode()
        req = urllib.request.Request(url, data=data, headers={
            **headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
        }, method="POST")
        with opener.open(req, timeout=30) as resp:
            final_url = resp.geturl()
            body = resp.read().decode("utf-8", errors="ignore").lower()
    except urllib.error.HTTPError as e:
        return {"provider": provider, "username": username, "panel": panel,
                "ok": False, "message": f"登录请求失败: HTTP {e.code}"}
    except Exception as e:
        return {"provider": provider, "username": username, "panel": panel,
                "ok": False, "message": f"登录请求失败: {e}"}

    # 3. 判定：还停在 /login/ 就是失败；跳出去了且有登出字样就是成功
    if "/login" in urllib.parse.urlparse(final_url).path:
        if "nieprawidłowy login lub hasło" in body or "invalid" in body or "incorrect" in body:
            msg = "登录失败：账号或密码不正确"
        else:
            msg = "登录失败：仍停留在登录页"
        return {"provider": provider, "username": username, "panel": panel,
                "ok": False, "message": msg}
    if any(k in body for k in ("logout", "wyloguj", "sign out", "log out")):
        return {"provider": provider, "username": username, "panel": panel,
                "ok": True, "message": "登录成功"}
    # 跳出登录页、无错误，就是成功（兜底）
    return {"provider": provider, "username": username, "panel": panel,
            "ok": True, "message": "登录成功"}


def main():
    try:
        accounts = json.loads(os.environ.get("ACCOUNTS_JSON", "[]"))
    except json.JSONDecodeError as e:
        log(f"ACCOUNTS_JSON 格式错误: {e}")
        return 1
    if not isinstance(accounts, list) or not accounts:
        log("没有账号：请在 Secrets 里设置 ACCOUNTS_JSON")
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump([{"provider": "?", "username": "?", "panel": "",
                        "ok": False, "message": "没有账号：请设置 ACCOUNTS_JSON"}],
                      f, ensure_ascii=False, indent=2)
        return 1

    results = []
    for i, account in enumerate(accounts, 1):
        log(f"[{i}/{len(accounts)}] 正在登录 {account.get('username', '?')} ({account.get('panel', '?')})...")
        r = login_one(account)
        log(f"[{'✅' if r['ok'] else '❌'}] {r['provider']} {r['username']}: {r['message']}")
        results.append(r)

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r["ok"])
    log(f"完成: {ok}/{len(results)} 个账号登录成功")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
