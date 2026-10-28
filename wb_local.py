# -*- coding: utf-8 -*-
"""Buddy 加油站 · 本机执行器（不依赖 GitHub 调度）

为什么要有这个：
    实测确认这个 GitHub 账号（个人账号）的 `schedule` 事件整体不投递 ——
    公开/私有、fork/非 fork 都试过，3 天 12 次请求全部落空，
    对照组里 Organization 账号的仓库（nodejs/node 等）同期正常投递。
    所以「等 GitHub 自己按时跑」这条路在这台机器上不成立。

这个脚本在本机直接完成全部工作：
    1. 从 WorkBuddy 客户端登录态解出 accessToken（明文只在内存里）
    2. 调 WorkBuddy 接口签到 + 派猫猫
    3. 用 QQ 邮箱 SMTP 把结果发给自己
完全离线于 GitHub —— 只要这台电脑开着就能跑。

幂等性（敢每小时跑的前提）：
    · 签到：服务端 today_checked_in / code=10001 兜住，不会重复领
    · 派猫：服务端 traveling 就跳过；已回来过的，用「上一趟 depart_at 是否落在今天」判断
    · 推送：只有「真的做了事」的那一轮才发邮件，一天最多一封

用法：
    python wb_local.py              # 正常跑一次
    python wb_local.py --probe      # 只探测，不签到不推送
"""

import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 打包成 exe 时，wb_auto.py / refresh_token.py 被放在 sys._MEIPASS 下，
# 不在源码目录里，所以两个位置都要加进搜索路径。
if hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, sys._MEIPASS)


def _force_utf8_stdout():
    """Windows 控制台默认 GBK，输出「✓」等符号会 UnicodeEncodeError。

    优先用 Python 3.7+ 的 reconfigure；失败则退回一个忽略错误的包装器。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_stdout()

import refresh_token as rt          # noqa: E402
import wb_auto as WA                # noqa: E402

DEFAULT_ENDPOINT = "https://copilot.tencent.com"


# ---------------------------------------------------------------- 本地配置

def local_conf_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "BuddyCheckin", "config.json")


def load_conf():
    """读取本机配置。邮箱授权码只存在这里（明文），文件权限设成仅当前用户可读。"""
    p = local_conf_path()
    if not os.path.isfile(p):
        return {}
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_conf(conf):
    p = local_conf_path()
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(p, 0o600)          # POSIX；Windows 上被忽略，但无害
    except OSError:
        pass
    return p


# ---------------------------------------------------------------- 拿 token

def read_local_token():
    """从 WorkBuddy 客户端登录态解出 accessToken。"""
    client = rt.find_client()
    envelope = rt.read_envelope(rt.find_auth_file())
    data = rt.run_helper(client, {"operation": "decrypt", "value": envelope})
    tok = (data or {}).get("accessToken") or ""
    if not tok:
        raise RuntimeError("登录态里没有 accessToken（可能未登录或格式已变）")
    return tok


# ---------------------------------------------------------------- 主流程

def main(argv):
    probe = "--probe" in argv[1:]
    conf = load_conf()

    print("=" * 58)
    print(" Buddy 加油站 · 本机执行器  %s"
          % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 58)

    # ---- 1. 取 token
    print("【1/4】读取 WorkBuddy 登录态")
    try:
        token = read_local_token()
    except Exception as e:
        print("  ✗ 失败：%s: %s" % (type(e).__name__, e))
        print("  （请先启动 WorkBuddy 客户端并登录）")
        return 2
    print("  ✓ 已取得 accessToken（长度 %d，内容不显示）" % len(token))

    # ---- 2. 体检 token 是否还有效
    print("【2/4】token 有效性体检")
    endpoint = (conf.get("endpoint") or DEFAULT_ENDPOINT).rstrip("/")
    headers = {"Authorization": "Bearer %s" % token, "Accept": "application/json",
               "Content-Type": "application/json", "User-Agent": "WorkBuddy/auto"}
    code, body = WA.call(endpoint, "/v2/billing/meter/checkin-activity-status",
                         headers, "POST", {}, "体检")
    if code < 0 or not isinstance(body, dict):
        print("  ✗ 体检请求失败：HTTP %s %s" % (code, WA.dump(body)))
        print("  （网络不通或接口变更）")
        return 3
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    print("  ✓ HTTP %s code=%s 今日已签=%s 连续=%s 天"
          % (code, body.get("code"),
             data.get("today_checked_in"), data.get("streak_days")))

    # ---- 3. 组装 wb_auto 需要的环境变量
    print("【3/4】准备运行环境")
    # wb_auto.main() 直接读 os.environ，所以必须写进进程环境而不是副本
    os.environ["WB_ACCESS_TOKEN"] = token
    os.environ["WB_ENDPOINT"] = endpoint
    os.environ["WB_PUSH"] = "never" if probe else (conf.get("push") or "auto")
    mail_user = (conf.get("mail_user") or "").strip()
    mail_auth = (conf.get("mail_auth") or "").strip()
    mail_to = (conf.get("mail_to") or "").strip()
    if mail_user and mail_auth:
        os.environ["QQ_MAIL_USER"] = mail_user
        os.environ["QQ_MAIL_AUTH_CODE"] = mail_auth
        if mail_to:
            os.environ["QQ_MAIL_TO"] = mail_to
        print("  ✓ 邮箱推送已配置（发件人 %s，收件人 %s）"
              % (mail_user, mail_to or mail_user))
    else:
        print("  ! 未配置邮箱推送（跑 wb_local.py --setup-mail 填授权码）")

    # ---- 4. 交给 wb_auto 干正事
    print("【4/4】执行签到与派猫猫")
    print("-" * 58)
    old_argv = sys.argv
    sys.argv = ["wb_auto.py"] + (["--probe"] if probe else [])
    try:
        rc = WA.main(sys.argv)
    finally:
        sys.argv = old_argv
    print("-" * 58)
    print("退出码：%d" % rc)
    return rc


def setup_mail(argv):
    """写入邮箱配置（授权码只落在本机，不上 GitHub）。

    支持两种用法：
        python wb_local.py --setup-mail              交互式输入（授权码不回显）
        python wb_local.py --setup-mail --user 123456@qq.com --auth-code XXXX
    """
    args = argv[1:]

    def argval(flag):
        if flag in args:
            i = args.index(flag)
            return args[i + 1] if i + 1 < len(args) else ""
        return ""

    user = argval("--user")
    auth = argval("--auth-code")
    to = argval("--to")

    if not user or not auth:
        print("=" * 58)
        print(" 配置 QQ 邮箱推送")
        print("=" * 58)
        print("  QQ 邮箱（如 123456@qq.com）")
        print("  16 位授权码：邮箱设置 → 账户 → 开启 POP3/SMTP 服务后生成")
        print()
        try:
            import getpass
            if not user:
                user = input("QQ 邮箱: ").strip()
            if not auth:
                auth = getpass.getpass("16 位授权码（输入不回显）: ").strip()
            if not to:
                to = input("收件邮箱（留空 = 发给自己）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 1

    if not user or not auth:
        print("✗ 用户名和授权码都要填")
        return 1

    conf = load_conf()
    conf["mail_user"] = user
    conf["mail_auth"] = auth
    if to:
        conf["mail_to"] = to
    p = save_conf(conf)
    print("✓ 已保存到 %s" % p)
    print("  授权码只存在本机这个文件里，不会上传到 GitHub。")
    return 0


if __name__ == "__main__":
    if "--setup-mail" in sys.argv:
        sys.exit(setup_mail(sys.argv))
    sys.exit(main(sys.argv))
