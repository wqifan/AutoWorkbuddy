#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refresh_token.py —— 本机一次性操作：把 WorkBuddy 登录态导出成 GitHub Secret。

为什么必须在【本机】跑
    桌面端把会话写在 workbuddy-desktop.info 里，accessToken 是一个
    {"$wbEncrypted": 1, "envelope": "<base64>"} 信封（内部 suite=1 / sym-v1，
    AES-256-GCM）。解信封需要的密钥由客户端运行时通过 Electron 私有绑定
    electron_browser_workbuddy_storage 提供 —— 云端 runner 既没有那个运行时，
    也拿不到这台机器的密钥，所以「云端现取 token」在原理上就不成立。

本脚本的做法
    读信封 → 以 ELECTRON_RUN_AS_NODE=1 起一个 WorkBuddy 子进程 →
    把信封从 stdin 递进去，由客户端自己的运行时解密 → 从 stdout 取回明文 token。
    密钥与明文只在内存里走一遍，不写临时文件，不进 argv，不进日志。

用法
    python refresh_token.py                          # 打印 token（手动填 Secret）
    python refresh_token.py --gh-repo OWNER/REPO      # 有 gh CLI 时直接写入 Secret
    python refresh_token.py --check                   # 只验证 token 是否可用，不打印

零第三方依赖，Python 3.8+。
"""

import json
import os
import re
import shutil
import subprocess
import sys

AUTH_TIMEOUT = 40.0
IO_LIMIT = 65536
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/\-]+=*$")

# 固定代码走 argv（不含任何凭据）；信封走 stdin。
HELPER_JS = r"""
'use strict';
const crypto = require('crypto');
const LIMIT = 65536;
const fail = reason => { throw { reason }; };
const isObj = x => x !== null && typeof x === 'object' && !Array.isArray(x);

function b64(value, length) {
  if (typeof value !== 'string' || value.length > LIMIT) fail('INVALID_FORMAT');
  const bytes = Buffer.from(value, 'base64');
  if (bytes.toString('base64') !== value) fail('INVALID_FORMAT');
  if (length !== undefined && bytes.length !== length) fail('INVALID_FORMAT');
  return bytes;
}
function utf8(bytes) {
  const text = bytes.toString('utf8');
  if (!Buffer.from(text, 'utf8').equals(bytes)) fail('INVALID_FORMAT');
  return text;
}
function parseEnvelope(value) {
  if (!isObj(value) || value.$wbEncrypted !== 1) fail('UNSUPPORTED_ENVELOPE');
  if (Object.keys(value).sort().join(',') !== '$wbEncrypted,envelope') fail('UNSUPPORTED_ENVELOPE');
  let env;
  try { env = JSON.parse(utf8(b64(value.envelope))); } catch (e) { fail(e.reason || 'INVALID_FORMAT'); }
  if (!isObj(env) || !Number.isInteger(env.suite)) fail('INVALID_FORMAT');
  if (env.suite !== 1) fail('UNSUPPORTED_ENVELOPE');
  if (Object.keys(env).sort().join(',') !== 'authTag,ciphertext,keyId,nonce,suite') fail('INVALID_FORMAT');
  if (typeof env.keyId !== 'string' || !/^[0-9a-f]{16}$/.test(env.keyId)) fail('INVALID_FORMAT');
  return {
    keyId: env.keyId,
    nonce: b64(env.nonce, 12),
    tag: b64(env.authTag, 16),
    body: b64(env.ciphertext)
  };
}
function storage() {
  let binding;
  try { binding = process._linkedBinding('electron_browser_workbuddy_storage'); }
  catch (_) { fail('RUNTIME_UNAVAILABLE'); }
  if (!binding || typeof binding.loggerGet !== 'function') fail('RUNTIME_UNAVAILABLE');
  return binding;
}
const lp = s => {
  const bytes = Buffer.from(s, 'utf8');
  const head = Buffer.alloc(4);
  head.writeUInt32BE(bytes.length);
  return Buffer.concat([head, bytes]);
};
function decrypt(value) {
  const env = parseEnvelope(value);
  let payload;
  try { payload = JSON.parse(storage().loggerGet()); } catch (_) { fail('RUNTIME_UNAVAILABLE'); }
  if (!isObj(payload) || payload.version !== 1) fail('RUNTIME_UNAVAILABLE');

  let secret;
  try { secret = b64(payload.atRestSecretKey, 32); } catch (_) { fail('RUNTIME_UNAVAILABLE'); }
  const blank = secret.every(b => b === 0);
  secret.fill(0);
  if (blank) fail('RUNTIME_UNAVAILABLE');

  let key, plain;
  try {
    key = crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();
    payload = null;
    if (crypto.createHash('sha256').update(key).digest('hex').slice(0, 16) !== env.keyId) fail('KEY_MISMATCH');
    const aad = Buffer.concat([
      Buffer.from('WB-AAD\0', 'ascii'), Buffer.from([1]),
      lp('WBEV1'), lp('sym-v1'), Buffer.from([0, 0, 0, 1]),
      lp(env.keyId), Buffer.from([2, 0, 0])
    ]);
    const cipher = crypto.createDecipheriv('aes-256-gcm', key, env.nonce, { authTagLength: 16 });
    cipher.setAAD(aad);
    cipher.setAuthTag(env.tag);
    plain = Buffer.concat([cipher.update(env.body), cipher.final()]);
    const token = utf8(plain);
    if (!token || token.length > 32768) fail('INVALID_FORMAT');
    return token;
  } catch (e) {
    fail(e.reason || 'DECRYPT_FAILED');
  } finally {
    if (key) key.fill(0);
    if (plain) plain.fill(0);
  }
}
function reply(obj) {
  process.stdout.write(JSON.stringify(Object.assign({ version: 1 }, obj)), () => process.exit(obj.ok ? 0 : 1));
}
let chunks = [], size = 0;
process.stdin.on('data', c => {
  size += c.length;
  if (size > LIMIT) reply({ ok: false, reason: 'INVALID_FORMAT' });
  else chunks.push(c);
});
process.stdin.on('error', () => reply({ ok: false, reason: 'HELPER_PROTOCOL' }));
process.stdin.on('end', () => {
  try {
    const req = JSON.parse(utf8(Buffer.concat(chunks)));
    chunks = [];
    if (!isObj(req) || req.version !== 1) fail('HELPER_PROTOCOL');
    if (req.operation === 'probe') {
      storage();
      if (!crypto.getCiphers().includes('aes-256-gcm')) fail('RUNTIME_UNAVAILABLE');
      reply({ ok: true, electron: process.versions.electron || 'unknown' });
    } else if (req.operation === 'decrypt') {
      reply({ ok: true, accessToken: decrypt(req.value) });
    } else {
      fail('HELPER_PROTOCOL');
    }
  } catch (e) {
    const known = ['INVALID_FORMAT', 'UNSUPPORTED_ENVELOPE', 'RUNTIME_UNAVAILABLE',
                   'KEY_MISMATCH', 'DECRYPT_FAILED', 'HELPER_PROTOCOL'];
    reply({ ok: false, reason: known.indexOf(e.reason) >= 0 ? e.reason : 'HELPER_PROTOCOL' });
  }
});
"""


def die(msg, code=1):
    print("✗ " + msg, file=sys.stderr)
    raise SystemExit(code)


def find_auth_file():
    """WORKBUDDY_AUTH_FILE 可覆盖；否则按常见安装位置探测。"""
    override = os.environ.get("WORKBUDDY_AUTH_FILE")
    if override:
        if os.path.isfile(override):
            return override
        die("WORKBUDDY_AUTH_FILE 指向的文件不存在：" + override)
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    for path in (
        os.path.join(local, "CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info"),
        os.path.join(local, "workbuddy-desktop.info"),
        os.path.join(home, "Library", "Application Support", "workbuddy-desktop.info"),
        os.path.join(home, ".config", "workbuddy-desktop.info"),
        os.path.join(home, ".workbuddy", "auth", "workbuddy-desktop.info"),
    ):
        if os.path.isfile(path):
            return path
    die("没找到登录态文件，请先在客户端登录一次；或用 WORKBUDDY_AUTH_FILE 指定路径")


def find_client():
    """WORKBUDDY_EXE 可覆盖；否则按安装目录 → 注册表 → 常见盘符探测。"""
    override = os.environ.get("WORKBUDDY_EXE")
    if override:
        if os.path.isfile(override):
            return os.path.abspath(override)
        die("WORKBUDDY_EXE 指向的文件不存在：" + override)
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    candidates = [os.path.join(local, "Programs", "WorkBuddy", "WorkBuddy.exe")]
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(var):
            candidates.append(os.path.join(os.environ[var], "WorkBuddy", "WorkBuddy.exe"))
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    if sys.platform == "win32":
        found = _from_registry()
        if found:
            return found
    for drive in ("C:/", "D:/", "E:/", "F:/"):
        path = os.path.join(drive, "WorkBuddy", "WorkBuddy.exe")
        if os.path.isfile(path):
            return os.path.abspath(path)
    die("没找到 WorkBuddy 客户端，请用 WORKBUDDY_EXE 指定 WorkBuddy.exe 的路径")


def _from_registry():
    """只读注册表里的字符串值，绝不执行卸载命令。"""
    try:
        import winreg
    except ImportError:
        return None
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as entry:
                                name, _ = winreg.QueryValueEx(entry, "DisplayName")
                                if not isinstance(name, str) or "workbuddy" not in name.lower():
                                    continue
                                icon, _ = winreg.QueryValueEx(entry, "DisplayIcon")
                                if isinstance(icon, str):
                                    path = icon.split(",")[0].strip().strip('"')
                                    if os.path.isfile(path):
                                        return os.path.abspath(path)
                        except OSError:
                            continue
            except OSError:
                continue
    return None


def run_helper(exe, request):
    """把请求递给客户端运行时；所有管道与超时都有界，异常信息不含捕获内容。"""
    data = json.dumps(dict(request, version=1), ensure_ascii=True).encode("ascii")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("NODE_", "ELECTRON_", "WORKBUDDY_"))}
    env["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        proc = subprocess.Popen(
            [exe, "-e", HELPER_JS],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        die("无法启动客户端运行时：%s" % e)
    try:
        out, _err = proc.communicate(data, timeout=AUTH_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        die("解密助手超时（%.0fs）" % AUTH_TIMEOUT)
    if len(out) > IO_LIMIT:
        die("解密助手输出超限")
    try:
        reply = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeError):
        die("解密助手没有返回有效结果（客户端版本可能已变更）")
    if reply.get("ok") is not True:
        reason = str(reply.get("reason", "UNKNOWN"))
        hint = {
            "UNSUPPORTED_ENVELOPE": "登录态信封格式不是 suite 1 / sym-v1，本脚本暂不支持",
            "RUNTIME_UNAVAILABLE": "客户端运行时拒绝提供密钥，确认 WorkBuddy 版本未被裁剪",
            "KEY_MISMATCH": "信封与当前机器密钥不匹配，常见于从别的电脑拷来的登录态",
            "DECRYPT_FAILED": "解封失败，登录态可能已损坏，请在客户端重新登录",
        }.get(reason, "未知错误")
        die("解密失败：%s（%s）" % (reason, hint))
    return reply


def read_envelope(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        die("读取登录态失败：%s" % e)
    token = (data.get("auth") or {}).get("accessToken")
    if not isinstance(token, dict) or token.get("$wbEncrypted") != 1:
        die("登录态里没有加密的 accessToken，请先在客户端登录一次")
    return token


def write_secret(repo, token):
    """有 gh CLI 就经 stdin 写入 Secret —— 明文不进命令行、不进 shell 历史。"""
    gh = shutil.which("gh")
    if not gh:
        return False, "未检测到 gh CLI"
    try:
        proc = subprocess.run([gh, "secret", "set", "WB_ACCESS_TOKEN", "--repo", repo],
                              input=token.encode("utf-8"), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, shell=False)
    except OSError as e:
        return False, "调用 gh 失败：%s" % e
    if proc.returncode != 0:
        return False, "gh 返回 %d：%s" % (proc.returncode,
                                         proc.stderr.decode("utf-8", "replace").strip()[:200])
    return True, "已写入 %s 的 Secret WB_ACCESS_TOKEN" % repo


def check_token(token):
    """只读验证：调一次签到状态接口，确认 token 与服务端端点都通。"""
    import ssl
    import urllib.error
    import urllib.request
    endpoint = os.environ.get("WB_ENDPOINT") or "https://copilot.tencent.com"
    req = urllib.request.Request(
        endpoint + "/v2/billing/meter/checkin-activity-status",
        data=b"{}", method="POST",
        headers={"Authorization": "Bearer %s" % token, "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": "WorkBuddy"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20,
                                    context=ssl.create_default_context()) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        return True, "HTTP %s code=%s 今日已签=%s 连续=%s 天" % (
            resp.status, body.get("code"),
            data.get("today_checked_in"), data.get("streak_days"))
    except urllib.error.HTTPError as e:
        return False, "HTTP %s（401/403 说明 token 失效，请重新导出）" % e.code
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def main(argv):
    args = [a for a in argv[1:]]
    repo = None
    if "--gh-repo" in args:
        i = args.index("--gh-repo")
        repo = args[i + 1] if i + 1 < len(args) else None
        if not repo:
            die("--gh-repo 后面要跟 OWNER/REPO")
    only_check = "--check" in args

    auth_file = find_auth_file()
    exe = find_client()
    print("· 登录态：%s" % auth_file)
    print("· 客户端：%s" % exe)

    token = run_helper(exe, {"operation": "decrypt", "value": read_envelope(auth_file)})["accessToken"]
    if not TOKEN_RE.match(token):
        die("解密结果不像一个 token，已中止（不打印内容）")
    print("· 已解密 accessToken（长度 %d，内容不显示）" % len(token))

    if only_check:
        ok, detail = check_token(token)
        print(("✓ token 可用：" if ok else "✗ token 不可用：") + detail)
        return 0 if ok else 1

    if repo:
        ok, detail = write_secret(repo, token)
        if ok:
            print("✓ " + detail)
            print("\n下一步：给同一个仓库再加一条 Secret FEISHU_WEBHOOK（飞书群机器人地址），")
            print("然后把 .github/workflows/daily.yml 推到默认分支即可。")
            return 0
        print("! 自动写入没成功：%s —— 改为手动复制下面的 token。" % detail)

    print("\n----- 以下为 token -----")
    print(token)
    print("----- 以上为 token -----")
    print("\n把它填进 仓库 → Settings → Secrets and variables → Actions → New repository secret")
    print("Name 用 WB_ACCESS_TOKEN；再建一条 FEISHU_WEBHOOK。填完建议跑一次：")
    print("  python refresh_token.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
