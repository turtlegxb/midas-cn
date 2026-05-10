#!/usr/bin/env node

function usage() {
  console.error(`Usage:
  node scripts/xueqiu_fetcher.js timeline <user_id[,user_id...]> [count]
  node scripts/xueqiu_fetcher.js following [count]

Required env:
  XQ_A_TOKEN=<xq_a_token from xueqiu.com cookies>`);
}

function stripHtml(html) {
  return String(html || '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

async function fetchJson(page, url) {
  const result = await page.evaluate(async (apiUrl) => {
    const response = await fetch(apiUrl, {
      method: 'GET',
      credentials: 'include',
      headers: {
        'Accept': 'application/json, text/plain, */*',
        'Referer': location.href,
        'X-Requested-With': 'XMLHttpRequest'
      }
    });
    const text = await response.text();
    return { status: response.status, text };
  }, url);

  if (result.status < 200 || result.status >= 300) {
    throw new Error(`HTTP ${result.status}: ${result.text.slice(0, 500)}`);
  }
  try {
    const parsed = JSON.parse(result.text);
    return typeof parsed === 'string' ? JSON.parse(parsed) : parsed;
  } catch (error) {
    throw new Error(`Could not parse JSON: ${result.text.slice(0, 500)}`);
  }
}

function extractStatuses(data) {
  if (Array.isArray(data.statuses)) return data.statuses;
  if (Array.isArray(data.home_timeline)) return data.home_timeline;
  if (Array.isArray(data.list)) return data.list;
  if (Array.isArray(data.items)) return data.items;
  if (data.data && Array.isArray(data.data.statuses)) return data.data.statuses;
  if (data.data && Array.isArray(data.data.home_timeline)) return data.data.home_timeline;
  if (data.data && Array.isArray(data.data.list)) return data.data.list;
  if (data.data && Array.isArray(data.data.items)) return data.data.items;
  return null;
}

function normalizeStatus(status, fallbackUserId = '') {
  const user = status.user || {};
  const userId = String(status.user_id || user.id || fallbackUserId || '');
  const id = status.id || status.status_id;
  const target = status.target
    ? (String(status.target).startsWith('http') ? status.target : `https://xueqiu.com${status.target}`)
    : '';
  return {
    user_id: userId,
    screen_name: user.screen_name || status.screen_name || '',
    id,
    created_at: status.created_at,
    title: status.title || '',
    text: stripHtml(status.text || status.description || ''),
    raw_text_html: status.text || '',
    full_text: stripHtml(status.full_text || status.fullText || status.longTextForIOS || status.text || status.description || ''),
    full_raw_text_html: status.full_text || status.fullText || status.longTextForIOS || status.text || '',
    detail_fetched: Boolean(status.__detail_fetched),
    detail_error: status.__detail_error || null,
    post_type: classifyStatus(status),
    raw_type: status.type ?? null,
    source: stripHtml(status.source || ''),
    retweet_count: status.retweet_count ?? null,
    reply_count: status.reply_count ?? null,
    fav_count: status.fav_count ?? status.like_count ?? null,
    is_retweet: Boolean(status.retweeted_status),
    retweeted_status_id: status.retweet_status_id || (status.retweeted_status && status.retweeted_status.id) || null,
    link: target || (userId && id ? `https://xueqiu.com/${userId}/${id}` : '')
  };
}

function classifyStatus(status) {
  if (status.retweeted_status) return 'repost';
  if (String(status.type) === '3' || status.title || status.rawTitle) return 'article';
  if (String(status.type) === '2') return 'long_post';
  if (String(status.type) === '0') return 'short_post';
  return 'unknown';
}

async function enrichFullText(page, normalized) {
  if (!['long_post', 'article'].includes(normalized.post_type) || !normalized.id) {
    return normalized;
  }
  try {
    const detail = await fetchJson(page, `https://xueqiu.com/statuses/show.json?id=${encodeURIComponent(normalized.id)}`);
    const detailStatus = Array.isArray(detail) ? detail[0] : detail;
    if (!detailStatus || typeof detailStatus !== 'object') {
      return { ...normalized, detail_fetched: false, detail_error: 'empty_detail' };
    }
    const detailNormalized = normalizeStatus({ ...detailStatus, __detail_fetched: true }, normalized.user_id);
    const fullText = detailNormalized.full_text || detailNormalized.text || normalized.full_text || normalized.text;
    return {
      ...normalized,
      title: detailNormalized.title || normalized.title,
      text: fullText,
      raw_text_html: detailNormalized.full_raw_text_html || detailNormalized.raw_text_html || normalized.raw_text_html,
      full_text: fullText,
      full_raw_text_html: detailNormalized.full_raw_text_html || detailNormalized.raw_text_html || normalized.full_raw_text_html,
      detail_fetched: true,
      detail_error: null,
      raw_type: detailNormalized.raw_type ?? normalized.raw_type,
      post_type: detailNormalized.post_type || normalized.post_type,
    };
  } catch (error) {
    return { ...normalized, detail_fetched: false, detail_error: String(error.message || error).slice(0, 300) };
  }
}

async function fetchUserTimeline(page, userId, count) {
  const perPage = Math.min(Math.max(count, 20), 100);
  const all = [];
  const seen = new Set();
  for (let pageNo = 1; all.length < count && pageNo <= 10; pageNo += 1) {
    const url = `https://xueqiu.com/v4/statuses/user_timeline.json?page=${pageNo}&user_id=${encodeURIComponent(userId)}&type=0&count=${perPage}`;
    const data = await fetchJson(page, url);
    const statuses = extractStatuses(data);
    if (!statuses) throw new Error(`Unexpected response shape: ${JSON.stringify(data).slice(0, 500)}`);
    if (statuses.length === 0) break;
    for (const status of statuses) {
      const normalized = normalizeStatus(status, userId);
      if (!normalized.id || seen.has(normalized.id)) continue;
      seen.add(normalized.id);
      all.push(await enrichFullText(page, normalized));
      if (all.length >= count) break;
    }
  }
  return all.slice(0, count);
}

function followingEndpointCandidates(count, pageNo) {
  const perPage = Math.min(Math.max(count, 20), 100);
  const query = `page=${pageNo}&count=${perPage}&since_id=-1&max_id=-1`;
  const simpleQuery = `page=${pageNo}&count=${perPage}`;
  return [
    { name: 'v4/statuses/home_timeline/simple', url: `https://xueqiu.com/v4/statuses/home_timeline.json?${simpleQuery}` },
    { name: 'statuses/home_timeline/simple', url: `https://xueqiu.com/statuses/home_timeline.json?${simpleQuery}` },
    { name: 'statuses/friends_timeline', url: `https://xueqiu.com/statuses/friends_timeline.json?${query}` },
    { name: 'statuses/home_timeline', url: `https://xueqiu.com/statuses/home_timeline.json?${query}` },
    { name: 'v4/statuses/friends_timeline', url: `https://xueqiu.com/v4/statuses/friends_timeline.json?${query}` },
    { name: 'v4/statuses/home_timeline', url: `https://xueqiu.com/v4/statuses/home_timeline.json?${query}` }
  ];
}

async function getCurrentUserId(page) {
  for (const url of ['https://xueqiu.com/user/show.json', 'https://xueqiu.com/v4/user/show.json']) {
    try {
      const data = await fetchJson(page, url);
      const id = data.id || (data.user && data.user.id) || (data.data && data.data.id);
      if (id) return String(id);
    } catch (error) {
      // Following feed may still work with token cookies only.
    }
  }
  return '';
}

async function fetchFollowingTimeline(page, count, { originalOnly = false } = {}) {
  const failures = [];
  const candidates = followingEndpointCandidates(count, 1);
  for (const candidate of candidates) {
    const all = [];
    const seen = new Set();
    try {
      for (let pageNo = 1; all.length < count && pageNo <= 10; pageNo += 1) {
        const pageCandidate = followingEndpointCandidates(count, pageNo).find((item) => item.name === candidate.name);
        const data = await fetchJson(page, pageCandidate.url);
        const statuses = extractStatuses(data);
        if (!statuses) throw new Error(`Unexpected response shape: ${JSON.stringify(data).slice(0, 500)}`);
        if (statuses.length === 0) break;
        for (const status of statuses) {
          const normalized = normalizeStatus(status);
          if (originalOnly && normalized.is_retweet) continue;
          if (!normalized.id || seen.has(normalized.id)) continue;
          seen.add(normalized.id);
          all.push(await enrichFullText(page, normalized));
          if (all.length >= count) break;
        }
      }
      return { source_endpoint: candidate.name, original_only: originalOnly, attempted_endpoints: candidates.map((item) => item.name), failures, posts: all.slice(0, count) };
    } catch (error) {
      failures.push({ endpoint: candidate.name, error: String(error.message || error) });
    }
  }
  return { source_endpoint: null, original_only: originalOnly, attempted_endpoints: candidates.map((item) => item.name), failures, posts: [] };
}

async function main() {
  const [command, arg, countArg = '1'] = process.argv.slice(2);
  if (!['timeline', 'following', 'followings', 'feed'].includes(command) || (command === 'timeline' && !arg)) {
    usage();
    process.exit(2);
  }
  const token = process.env.XQ_A_TOKEN;
  if (!token) {
    console.error(JSON.stringify({ error: 'XQ_A_TOKEN environment variable not set' }));
    process.exit(2);
  }

  const { chromium } = await import('playwright-extra');
  const stealth = (await import('puppeteer-extra-plugin-stealth')).default;
  chromium.use(stealth());

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai'
  });
  const cookies = [
    { name: 'xq_a_token', value: token, domain: '.xueqiu.com', path: '/', httpOnly: false, secure: true, sameSite: 'Lax' },
    { name: 'xqat', value: token, domain: '.xueqiu.com', path: '/', httpOnly: false, secure: true, sameSite: 'Lax' },
    { name: 'xq_is_login', value: '1', domain: '.xueqiu.com', path: '/', httpOnly: false, secure: true, sameSite: 'Lax' }
  ];
  if (process.env.XQ_UID) cookies.push({ name: 'u', value: process.env.XQ_UID, domain: '.xueqiu.com', path: '/', httpOnly: false, secure: true, sameSite: 'Lax' });
  await context.addCookies(cookies);

  const page = await context.newPage();
  await page.goto('https://xueqiu.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);
  if (!process.env.XQ_UID) {
    const currentUserId = await getCurrentUserId(page);
    if (currentUserId) await context.addCookies([{ name: 'u', value: currentUserId, domain: '.xueqiu.com', path: '/', httpOnly: false, secure: true, sameSite: 'Lax' }]);
  }

  if (command === 'timeline') {
    const count = Math.max(1, Number.parseInt(countArg, 10) || 1);
    const posts = [];
    for (const userId of arg.split(',').map((id) => id.trim()).filter(Boolean)) {
      try {
        posts.push(...await fetchUserTimeline(page, userId, count));
      } catch (error) {
        posts.push({ user_id: userId, error: String(error.message || error) });
      }
    }
    await browser.close();
    console.log(JSON.stringify({ fetched_at: new Date().toISOString(), mode: 'timeline', count: posts.length, posts }, null, 2));
    return;
  }

  const count = Math.max(1, Number.parseInt(arg || countArg, 10) || 20);
  const result = await fetchFollowingTimeline(page, count, { originalOnly: false });
  await browser.close();
  console.log(JSON.stringify({ fetched_at: new Date().toISOString(), mode: 'following', count: result.posts.length, ...result }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
