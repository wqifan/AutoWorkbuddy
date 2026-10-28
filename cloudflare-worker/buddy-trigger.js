/**
 * Buddy 加油站 · 云端定时器
 *
 * 每天 UTC 23:07（北京时间次日 07:07）触发 GitHub 仓库的 daily.yml。
 *
 * 为什么需要它：
 *   实测这个 GitHub 账号（个人账号）的 `schedule` 事件整体不投递 ——
 *   公开/私有仓库、新建仓库、每 5 分钟空跑探针都试过，全部零命中；
 *   同期 Organization 账号的仓库正常投递。
 *   所以「等 GitHub 自己按时跑」这条路走不通，改用 Cloudflare Workers
 *   的 Cron Trigger —— 它是完全独立的外部闹钟，与 GitHub 调度无关。
 *
 * 鉴权用 secrets.GH_PAT：
 *   实测本机 git credential 里的 OAuth token（scope: gist, repo, workflow）
 *   就足够触发 dispatch，无需另造 fine-grained PAT。
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
    },
    body: JSON.stringify({ ref: 'main', inputs: { mode: 'run', push: 'auto' } }),
  });
  const body = await res.text();
  console.log(`dispatch -> HTTP ${res.status} ${body.slice(0, 300)}`);
  if (res.status !== 204) {
    console.error('触发失败，请检查 GH_PAT 是否有效且有 Actions:Read and write 权限');
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
        JSON.stringify({
          ok: true,
          worker: 'buddy-trigger',
          gh_pat: Boolean((env.GH_PAT || '').trim()),
          trigger_key: Boolean((env.TRIGGER_KEY || '').trim()),
          cron: '7 23 * * * (UTC) = 07:07 北京时间次日',
        }, null, 2),
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
