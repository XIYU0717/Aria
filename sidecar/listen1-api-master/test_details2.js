const listen1Api = require('./dist/listen1-api.js');

async function run(label, fn) {
  try {
    const data = await fn();
    console.log('\n' + label + ' OK ' + JSON.stringify(data).slice(0, 1800));
  } catch (e) {
    console.log('\n' + label + ' ERROR ' + (e && (e.stack || e.message || e))).slice(0, 1800);
  }
}

(async () => {
  await run('NE_PLAYLIST', () => listen1Api.apiGet('/playlist?list_id=neplaylist_18359211192'));
  await run('NE_BOOTSTRAP', () => listen1Api.apiGet('/bootstrap_track?track_id=netrack_405599119'));
  const qq = await listen1Api.apiGet('/show_playlist?source=qq');
  const qqFirst = qq.result[0];
  await run('QQ_PLAYLIST', () => listen1Api.apiGet('/playlist?list_id=' + qqFirst.id));
  await run('QQ_SEARCH', () => listen1Api.apiGet('/search?source=qq&keywords=' + encodeURIComponent('晴天') + '&curpage=1'));
  process.exit(0);
})();
