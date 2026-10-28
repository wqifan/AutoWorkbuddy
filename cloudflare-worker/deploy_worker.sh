#!/usr/bin/env bash
# Cloudflare Worker 一键部署
#
# ⚠️ 这里固化了两个顺序要求，顺序反了一定会静默失败：
#
#   1. 必须 **先 deploy 再 secret put**。
#      `wrangler deploy` 会用本地源码建一个新版本，**把已有的 Secret 冲掉**。
#      顺序反了 Worker 就会因为拿不到 GH_PAT 而每次 dispatch 都失败。
#
#   2. 部署完必须确认 secret list 里 GH_PAT 和 TRIGGER_KEY 都在。
#
# 用法：
#   ./deploy_worker.sh          # 正常部署
#   ./deploy_worker.sh --cron-only   # 只改 cron 表达式（同样要重放 secret）
#
set -euo pipefail

cd "$(dirname "$0")"

echo "==> 1/4 部署 Worker（源码变更 + cron 变更）"
npx --yes wrangler@latest deploy

echo
echo "==> 2/4 注入 GH_PAT（取本机 git credential，scope 含 workflow，够用）"
printf 'protocol=https\nhost=github.com\n\n' \
  | timeout 30 git credential fill 2>/dev/null \
  | grep '^password=' | cut -d= -f2- \
  | npx --yes wrangler@latest secret put GH_PAT

echo
echo "==> 3/4 注入 TRIGGER_KEY（手动触发口令）"
if [ -f .trigger_key ]; then
  cat .trigger_key | npx --yes wrangler@latest secret put TRIGGER_KEY
else
  echo "!! .trigger_key 不存在，跳过（手动触发入口将不可用）"
fi

echo
echo "==> 4/4 校验"
npx --yes wrangler@latest secret list
echo
echo "当前 cron："
grep -A2 '"crons"' wrangler.jsonc
echo
echo "✅ 部署完成。5 分钟内 GitHub 应出现 event=workflow_dispatch 的运行。"
