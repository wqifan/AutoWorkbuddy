# -*- coding: utf-8 -*-
"""观察 Cloudflare Worker 的 cron 是否真的投递 dispatch。

背景：新建 Worker 的 cron 触发可能需要很久才开始调度（社区实测接近 3 小时），
所以不能只看部署后 10 分钟的结果。本脚本持续轮询 GitHub 的 workflow run 列表，
一旦发现由 Cloudflare 触发的新 run 就立刻报出来。

用法：
    ./.build-venv/Scripts/python.exe watch_cloudflare_cron.py [最长等待分钟数]
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

import wb_update_secret as U   # noqa: E402

REPO = "wqifan/AutoWorkbuddy"
WORKFLOW = "daily.yml"


def main():
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    opener, label, tried = U.pick_outlet("")
    if opener is None:
        print("找不到可用出口：", tried)
        return 1
    gh = U.GitHub(U.read_github_token()[0], opener)

    st, body = gh.call("/repos/%s/actions/workflows/%s/runs?per_page=20"
                       % (REPO, WORKFLOW))
    runs = body.get("workflow_runs") or []
    baseline = runs[0]["created_at"] if runs else ""
    print("出口: %s" % label)
    print("基线最新 run: %s" % (baseline or "(无)"))
    print("开始观察，最长 %d 分钟...\n" % minutes)

    deadline = time.time() + minutes * 60
    polls = 0
    while time.time() < deadline:
        time.sleep(60)
        polls += 1
        st, body = gh.call("/repos/%s/actions/workflows/%s/runs?per_page=10"
                           % (REPO, WORKFLOW))
        runs = body.get("workflow_runs") or []
        if runs and runs[0]["created_at"] > baseline:
            r = runs[0]
            print("✓ 发现新 run！")
            print("   created_at = %s" % r["created_at"])
            print("   status     = %s" % r["status"])
            print("   conclusion = %s" % r.get("conclusion"))
            print("   url        = %s" % r.get("html_url"))
            print("\n结论：Cloudflare cron 已确认可以投递 dispatch。")
            return 0
        print("[%3d 分钟] 暂无新 run（最新 %s）"
              % (polls, runs[0]["created_at"] if runs else "none"))

    print("\n结论：等待 %d 分钟后仍无新 run —— Cloudflare cron 未投递。" % minutes)
    return 2


if __name__ == "__main__":
    sys.exit(main())
