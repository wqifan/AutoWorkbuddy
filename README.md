# WorkBuddy「Buddy 加油站」每日自动化

每天定点在 **GitHub Actions 的免费 runner** 上跑一次：**签到领积分** + **派猫猫旅行并领回积分**，结果推到**QQ 邮箱**（也可同时推飞书）。本机关机照常执行，不需要任何常开设备。

一次运行的产出：

- 签到：`已签到（今日 +100，连续 5 天，累计 500）`
- 猫猫：`领到上一趟旅行积分 +5；猫猫已出发去咖啡馆，约 3 小时后到家`
- 邮件：一封 HTML 邮件，签到与猫猫**分两块**展示，中间留白，可同时附飞书卡片

---

## 一、项目结构

```
wb_auto.py                    云端执行主体：签到、领旅行积分、派出、发邮件（+ 可选飞书）
refresh_token.py              本机一次性操作：读登录态 → 解信封 → 写入 Secret
wb_update_secret.py           本机工具：先体检再决定要不要重写 token（可打包成 exe）
selftest.py                   离线自检（假响应，不打网络），36 条断言
.github/workflows/daily.yml   cron 定义与执行步骤
evidence/run-2026-10-26.json  一次真实运行的脱敏原始返回
evidence/mail-preview.html    邮件正文样式预览（可直接用浏览器打开）
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

### 2. 拿 QQ 邮箱的 SMTP 授权码

推送走标准 SMTP，**没有条数限制**，每天一封完全够用。拿授权码四步：

1. 电脑浏览器打开 <https://mail.qq.com> 并登录（手机 QQ 邮箱 App 里没有这个设置入口）
2. 顶部 **设置 → 账户**
3. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务」，点 **开启**「POP3/SMTP服务」
4. 按提示用手机发一条短信，页面会给出一串 **16 位授权码**

> 授权码不是 QQ 密码，两者不通用。它只用于第三方客户端登录，随时可以在同一页「删除」作废。
> 建议复制到记事本，页面刷新后看不全。

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

### 4. 再写三条 QQ 邮箱 Secret

同一个页面（仓库 → Settings → Secrets and variables → Actions）新建三条：

| Name | 值 | 必填 |
|---|---|---|
| `QQ_MAIL_USER` | 你的 QQ 邮箱，如 `123456@qq.com` | 是 |
| `QQ_MAIL_AUTH_CODE` | 上一步拿到的 16 位授权码 | 是 |
| `QQ_MAIL_TO` | 收件人，多个用逗号分隔；**留空则发给自己** | 否 |

`QQ_MAIL_USER` 与 `QQ_MAIL_AUTH_CODE` 两个都填才会启用邮箱推送，只填一个等于没配（脚本会跳过并在日志里说明）。

不想进 shell 历史的话，可以用 exe 那个工具写（支持任意 Secret 名）：

```bash
python wb_update_secret.py --name QQ_MAIL_AUTH_CODE --value-file code.txt
```

### 5. （可选）飞书

飞书与邮箱可以**同时配**，两边都会收到。不需要就跳过这一步。

飞书群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人** → 复制地址
`https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`

建 `FEISHU_WEBHOOK` 这条 Secret，值粘地址。机器人若勾了**签名校验**，再建 `FEISHU_SECRET`，值是设置页里的那串密钥；
不配也没关系，`FEISHU_SECRET` 为空时脚本就不带签名字段。

### 6. 启用 Actions

进 Actions 页签，点 **I understand my workflows, go ahead and enable them**（新建仓库一般已经是启用状态）。然后在 workflow 页面点 **Run workflow**，`mode` 选 `dry-run` 先看一遍只读结果，再选 `run` 真跑一次。

Secret 配好后，cron 会每天自动跑：**23:17 / 23:27 / 23:37 / 23:47 UTC = 次日 07:17~07:47（北京时间）**。

> 为什么要排 4 个时间点而不是 1 个：GitHub 的 `schedule` 是「尽力而为」，不保证准点、也不保证一定执行。
> 2026-10-27 实测就遇到整天没投递的情况（详见「十、定时没跑怎么办」）。4 个点只要有一个被投递就够了，
> 脚本是幂等的，多跑不会多领、也不会重复推卡片。

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
| 上一趟出发时间在今天 | 只领积分，**不再派第二趟** | `DONE_TODAY` |
| 401 / 403 | 提示本机重跑 `refresh_token.py` | `AUTH` |
| 猫猫模块任何异常 | 捕获后单独汇报，**不影响签到结论** | `ERROR` |
| 旅行状态值不认识 | 原样汇报，不做任何写操作 | `UNKNOWN_STATE` |

**退出码只反映签到**：`0` 签到成功（猫猫失败也算 0）、`1` 签到未达成、`2` 缺 `WB_ACCESS_TOKEN`。

**推送渠道互不影响，可以同时开**：

| 渠道 | 启用条件 | 说明 |
|---|---|---|
| QQ 邮箱 | `QQ_MAIL_USER` + `QQ_MAIL_AUTH_CODE` 都填 | 无条数限制，HTML 正文 + 纯文本兜底 |
| 飞书 | `FEISHU_WEBHOOK` 填了 | 卡片形式，与邮箱可并存 |

`WB_PUSH` 控制何时推：`auto`（默认，只有真的签到成功或派出新一趟才推，避免 4 个时段推 4 遍）、
`always`（每轮都推，手动验证用）、`never`（不推）。任一渠道失败都只影响该渠道的提示行，不改签到结论。

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
✓ 判为 AUTH 且 ok=False           ✓ 文案指向 refresh_token.py
✓ 签到成功+猫猫失败 → 退出码 0    ✓ 签到失败 → 退出码 1
✓ 已签 + 旅行中（无新增动作）→ 不推 ✓ WB_PUSH=always → 照推
✓ 发信成功，收件人为两人          ✓ 主题含中文且已编码
✓ 签到与猫猫分成两个区块          ✓ 登录用的是授权码而不是密码
✓ 认证失败 → 提示是授权码问题     ✓ 465 不通 → 自动换 587
✓ 收件端还原后主题与发件人一致    ✓ 邮箱与飞书各推一次
36/36 通过
```

邮件相关用例用假的 SMTP 类替换 `smtplib`，所以整套自检**完全不联网、也不发真信**。

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

**为什么不直接推 QQ 机器人？** 试过，卡在配额上，所以改用邮箱。

- QQ 官方机器人的接口本身是通的：`POST https://api.sgroup.qq.com/v2/groups/{group_openid}/messages`，
  `Authorization: QQBot {access_token}`，`access_token` 用 AppID + AppSecret 换（约 2 小时有效，可自动续）。
- 但机器人发消息分两种：**被动消息**必须带 `msg_id`（别人 @ 它之后 5 分钟内回复），**主动消息**自己发起却
  **群聊每群每月只有 4 条**、私聊每用户每月 4 条。每天推一次需要约 30 条/月，实际第二条就会收到
  `40034100 主动消息发送超过频控限制`，未上线审核的机器人还会直接 `40034105 无权限`。
- 被动消息也接不了：它需要有个常驻服务监听事件，而 GitHub Actions 跑一次就退出。
- 另外 `group_openid` 是平台按机器人维度哈希的，**无法提前获知**，只能从真实收到的消息里抄；
  2026 年 1 月起沙箱模式也不再支持群聊测试，连调试都受限。
- 结论：QQ 邮箱 SMTP 没有条数限制，最适合"每天一封"的场景，因此作为默认渠道。

**邮件没收到？** 先看垃圾箱，再看日志里 `[邮箱]` 那行，脚本会直接说清是哪一环：

| 日志 | 含义 | 处理 |
|---|---|---|
| `SMTP 认证失败（535 ...）` | 授权码不对，或用了 QQ 密码 | 回 QQ 邮箱「设置 → 账户」重新生成 16 位授权码 |
| `服务端在认证阶段主动断开` | 同上；QQ 对错误凭据不回 535，直接断连 | 确认 `QQ_MAIL_USER` 与授权码同属一个邮箱，且该邮箱已开启 SMTP 服务 |
| `发件人被拒` | `QQ_MAIL_USER` 与授权码所属邮箱不一致 | 改成同一个邮箱 |
| `收件人被拒` | `QQ_MAIL_TO` 写错或不是有效地址 | 检查拼写，多个收件人用逗号分隔 |
| `SMTP 端口都连不上` | 465 与 587 都不通 | 本机/公司网络拦了外发邮件；Actions 云端一般正常 |

认证类失败不会重试（重试也没用），也不会把整次运行判为失败——签到结论不受影响。

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

**某天完全没跑？** 大概率不是脚本问题，是 GitHub 没投递。判断方法与处理见下一节。

---

## 九、已知限制

- 只覆盖签到与猫猫旅行；不含补签、连签档位兑换、盲盒、成长中心其它任务。
- 依赖客户端私有接口，客户端升级可能改接口或改信封格式（届时 `refresh_token.py` 会明确报 `UNSUPPORTED_ENVELOPE`，而不是发无效请求）。
- 只支持 `suite 1`（sym-v1）信封；其它格式直接报错退出。
- 定时触发的时间点由 GitHub 决定，不保证准点，也不保证每天都执行（见下一节）。

---

## 十、定时没跑怎么办（平台侧，实测记录）

### 怎么区分「没投递」和「跑了但失败」

打开仓库 Actions，看那次运行的 **event** 字段；或者直接问 API：

```bash
# 只看定时触发的运行，条数为 0 就说明根本没触发
curl -s -H "Authorization: Bearer <你的 token>" \
  "https://api.github.com/repos/OWNER/REPO/actions/runs?event=schedule" | grep total_count
```

- `total_count: 0` → 平台没投递，脚本一行都没执行，**不是代码问题**
- 有记录但 `conclusion: failure` → 才需要看日志（多为 token 过期，见「十一」）

### 实测记录：三天 12 次请求，全部落空

```
仓库        wqifan/AutoWorkbuddy（私有，非 fork，默认分支 main，state=active）
            权限 enabled=true / allowed_actions=all / disabled=false / archived=false
远端原文    cron: "17 23,0,1,2 * * *"  ← 已逐行核对，语法合法、确实在 main 上
应触发      ① 2026-10-26T23:17Z（北京 10-27 07:17，4 个点）
            ② 2026-10-27T23:17Z（北京 10-28 07:17，4 个点）
            ③ 2026-10-28 23:17Z（4 个点）
实际        ①②③ 共 12 次请求，event=schedule 计数始终为 0
对照实验    另建 cron "*/5 * * * *" 探针，连盯 50 分钟，同样 0 次
平台状态    githubstatus.com 显示 Actions operational
对比        workflow_dispatch 手动触发 9 次，9 次全部 success（通道本身没问题）
```

### 根因：免费个人账号下，私有仓库的 `schedule` 事件不投递

GitHub 社区 2026 年的确认结论（官方文档没写清楚）：

> Scheduled workflows only run on private repos if you have a GitHub Pro plan ($4/month)
> or if the repository is public.
> —— [community discussion #210422](https://github.com/orgs/community/discussions/210422)

也就是说：**账号是免费版 + 仓库是私有的，`on: schedule` 就是不会触发。**
这不是配置错误，也不是网络问题。所以下面这些排查方向全部是误判，别再走一遍：

| 曾经的怀疑 | 实际情况 |
|---|---|
| cron 表达式写错了 / 时区算错了 | 远端原文逐行核对过，语法合法、UTC 换算正确 |
| workflow 不在默认分支 | `default_branch=main`，文件确实在 main 上 |
| workflow 被禁用了 | `state=active` |
| Actions 权限被限制 | `enabled=true` / `allowed_actions=all` |
| 60 天不活动被自动停用 | 仓库才建 2 天，不适用 |
| 新仓库调度优先级低 | 属实，但不是主因 —— 免费账号私有仓库是**完全**不投递 |

**验证方法**：新建一个**公开**仓库放同样的 cron，立刻就能被投递。
本仓库的 `wqifan/AutoWorkbuddy-Trigger` 就是这么建的，实测通过。

### 修复：公开仓库当闹钟，私有仓库当工人

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│ AutoWorkbuddy-Trigger   │  POST   │ AutoWorkbuddy（私有）        │
│ 【公开】cron 每小时 :17  │ ──────► │ daily.yml                    │
│ 用 TRIGGER_PAT 鉴权      │ dispatch│ → 签到 + 派猫 + 发邮件        │
└─────────────────────────┘         └──────────────────────────────┘
```

定时器仓库 `wqifan/AutoWorkbuddy-Trigger`（公开）：

| 文件 | 作用 |
|---|---|
| `.github/workflows/trigger.yml` | `cron: "17 * * * *"` → 调私有仓库的 `workflow_dispatch` |
| `secrets.TRIGGER_PAT` | fine-grained PAT，仅 AutoWorkbuddy + Actions:Read and write |
| `README.md` | 说明这个仓库是干什么的 |

**为什么每小时而不是每天一次**：即使公开仓库，GitHub 的 schedule 仍是 best-effort
（官方明说会延迟、负载高时可能整批丢弃）。每小时给 24 次机会，命中一次即可。
私有仓库那边的 `daily.yml` 也一并改成了 `17 * * * *`，将来若升级 Pro 就直接生效。

**幂等性**（敢每小时跑的前提）：

1. **推送去重**：只有「真的做了事」的那一轮（签到新领到 / 派出新一趟）才发邮件，
   一天最多一封。手动验证想每次都推，`push` 选 `always`。
2. **「当天已派过」判断**：服务端的 `daily_limit_reached` 在派出后仍是 `False`，
   所以改用「上一趟的 `depart_at` 是否落在今天（北京时间）」来兜底 —— 猫回来得早、
   状态又变回 idle 时也不会重复派。
3. **签到由服务端幂等**：`today_checked_in=true` 或 `code=10001` 都判定为「已签」并视为成功。

### 兜底：本机计划任务（已注册）

即使公开仓库的 cron 也被丢弃，本机这条路仍在。任务名「Buddy加油站-每日签到」，
**5 个触发器**：

- 每天 **07:20 / 12:20 / 18:20 / 21:20**
- **登录时**（开机就补一次）
- 勾了「错过计划开始时间就尽快启动」+「唤醒计算机执行此任务」

```powershell
Get-ScheduledTask -TaskName "Buddy加油站-每日签到" | Format-List TaskName,State
Get-ScheduledTaskInfo -TaskName "Buddy加油站-每日签到" | Format-List LastRunTime,LastTaskResult
```

它跑 `UpdateBuddyToken.exe --only-trigger`，行为是**先查后补**：
调 API 看今天有没有已经跑过（`success` 或正在 `queued/in_progress`），有就直接退出，
没有才发一次 `workflow_dispatch`。所以它和云端定时器撞车也不会重复干活。

- 日志：`%LOCALAPPDATA%\BuddyCheckin\trigger.log`（超过 200KB 自动重置）
- **不需要任何 PAT，也不落盘任何密码**：它复用本机 git 凭据管理器里已有的 GitHub 凭据，
  真正的 token / 授权码都只在 GitHub Secrets 里
- 附带做 **token 续期体检**：Secret 超过 25 天没更新时，自动从本机登录态重新导出并写回
  （token 有效期约 28 天，这是防止「某天起突然全挂」的关键）

### 如果连着几天都没跑

```bash
# 看触发器有没有被投递
curl -s -H "Authorization: Bearer <token>" \
  "https://api.github.com/repos/wqifan/AutoWorkbuddy-Trigger/actions/runs?event=schedule" | grep total_count
```

| 现象 | 检查点 |
|---|---|
| 触发仓库 `event=schedule` 也是 0 | 公开仓库的 cron 需 15~60 分钟才被识别，等一个周期再看 |
| 触发仓库有 schedule，私有仓库没运行 | 私有仓库的 `daily.yml` 是不是被改了导致解析失败 |
| 私有仓库有运行但 `failure` | 多半是 token 过期，见「十一」 |
| 触发仓库 `failure` 且日志是 403/404 | `TRIGGER_PAT` 过期或权限不对，重新生成 |
| 本机任务也没跑 | 看 `%LOCALAPPDATA%\BuddyCheckin\trigger.log` 最后一段的退出码 |

> **为什么不用 cron-job.org**：实测该站注册直接报
> "An error occurred while creating your account"，且它的 FAQ / help / status 页全部 404
> —— 是它自己服务端的问题，跟你的操作无关。第三方在线定时器还会多一份 PAT 落在别人手里，
> 不如用公开 GitHub 仓库当闹钟，凭据不出 GitHub。

---

## 十一、token 到期怎么更新（双击即可）

token 约 28 天过期。到期后 Actions 会失败并提示 `HTTP 401`，此时**双击 `UpdateBuddyToken.exe`** 就行 ——
不用记命令，不用手动去网页改 Secret，也不用创建 PAT。

### 双击版：先体检，需要才写

`UpdateBuddyToken.exe` 放在本目录。双击后走五步，每步都打印依据，不会闷头乱写：

```
【1/5】读取本机登录态
  ✓ 已解出，长度 1319（不显示内容）
  · token 有效期至 2026-11-23 12:01（还剩 28.0 天）

【2/5】获取 GitHub 凭据
  ✓ 已取得（账号：wqifan）

【3/5】探测网络出口
  ✓ 可用出口：本机 127.0.0.1:10974
  ✓ 凭据有效，身份：wqifan

【4/5】检查仓库与现状
  ✓ 仓库：wqifan/AutoWorkbuddy（私有）
  · Secret 上次更新：2026-10-26 12:13（0.0 天前）
  · 最近一次运行：success（2026-10-26 11:51）
  · 本地 token 有效期至 2026-11-23 12:01（还剩 28.0 天）

【5/5】更新 Secret
  ✓ 体检结论：暂时不用更新，若想覆盖请加 --force
```

**判断依据**（三条命中任意一条就认为需要更新）：

| 依据 | 判定 | 含义 |
|---|---|---|
| Secret 上次更新距今超过 25 天 | 需要更新 | token 生命周期是 28 天，快到了 |
| 最近一次运行是 `failure` 且发生在 Secret 更新之后 | 需要更新 | 基本可以确定就是 401 |
| 本地 token 剩余不足 3 天 | 需要更新 | 再不换就来不及 |

都不满足时它会问一句「仍要强制覆盖吗？[y/N]」，输入 `y` 就写。
加 `--force` 则跳过判断直接写。窗口会停住等你按回车，不会一闪而过。

### 为什么不会像之前那样报 HTTP -1

之前踩过一个坑：从 Git Bash 运行时，环境里带着 `https_proxy=http://127.0.0.1:10974`，
所以能连 GitHub；但**双击 exe 从资源管理器启动时，这些变量不存在**，直连又慢又不稳，于是失败。

现在工具会自己找出口，按顺序试：

1. `--proxy` 参数
2. 环境变量 `https_proxy` / `HTTPS_PROXY` / `http_proxy` / `HTTP_PROXY`
3. exe 同目录的 `proxy.txt`（写一行代理地址，例如 `http://127.0.0.1:7890`）
4. 本机常见代理端口（`10974`、`7890`、`7897`、`10809`、`1080`、`8080`、`8888`，先用 TCP 探测再 HTTP 验证）
5. Windows 系统代理（读注册表 `ProxyEnable` / `ProxyServer`）
6. 直连

第一个能通的就用来做后续所有请求。全部不通时会把每个出口的失败原因列出来，而不是只报一个 `-1`。

如果哪天它说「所有出口都连不上」，在 exe 同目录新建 `proxy.txt`，写一行你的代理地址即可，下次双击自动生效。

### 为什么不需要 PAT / gh CLI

写 Secret 需要 GitHub 认证。本工具直接复用 git 凭据管理器里那个 token（`gho_` 开头，OAuth App 签发），
实测它有仓库 Admin 权限，能读 `actions/secrets/public-key` 也能写 Secret。
所以只要你曾经 `git push` 成功过，这个工具就能用。

顺带一提，Secret 的值不是明文传输的：GitHub 要求用仓库公钥做 libsodium sealed box 加密，
本工具用 PyNaCl 完成这一步，PUT 上去的是密文。

顺带说明「为什么能看到 token 有效期」：accessToken 是 RS256 的 JWT，`exp` 字段就是过期时间。
工具只解 payload 读 `exp`，不做任何签名验证，也不打印 token 本体。

### 命令行版（同一份逻辑）

```bash
python wb_update_secret.py --check      # 只体检：报有效期、Secret 更新时间、最近运行结论
python wb_update_secret.py              # 智能判断，需要才写
python wb_update_secret.py --force      # 不问，直接覆盖
python wb_update_secret.py --trigger    # 写完后立刻触发一次运行（push=always，必定发一封邮件便于验证）
python wb_update_secret.py --proxy http://127.0.0.1:7890
python wb_update_secret.py --name FEISHU_WEBHOOK --value-file hook.txt
```

### 其它情况

| 现象 | 原因 | 处理 |
|---|---|---|
| `取不到登录态` | WorkBuddy 客户端未登录 | 打开客户端登录后重跑 |
| `所有出口都连不上 api.github.com` | 代理没开，或端口变了 | 开代理；或在 exe 旁建 `proxy.txt` 写一行代理地址 |
| `git 凭据管理器里没有 github.com 的凭据` | 这台机器没 push 过 | 先 `git push` 一次并登录，或 `--gh-token ghp_xxx` 手动传 PAT |
| `这个 GitHub 凭据无效（HTTP 403/401）` | 凭据过期/被撤销 | 重新 `git push` 触发登录 |
| `这个 GitHub 凭据无效（HTTP -1）` | 其实是网络断了，不是凭据问题 | 工具会自己换出口；全都失败时看它列出的每一条原因 |
| `本地 token 已经过期` | 客户端那次登录也过期了 | 在客户端重新登录一次，让它签发新 token |
| `HTTP 403`（写入时） | 该账号不是仓库管理员 | 换管理员账号，或用 fine-grained PAT（需 Actions secrets 写权限） |
| `触发失败（HTTP 404）` | `workflow_dispatch` 端点只接受 **POST**，旧版工具误用了 PUT | 用当前版 `wb_update_secret.py` / 重打的 exe；已修 |
| `git push` 报 `Empty reply` / `CONNECT tunnel failed, response 502` | 代理只放行了 `api.github.com`，没放行 `github.com:443` | 换代理节点；或改用 Contents API 提交（工具链里有现成脚本） |
