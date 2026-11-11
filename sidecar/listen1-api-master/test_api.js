const listen1Api = require('./dist/listen1-api.js');

async function test(source) {
  try {
    const data = await listen1Api.apiGet(`/show_playlist?source=${source}`);
    console.log(source, JSON.stringify(data).slice(0, 1200));
  } catch (err) {
    console.log(source, 'ERROR', err && (err.stack || err.message || err));
  }
}

(async () => {
  await test('netease');
  await test('qq');
  process.exit(0);
})();
