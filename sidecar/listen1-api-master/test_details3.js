const listen1Api = require('./dist/listen1-api.js');

async function run(label, fn) {
  try {
    const data = await fn();
    console.log('\n' + label + ' OK ' + JSON.stringify(data).slice(0, 1800));
  } catch (e) {
    console.log('\n' + label + ' ERROR ' + String(e && (e.stack || e.message || JSON.stringify(e))).slice(0, 1800));
  }
}

(async () => {
  const qq = await listen1Api.apiGet('/show_playlist?source=qq');
  console.log('QQ first', qq.result[0]);
  await run('QQ_PLAYLIST', () => listen1Api.apiGet('/playlist?list_id=' + qq.result[0].id));
  let qqTracks = [];
  try { qqTracks = (await listen1Api.apiGet('/playlist?list_id=' + qq.result[0].id)).tracks; } catch (e) {}
  if (qqTracks && qqTracks[0]) {
    await run('QQ_BOOTSTRAP', () => listen1Api.apiGet('/bootstrap_track?track_id=' + qqTracks[0].id));
  }
  await run('QQ_SEARCH', () => listen1Api.apiGet('/search?source=qq&keywords=' + encodeURIComponent('晴天') + '&curpage=1'));
  process.exit(0);
})();
