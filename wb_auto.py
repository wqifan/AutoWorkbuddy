#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_auto.py —— 云端 runner 每天跑一次：签到 + 派猫猫旅行，结果推飞书。

设计约束（对应需求）：
  1. 一次运行做完两件事：签到、猫猫旅行；
  2. 猫猫先领【已到家】那一趟的积分，再看能不能派新一趟；今天已派满则不派；
  3. 飞书里签到与猫猫分两块呈现，互不混淆；
  4. 猫猫任何异常都不影响签到结论 —— 只要签到成功，整体就算成功。

凭据只从环境变量 WB_ACCESS_TOKEN 读（由 GitHub Secret 注入），不落盘、不打印。
用法：
    python wb_auto.py            # 完整流程（会调用写接口）
    python wb_auto.py --probe    # 只读探测：只查状态，不发任何写请求
"""

import base64
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "https://copilot.tencent.com"
TIMEOUT = 20
MAX_RETRY = 3

EP_CHECKIN_STATUS = "/v2/billing/meter/checkin-activity-status"
EP_CHECKIN_CLAIM = "/v2/billing/meter/daily-checkin"
EP_TRAVEL_STATUS = "/v2/activity/growth/buddy/travel/status"
EP_TRAVEL_CONFIG = "/v2/activity/growth/buddy/travel/config"
EP_TRAVEL_CLAIM = "/v2/activity/growth/buddy/travel/claim"
EP_TRAVEL_DEPART = "/v2/activity/growth/buddy/travel/depart"

NET_UNREACHABLE = -1
_TOKEN = ""

# 这些字段即便不是凭据，也不适合出现在日志/产物里，一律整值打码
SENSITIVE_KEYS = {"accesstoken", "refreshtoken", "token", "authorization", "cookie",
                  "buddy_id", "uin", "uid", "sessionstate", "mpopenid",
                  "phonenumber", "phone", "email"}


def log(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------- 小工具

def mask(obj):
    """日志/产物里出现的长字符串一律截断；token 等内容永远不打印。"""
    if isinstance(obj, dict):
        return {k: ("***" if str(k).lower() in SENSITIVE_KEYS else mask(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(x) for x in obj]
    if isinstance(obj, str):
        if _TOKEN and len(_TOKEN) > 8 and _TOKEN in obj:
            obj = obj.replace(_TOKEN, "***TOKEN***")
        if len(obj) > 64:
            return obj[:8] + "…<%d chars>" % len(obj)
    return obj


def dump(obj):
    return json.dumps(mask(obj), ensure_ascii=False, sort_keys=True)


def dig(obj, *keys):
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        else:
            return None
    return obj


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def unwrap(body):
    """真实返回统一是 {code, msg, data} 信封，业务字段都在 data 里。"""
    if isinstance(body, dict):
        data = body.get("data")
        return data if isinstance(data, dict) else {}
    return {}


# ---------------------------------------------------------------- HTTP

def call(endpoint, path, headers, method="POST", payload=None, label="", raw=None):
    """返回 (http_code, body_dict)。网络异常与 5xx 有限重试，写操作靠服务端幂等。"""
    url = endpoint + path
    data = None if method == "GET" else json.dumps(payload or {}).encode("utf-8")
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=ssl.create_default_context()) as resp:
                text = resp.read().decode("utf-8", "replace")
                code = resp.status
                body = _json_or_raw(text)
                break
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            code, body = e.code, _json_or_raw(text)
            if 300 <= e.code < 400 and attempt < MAX_RETRY:
                log("  · %s 收到 %d，%.0fs 后重试（%d/%d）" % (label, e.code, 1.5 * attempt, attempt, MAX_RETRY))
                time.sleep(1.5 * attempt)
                continue
            break
        except Exception as e:  # 网络抖动 / 连接被重置
            if attempt >= MAX_RETRY:
                return NET_UNREACHABLE, {"error": "%s: %s" % (type(e).__name__, e)}
            log("  · %s 请求异常，%.0fs 后重试（%d/%d）：%s" % (label, 1.5 * attempt, attempt, MAX_RETRY, type(e).__name__))
            time.sleep(1.5 * attempt)
    if raw is not None:
        raw.append({"step": label, "path": path, "method": method,
                    "http": code, "body": mask(body)})
    log("  [%s] %s %s → HTTP %s %s" % (label, method, path, code, dump(body)))
    return code, body


def _json_or_raw(text):
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text[:200]}


def biz_ok(code, body):
    return 200 <= code < 300 and isinstance(body, dict) and body.get("code") in (0, None)


def _already(code, body):
    if not isinstance(body, dict):
        return False
    return body.get("code") == 10001 or "已签" in str(body.get("msg") or "")


def _auth_failed(code):
    return code in (401, 403)


# ---------------------------------------------------------------- 签到

def run_checkin(endpoint, headers, raw, write=True):
    code, body = call(endpoint, EP_CHECKIN_STATUS, headers, label="签到-状态", raw=raw)
    if code == NET_UNREACHABLE:
        return {"ok": False, "result": "NETWORK", "report": "网络不可达，签到没做成"}
    if _auth_failed(code):
        return {"ok": False, "result": "AUTH", "report": "认证被拒（HTTP %d），需要在本机重跑 refresh_token.py 换 token" % code}
    if not 200 <= code < 300:
        return {"ok": False, "result": "ERROR", "report": "签到状态接口异常（HTTP %d）" % code}

    status = unwrap(body)
    if dig(status, "active") is False:
        name = dig(status, "activity_name")
        return {"ok": True, "result": "INACTIVE",
                "report": "本期活动未开启" + ("（%s）" % name if name else "") + "，今天没有可领的积分"}

    if dig(status, "today_checked_in") in (True, 1):
        return {"ok": True, "result": "ALREADY", "report": _fmt_signed(status, "今天已经签过了")}

    if not write:
        return {"ok": True, "result": "READY",
                "report": "今天还没签（探测模式，未领取）" + _fmt_signed(status, "")}

    code, body = call(endpoint, EP_CHECKIN_CLAIM, headers, label="签到-领取", raw=raw)
    if code == NET_UNREACHABLE:
        return {"ok": False, "result": "NETWORK", "report": "领取请求没送达，签到没做成"}
    if _auth_failed(code):
        return {"ok": False, "result": "AUTH", "report": "认证被拒（HTTP %d）" % code}
    if _already(code, body):
        return {"ok": True, "result": "ALREADY", "report": "今天已经签过了（服务端确认）"}

    credit = dig(unwrap(body), "credit")
    if credit is not None:
        # 领取接口返回的是「领取前」的口径，连签天数回读一次状态接口更准
        _, fresh = call(endpoint, EP_CHECKIN_STATUS, headers, label="签到-回读", raw=raw)
        s = unwrap(fresh)
        tail = ""
        streak = dig(s, "streak_days")
        if streak is not None:
            tail = "，已连续 %s 天" % streak
            if dig(s, "is_streak_day"):
                tail += "（今天是连签奖励日）"
        return {"ok": True, "result": "CLAIMED", "credit": credit,
                "report": "签到成功，+%s 积分%s" % (credit, tail)}

    msg = (body.get("msg") if isinstance(body, dict) else None) or "未识别的返回"
    return {"ok": False, "result": "UNKNOWN", "report": "领取结果无法识别：%s（HTTP %d）" % (msg, code)}


def _fmt_signed(status, lead):
    bits = []
    if dig(status, "today_credit") is not None:
        bits.append("今日 +%s" % dig(status, "today_credit"))
    if dig(status, "streak_days") is not None:
        bits.append("连续 %s 天" % dig(status, "streak_days"))
    if dig(status, "total_credits") is not None:
        bits.append("累计 %s" % dig(status, "total_credits"))
    detail = "，".join(bits)
    if lead and detail:
        return "%s（%s）" % (lead, detail)
    return lead or detail or "状态已读取"


# ---------------------------------------------------------------- 猫猫旅行

BJ_OFFSET = 8 * 3600  # 北京时间相对 UTC 的偏移


def _bj_date(ts):
    """把秒级时间戳换算成北京时间日期串（YYYY-MM-DD）。"""
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + BJ_OFFSET))
    except (TypeError, ValueError):
        return ""


def _bj_hm(ts):
    """把秒级时间戳换算成北京时间 HH:MM。"""
    try:
        return time.strftime("%H:%M", time.gmtime(int(ts) + BJ_OFFSET))
    except (TypeError, ValueError):
        return "?"


def _eta(body):
    arrive, now = as_int(dig(body, "arrive_at")), as_int(dig(body, "server_now"))
    if arrive is None or now is None:
        return ""
    left = arrive - now
    if left <= 0:
        return "，已到家待领"
    if left < 3600:
        return "，约 %d 分钟后到家" % max(1, left // 60)
    if left % 3600 == 0:
        return "，约 %d 小时后到家" % (left // 3600)
    return "，约 %.1f 小时后到家" % (left / 3600.0)


def run_travel(endpoint, headers, raw, write=True):
    """先领已到家的积分，再判断能否派新一趟。函数内任何异常都不外泄到签到结论。"""
    notes = []
    code, body = call(endpoint, EP_TRAVEL_STATUS, headers, method="GET", label="猫猫-状态", raw=raw)

    if code == NET_UNREACHABLE:
        return {"result": "NETWORK", "report": "网络不可达，猫猫没处理"}
    if _auth_failed(code):
        return {"result": "AUTH", "report": "认证被拒（HTTP %d），签到结论不受影响" % code}
    if not 200 <= code < 300:
        return {"result": "ERROR", "report": "旅行状态接口异常（HTTP %d）" % code}

    view = unwrap(body)
    state = dig(view, "state")
    limit_reached = bool(dig(view, "daily_limit_reached"))
    # 上一趟的出发时间：派出后服务端的 daily_limit_reached 仍是 False，
    # 所以「当天是否已经派过」只能靠这个字段判断（秒级时间戳）
    last_depart = as_int(dig(view, "depart_at"))

    # ① 到家了就先领这趟的积分
    if state == "arrived":
        if not write:
            return {"result": "ARRIVED", "report": "猫猫已到家，有待领积分（探测模式，未领取）"}
        rid = dig(view, "record_id")
        payload = {"record_id": rid} if rid is not None else {}
        ccode, cbody = call(endpoint, EP_TRAVEL_CLAIM, headers, payload=payload,
                            label="猫猫-领奖励", raw=raw)
        reward = dig(unwrap(cbody), "reward_credit") or dig(unwrap(cbody), "credit")
        if biz_ok(ccode, cbody) and reward is not None:
            notes.append("领到上一趟旅行积分 +%s" % reward)
            state = "idle"
        else:
            return {"result": "ERROR",
                    "report": "领取旅行积分失败：%s（HTTP %s）" % (dig(cbody, "msg") or "未知", ccode)}
    elif state == "traveling":
        loc = dig(view, "location", "name") or "路上"
        notes.append("猫猫正在「%s」旅行%s" % (loc, _eta(view)))
        return {"result": "IN_TRIP", "report": "；".join(notes)}
    elif state != "idle":
        return {"result": "UNKNOWN_STATE",
                "report": "旅行状态未识别（%s），没有做任何写操作" % state}

    # ② 同一趟别派两次：一天里有多个定时时段，前一个时段可能已经把猫派出去了，
    #    猫回来得更早（旅程 1~3 小时）时状态会重新变成 idle/arrived，
    #    这里用「上一趟的出发时间是否落在今天」兜住，保证一天只派一趟。
    if last_depart is not None and _bj_date(last_depart) == _bj_date(time.time()):
        notes.append("今天已经派过一趟（%s 出发），不重复派" % _bj_hm(last_depart))
        return {"result": "DONE_TODAY", "report": "；".join(notes)}

    # ③ 空闲：看今天还有没有名额，有就派一趟
    if limit_reached:
        notes.append("今天的旅行名额已经用完了")
        return {"result": "LIMIT_REACHED", "report": "；".join(notes)}
    if not write:
        notes.append("猫猫空闲，今天还能派（探测模式，未派出）")
        return {"result": "IDLE", "report": "；".join(notes)}

    kcode, kbody = call(endpoint, EP_TRAVEL_CONFIG, headers, method="GET", label="猫猫-目的地", raw=raw)
    locations = dig(unwrap(kbody), "locations")
    if not (200 <= kcode < 300) or not isinstance(locations, list) or not locations:
        return {"result": "ERROR", "report": "；".join(notes + ["没取到可派的目的地"])}
    target = locations[0]
    if not isinstance(target, dict) or target.get("id") is None:
        return {"result": "ERROR", "report": "；".join(notes + ["目的地数据不完整"])}

    dcode, dbody = call(endpoint, EP_TRAVEL_DEPART, headers,
                        payload={"location_id": target.get("id")},
                        label="猫猫-派出", raw=raw)
    if biz_ok(dcode, dbody):
        view2 = unwrap(dbody)
        name = dig(view2, "location", "name") or target.get("name") or "目的地"
        # 回家时间以服务端的 arrive_at - server_now 为准：
        # 派出返回里顶层 duration_hours 可能是 0，真实时长在 location.duration_hours 里
        notes.append("猫猫已出发去%s%s" % (name, _eta(view2)))
        return {"result": "DEPARTED", "report": "；".join(notes)}
    if dcode == 429 or "limit" in str(dig(dbody, "msg") or "").lower():
        notes.append("今天的旅行名额已经用完了")
        return {"result": "LIMIT_REACHED", "report": "；".join(notes)}
    return {"result": "ERROR",
            "report": "；".join(notes + ["派出失败：%s（HTTP %s）" % (dig(dbody, "msg") or "未知", dcode)])}


# ---------------------------------------------------------------- 飞书

def build_card(checkin_line, travel_line, ok, day):
    """签到与猫猫分两块（中间有分隔线），内容不混在一起。"""
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "green" if ok else "red",
                "title": {"tag": "plain_text",
                          "content": "Buddy 加油站 · %s" % ("签到成功" if ok else "签到失败")},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": "**签到**\n%s" % checkin_line}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": "**猫猫旅行**\n%s" % travel_line}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text",
                                              "content": "%s · GitHub Actions 云端执行 · 猫猫异常不影响签到结论" % day}]},
            ],
        },
    }


def feishu_sign(secret, timestamp):
    """飞书自定义机器人的「签名校验」：HmacSHA256(key=timestamp+"\\n"+secret, msg 为空) 再 base64。"""
    key = ("%s\n%s" % (timestamp, secret)).encode("utf-8")
    return base64.b64encode(
        hmac.new(key, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")


def push_feishu(webhook, card, secret=""):
    payload = dict(card)
    if secret:
        # 机器人开了签名校验时，payload 里必须带 timestamp + sign
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = feishu_sign(secret, ts)
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(1, MAX_RETRY + 1):
        try:
            req = urllib.request.Request(webhook, data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15,
                                        context=ssl.create_default_context()) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            log("  [飞书] HTTP %s %s" % (resp.status, dump(body)))
            if body.get("code") == 0:
                return True, ""
            hints = {19001: "webhook 地址不完整，或被截断",
                     19021: "签名校验未通过：检查 FEISHU_SECRET 是否与机器人一致",
                     19024: "关键词不匹配：机器人设了自定义关键词，卡片里没出现该词",
                     9499: "请求体格式有误"}
            return False, ("飞书返回 %s：%s。这是配置问题，重试无用"
                           % (body.get("code"), hints.get(body.get("code"), "见飞书开放平台错误码")))
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < MAX_RETRY:
                time.sleep(1.5 * attempt)
                continue
            return False, "飞书 HTTP %s" % e.code
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(1.5 * attempt)
                continue
            return False, "飞书请求异常：%s: %s" % (type(e).__name__, e)
    return False, "飞书推送未完成"


# ---------------------------------------------------------------- 主流程

def main(argv):
    global _TOKEN
    probe = "--probe" in argv[1:]
    print_card = "--print-card" in argv[1:]
    _TOKEN = os.environ.get("WB_ACCESS_TOKEN", "").strip()
    webhook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    # 可选：机器人开了「签名校验」时才需要
    feishu_secret = os.environ.get("FEISHU_SECRET", "").strip()
    endpoint = (os.environ.get("WB_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")

    if not _TOKEN:
        log("缺环境变量 WB_ACCESS_TOKEN —— 先在本机跑 refresh_token.py 把 token 写进仓库 Secret")
        return 2

    headers = {"Authorization": "Bearer %s" % _TOKEN, "Accept": "application/json",
               "Content-Type": "application/json", "User-Agent": "WorkBuddy/auto"}
    raw = []
    day = time.strftime("%Y-%m-%d")

    log("=== 1/2 签到 ===")
    checkin = run_checkin(endpoint, headers, raw, write=not probe)
    log("  结论：%s" % checkin["report"])

    log("=== 2/2 猫猫旅行 ===")
    try:
        travel = run_travel(endpoint, headers, raw, write=not probe)
    except Exception as e:  # 兜底：猫猫模块绝不影响签到结论
        travel = {"result": "ERROR", "report": "猫猫模块异常（%s: %s）" % (type(e).__name__, e)}
    log("  结论：%s" % travel.get("report", ""))

    checkin_ok = bool(checkin.get("ok"))
    checkin_line = ("✅ " if checkin_ok else "❌ ") + checkin["report"]
    travel_line = "🐱 " + (travel.get("report") or "无结论")

    # 一天有多个定时时段，只有「真的做了事」的那一轮才推卡片，避免同一件事推好几遍。
    # WB_PUSH=always 可强制每轮都推（手动验证时用）。
    push_mode = (os.environ.get("WB_PUSH") or "auto").strip().lower()
    push_ok, push_reason = None, "未配置 FEISHU_WEBHOOK，本次不推送"
    card = build_card(checkin_line, travel_line, checkin_ok, day)
    if print_card:
        log("CARD_JSON " + json.dumps(card, ensure_ascii=False))
        return 0 if checkin_ok else 1
    if webhook and not probe:
        should_push = True
        if push_mode == "never":
            should_push = False
            push_reason = "WB_PUSH=never，本次不推送"
        elif push_mode != "always":
            did_something = (checkin.get("result") == "CLAIMED"
                             or travel.get("result") == "DEPARTED")
            if checkin_ok and not did_something:
                should_push = False
                push_reason = "本轮没有新增动作，跳过推送（避免同一件事推多遍）"
        if should_push:
            push_ok, push_reason = push_feishu(webhook, card, feishu_secret)
            log("  [飞书] %s" % ("已推送" if push_ok else "失败：" + push_reason))
        else:
            log("  [飞书] 跳过：%s" % push_reason)

    # 汇总里把「未配置」「跳过」「失败」区分开，避免把跳过看成没配
    if push_ok:
        push_line = "✅ 已推送"
    elif push_ok is None and webhook:
        push_line = "— 跳过（%s）" % push_reason
    elif push_ok is None:
        push_line = "— 未配置 webhook"
    else:
        push_line = "❌ %s" % push_reason

    result = {"date": day, "probe": probe,
              "checkin": {"ok": checkin_ok, **{k: v for k, v in checkin.items() if k != "ok"}},
              "travel": travel,
              "push": {"ok": push_ok, "reason": push_reason},
              "ok": checkin_ok}
    log("RESULT_JSON " + json.dumps(result, ensure_ascii=False))

    if os.environ.get("WB_RAW_FILE"):
        try:
            with open(os.environ["WB_RAW_FILE"], "w", encoding="utf-8") as f:
                json.dump({"date": day, "endpoint": endpoint, "responses": raw},
                          f, ensure_ascii=False, indent=2)
            log("· 原始返回（已脱敏）已写入 %s" % os.environ["WB_RAW_FILE"])
        except OSError as e:
            log("· 原始返回写入失败：%s" % e)

    summary = os.environ.get("WB_RESULT_FILE")
    if summary:
        try:
            with open(summary, "w", encoding="utf-8") as f:
                f.write("| 项目 | 结论 |\n| --- | --- |\n")
                f.write("| 签到 | %s |\n" % checkin_line)
                f.write("| 猫猫旅行 | %s |\n" % travel_line)
                f.write("| 飞书推送 | %s |\n" % push_line)
                f.write("| 整体 | %s |\n" % ("✅ 成功（签到已达成）" if checkin_ok else "❌ 失败（签到未达成）"))
        except OSError as e:
            log("· 汇总写入失败：%s" % e)

    return 0 if checkin_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
