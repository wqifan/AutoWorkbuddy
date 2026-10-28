/**
 * Buddy 加油站 · 云端定时器
 * 每天 UTC 23:00（北京时间次日 07:00）触发私有仓库的 daily.yml。
 *
 * 鉴权用 secrets.GH_PAT（fine-grained PAT）：
 *   仅 AutoWorkbuddy 仓库 + Actions: Read and write，其余一律 No access。
 *
 * 返回 204 表示 GitHub 已接受这次调度。
 */
export default {
  async scheduled(event, env, ctx) {
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
  },
};
