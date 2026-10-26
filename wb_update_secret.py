#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_update_secret.py —— 更新 GitHub 仓库里的 WB_ACCESS_TOKEN。

先判断再动手：
  1. 解出本机 accessToken，读它的 JWT 有效期，告诉你还能用多久
  2. 读 GitHub 上这条 Secret 的更新时间，以及最近一次 workflow 运行结果
  3. 综合判断「要不要更新」，需要才写；不需要就问你要不要强制覆盖
  4. 写之前自动探测可用网络出口（代理/直连），不会因为双击时少了代理变量就失败

用法：
    UpdateBuddyToken.exe                 # 智能判断，需要才写
    UpdateBuddyToken.exe --force         # 不问，强制覆盖
    UpdateBuddyToken.exe --check         # 只体检，不写
    UpdateBuddyToken.exe --proxy http://127.0.0.1:10974
    UpdateBuddyToken.exe --trigger       # 写完后立刻触发一次运行
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
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
TOKEN_TTL_DAYS = 28
REFRESH_AHEAD_DAYS = 3      # 剩不到 3 天就建议更新
STALE_DAYS = 25             # Secret 超过 25 天没动过，建议更新

PROBE_TIMEOUT = 10          # 探测出口用的超时
CALL_TIMEOUT = 40           # 正式请求的超时（这台机器直连 github 很慢）

BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 输出

def ok(msg):
    print("  ✓ " + msg, flush=True)


def bad(msg):
    print("  ✗ " + msg, flush=True)


def info(msg):
    print("  · " + msg, flush=True)


def step(msg):
    print("\n" + msg, flush=True)


# ---------------------------------------------------------------- 小工具

def fmt_ts(ts):
    if not ts:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def parse_iso(s):
    """GitHub 返回的 2026-10-26T04:05:16Z → 本地时间戳。"""
    try:
        return int(time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone)
    except Exception:
        return None


def jwt_exp(tok):
    """accessToken 是 RS256 JWT，exp 就是过期时间（秒级）。拿不到就返回 None。"""
    parts = tok.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def port_alive(port, timeout=0.35):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def system_proxy():
    """读 Windows 系统代理（IE 设置）。读不到就返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enable:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        server = (server or "").strip()
        if not server:
            return None
        if "=" in server:  # http=127.0.0.1:7890;https=...
            for part in server.split(";"):
                if part.lower().startswith("http="):
                    server = part.split("=", 1)[1]
                    break
        if not server.lower().startswith("http"):
            server = "http://" + server
        return server
    except Exception:
        return None


def read_proxy_file():
    """支持在 exe 旁边放一个 proxy.txt，写一行代理地址。"""
    for name in ("proxy.txt", ".proxy"):
        path = os.path.join(BASE, name)
        if os.path.isfile(path):
            try:
                val = open(path, encoding="utf-8").read().strip().splitlines()
                val = val[0].strip() if val else ""
                if val and not val.startswith("#"):
                    return val
            except Exception:
                pass
    return None


# ---------------------------------------------------------------- 网络出口

def make_opener(proxy):
    handler = urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {})
    return urllib.request.build_opener(handler)


def candidate_proxies(explicit):
    """按优先级给出候选出口，(说明, 代理地址或 None)。"""
    out, seen = [], set()

    def add(label, url):
        key = (url or "DIRECT").lower().rstrip("/")
        if key in seen:
            return
        seen.add(key)
        out.append((label, url))

    if explicit:
        add("命令行 --proxy", explicit)
    for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            add("环境变量 %s" % name, v)
    v = read_proxy_file()
    if v:
        add("proxy.txt", v)
    for port in (10974, 7890, 7897, 10809, 1080, 8080, 8888):
        if port_alive(port):
            add("本机 127.0.0.1:%d" % port, "http://127.0.0.1:%d" % port)
    v = system_proxy()
    if v:
        add("Windows 系统代理", v)
    add("直连（不走代理）", None)
    return out


def pick_outlet(explicit=""):
    """依次试候选出口，返回 (opener, 说明, 失败明细)。"""
    tried = []
    for label, proxy in candidate_proxies(explicit):
        opener = make_opener(proxy)
        try:
            req = urllib.request.Request("https://api.github.com/rate_limit",
                                         headers={"User-Agent": UA,
                                                  "Accept": "application/vnd.github+json"})
            with opener.open(req, timeout=PROBE_TIMEOUT) as r:
                if r.status == 200:
                    return opener, label, tried
                tried.append("%s → HTTP %s" % (label, r.status))
        except Exception as e:
            tried.append("%s → %s" % (label, type(e).__name__))
    return None, None, tried


# ---------------------------------------------------------------- GitHub

class GitHub(object):
    def __init__(self, token, opener):
        self.token = token
        self.opener = opener

    def call(self, path, method="GET", body=None):
        req = urllib.request.Request(
            API + path, method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "User-Agent": UA,
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener.open(req, timeout=CALL_TIMEOUT) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"message": raw.decode("utf-8", "replace")[:200]}
        except Exception as e:
            # 保留真实原因，别只报一个 -1
            return -1, {"message": "%s: %s" % (type(e).__name__, str(e)[:180])}

    def whoami(self):
        st, body = self.call("/user")
        return (body.get("login") if st == 200 else None), st, body

    def repo_info(self, repo):
        return self.call("/repos/%s" % repo)

    def secret_meta(self, repo, name):
        st, body = self.call("/repos/%s/actions/secrets/%s" % (repo, name))
        return (body if st == 200 else None), st

    def latest_run(self, repo, workflow="daily.yml"):
        st, body = self.call(
            "/repos/%s/actions/workflows/%s/runs?per_page=1" % (repo, workflow))
        if st != 200:
            return None, st, body
        runs = body.get("workflow_runs") or []
        return (runs[0] if runs else None), st, body

    def put_secret(self, repo, name, value):
        st, key = self.call("/repos/%s/actions/secrets/public-key" % repo)
        if st != 200:
            return st, "读不到仓库公钥：%s" % key.get("message", "")
        pk = public.PublicKey(key["key"].encode("utf-8"), encoding.Base64Encoder)
        encrypted = base64.b64encode(
            public.SealedBox(pk).encrypt(value.encode("utf-8"))).decode("utf-8")
        st2, body = self.call("/repos/%s/actions/secrets/%s" % (repo, name),
                              method="PUT",
                              body={"encrypted_value": encrypted, "key_id": key["key_id"]})
        return (0, "") if st2 in (201, 204) else (st2, body.get("message", ""))

    def trigger(self, repo, workflow="daily.yml"):
        st, body = self.call("/repos/%s/actions/workflows/%s/dispatches" % (repo, workflow),
                             method="PUT", body={"ref": "main"})
        return (0, "") if st == 204 else (st, body.get("message", ""))


# ---------------------------------------------------------------- 两个凭据

def read_workbuddy_token():
    client = rt.find_client()
    envelope = rt.read_envelope(rt.find_auth_file())
    data = rt.run_helper(client, {"operation": "decrypt", "value": envelope})
    tok = (data or {}).get("accessToken") or ""
    if not tok:
        raise RuntimeError("登录态里没有 accessToken（可能未登录或格式已变）")
    return tok


def read_github_token():
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        proc = subprocess.run(["git", "credential", "fill"],
                              input=b"protocol=https\nhost=github.com\n\n",
                              capture_output=True, env=env, timeout=45)
    except FileNotFoundError:
        raise RuntimeError("找不到 git 命令。请确认已安装 Git 且它在 PATH 里")
    cred = dict(l.split("=", 1) for l in proc.stdout.decode("utf-8", "replace").splitlines()
                if "=" in l)
    tok = cred.get("password", "")
    if not tok:
        raise RuntimeError("git 凭据管理器里没有 github.com 的凭据。"
                           "先在本目录执行一次 git push 并登录，或用 --gh-token 传 PAT")
    return tok, cred.get("username", "")


# ---------------------------------------------------------------- 判断是否要更新

def decide(secret_meta, latest_run, exp):
    """返回 (是否需要更新, 判断依据列表)。"""
    now = int(time.time())
    reasons, needs = [], False

    if secret_meta is None:
        return True, ["GitHub 上还没有这条 Secret"]

    updated = parse_iso(secret_meta.get("updated_at", ""))
    if updated:
        days = (now - updated) / 86400.0
        reasons.append("Secret 上次更新：%s（%.1f 天前）" % (fmt_ts(updated), days))
        if days > STALE_DAYS:
            needs = True
            reasons.append("已超过 %d 天，token 可能已到期" % STALE_DAYS)
    else:
        reasons.append("Secret 存在，但读不到更新时间")

    if latest_run:
        concl = latest_run.get("conclusion") or latest_run.get("status")
        created = parse_iso(latest_run.get("created_at", ""))
        reasons.append("最近一次运行：%s（%s）" % (concl, fmt_ts(created)))
        if concl == "failure" and updated and created and created >= updated:
            needs = True
            reasons.append("最近一次运行失败，很可能就是 token 失效了")
    else:
        reasons.append("查不到最近一次运行记录")

    if exp:
        left = (exp - now) / 86400.0
        reasons.append("本地 token 有效期至 %s（还剩 %.1f 天）"
                       % (fmt_ts(exp), left))
        if left <= REFRESH_AHEAD_DAYS:
            needs = True
            reasons.append("本地 token 也快到期了，抓紧更新")

    return needs, reasons


# ---------------------------------------------------------------- 主流程

def parse_args(argv):
    opt = {"repo": DEFAULT_REPO, "name": "WB_ACCESS_TOKEN", "check": False,
           "trigger": False, "force": False, "proxy": "", "gh_token": "",
           "value_file": "", "assume_yes": False}
    i = 1
    while i < len(argv):
        a = argv[i]

        def take():
            return argv[i + 1] if i + 1 < len(argv) else ""

        if a == "--repo":
            opt["repo"] = take(); i += 2
        elif a == "--name":
            opt["name"] = take(); i += 2
        elif a == "--gh-token":
            opt["gh_token"] = take(); i += 2
        elif a == "--value-file":
            opt["value_file"] = take(); i += 2
        elif a == "--proxy":
            opt["proxy"] = take(); i += 2
        elif a == "--check":
            opt["check"] = True; i += 1
        elif a == "--trigger":
            opt["trigger"] = True; i += 1
        elif a == "--force":
            opt["force"] = True; i += 1
        elif a in ("--yes", "-y"):
            opt["assume_yes"] = True; i += 1
        elif a == "--no-pause":
            i += 1
        elif a in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        else:
            bad("不认识的参数：%s" % a)
            raise SystemExit(2)
    return opt


def ask_yes(question):
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    try:
        return input(question).strip().lower() in ("y", "yes", "是")
    except Exception:
        return False


def main(argv):
    opt = parse_args(argv)
    print("=" * 54)
    print(" Buddy 加油站 · GitHub Secret 维护工具")
    print("=" * 54)
    print(" 仓库：%s   名称：%s" % (opt["repo"], opt["name"]))

    # 1) 本地 token + 有效期
    step("【1/5】读取本机登录态")
    if opt["value_file"]:
        value = open(opt["value_file"], encoding="utf-8").read().strip()
        ok("从文件读取，长度 %d" % len(value))
    else:
        try:
            value = read_workbuddy_token()
            ok("已解出，长度 %d（不显示内容）" % len(value))
        except Exception as e:
            bad("取不到登录态：%s" % e)
            info("→ 打开并登录 WorkBuddy 客户端后重试")
            return 1

    exp = jwt_exp(value)
    if exp:
        left = (exp - time.time()) / 86400.0
        if left > 0:
            info("token 有效期至 %s（还剩 %.1f 天）" % (fmt_ts(exp), left))
        else:
            bad("本地 token 已经过期（%s）" % fmt_ts(exp))
            info("→ 在客户端里重新登录一次，让它签发新 token，再跑本工具")
            return 1
    else:
        info("这不是标准的 JWT，跳过有效期检查")

    # 2) GitHub 凭据
    step("【2/5】获取 GitHub 凭据")
    try:
        gh_token, gh_user = (opt["gh_token"], "") if opt["gh_token"] else read_github_token()
        ok("已取得（账号：%s）" % (gh_user or "未知"))
    except Exception as e:
        bad("取不到 GitHub 凭据：%s" % e)
        info("→ 也可以 --gh-token ghp_xxx 手动传一个 PAT")
        return 1

    # 3) 网络出口
    step("【3/5】探测网络出口")
    opener, label, tried = pick_outlet(opt["proxy"])
    if opener is None:
        bad("所有出口都连不上 api.github.com")
        for t in tried:
            info("  试过 %s" % t)
        info("→ 打开代理软件后重试，或在 exe 同目录建 proxy.txt 写一行代理地址，")
        info("  形如 http://127.0.0.1:7890；也可以用 --proxy 指定")
        return 1
    ok("可用出口：%s" % label)

    gh = GitHub(gh_token, opener)
    login, st, body = gh.whoami()
    if st != 200:
        bad("GitHub 凭据无效（HTTP %s）：%s" % (st, body.get("message", "")))
        if st == -1:
            info("→ 这是网络问题，不是凭据问题。换个出口再试")
        else:
            info("→ 重新执行一次 git push，在弹窗里登录后重试")
        return 1
    ok("凭据有效，身份：%s" % login)

    # 4) 仓库 + 现在的状态
    step("【4/5】检查仓库与现状")
    st, repo_body = gh.repo_info(opt["repo"])
    if st != 200:
        bad("仓库不可访问（HTTP %s）：%s" % (st, repo_body.get("message", "")))
        return 1
    ok("仓库：%s（%s）" % (repo_body.get("full_name"),
                         "私有" if repo_body.get("private") else "公开"))

    secret, st = gh.secret_meta(opt["repo"], opt["name"])
    run, _st, _b = gh.latest_run(opt["repo"])
    needs, reasons = decide(secret, run, exp)
    for r in reasons:
        info(r)

    # 5) 写入决策
    step("【5/5】更新 Secret")
    if opt["check"]:
        concl = "需要更新" if needs else "暂时不用更新"
        ok("体检结论：%s%s" % (concl, "" if needs else "，若想覆盖请加 --force"))
        return 0

    if not needs and not opt["force"]:
        info("判断：当前不需要更新（上面三条依据都正常）")
        if opt["assume_yes"] or ask_yes("  仍要强制覆盖吗？[y/N] "):
            needs = True
        else:
            print("\n已跳过写入。想强制更新请加 --force，或用 --check 只看体检结果。")
            return 0

    st, msg = gh.put_secret(opt["repo"], opt["name"], value)
    if st != 0:
        bad("写入失败（HTTP %s）：%s" % (st, msg))
        if st == 403:
            info("→ 账号没有 Actions secrets 写权限，请用仓库管理员账号")
        elif st == -1:
            info("→ 网络中断，确认代理还开着")
        return 1
    ok("已写入（旧值已被覆盖）")

    if opt["trigger"]:
        st, msg = gh.trigger(opt["repo"])
        if st == 0:
            ok("已触发一次运行")
            info("https://github.com/%s/actions" % opt["repo"])
        else:
            bad("触发失败（HTTP %s）：%s" % (st, msg))

    print("\n" + "=" * 54)
    print(" 完成。下次定时运行会用新 token。")
    print("=" * 54)
    return 0


def run():
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
