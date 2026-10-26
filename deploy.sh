#!/usr/bin/env bash
# 把本目录推到你的 GitHub 仓库。用法：
#   bash deploy.sh 你的用户名/仓库名
#
# 只做三件事：绑定 remote、推 main、把接下来的两条命令打印出来。
# 不碰凭据：认证交给 git 的凭据管理器弹窗（或你已有的 SSH 配置）。
set -euo pipefail

REPO="${1:-}"
if [ -z "$REPO" ]; then
  echo "用法：bash deploy.sh 你的用户名/仓库名"
  echo "例：  bash deploy.sh wqf/workbuddy_auto_checkin"
  exit 1
fi

case "$REPO" in
  */*) ;;
  *) echo "✗ 仓库名要写成 用户名/仓库名 的形式，收到的是：$REPO"; exit 1 ;;
esac

cd "$(dirname "$0")"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "✗ 当前目录不是 git 仓库"
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "! 还有未提交的改动，先提交再推："
  git status --short
  exit 1
fi

URL="https://github.com/${REPO}.git"
echo "· 目标仓库：$URL"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$URL"
else
  git remote add origin "$URL"
fi
echo "· remote origin 已就位"

echo "· 开始推送（首次会弹窗让你登录 GitHub）..."
if ! git push -u origin main; then
  cat <<'TIP'

✗ 推送失败。按现象对号：

  弹窗登录后仍 403 / 一直转圈
      网络到 GitHub 太慢或代理不稳，换个网络再试；或改用 SSH：
      git remote set-url origin git@github.com:用户名/仓库名.git

  提示 repository not found
      仓库还没在 GitHub 上创建，或名字拼错（大小写敏感）

  提示 Authentication failed 且没有弹窗
      本机没配凭据管理器，改用 Personal Access Token：
      git push https://<你的用户名>@github.com/用户名/仓库名.git
      （弹 Password 时粘 PAT，不要把 PAT 写进 remote URL 或脚本里）

TIP
  exit 1
fi

PY="$(command -v python3 || true)"

cat <<NEXT

✓ 代码已推上 main。还剩两步才能让它每天自动跑：

1) 填两条 Secret（仓库 → Settings → Secrets and variables → Actions）
   WB_ACCESS_TOKEN   本机运行下面的命令拿：
                     ${PY:-python} refresh_token.py
   FEISHU_WEBHOOK    飞书群机器人地址（建机器人时别勾签名校验）

2) 启用 Actions（fork 来的仓库才需要）：Actions 页签 → I understand my workflows...

之后每天 23:17 UTC（北京 07:17）自动执行，本机关机也不影响。
想立刻试一次：Actions → Buddy 加油站 · 每日签到 + 派猫猫 → Run workflow → mode 选 dry-run。
NEXT
