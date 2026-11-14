# renew.py - DigitalPlat 严格账号与 API 密钥对齐版
#
# 职责只有一件事：调用 DigitalPlat API 检查 / 续期域名，
# 把结构化结果写到 renew_result.json。
#
# 本脚本不发任何通知、不做任何消息样式：
#   - 没有 Telegram / 微信代码，没有 TG_BOT_TOKEN / WX_PUSH_URL 等环境变量
#   - 没有 emoji、没有 Markdown、没有 HTML，返回的只是纯数据
#   - 通知文案（TG 用 HTML、微信用纯文本）全部在
#     .github/workflows/renew.yml 的 "Send notification" 步骤里，
#     以后想改推送样式只改工作流文件，不用动这个脚本。

import json
import logging
import os
import sys
from datetime import datetime

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- 1. 从环境变量中读取配置 ---
DP_API_KEYS_RAW = os.getenv("DP_API_KEY", "")
DP_API_KEYS = [k.strip() for k in DP_API_KEYS_RAW.split(",") if k.strip()]

DP_ACCOUNT_EMAILS_RAW = os.getenv("DP_ACCOUNT_EMAILS", "")
DP_ACCOUNT_EMAILS = [e.strip() for e in DP_ACCOUNT_EMAILS_RAW.split(",") if e.strip()]

# 结构化结果的输出路径，工作流的通知步骤会读这个文件。
# 本地测试可改成别的路径；工作流里不设此变量时默认就是 renew_result.json。
RESULT_PATH = os.getenv("RENEW_RESULT_PATH", "renew_result.json")

API_BASE_URL = "https://domain-api.digitalplat.org/api/v1"

# 提前 120 天内才尝试续期，超过 120 天跳过（原逻辑不变）。
RENEW_WITHIN_DAYS = 120


def get_headers(api_key):
    """构建官方 API 请求头"""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def parse_expiry_days(expiry_str):
    """解析到期时间字符串并计算剩余天数，解析失败返回 None"""
    if not expiry_str:
        return None
    expiry_str = str(expiry_str).strip()

    formats = [
        "%Y%m%d",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ]

    clean_str = expiry_str.split(".")[0].rstrip("Z")
    for fmt in formats:
        try:
            exp_dt = datetime.strptime(clean_str, fmt)
            return (exp_dt - datetime.now()).days
        except ValueError:
            continue
    return None


def fetch_domains(api_key):
    """通过 API 获取域名列表，返回 (domains, error)"""
    url = f"{API_BASE_URL}/domains"
    try:
        response = requests.get(url, headers=get_headers(api_key), timeout=30)
        if response.status_code != 200:
            return None, f"获取域名列表失败，状态码: {response.status_code}, 响应: {response.text}"

        data = response.json()
        domains = data.get("data", [])
        if isinstance(domains, dict):
            domains = domains.get("domains", [])
        return domains, None
    except Exception as e:
        return None, f"获取域名列表异常: {str(e)}"


def renew_domain(api_key, domain_identifier):
    """通过 API 续期指定域名，返回 {"status": ..., "body": ...}"""
    url = f"{API_BASE_URL}/domains/{domain_identifier}/renew"
    headers = get_headers(api_key)
    headers["Idempotency-Key"] = f"renew-{domain_identifier}-{int(datetime.now().timestamp())}"

    payload = {
        "renewal_type": "free",
        "years": 1,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return {
            "status": response.status_code,
            "body": response.text,
        }
    except Exception as e:
        return {
            "status": 500,
            "body": str(e),
        }


def process_account(api_key, email, index):
    """处理单个账号下的域名检查与续期，返回结构化 dict（不含任何样式）"""
    account_label = f"{index}. {email}"
    logger.info(f"开始处理账号: {account_label}")

    # 严格校验当前账号是否有对应的 API Key
    if not api_key:
        logger.warning(f"账号 {account_label} 未配置对应的 DP_API_KEY，已跳过。")
        return {
            "index": index,
            "email": email,
            "status": "no_key",
            "error": "未配置对应的 API 密钥 (DP_API_KEY)",
            "domains": [],
        }

    domains, err = fetch_domains(api_key)
    if err:
        logger.error(f"账号 {account_label} 出错: {err}")
        return {
            "index": index,
            "email": email,
            "status": "error",
            "error": err,
            "domains": [],
        }

    if not domains:
        logger.info(f"账号 {account_label} 未找到任何域名或获取列表为空。")
        return {
            "index": index,
            "email": email,
            "status": "empty",
            "error": None,
            "domains": [],
        }

    domain_results = []
    for d in domains:
        name = d.get("name") or d.get("domain")
        domain_id = d.get("id") or name
        if not name:
            continue

        expiry_raw = d.get("expires_at") or d.get("expiry_date") or d.get("expire_at") or d.get("expires")
        remaining_days = parse_expiry_days(expiry_raw)

        logger.info(f"检查域名: {name} (到期时间: {expiry_raw}, 剩余天数: {remaining_days})")

        if remaining_days is not None and remaining_days > RENEW_WITHIN_DAYS:
            logger.info(f"域名 {name} 距离到期还有 {remaining_days} 天，暂无需续期，已跳过。")
            domain_results.append({
                "name": name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "skipped",
                "detail": "未到期",
            })
            continue

        result = renew_domain(api_key, domain_id)
        if result["status"] in [200, 201]:
            logger.info(f"域名 {name} 续期成功！")
            domain_results.append({
                "name": name,
                "expiry": str(expiry_raw) if expiry_raw is not None else None,
                "remaining_days": remaining_days,
                "result": "renewed",
                "detail": "+1年",
            })
        else:
            msg = result["body"]
            if "more than 120 days" in msg or "cannot renew" in msg.lower():
                days_str = f"剩 {remaining_days} 天" if remaining_days is not None else "未到期"
                logger.info(f"域名 {name} 暂无需续期。")
                domain_results.append({
                    "name": name,
                    "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days,
                    "result": "skipped",
                    "detail": days_str,
                })
            else:
                logger.warning(f"域名 {name} 续期失败: {msg[:120]}")
                domain_results.append({
                    "name": name,
                    "expiry": str(expiry_raw) if expiry_raw is not None else None,
                    "remaining_days": remaining_days,
                    "result": "failed",
                    "detail": msg[:120],
                })

    return {
        "index": index,
        "email": email,
        "status": "ok",
        "error": None,
        "domains": domain_results,
    }


def write_result(result):
    """把结构化结果写文件，供工作流的通知步骤读取"""
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已写入 {RESULT_PATH}")


def main():
    try:
        if not DP_API_KEYS:
            raise ValueError("缺少必需的环境变量 DP_API_KEY，请在 GitHub Secrets 中配置。")

        account_emails = DP_ACCOUNT_EMAILS
        if not account_emails:
            account_emails = [f"account_{i+1}@digitalplat.org" for i in range(len(DP_API_KEYS))]

        accounts = []
        for idx, email in enumerate(account_emails, 1):
            # 严格按照索引匹配：第 1 个邮箱配第 1 个 Key，第 2 个邮箱配第 2 个 Key
            key_index = idx - 1
            current_api_key = DP_API_KEYS[key_index] if key_index < len(DP_API_KEYS) else None
            accounts.append(process_account(current_api_key, email, idx))

        summary = {
            "accounts": len(accounts),
            "domains": sum(len(a["domains"]) for a in accounts),
            "renewed": sum(1 for a in accounts for d in a["domains"] if d["result"] == "renewed"),
            "skipped": sum(1 for a in accounts for d in a["domains"] if d["result"] == "skipped"),
            "failed": sum(1 for a in accounts for d in a["domains"] if d["result"] == "failed"),
        }
        status = "ok" if summary["failed"] == 0 else "partial"

        result = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "summary": summary,
            "accounts": accounts,
        }
        write_result(result)
        logger.info(
            "所有账号任务执行完毕：共 %d 个账号 / %d 个域名，续期成功 %d，跳过 %d，失败 %d。",
            summary["accounts"], summary["domains"],
            summary["renewed"], summary["skipped"], summary["failed"],
        )

    except Exception as e:
        error_msg = f"脚本运行发生严重错误: {str(e)}"
        logger.error(error_msg)
        # 致命错误也要写一份结构化结果，让工作流的通知步骤（if: always()）
        # 能照常发一条报错通知，而不是静默失败。
        write_result({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "error",
            "summary": {"accounts": 0, "domains": 0, "renewed": 0, "skipped": 0, "failed": 0},
            "accounts": [],
            "error": error_msg,
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
