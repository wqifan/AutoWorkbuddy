# -*- coding: utf-8 -*-
"""持续观察 GitHub `schedule` 事件是否稳定投递。

背景（2026-10-28 的反转发现）：
    之前连续 3 天、12 次 cron 请求全部零命中，一度判定
    「账号 wqifan 的 schedule 事件整体不投递」。
    但在 08:28:43Z 出现了第一条 event=schedule 的运行 ——
    说明它**不是不投递，而是极不稳定 / 延迟巨大**。

    本脚本连续观察 N 分钟，把每条 schedule 运行的时间记下来，
    用来判断：
        · schedule 是恢复了并稳定下来，还是偶尔漏过？
        · 两次投递的间隔是多少（对应 cron `17 * * * *` 的每小时一次）？

用法：
    ./.build-venv/Scripts/python.exe watch_schedule.py [分钟数]
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

import wb_update_secret as U   # noqa: E402

REPO = "wqifan/AutoWorkbuddy"
WORKFLOW = "daily.yml"


def fetch_runs(gh):
    st, body = gh.call("/repos/%s/actions/workflows/%s/runs?per_page=30"
                       % (REPO, WORKFLOW))
    return body.get("workflow_runs") or []


def main():
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    opener, label, tried = U.pick_outlet("")
    if opener is None:
        print("找不到可用出口：", tried)
        return 1
    gh = U.GitHub(U.read_github_token()[0], opener)

    runs = fetch_runs(gh)
    seen = set(r["id"] for r in runs)
    sched = [r for r in runs if r.get("event") == "schedule"]
    print("出口: %s" % label)
    print("基线：共 %d 条运行，其中 event=schedule 的有 %d 条" % (len(runs), len(sched)))
    for r in sched[:5]:
        print("   %s  %s" % (r["created_at"], r.get("html_url")))
    print("开始观察 %d 分钟...\n" % minutes)

    deadline = time.time() + minutes * 60
    new_sched = []
    while time.time() < deadline:
        time.sleep(60)
        runs = fetch_runs(gh)
        fresh = [r for r in runs if r["id"] not in seen]
        for r in fresh:
            seen.add(r["id"])
            mark = "  ★ schedule" if r.get("event") == "schedule" else "    dispatch"
            print("%s  %s | %s | %s"
                  % (mark, r["created_at"], r.get("event"), r.get("conclusion")))
            if r.get("event") == "schedule":
                new_sched.append(r)
        if not fresh:
            print("  ...无新运行")

    print("\n===== 观察结论 =====")
    print("新出现的 schedule 事件：%d 条" % len(new_sched))
    for r in new_sched:
        print("  %s  %s" % (r["created_at"], r.get("html_url")))
    if len(new_sched) >= 2:
        print("\n→ schedule 已在稳定投递。")
    elif len(new_sched) == 1:
        print("\n→ 只投递过一次，还不能判定稳定，继续观察。")
    else:
        print("\n→ 本轮仍无 schedule 投递，保持 Cloudflare 兜底为主。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
