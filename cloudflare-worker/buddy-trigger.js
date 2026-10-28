/**
 * Buddy 加油站 · 云端定时器
 *
 * 定时触发 GitHub 仓库的 daily.yml，让电脑关机时也能完成签到。
 *
 * 为什么需要它：
 *   这个 GitHub 账号（个人账号）的 `schedule` 事件**极不稳定** ——
 *   实测连续 3 天 12 次 cron 请求全部落空，偶尔才来一条（延迟可达 11 分钟）。
 *   语法从来没错，是平台侧投递不可靠。
 *   所以不能把「等 GitHub 自己按时跑」当唯一保障，必须有独立于 GitHub 的外部闹钟。
 *
 * 鉴权用 secrets.GH_PAT：
 *   实测本机 git credential 里的 OAuth token（scope: gist, repo, workflow）
 *   就足够触发 dispatch，无需另造 fine-grained PAT。
 *
 * ⚠️ 两个必须记住的坑：
 *   1. 请求 GitHub API **必须带 User-Agent 头**，否则一律返回 403
 *      "Request forbidden by administrative rules"。
 *      这个坑曾让 cron 空跑 5 小时、GitHub 上零记录，却完全看不出原因。
 *   2. `wrangler deploy` **会清掉 Secret**，必须 deploy 之后再 `secret put`，
 *      顺序反了 Worker 就会因为缺 GH_PAT 而静默失败。
 *
 * 返回 204 表示 GitHub 已接受这次调度。
 */

/** 手动触发用的口令，存在 secret TRIGGER_KEY 里；没配就禁用 HTTP 触发。 */
function isAuthorized(request, env) {
  const key = (env.TRIGGER_KEY || '').trim();
  if (!key) return false;
  const supplied =
    request.headers.get('X-Trigger-Key') ||
    new URL(request.url).searchParams.get('key') ||
    '';
  return supplied.length > 0 && supplied === key;
}

/** 真正的调度动作：POST 到 GitHub 的 workflow dispatch 端点。 */
async function dispatch(env, ctx) {
  const url =
    'https://api.github.com/repos/wqifan/AutoWorkbuddy/actions/workflows/daily.yml/dispatches';
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GH_PAT}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
      // 必须带 User-Agent！GitHub API 缺失这个头会直接返回 403
      // "Request forbidden by administrative rules"。
      // 之前 cron 一直有在跑，但每次都被这行挡掉，GitHub 上零记录。
      'User-Agent': 'buddy-trigger-cloudflare-worker',
    },
    body: JSON.stringify({ ref: 'main', inputs: { mode: 'run', push: 'auto' } }),
  });
  const body = await res.text();
  console.log(`dispatch -> HTTP ${res.status} ${body.slice(0, 300)}`);
  if (res.status !== 204) {
    console.error(
      `触发失败 HTTP ${res.status}。请检查：1) GH_PAT 是否有效；` +
        `2) 是否有 Actions:Read and write 权限；3) 请求是否带 User-Agent 头。`,
    );
  }
  return res.status;
}

export default {
  /** 定时触发：每天一次。 */
  async scheduled(event, env, ctx) {
    // 用 await 而不是 ctx.waitUntil：官方明确建议 await，
    // 否则 handler 返回时任务可能被提前截断，且 waitUntil 只记录第一个失败。
    const status = await dispatch(env, ctx);
    console.log(`scheduled done, dispatch HTTP ${status}`);
  },

  /**
   * HTTP 触发：手动补一次签到用。
   *   GET /?key=<TRIGGER_KEY>      → 触发一次
   *   GET /health                   → 只看状态，不触发
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === '/health') {
      return new Response(
        JSON.stringify(
          {
            ok: true,
            worker: 'buddy-trigger',
            gh_pat: Boolean((env.GH_PAT || '').trim()),
            trigger_key: Boolean((env.TRIGGER_KEY || '').trim()),
            // 真实 cron 由 wrangler.jsonc 的 triggers.crons 定义，改那边后这里会自动跟着走
            cron: '见 wrangler.jsonc -> triggers.crons（当前 */5 * * * * UTC，即每 5 分钟一次）',
          },
          null,
          2,
        ),
        { headers: { 'Content-Type': 'application/json; charset=utf-8' } },
      );
    }

    if (!isAuthorized(request, env)) {
      return new Response('需要正确的 trigger key\n', { status: 401 });
    }

    const status = await dispatch(env, ctx);
    return new Response(`dispatch -> HTTP ${status}\n`, {
      status: status === 204 ? 200 : 502,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  },
};
