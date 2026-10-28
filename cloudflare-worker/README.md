# Cloudflare Worker · 云端定时器（备用方案）

本方案在**电脑关机时**也能每天跑一次签到。

## 为什么需要它

已实测确认：GitHub Actions 的 `schedule` 事件在**个人账号**下完全不投递
（公开仓库、私有仓库、新建仓库都试过；`*/5` 空跑探针静置 40 分钟零命中；
同期 `nodejs/node` 等组织账号的仓库几分钟前刚跑过 → 是账号级限制）。

Cloudflare Workers 的 Cron Trigger 与 GitHub 完全无关，是真正独立的外部闹钟。

## 部署步骤（约 10 分钟）

1. 打开 <https://dash.cloudflare.com> → 登录 → 左侧 **Workers & Pages**
2. **Create Worker** → 命名 `buddy-trigger` → Deploy
3. 进入该 Worker → **Edit code** → 把 `buddy-trigger.js` 的全部内容
   粘贴替换进去 → **Deploy**
4. **Settings** → **Variables and Secrets** → **Add**：
   - 名称：`GH_PAT`
   - 类型：**Secret**（不是 Plaintext）
   - 值：你的 fine-grained PAT
5. 回到 **Triggers** → **Cron Triggers** → **Add**：
   - Cron expression：`0 23 * * *`（UTC 23:00 = 北京时间次日 07:00）
   - 建议避开整点，可改 `7 23 * * *`
6. **Deploy**

## PAT 怎么拿

GitHub → 头像 → **Settings** → **Developer settings** → **Personal access tokens**
→ **Fine-grained tokens** → **Generate new token**

- Resource owner：`wqifan`
- Repository access：**Only select repositories** → `AutoWorkbuddy`
- Permissions → Repository permissions：**Actions: Read and write**，其余 **No access**
- 有效期建议 1 年，生成后复制（`github_pat_...`，只显示一次）

> 这个 PAT 读不到代码、看不到 Secret、改不了别的东西。
> 即使泄露，最坏结果只是别人帮你多点几次签到。

## 验证

部署后点 **Triggers** 里的 **Send Test Event**，然后看：

- Cloudflare 日志里应出现 `dispatch -> HTTP 204`
- 私有仓库 Actions 里会多一条 `event=workflow_dispatch` 的运行

## 出问题时看哪里

| 现象 | 检查点 |
|---|---|
| 日志报 HTTP 401/403 | `GH_PAT` 无效或已过期，重新生成 |
| 日志报 HTTP 404 | 端点不对，或 PAT 没有该仓库的 Actions 权限 |
| 日志报 HTTP 422 | `inputs` 字段与 `daily.yml` 不匹配（一般不会） |
| 私有仓库有运行但 `failure` | 多半是 `WB_ACCESS_TOKEN` 过期，跑一次 `UpdateBuddyToken.exe` 续期 |
| 定时器没触发 | 检查 Cron Trigger 是否启用（必须是 **Active** 状态） |

## 注意

- 私有仓库的 `daily.yml` 本身是幂等的，重复触发不会多签到、多派猫、多发邮件
- 本机的 Windows 计划任务仍在运行，两边不会重复干活
