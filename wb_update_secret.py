#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_update_secret.py —— 双击一下，把最新的 accessToken 写进 GitHub 仓库 Secret。

它做四件事，全程不打印任何凭据：
  1. 从本机 WorkBuddy 登录态解出 accessToken（复用 refresh_token.py 的解密逻辑）
  2. 从 git 凭据管理器取 GitHub 凭据 —— 就是你推送代码时登录过的那个，
     不需要另外创建 PAT，也不需要安装 gh CLI
  3. 用仓库公钥做 libsodium sealed box 加密后 PUT 到 Actions Secret
  4. 可选：立刻触发一次 workflow，验证整条链路

用法：
    python wb_update_secret.py                 # 更新 WB_ACCESS_TOKEN
    python wb_update_secret.py --check         # 只检查，不写入
    python wb_update_secret.py --trigger       # 写入后立刻触发一次运行
    python wb_update_secret.py --repo OWNER/REPO
    python wb_update_secret.py --name FEISHU_WEBHOOK --value-file hook.txt
"""

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def _init_console():
    """双击 exe 时是 GBK 控制台，中文会乱码。切到 UTF-8 代码页再重绑输出流。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_init_console()

try:
    from nacl import encoding, public
except ImportError as _e:  # pragma: no cover
    print("缺少 PyNaCl，无法加密 Secret：%s" % _e)
    print("源码运行：pip install pynacl；exe 版出现此项说明打包时未带上 libsodium")
    sys.exit(3)

import refresh_token as rt

DEFAULT_REPO = "wqifan/AutoWorkbuddy"
API = "https://api.github.com"
UA = "wb-update-secret"


# ---------------------------------------------------------------- 输出小工具

def line(msg=""):
    print(msg, flush=True)


def ok(msg):
    print("  ✓ " + msg, flush=True)


def bad(msg):
    print("  ✗ " + msg, flush=True)


def step(msg):
    print("\n· " + msg, flush=True)


# ---------------------------------------------------------------- 两个凭据来源

def read_workbuddy_token():
    """解出本机登录态里的 accessToken。失败直接抛异常，由上层给中文提示。"""
    client = rt.find_client()
    envelope = rt.read_envelope(rt.find_auth_file())
    data = rt.run_helper(client, {"operation": "decrypt", "value": envelope})
    tok = (data or {}).get("accessToken") or ""
    if not tok:
        raise RuntimeError("登录态里没有 accessToken（可能未登录或格式已变）")
    return tok


def read_github_token():
    """从 git 凭据管理器取 GitHub 凭据；不弹窗、不打印。"""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        proc = subprocess.run(["git", "credential", "fill"],
                              input=b"protocol=https\nhost=github.com\n\n",
                              capture_output=True, env=env, timeout=45)
    except FileNotFoundError:
        raise RuntimeError("找不到 git 命令。请确认已安装 Git，并且它在 PATH 里")
    text = proc.stdout.decode("utf-8", "replace")
    cred = dict(l.split("=", 1) for l in text.splitlines() if "=" in l)
    tok = cred.get("password", "")
    if not tok:
        raise RuntimeError("git 凭据管理器里没有 github.com 的凭据。\n"
                           "    先在本目录执行一次 git push 并登录，或改用 --gh-token 参数")
    return tok, cred.get("username", "")


# ---------------------------------------------------------------- GitHub API

class GitHub(object):
    def __init__(self, token):
        self.token = token

    def call(self, path, method="GET", body=None):
        req = urllib.request.Request(
            API + path, method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "User-Agent": UA,
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                body = json.loads(raw)
            except Exception:
                body = {"message": raw.decode("utf-8", "replace")[:200]}
            return e.code, body
        except Exception as e:
            return -1, {"message": "%s: %s" % (type(e).__name__, e)}

    def whoami(self):
        st, body = self.call("/user")
        return (body.get("login") if st == 200 else None), st

    def put_secret(self, repo, name, value):
        """sealed box 加密后写入仓库 Secret。"""
        st, key = self.call("/repos/%s/actions/secrets/public-key" % repo)
        if st != 200:
            return st, "读不到仓库公钥：%s" % key.get("message", "")

        pk = public.PublicKey(key["key"].encode("utf-8"), encoding.Base64Encoder)
        encrypted = base64.b64encode(
            public.SealedBox(pk).encrypt(value.encode("utf-8"))).decode("utf-8")

        st2, body = self.call("/repos/%s/actions/secrets/%s" % (repo, name),
                              method="PUT",
                              body={"encrypted_value": encrypted, "key_id": key["key_id"]})
        if st2 in (201, 204):
            return 0, ""
        return st2, body.get("message", "")

    def secret_exists(self, repo, name):
        st, _ = self.call("/repos/%s/actions/secrets/%s" % (repo, name))
        return st == 200

    def trigger(self, repo, workflow="daily.yml"):
        st, body = self.call("/repos/%s/actions/workflows/%s/dispatches" % (repo, workflow),
                             method="PUT", body={"ref": "main"})
        if st == 204:
            return 0, ""
        return st, body.get("message", "")


# ---------------------------------------------------------------- 主流程

def parse_args(argv):
    opt = {"repo": DEFAULT_REPO, "name": "WB_ACCESS_TOKEN", "check": False,
           "trigger": False, "gh_token": "", "value_file": "", "no_pause": False}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--repo" and i + 1 < len(argv):
            opt["repo"] = argv[i + 1]
            i += 2
        elif a == "--name" and i + 1 < len(argv):
            opt["name"] = argv[i + 1]
            i += 2
        elif a == "--gh-token" and i + 1 < len(argv):
            opt["gh_token"] = argv[i + 1]
            i += 2
        elif a == "--value-file" and i + 1 < len(argv):
            opt["value_file"] = argv[i + 1]
            i += 2
        elif a == "--check":
            opt["check"] = True
            i += 1
        elif a == "--trigger":
            opt["trigger"] = True
            i += 1
        elif a == "--no-pause":
            opt["no_pause"] = True
            i += 1
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            bad("不认识的参数：%s" % a)
            sys.exit(2)
    return opt


def main(argv):
    opt = parse_args(argv)
    line("=" * 52)
    line(" Buddy 加油站 · 更新 GitHub Secret")
    line("=" * 52)
    line(" 仓库：%s   名称：%s" % (opt["repo"], opt["name"]))

    # 1) 取要写入的值
    step("1/4 从本机登录态取 accessToken")
    if opt["value_file"]:
        value = open(opt["value_file"], encoding="utf-8").read().strip()
        ok("从文件读取，长度 %d" % len(value))
    else:
        try:
            value = read_workbuddy_token()
            ok("已解出，长度 %d（不显示内容）" % len(value))
        except SystemExit:
            raise
        except Exception as e:
            bad("取不到登录态：%s" % e)
            line("    → 请先打开并登录 WorkBuddy 客户端，再重跑本工具")
            return 1

    # 2) 取 GitHub 凭据
    step("2/4 从 git 凭据管理器取 GitHub 凭据")
    try:
        gh_token, gh_user = (opt["gh_token"], "") if opt["gh_token"] else read_github_token()
        ok("已取得（账号：%s）" % (gh_user or "未知"))
    except Exception as e:
        bad("取不到 GitHub 凭据：%s" % e)
        line("    → 也可以用 --gh-token ghp_xxx 手动传一个 PAT")
        return 1

    gh = GitHub(gh_token)
    login, st = gh.whoami()
    if st != 200:
        bad("这个 GitHub 凭据无效（HTTP %s）" % st)
        line("    → 重新执行一次 git push，在弹窗里登录后重试")
        return 1
    ok("凭据有效，身份：%s" % login)

    # 3) 检查仓库权限
    step("3/4 检查仓库写入权限")
    st, body = gh.call("/repos/%s" % opt["repo"])
    if st == 404:
        bad("仓库不存在，或这个账号没有访问权限：%s" % opt["repo"])
        return 1
    if st != 200:
        bad("查询仓库失败（HTTP %s）：%s" % (st, body.get("message", "")))
        return 1
    ok("仓库可访问：%s（%s）" % (body.get("full_name"),
                              "私有" if body.get("private") else "公开"))

    if opt["check"]:
        st, key = gh.call("/repos/%s/actions/secrets/public-key" % opt["repo"])
        if st == 200:
            ok("可以写入 Secret（已读到公钥 key_id=%s）" % key.get("key_id"))
            line("\n--check 模式：以上检查全部通过，未做任何写入。")
            return 0
        bad("没有写 Secret 的权限（HTTP %s）——通常是 token 缺少 repo/workflow 权限" % st)
        return 1

    # 4) 写入
    step("4/4 写入 Secret %s" % opt["name"])
    st, msg = gh.put_secret(opt["repo"], opt["name"], value)
    if st == 0:
        ok("已写入（旧值已被覆盖）")
    else:
        bad("写入失败（HTTP %s）：%s" % (st, msg))
        if st == 403:
            line("    → token 没有 Actions secrets 写权限；本工具用 git 凭据里的 OAuth token，"
                 "若该账号不是仓库管理员请改用 --gh-token")
        return 1

    # 可选：触发一次
    if opt["trigger"]:
        step("额外：触发一次 workflow 验证")
        st, msg = gh.trigger(opt["repo"])
        if st == 0:
            ok("已触发，稍后到 Actions 页面看结果")
            line("     https://github.com/%s/actions" % opt["repo"])
        else:
            bad("触发失败（HTTP %s）：%s" % (st, msg))

    line("\n" + "=" * 52)
    line(" 完成。Secret 已更新，定时任务下次运行就会用新 token。")
    line("=" * 52)
    return 0


def run():
    """双击入口：无论成败都把窗口留住，避免一闪而过。"""
    code = 1
    try:
        code = main(sys.argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        bad("未预期的错误：%s: %s" % (type(e).__name__, e))
        code = 1

    if "--no-pause" not in sys.argv and sys.stdin and sys.stdin.isatty():
        try:
            print("\n按回车键退出 ...", end="", flush=True)
            input()
        except Exception:
            pass
    return code


if __name__ == "__main__":
    sys.exit(run())
