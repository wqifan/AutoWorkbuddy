# -*- mode: python ; coding: utf-8 -*-
# 打包「本机执行器」：BuddyCheckin.exe
#
# 与 UpdateBuddyToken.exe 的分工：
#   UpdateBuddyToken.exe  维护 GitHub Secret + 触发云端运行
#   BuddyCheckin.exe      本机直接完成签到/派猫/发邮件（不依赖 GitHub 调度）
#
# wb_auto.py 作为数据文件一起打包 —— PyInstaller 的 Analysis 会
# 自动把被 import 的模块当脚本处理，但这里显式加进 datas 更稳：
# 运行时用 sys._MEIPASS 找到它再 import。

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['_cffi_backend']
for pkg in ('nacl', 'cffi'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

# wb_auto.py / refresh_token.py 一起带上
datas += [('wb_auto.py', '.'), ('refresh_token.py', '.')]

a = Analysis(
    ['wb_local.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BuddyCheckin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
