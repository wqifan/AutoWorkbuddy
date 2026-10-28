# Cloudflare Worker · 云端定时器（备用方案）

本方案在**电脑关机时**也能每天跑一次签到。

> **状态：已实际部署上线（2026-10-28）**
> Worker 名 `buddy-trigger` · 地址 `https://buddy-trigger.852645480.workers.dev`
> · Cron `*/5 * * * *`（UTC）· Secret `GH_PAT` / `TRIGGER_KEY` 已注入
> 下面的部署步骤是给「以后重建一个」时用的参考。

## 为什么需要它

已实测确认：GitHub Actions 的 `schedule` 事件在**个人账号**下完全不投递
（公开仓库、私有仓库、新建仓库都试过；`*/5` 空跑探针静置 40 分钟零命中；
同期 `nodejs/node` 等组织账号的仓库几分钟前刚跑过 → 是账号级限制）。

Cloudflare Workers 的 Cron Trigger 与 GitHub 完全无关，是真正独立的外部闹钟。

## 网页界面入口（新版已改版，容易找不到）

Cloudflare 改版后，**顶层不再有 "Create Worker" 按钮**。现在的路径是：

```
左侧栏 → Workers & Pages → （右上角）Create application → Create Worker → Deploy
```

`Create Worker` 藏在 `Create application` 的**二级菜单**里，所以第一眼看过去
像是"没有这个按钮"。左边栏也可能显示为 `Compute (Workers)` → `Workers & Pages`。

不过**推荐用命令行**，一步到位，不用点网页：

```bash
cd cloudflare-worker
npx wrangler login      # 浏览器里点一下「Allow」完成授权
npx wrangler deploy     # 部署，cron 一并生效
```

`wrangler.jsonc` 已经写好了：

```jsonc
"triggers": { "crons": ["*/5 * * * *"] }
```

## Secret 怎么配

```bash
# GH_PAT：GitHub 凭据
npx wrangler secret put GH_PAT

# TRIGGER_KEY：手动触发用的口令，随便生成一个随机串
npx wrangler secret put TRIGGER_KEY
```

### GH_PAT 填什么

**直接填本机 git credential 里那个 OAuth token 就行**，实测权限已经够用：

```bash
printf 'protocol=https\nhost=github.com\n\n' | git credential fill | grep '^password=' | cut -d= -f2-
```

它返回的 `gho_...` token 的 scope 是 `gist, repo, workflow`，
`workflow` 正好覆盖 dispatch 所需的 **Actions: Read and write**，
所以**不需要**再去 GitHub 设置页手工造 fine-grained PAT。

> 如果你更想用 PAT，规格是：只选 `AutoWorkbuddy` 一个仓库，
> 权限只给 `Actions: Read and write`，其余一律 No access。

## 手动触发（不用等 cron）

```bash
curl -H "X-Trigger-Key: $(cat .trigger_key)" \
     https://buddy-trigger.852645480.workers.dev/
```

`/health` 端点只返回配置状态，不触发：

```bash
curl https://buddy-trigger.852645480.workers.dev/health
```

## 验证

- Cloudflare 日志里应出现 `dispatch -> HTTP 204`
- 私有仓库 Actions 里会多一条 `event=workflow_dispatch` 的运行
- 仓库地址：<https://github.com/wqifan/AutoWorkbuddy/actions>

## 出问题时看哪里

| 现象 | 检查点 |
|---|---|
| 日志报 HTTP 401/403 | `GH_PAT` 无效或已过期，重新注入 |
| 日志报 HTTP 404 | 端点不对，或 PAT 没有该仓库的 Actions 权限 |
| 日志报 HTTP 422 | `inputs` 字段与 `daily.yml` 不匹配（一般不会） |
| 私有仓库有运行但 `failure` | 多半是 `WB_ACCESS_TOKEN` 过期，跑一次 `UpdateBuddyToken.exe` 续期 |
| 定时器没触发 | **可能只是还没到首次调度时间**，见下 |

## ⚠️ 两个已知坑

### 1. 新建 Worker 的 cron 可能很久才开始投递

Cloudflare 文档说 cron 变更"最多 15 分钟生效"，但**社区实测有接近 3 小时
才首次触发的案例**（同一个 Free 账号上连发三个探针 Worker，前两个都被
"等不及"删掉了，第三个留了 2 小时 50 分钟才开始跑）。

所以：**刚部署完看不到动静是正常的，别急着判定失败。**
本仓库留了一个观察脚本：

```bash
./.build-venv/Scripts/python.exe watch_cloudflare_cron.py 240   # 观察 4 小时
```

### 2. 本机可能访问不通 `*.workers.dev`

实测本机直连和走代理都超时。**这不影响 cron 触发** ——
cron 是 Cloudflare 内部调度，不经本机网络。

只是手动触发时如果连不上，改走 GitHub 那条路：

```bash
./UpdateBuddyToken.exe --only-trigger --no-pause
```

## 注意

- 仓库的 `daily.yml` 本身是幂等的，重复触发不会多签到、多派猫、多发邮件
- 本机的 Windows 计划任务仍在运行，两边不会重复干活
- Free 计划每个账号最多 **5 个** cron trigger（够用）
- 免费版 cron **失败不重试、也不告警**，所以监控要靠 `watch_cloudflare_cron.py`
