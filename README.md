# WorkBuddy「Buddy 加油站」每日自动化

每天定点在 **GitHub Actions 的免费 runner** 上跑一次：**签到领积分** + **派猫猫旅行并领回积分**，结果推到**飞书群**。本机关机照常执行，不需要任何常开设备。

一次运行的产出：

- 签到：`已签到（今日 +100，连续 5 天，累计 500）`
- 猫猫：`领到上一趟旅行积分 +5；猫猫已出发去咖啡馆，约 3 小时后到家`
- 飞书：一张卡片，签到与猫猫**分两块**展示，中间有分隔线

---

## 一、项目结构

```
wb_auto.py                    云端执行主体：签到、领旅行积分、派出、推飞书
refresh_token.py              本机一次性操作：读登录态 → 解信封 → 写入 Secret
selftest.py                   离线自检（假响应，不打网络），16 条断言
.github/workflows/daily.yml   cron 定义与执行步骤
evidence/run-2026-10-26.json  一次真实运行的脱敏原始返回
```

---

## 二、为什么 token 必须在本机导出

桌面端把会话写在 `workbuddy-desktop.info` 里，`accessToken` 不是明文，而是一个加密信封：

```json
"accessToken": { "$wbEncrypted": 1, "envelope": "eyJzdWl0ZSI6MSwia2V5…（截断）" }
```

信封解开后是 `{suite: 1, keyId, nonce, ciphertext, authTag}` —— AES-256-GCM，密钥由**客户端自己的运行时**（`electron_browser_workbuddy_storage`）持在进程内存里。云端 runner 既没有这个运行时，也拿不到这台机器的密钥，所以「云端现取 token」在原理上走不通。

`refresh_token.py` 走的是另一条路：

```
读信封 → 以 ELECTRON_RUN_AS_NODE=1 起一个 WorkBuddy 子进程
      → 信封从 stdin 递进去，由客户端运行时解密
      → 从 stdout 取回明文 token → 直接写进 GitHub Secret
```

密钥和明文只在内存里过一遍：不写临时文件、不进 argv、不进 shell 历史、不进日志。

---

## 三、部署（约 5 分钟）

### 1. 建仓库并上传

新建一个 **Private** 仓库，把这四个文件 + `.github/` 目录一起上传（**别漏 `.github`**，定时任务定义在里面）。

### 2. 拿一个飞书机器人地址

飞书群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人** → 复制地址
`https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`
> 安全设置可以留空，也可以选**签名校验**（这时要再配一条 `FEISHU_SECRET`，见第 4 步）。
> 若选了**自定义关键词**，关键词建议填 `Buddy`（卡片标题里含这个词），否则推送会被拒。

### 3. 在本机导出 token 并写入 Secret

```bash
python refresh_token.py --gh-repo 你的用户名/你的仓库
```

脚本自动定位登录态和客户端；没装 `gh` CLI 时改为打印 token，手动粘贴到
`仓库 → Settings → Secrets and variables → Actions → New repository secret`。

先验证一下 token 真的能用（只读，不发写请求）：

```bash
python refresh_token.py --check
# ✓ token 可用：HTTP 200 code=0 今日已签=True 连续=5 天
```

### 4. 再加一条 `FEISHU_WEBHOOK`

同页面再建一条 Secret，Name 填 `FEISHU_WEBHOOK`，值粘机器人地址。
用 gh 的话可以不进 shell 历史：

```bash
gh secret set FEISHU_WEBHOOK --repo 你的用户名/你的仓库   # 粘贴后回车
```

机器人若勾了**签名校验**，再建一条 `FEISHU_SECRET`，值是机器人设置页里的那串密钥：

```bash
gh secret set FEISHU_SECRET --repo 你的用户名/你的仓库
```

不配也没关系：`FEISHU_SECRET` 为空时脚本就不带签名字段，正常推送。

### 5. 启用 Actions

进 Actions 页签，点 **I understand my workflows, go ahead and enable them**（新建仓库一般已经是启用状态）。然后在 workflow 页面点 **Run workflow**，`mode` 选 `dry-run` 先看一遍只读结果，再选 `run` 真跑一次。

两条 Secret 配好后，cron 会每天自动跑：**23:17 UTC = 次日 07:17（北京时间）**。

---

## 四、接口（都是从本机实测返回里核对的，不是网上流传的版本）

所有响应统一是 `{code, msg, data, requestId}` 信封，业务字段在 `data` 里。

| 用途 | 方法 | 路径 |
| --- | --- | --- |
| 查今日签到状态 | POST | `/v2/billing/meter/checkin-activity-status` |
| 领取签到积分 | POST | `/v2/billing/meter/daily-checkin` |
| 查旅行状态 | GET | `/v2/activity/growth/buddy/travel/status` |
| 领已到家的旅行积分 | POST | `/v2/activity/growth/buddy/travel/claim` |
| 取可派目的地 | GET | `/v2/activity/growth/buddy/travel/config` |
| 派出一趟 | POST | `/v2/activity/growth/buddy/travel/depart` |

几个必须按实测来、容易踩的点：

- 域名是 `https://copilot.tencent.com`（登录态里的 `domain=www.workbuddy.cn` 只是前端域，别拿它当接口域）。
- 签到状态字段：`today_checked_in` / `streak_days` / `today_credit` / `total_credits` / `is_streak_day` / `season` / `week_progress`。
- 旅行状态 `state` 的实测取值：`idle`（空闲）、`traveling`（在路上）、`arrived`（已到家待领）。
- **`depart` 返回里顶层 `duration_hours` 可能是 `0`**，真实时长在 `location.duration_hours`；算回家时间一律用 `arrive_at - server_now`（这两个是**秒级**时间戳）。
- 领取接口返回的 `reward_credit` 是这一趟的收益（实测 5），不是账户余额。
- 名额上限读 `status.daily_limit_reached`，不要硬撞 `depart` 等它报错。
- 今天已签时 `daily-checkin` 会返回 `code=10001`，属正常幂等，不是失败。

---

## 五、行为矩阵

| 情况 | 脚本行为 | `result` |
| --- | --- | --- |
| 今天没签 | 调 `daily-checkin` 领取，再回读状态拿连签天数 | `CLAIMED` |
| 今天已签 | 不重复领取，只汇报 | `ALREADY` |
| 活动未开启 | 直接汇报，不发写请求 | `INACTIVE` |
| 猫猫已到家 | **先领**这趟积分，再判断能否派下一趟 | `DEPARTED` |
| 猫猫在路上 | 汇报位置与到家时间，**不派** | `IN_TRIP` |
| 猫猫空闲、名额还有 | 取 `config` 的第一个目的地，派出去 | `DEPARTED` |
| 今日名额已用完 | 读 `daily_limit_reached`，**不派** | `LIMIT_REACHED` |
| 401 / 403 | 提示本机重跑 `refresh_token.py` | `AUTH` |
| 猫猫模块任何异常 | 捕获后单独汇报，**不影响签到结论** | `ERROR` |
| 旅行状态值不认识 | 原样汇报，不做任何写操作 | `UNKNOWN_STATE` |

**退出码只反映签到**：`0` 签到成功（猫猫失败也算 0）、`1` 签到未达成、`2` 缺 `WB_ACCESS_TOKEN`。

---

## 六、验收：一次真实运行（2026-10-26）

环境：Windows 11 + 本机客户端 `F:\WorkBuddy\WorkBuddy.exe`，token 由 `refresh_token.py` 从登录态解出后注入环境变量，执行 `python wb_auto.py`。

### 控制台输出（已脱敏，长字段由脚本截断）

```
=== 1/2 签到 ===
  [签到-状态] POST /v2/billing/meter/checkin-activity-status → HTTP 200
  {"code": 0, "msg": "OK", "data": {"active": true, "today_checked_in": true, "streak_days": 5,
   "today_credit": 100, "daily_credit": 100, "total_credits": 500, "is_streak_day": false,
   "next_streak_day": 0, "season": 10, "theme_name": "Buddy加油站", "activity_name": "Buddy应用",
   "start_time": "2026-09-30 00:00:00", "end_time": "2026-12-31 23:59:59",
   "checkin_dates": ["2026-10-26","2026-10-25","2026-10-22","2026-10-21","2026-10-20"],
   "week_checkin_days": 1, "week_progress": [true,false,false,false,false,false,false]}}
  结论：今天已经签过了（今日 +100，连续 5 天，累计 500）

=== 2/2 猫猫旅行 ===
  [猫猫-状态] GET /v2/activity/growth/buddy/travel/status → HTTP 200
  {"code": 0, "msg": "OK", "data": {"state": "arrived", "buddy_id": "***", "record_id": 29943661,
   "location": {"id": 1, "code": "coffee", "name": "咖啡馆", "duration_hours": 2},
   "depart_at": 1792940042, "arrive_at": 1792947242, "server_now": 1792977364,
   "daily_limit_reached": false, "duration_hours": 2, "reward_credit": 5,
   "letter": {"id": 3, "text": "亲爱的铲屎官：…<97 chars>", "guide_text": "Buddy 帮你把研报压成 3 分钟精华了，速看"}}}
  [猫猫-领奖励] POST /v2/activity/growth/buddy/travel/claim → HTTP 200
  {"code": 0, "msg": "OK", "data": {"state": "idle", "record_id": 29943661, "reward_credit": 5,
   "letter": {"id": 3, "text": "亲爱的铲屎官：…<97 chars>"}}}
  [猫猫-目的地] GET /v2/activity/growth/buddy/travel/config → HTTP 200
  {"code": 0, "msg": "OK", "data": {"locations": [
     {"id": 1, "code": "coffee",      "name": "咖啡馆",     "duration_hours_min": 1, "duration_hours_max": 4, "reward_credit_min": 5, "reward_credit_max": 10},
     {"id": 2, "code": "mall",        "name": "商场店铺",   "duration_hours_min": 1, "duration_hours_max": 4, "reward_credit_min": 5, "reward_credit_max": 10},
     {"id": 3, "code": "gym",         "name": "健身房",     "duration_hours_min": 1, "duration_hours_max": 4, "reward_credit_min": 5, "reward_credit_max": 10},
     {"id": 4, "code": "ancient_town","name": "古镇客栈",   "duration_hours_min": 1, "duration_hours_max": 4, "reward_credit_min": 5, "reward_credit_max": 10}],
   "intro_slogans": ["…共 10 条文案，此处省略"], "server_now": 1792977365}}
  [猫猫-派出] POST /v2/activity/growth/buddy/travel/depart → HTTP 200
  {"code": 0, "msg": "OK", "data": {"state": "traveling", "record_id": 30547969,
   "location": {"id": 1, "code": "coffee", "name": "咖啡馆", "duration_hours": 3},
   "depart_at": 1792977365, "arrive_at": 1792988165, "server_now": 1792977365,
   "daily_limit_reached": false, "duration_hours": 0, "reward_credit": 0}}
  结论：领到上一趟旅行积分 +5；猫猫已出发去咖啡馆

RESULT_JSON {"date": "2026-10-26", "probe": false,
  "checkin": {"ok": true, "result": "ALREADY", "report": "今天已经签过了（今日 +100，连续 5 天，累计 500）"},
  "travel": {"result": "DEPARTED", "report": "领到上一趟旅行积分 +5；猫猫已出发去咖啡馆"},
  "push": {"ok": null, "reason": "未配置 FEISHU_WEBHOOK，本次不推送"}, "ok": true}
```

> 上面为了可读性做了换行与缩进，字段值未改动；`config` 里 4 个目的地各带 3 张预览图 + 3 张旅行图、外加 10 条 intro 文案，完整原文见 `evidence/run-2026-10-26.json`。本次没配 webhook，所以 `push.ok` 是 `null`（跳过推送），卡片内容由 `--print-card` 单独渲染校验过：

```json
{"msg_type":"interactive","card":{
  "header":{"template":"green","title":{"tag":"plain_text","content":"Buddy 加油站 · 签到成功"}},
  "elements":[
    {"tag":"div","text":{"tag":"lark_md","content":"**签到**\n✅ 今天已经签过了（今日 +100，连续 5 天，累计 500）"}},
    {"tag":"hr"},
    {"tag":"div","text":{"tag":"lark_md","content":"**猫猫旅行**\n🐱 猫猫正在「咖啡馆」旅行，约 3 小时后到家"}},
    {"tag":"hr"},
    {"tag":"note","elements":[{"tag":"plain_text","content":"2026-10-26 · GitHub Actions 云端执行 · 猫猫异常不影响签到结论"}]}]}}
```

### 复跑一次：幂等验证

紧接着再跑一遍（猫猫刚被派出），应当什么都不写就收工：

```
  [签到-状态] HTTP 200 {"code": 0, "data": {"active": true, "today_checked_in": true, "streak_days": 5, ...}}
  结论：今天已经签过了（今日 +100，连续 5 天，累计 500）
  [猫猫-状态] HTTP 200 {"code": 0, "data": {"state": "traveling", "daily_limit_reached": true,
   "location": {"name": "咖啡馆", "duration_hours": 3}, "arrive_at": 1792988165, "server_now": 1792977731, ...}}
  结论：猫猫正在「咖啡馆」旅行，约 2.9 小时后到家
RESULT_JSON {..., "travel": {"result": "IN_TRIP", ...}, "ok": true}
退出码 0
```

两次运行之间没有出现 `daily-checkin` 与 `depart` 请求 —— 已签不重领、已派出不重派，靠的是先读状态再决定，而不是撞接口报错。

### 离线自检

```
$ python selftest.py
✓ 签到判为 ALREADY 且 ok          ✓ 判为 IN_TRIP
✓ 猫猫先领奖励再派出              ✓ 没有调用 depart
✓ 调用顺序：status→claim→config→depart
✓ 签到判为 CLAIMED +100           ✓ 判为 LIMIT_REACHED
✓ 签到仍 ok                       ✓ 猫猫记为 ERROR 而不是抛异常
✓ 判为 AUTH 且 ok=False           ✓ 文案指向 refresh_token.py
✓ 判为 UNKNOWN_STATE              ✓ 没有任何写请求
✓ 签到成功+猫猫失败 → 退出码 0    ✓ 签到失败 → 退出码 1
16/16 通过
```

---

## 七、安全设计

- 仓库里**没有任何凭据**：token 只在 GitHub Secret 和运行时内存里。
- 明文 token 不进命令行、不进环境变量以外的位置、不进日志；日志与产物里的长字符串统一截断，`buddy_id` / `uin` / token 类字段一律打成 `***`。
- 解密用的 JS 是固定代码，通过 argv 传给客户端；加密信封走 stdin，不经过 argv。
- 秘书钥用后立刻 `fill(0)` 清零，不落临时文件。
- 写操作只在网络异常时有限重试；真正的幂等由服务端的 `today_checked_in` / `daily_limit_reached` 保证。

---

## 八、常见问题

**token 多久过期？** 客户端签发，`expiresIn=2419200` 秒（约 28 天）。到期后定时任务会以「认证被拒（HTTP 401）」失败，**双击 `UpdateBuddyToken.exe` 即可**，详见第十节。

**飞书没收到卡片？** 看日志里 `[飞书]` 那行，脚本会直接给出中文字段提示：

| 返回码 | 含义 | 处理 |
|---|---|---|
| `19001` | webhook 地址不完整或被截断 | 重新复制机器人地址 |
| `19021` | 签名校验没通过 | 建一条 `FEISHU_SECRET`，值与机器人设置页一致 |
| `19024` | 关键词不匹配 | 机器人设了自定义关键词，把关键词改成 `Buddy` 或清空 |
| 5xx / 网络异常 | 临时故障 | 脚本已自动退避重试 3 次 |

这三类都是配置问题，重试无用，脚本不会因推送失败而把整次运行判为失败。

**fork 来的仓库 Actions 是空的？** fork 默认不启用，进 Actions 页签点一次启用。

**为什么不用本机 IPC 通道（命名管道）？** 那条通道要客户端开着才有，云端 runner 里不存在。Actions 方案只能走 token，所以必须有本机导出这一步。

**cron 时间不准？** GitHub 的 `schedule` 在高峰会延后几分钟到十几分钟，属正常；脚本幂等，晚到不会多领。

---

## 九、已知限制

- 只覆盖签到与猫猫旅行；不含补签、连签档位兑换、盲盒、成长中心其它任务。
- 依赖客户端私有接口，客户端升级可能改接口或改信封格式（届时 `refresh_token.py` 会明确报 `UNSUPPORTED_ENVELOPE`，而不是发无效请求）。
- 只支持 `suite 1`（sym-v1）信封；其它格式直接报错退出。

---

## 十、token 到期怎么更新（双击即可）

token 约 28 天过期。到期后 Actions 会失败并提示 `HTTP 401`，此时**双击 `UpdateBuddyToken.exe`** 就行 ——
不用记命令，不用手动去网页改 Secret，也不用创建 PAT。

### 双击版

`UpdateBuddyToken.exe` 放在本目录。双击后它会依次做四件事，每一步都会打印结果：

```
· 1/4 从本机登录态取 accessToken
  ✓ 已解出，长度 1319（不显示内容）
· 2/4 从 git 凭据管理器取 GitHub 凭据
  ✓ 已取得（账号：wqifan）
  ✓ 凭据有效，身份：wqifan
· 3/4 检查仓库写入权限
  ✓ 仓库可访问：wqifan/AutoWorkbuddy（私有）
· 4/4 写入 Secret WB_ACCESS_TOKEN
  ✓ 已写入（旧值已被覆盖）
```

窗口会停住等你按回车，不会一闪而过。前提只有两条：**WorkBuddy 客户端处于登录状态**、
**这台机器之前 push 过代码**（git 凭据管理器里有 GitHub 凭据）。

### 为什么不需要 PAT / gh CLI

写 Secret 需要 GitHub 认证。本工具直接复用 git 凭据管理器里那个 token（`gho_` 开头，OAuth App 签发），
实测它有仓库 Admin 权限，能读 `actions/secrets/public-key` 也能写 Secret。
所以只要你曾经 `git push` 成功过，这个工具就能用。

顺带一提，Secret 的值不是明文传输的：GitHub 要求用仓库公钥做 libsodium sealed box 加密，
本工具用 PyNaCl 完成这一步，PUT 上去的是密文。

### 命令行版（同一份逻辑）

```bash
python wb_update_secret.py --check      # 只检查链路，不写入
python wb_update_secret.py              # 更新 WB_ACCESS_TOKEN
python wb_update_secret.py --trigger    # 更新后立刻触发一次运行
python wb_update_secret.py --name FEISHU_WEBHOOK --value-file hook.txt
```

`--check` 会验证到「能读到公钥」为止，适合先确认权限再真正写。

### 其它情况

| 现象 | 原因 | 处理 |
|---|---|---|
| `取不到登录态` | WorkBuddy 客户端未登录 | 打开客户端登录后重跑 |
| `git 凭据管理器里没有 github.com 的凭据` | 这台机器没 push 过 | 先 `git push` 一次并登录，或 `--gh-token ghp_xxx` 手动传 PAT |
| `这个 GitHub 凭据无效` | 凭据过期/被撤销 | 重新 `git push` 触发登录 |
| `HTTP 403` | 该账号不是仓库管理员 | 换管理员账号，或用 fine-grained PAT（需 Actions secrets 写权限） |
