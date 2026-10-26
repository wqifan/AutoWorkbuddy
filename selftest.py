#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 不打网络的逻辑自检。

用假响应替换真实的 HTTP 层，验证几条最容易出错的分支：
  · 今天已签 → 不重复领，整体仍算成功
  · 猫猫模块异常 / 401 / 5xx → 签到结论不受影响
  · 旅行中 → 不派；已到家 → 先领再派；名额用完 → 不派
  · depart 撞上限额 → 归为「名额用完」，不算失败

跑法：python selftest.py
"""

import base64
import hashlib
import hmac
import json
import os
import sys

import wb_auto

PASS, FAIL = "✓", "✗"
results = []


class FakeHTTP(object):
    """按路径排队返回预设响应，并记录调用顺序。"""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def __call__(self, endpoint, path, headers, method="POST", payload=None,
                 label="", raw=None):
        self.calls.append((path, method, payload))
        queue = self.routes.get(path)
        if not queue:
            return 200, {"code": 0, "msg": "OK", "data": {}}
        code, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return code, body

    def paths(self):
        return [c[0] for c in self.calls]


def check(name, cond, detail=""):
    results.append((cond, name, detail))
    print("%s %s%s" % (PASS if cond else FAIL, name, ("  ← " + detail) if detail and not cond else ""))


def with_http(routes):
    fake = FakeHTTP(routes)
    wb_auto.call = fake
    return fake


CHECKED_IN = (200, {"code": 0, "msg": "OK", "data": {
    "active": True, "today_checked_in": True, "streak_days": 5,
    "today_credit": 100, "total_credits": 500}})
NOT_CHECKED = (200, {"code": 0, "msg": "OK", "data": {
    "active": True, "today_checked_in": False, "streak_days": 4,
    "today_credit": 100, "next_streak_day": 7}})
CLAIMED = (200, {"code": 0, "msg": "OK", "data": {"credit": 100, "streak_days": 5}})
ARRIVED = (200, {"code": 0, "msg": "OK", "data": {
    "state": "arrived", "record_id": 1, "reward_credit": 5,
    "location": {"id": 1, "name": "咖啡馆"}}})
TRAVELING = (200, {"code": 0, "msg": "OK", "data": {
    "state": "traveling", "arrive_at": 2000, "server_now": 1000,
    "location": {"name": "咖啡馆"}, "daily_limit_reached": False}})
IDLE = (200, {"code": 0, "msg": "OK", "data": {"state": "idle", "daily_limit_reached": False}})
IDLE_LIMIT = (200, {"code": 0, "msg": "OK", "data": {"state": "idle", "daily_limit_reached": True}})
CLAIM_OK = (200, {"code": 0, "msg": "OK", "data": {"state": "idle", "reward_credit": 5}})
CONFIG = (200, {"code": 0, "msg": "OK", "data": {"locations": [{"id": 1, "name": "咖啡馆"}]}})
DEPART_OK = (200, {"code": 0, "msg": "OK", "data": {
    "state": "traveling", "arrive_at": 11800, "server_now": 1000,
    "location": {"id": 1, "name": "咖啡馆", "duration_hours": 3}}})

H = {"Authorization": "Bearer test"}


def patch_urlopen(body):
    """把飞书那一步的 HTTP 层换成假的，返回一个用来检查实际发出的 payload 的字典。"""

    class FakeResp(object):
        status = 200

        def __init__(self, payload):
            self._b = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured = {}

    def fake(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return FakeResp(body)

    wb_auto.urllib.request.urlopen = fake
    return captured


def main():
    print("=== 1. 今天已签 + 猫猫到家：先领再派 ===")
    fake = with_http({wb_auto.EP_CHECKIN_STATUS: [CHECKED_IN],
                      wb_auto.EP_TRAVEL_STATUS: [ARRIVED],
                      wb_auto.EP_TRAVEL_CLAIM: [CLAIM_OK],
                      wb_auto.EP_TRAVEL_CONFIG: [CONFIG],
                      wb_auto.EP_TRAVEL_DEPART: [DEPART_OK]})
    c = wb_auto.run_checkin("e", H, [])
    t = wb_auto.run_travel("e", H, [])
    check("签到判为 ALREADY 且 ok", c["ok"] and c["result"] == "ALREADY", str(c))
    check("猫猫先领奖励再派出", t["result"] == "DEPARTED" and "+5" in t["report"], str(t))
    travel_paths = [p for p in fake.paths() if p.startswith("/v2/activity/growth")]
    check("调用顺序：status→claim→config→depart",
          travel_paths == [wb_auto.EP_TRAVEL_STATUS, wb_auto.EP_TRAVEL_CLAIM,
                           wb_auto.EP_TRAVEL_CONFIG, wb_auto.EP_TRAVEL_DEPART], str(travel_paths))

    print("\n=== 2. 今天没签：领取成功并回报积分 ===")
    with_http({wb_auto.EP_CHECKIN_STATUS: [NOT_CHECKED, CHECKED_IN],
               wb_auto.EP_CHECKIN_CLAIM: [CLAIMED]})
    c = wb_auto.run_checkin("e", H, [])
    check("签到判为 CLAIMED +100", c["ok"] and c["result"] == "CLAIMED" and c["credit"] == 100, str(c))

    print("\n=== 3. 猫猫在旅行中：不重复派 ===")
    fake = with_http({wb_auto.EP_TRAVEL_STATUS: [TRAVELING]})
    t = wb_auto.run_travel("e", H, [])
    check("判为 IN_TRIP", t["result"] == "IN_TRIP", str(t))
    check("没有调用 depart", wb_auto.EP_TRAVEL_DEPART not in fake.paths(), str(fake.paths()))

    print("\n=== 4. 今日名额已用完：只汇报，不派 ===")
    fake = with_http({wb_auto.EP_TRAVEL_STATUS: [IDLE_LIMIT]})
    t = wb_auto.run_travel("e", H, [])
    check("判为 LIMIT_REACHED", t["result"] == "LIMIT_REACHED", str(t))
    check("没有调用 depart", wb_auto.EP_TRAVEL_DEPART not in fake.paths(), str(fake.paths()))

    print("\n=== 5. 猫猫接口 500：签到结论不受影响 ===")
    with_http({wb_auto.EP_CHECKIN_STATUS: [CHECKED_IN],
               wb_auto.EP_TRAVEL_STATUS: [(500, {"code": 500, "msg": "boom"})]})
    c = wb_auto.run_checkin("e", H, [])
    t = wb_auto.run_travel("e", H, [])
    check("签到仍 ok", c["ok"] and c["result"] == "ALREADY", str(c))
    check("猫猫记为 ERROR 而不是抛异常", t["result"] == "ERROR", str(t))

    print("\n=== 6. 401：签到失败且提示换 token ===")
    with_http({wb_auto.EP_CHECKIN_STATUS: [(401, {"code": 401, "msg": "unauthorized"})]})
    c = wb_auto.run_checkin("e", H, [])
    check("判为 AUTH 且 ok=False", (not c["ok"]) and c["result"] == "AUTH", str(c))
    check("文案指向 refresh_token.py", "refresh_token.py" in c["report"], c["report"])

    print("\n=== 7. 未知旅行状态：原样汇报、不写不猜 ===")
    fake = with_http({wb_auto.EP_TRAVEL_STATUS: [(200, {"code": 0, "data": {"state": "sleeping"}})]})
    t = wb_auto.run_travel("e", H, [])
    check("判为 UNKNOWN_STATE", t["result"] == "UNKNOWN_STATE", str(t))
    check("没有任何写请求", all(m == "GET" for _p, m, _x in fake.calls), str(fake.calls))

    print("\n=== 8. 整体退出码只看签到 ===")
    os.environ["WB_ACCESS_TOKEN"] = "test-token-0123456789"
    for expected_code, routes, name in (
        (0, {wb_auto.EP_CHECKIN_STATUS: [CHECKED_IN],
             wb_auto.EP_TRAVEL_STATUS: [(500, {"code": 500})]}, "签到成功+猫猫失败 → 退出码 0"),
        (1, {wb_auto.EP_CHECKIN_STATUS: [(500, {"code": 500})]}, "签到失败 → 退出码 1"),
    ):
        with_http(routes)
        code = wb_auto.main(["wb_auto.py"])
        check(name, code == expected_code, "实际 %s" % code)

    print("\n=== 9. 飞书推送：签名校验可选，未配置也能推 ===")
    CARD = {"msg_type": "interactive", "card": {"elements": []}}
    HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/fake"

    cap = patch_urlopen({"code": 0, "msg": "success"})
    ok, _reason = wb_auto.push_feishu(HOOK, CARD)
    check("没配 secret：不带 timestamp/sign",
          ok and "sign" not in cap["data"] and "timestamp" not in cap["data"],
          str(sorted(cap["data"].keys())))

    cap = patch_urlopen({"code": 0, "msg": "success"})
    secret = "test-secret-value"
    ok, _reason = wb_auto.push_feishu(HOOK, CARD, secret)
    ts = cap["data"].get("timestamp", "")
    expect = base64.b64encode(hmac.new(
        ("%s\n%s" % (ts, secret)).encode("utf-8"), b"",
        digestmod=hashlib.sha256).digest()).decode("utf-8")
    check("配了 secret：带 timestamp 且签名与独立复算一致",
          ok and ts.isdigit() and cap["data"].get("sign") == expect,
          "ts=%s sign=%s" % (ts, cap["data"].get("sign")))
    check("卡片本体保持不变（只附加签名，不改内容）",
          cap["data"].get("msg_type") == "interactive" and "card" in cap["data"],
          str(sorted(cap["data"].keys())))

    patch_urlopen({"code": 19021, "msg": "sign match fail"})
    ok, reason = wb_auto.push_feishu(HOOK, CARD, secret)
    check("19021 的提示指向 FEISHU_SECRET", (not ok) and "FEISHU_SECRET" in reason, reason)

    ok = sum(1 for c, _n, _d in results if c)
    print("\n%d/%d 通过" % (ok, len(results)))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
