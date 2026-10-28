# -*- coding: utf-8 -*-
"""把本机执行器注册成 Windows 计划任务。

与原先的 --only-trigger（只负责「喊一声」云端）不同，这个任务在本机
直接完成签到 + 派猫 + 发邮件，完全不依赖 GitHub 调度。

用法：
    python setup_local_task.py            # 注册/更新任务
    python setup_local_task.py --uninstall # 卸载
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

TASK_NAME = "Buddy加油站-每日签到"
TASK_DESC = ("每小时 + 每次登录，直接在本机完成 Buddy 加油站签到/派猫/发邮件；"
              "不依赖 GitHub 定时任务")

PS_TEMPLATE = r'''
$ErrorActionPreference = "Stop"
$name = "{name}"
$exe  = "{exe}"
$dir  = "{dir}"

{tasks}

Write-Output "=== 任务已注册 ==="
$t = Get-ScheduledTask -TaskName $name
Write-Output ("State = " + $t.State)
$i = 1
foreach ($tr in $t.Triggers) {{
  Write-Output ("  [" + $i + "] " + $tr.CimClass.CimClassName + "  Start=" + $tr.StartBoundary +
                "  Interval=" + $tr.Repetition.Interval + "  Duration=" + $tr.Repetition.Duration +
                "  Delay=" + $tr.Delay)
  $i++
}}
Get-ScheduledTaskInfo -TaskName $name | Format-List LastRunTime, LastTaskResult, NextRunTime
'''

PS_REGISTER = r'''
# 每小时重复：错过的周期会被下一个补上（每日固定时刻的触发器不可靠，
# 实测会出现 NextRunTime 卡在过去的时间点、当天全部漏跑的情况）
$rep = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(-1) `
  -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
# 登录时补一次（AtStartup 需要管理员权限，普通用户会报「拒绝访问」）
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = "PT2M"

$action = New-ScheduledTaskAction -Execute $exe -Argument "--local" -WorkingDirectory $dir
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
  -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($rep, $logon) `
  -Settings $set -Description "{desc}" -Force | Out-Null
'''

PS_UNINSTALL = r'''
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Write-Output "已卸载任务：$name"
'''


def ps_quote(s):
    return s.replace('"', '`"')


def run_ps(body):
    ps = PS_TEMPLATE.format(name=ps_quote(TASK_NAME),
                             exe=ps_quote(os.path.join(HERE, "BuddyCheckin.exe")),
                             dir=ps_quote(HERE),
                             tasks=body.replace("{desc}", TASK_DESC))
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8-sig") as f:
        f.write(ps)
        path = f.name
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", path], capture_output=True)
        out = r.stdout.decode("utf-8", "replace").strip()
        err = r.stderr.decode("utf-8", "replace").strip()
        if out:
            print(out)
        if err:
            print("stderr:", err[:800])
        return r.returncode
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main(argv):
    exe = os.path.join(HERE, "BuddyCheckin.exe")
    if not os.path.isfile(exe):
        print("✗ 找不到 %s" % exe)
        print("  先打包：pyinstaller BuddyCheckin.spec --noconfirm --distpath dist")
        return 1

    if "--uninstall" in argv[1:]:
        return run_ps(PS_UNINSTALL)

    print("任务名称：%s" % TASK_NAME)
    print("执行程序：%s --local" % exe)
    print("工作目录：%s" % HERE)
    print()
    return run_ps(PS_REGISTER)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
