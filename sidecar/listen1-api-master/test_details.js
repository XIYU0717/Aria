const listen1Api = require('./dist/listen1-api.js');

async function safe(label, fn) {
  try {
    const data = await fn();
    console.log('\n' + label, JSON.stringify(data).slice(0, 1600));
  } catch (e) {
    console.log('\n' + label, 'ERROR', e && (e.stack || e.message || e));
  }
}

(async () => {
  await safe('NE_SEARCH', () => listen1Api.apiGet('/search?source=netease&keywords=' + encodeURIComponent('晴天') + '&curpage=1'));
  await safe('QQ_SEARCH', () => listen1Api.apiGet('/search?source=qq&keywords=' + encodeURIComponent('晴天') + '&curpage=1'));
  const ne = await listen1Api.apiGet('/show_playlist?source=netease');
  const neFirst = ne.result[0];
  await safe('NE_PLAYLIST', () => listen1Api.apiGet('/playlist?list_id=' + neFirst.id));
  await safe('NE_BOOTSTRAP', () => listen1Api.apiGet('/bootstrap_track?track_id=netrack_405599119'));
  const qq = await listen1Api.apiGet('/show_playlist?source=qq');
  const qqFirst = qq.result[0];
  await safe('QQ_PLAYLIST', () => listen1Api.apiGet('/playlist?list_id=' + qqFirst.id));
  process.exit(0);
})();
