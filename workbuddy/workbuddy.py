"""本机专用：把已绑定账号的加密凭据解密为明文凭据文件，供 GitHub Secrets 使用。

只在装有 WorkBuddy 桌面端的机器上运行（解密依赖桌面端运行时）。
输出到 export/ 目录，每个账号一个 JSON 文件；文件内容即 GitHub Secret 的值。
绝不在终端打印 token 内容。

用法：
  python export_tokens.py            # 导出 accounts 清单里全部已绑定账号
  python export_tokens.py 账号名      # 只导出指定账号
"""

import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent.parent / \
    "2026-11-23-10-12-12" / "workbuddy-auto-signin"
if not (BASE / "signin.py").is_file():
    BASE = pathlib.Path(r"C:/Users/Administrator/WorkBuddy/2026-11-23-10-12-12/workbuddy-auto-signin")
sys.path.insert(0, str(BASE))
import signin  # noqa: E402


def export_one(entry, outdir):
    snapshot = BASE / "accounts" / entry["credential"]
    session = signin.load_session_retry(snapshot)
    plain = signin.resolve_session(session)  # 加密则解密，明文直接用
    safe = "".join(c if c.isalnum() else "_" for c in entry["name"]).upper()
    target = outdir / ("WB_CREDS_%s.json" % safe)
    target.write_text(json.dumps(plain, ensure_ascii=False), encoding="utf-8")
    expiry = signin._expires_text(plain.get("auth", {}).get("expiresAt"))
    print("已导出 %-12s -> %s（uid %s，凭据有效期 %s）" % (
        entry["name"], target.name, signin._uid_hint(plain["account"].get("uid")),
        expiry or "未知，建议尽快在 GitHub 上验证"))


def main():
    registry = signin.load_registry()
    names = None if len(sys.argv) < 2 else sys.argv[1:]
    outdir = pathlib.Path(__file__).resolve().parent / "export"
    outdir.mkdir(exist_ok=True)
    done = 0
    for entry in registry["accounts"]:
        if names and entry["name"] not in names:
            continue
        export_one(entry, outdir)
        done += 1
    if not done:
        print("没有匹配的账号；用 signin.py accounts 查看已绑定账号")
        return 1
    print("\n导出目录：%s" % outdir)
    print("把这些文件的内容分别粘贴到 GitHub 仓库的 Secrets（Settings → Secrets and variables → Actions）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
