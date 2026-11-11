/*
 * Aria · Listen1 sidecar
 *
 * A small local HTTP bridge around listen1-api.  It is started by Aria with a
 * random token, listens on 127.0.0.1:0, and prints one JSON "ready" line on
 * stdout.  Aria never exposes this port outside the loopback interface.
 *
 * NetEase / QQ use Listen1's bundled runtime.  Kugou, Kuwo and Qishui use their
 * public web/PC endpoints directly because Listen1's old implementations for
 * those platforms have bit-rotted.
 */
'use strict';

const http = require('http');
const https = require('https');
const crypto = require('crypto');
const { URL } = require('url');
const path = require('path');

const listen1 = require(path.join(__dirname, 'runtime', 'listen1-api.js'));

const TOKEN = process.env.ARIA_TOKEN || '';
const HOST = '127.0.0.1';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
  + '(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36';

const SOURCE_NAMES = {
  netease: '网易云音乐',
  qq: 'QQ音乐',
  kugou: '酷狗音乐',
  kuwo: '酷我音乐',
  qishui: '汽水音乐',
};

const NETEASE_CATEGORIES = [
  '华语', '欧美', '日语', '韩语', '粤语',
  '流行', '摇滚', '民谣', '电子', '说唱',
  '轻音乐', '古典', '爵士', '乡村', '金属',
  '朋克', 'R&B', '嘻哈', '治愈', '感动',
  '快乐', '悲伤', '安静', '思念', '旅行',
  '校园', '游戏', '影视', '综艺', '深夜',
];

const QQ_CHARTS = [
  { id: 26, name: '热歌榜' },
  { id: 27, name: '新歌榜' },
  { id: 28, name: '原创榜' },
  { id: 29, name: '飙升榜' },
  { id: 4, name: '流行指数榜' },
  { id: 5, name: '内地榜' },
  { id: 6, name: '香港地区榜' },
  { id: 7, name: '台湾地区榜' },
  { id: 8, name: '欧美榜' },
  { id: 31, name: '粤语榜' },
];

const KUGOU_CATEGORIES = [
  '华语', '流行', '摇滚', '民谣', '电子', '说唱',
  '影视', '二次元', '游戏', '治愈', '睡前', '学习',
];

// Kuwo still serves this legacy category endpoint.  The ids come from its own
// playlist navigation; `37` is 华语 and the rest are the common genres/scenes.
const KUWO_CATEGORIES = [
  { id: '37', name: '华语' },
  { id: '393', name: '流行' },
  { id: '389', name: '摇滚' },
  { id: '392', name: '民谣' },
  { id: '391', name: '电子' },
  { id: '146', name: '伤感' },
  { id: '66', name: '治愈' },
  { id: '180', name: '影视' },
  { id: '181', name: '二次元' },
  { id: '1877', name: '游戏' },
  { id: '1876', name: '学习' },
  { id: '354', name: '睡前' },
];

const KUWO_CHARTS = [
  { id: '16', name: '酷我热歌榜' },
  { id: '17', name: '酷我新歌榜' },
  { id: '1', name: '中国歌曲排行榜' },
  { id: '2', name: '百度中文金曲榜' },
  { id: '20', name: '百度新歌榜' },
  { id: '21', name: '百度热歌榜' },
  { id: '22', name: '酷我欧美榜' },
  { id: '23', name: '酷我日韩榜' },
  { id: '26', name: '酷我经典榜' },
  { id: '25', name: '钱柜KTV榜' },
];

const QISHUI_CATEGORIES = [
  { id: '0', name: '每日推荐' },
  { id: '14', name: '流行' },
  { id: '8', name: '华语' },
  { id: '9', name: '欧美' },
  { id: '20', name: '国风' },
  { id: '18', name: '民谣' },
  { id: '15', name: '摇滚' },
  { id: '38', name: '说唱' },
  { id: '16', name: '电子' },
  { id: '19', name: 'R&B' },
  { id: '69', name: '治愈' },
  { id: '45', name: '睡前' },
  { id: '40', name: '学习' },
];

const QISHUI_CHARTS = [
  { id: '7036274230471712007', name: '热歌榜', count: 50, cover: '' },
  { id: '7060812597884869927', name: '新歌榜', count: 50, cover: '' },
  { id: '7061475546400005410', name: '欧美榜', count: 50, cover: '' },
  { id: '7415959718721494311', name: '音乐人榜', count: 25, cover: '' },
];

const QS_BASE = 'https://api.qishui.com';
const QS_PC_UA = 'LunaPC/3.5.2(412998333)';

function refererFor(target) {
  const host = target.hostname || '';
  if (host.includes('y.qq.com') || host.includes('qq.com')) return 'https://y.qq.com/';
  if (host.includes('kugou.com')) return 'https://www.kugou.com/';
  if (host.includes('kuwo.cn')) return 'https://www.kuwo.cn/';
  if (host.includes('qishui.com') || host.includes('douyin.com')) return 'https://www.qishui.com/';
  return 'https://music.163.com/';
}

function request(url, headers = {}, body = null, method = 'GET') {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const lib = target.protocol === 'http:' ? http : https;
    const req = lib.request(target, {
      method,
      headers: {
        'User-Agent': UA,
        Accept: '*/*',
        Referer: refererFor(target),
        ...headers,
      },
    }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        resolve(request(new URL(res.headers.location, url).toString(), headers, body, method));
        return;
      }
      let text = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { text += chunk; });
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }
        resolve(text);
      });
    });
    req.on('error', reject);
    req.setTimeout(20000, () => req.destroy(new Error('timeout')));
    if (body) req.write(body);
    req.end();
  });
}

function get(url, headers = {}) {
  return request(url, headers);
}

function postJson(url, payload, headers = {}) {
  return request(url, {
    'Content-Type': 'application/json; charset=utf-8',
    ...headers,
  }, JSON.stringify(payload), 'POST');
}

function decodeEntities(text) {
  return String(text == null ? '' : text)
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function parseJson(text) {
  let body = String(text || '').replace(/^\uFEFF/, '').trim();
  if (body.startsWith('<!--KG_TAG_RES_START-->')) {
    body = body.replace(/^<!--KG_TAG_RES_START-->/, '')
      .replace(/<!--KG_TAG_RES_END-->\s*$/, '');
  }
  try {
    return JSON.parse(body);
  } catch (err) {
    throw new Error(`接口返回非 JSON（${body.slice(0, 48).replace(/\s+/g, ' ')}）`);
  }
}

function parseJsonp(text, callback) {
  let body = String(text || '').replace(/^\uFEFF/, '').trim();
  const prefix = `${callback}(`;
  if (body.startsWith(prefix)) body = body.slice(prefix.length);
  if (body.endsWith(')')) body = body.slice(0, -1);
  return parseJson(body);
}

function parseLooseJson(text) {
  const clean = String(text || '').replace(/^\uFEFF/, '').trim();
  try {
    return JSON.parse(clean);
  } catch (err) { /* Kuwo returns a Python-dict-ish payload */ }
  try {
    return JSON.parse(clean.replace(/'/g, '"'));
  } catch (err) {
    throw new Error(`接口返回非 JSON（${clean.slice(0, 48).replace(/\s+/g, ' ')}）`);
  }
}

function jsonGet(url, headers) {
  return get(url, headers).then(parseJson);
}

function qsCover(image) {
  if (!image) return '';
  const base = (image.urls && image.urls[0]) || '';
  const uri = image.uri || '';
  const prefix = image.template_prefix || '';
  if (!base || !uri) return '';
  return `${base}${uri}~${prefix}-resize:960:960.png`;
}

function kugouCover(url) {
  if (!url) return '';
  return String(url).replace('{size}', '240');
}

function kuwoCover(item) {
  const direct = item.albumpic || item.img || item.pic || '';
  if (direct) return direct;
  const short = item.web_albumpic_short || item.PICPATH || '';
  if (short) {
    if (short.startsWith('http')) return short;
    return `http://img1.kuwo.cn/star/albumcover/240/${short.replace(/^\/+/, '')}`;
  }
  return '';
}

function qqSongUrl(albumMid) {
  return albumMid ? `https://y.qq.com/music/photo_new/T002R300x300M000${albumMid}.jpg` : '';
}

function normalizePlaylist(item, source) {
  return {
    id: item.id,
    title: item.title || item.dissname || '',
    cover: item.cover_img_url || item.coverImgUrl || item.imgurl || '',
    source,
    provider_id: source,
    source_name: SOURCE_NAMES[source] || source,
    source_url: item.source_url || '',
    count: item.count || item.trackCount || 0,
    count_unit: item.count_unit || '首',
    subtitle: item.subtitle || '',
  };
}

function normalizeTrack(item, source) {
  return {
    id: item.id,
    title: item.title || '',
    artist: item.artist || '',
    album: item.album || '',
    cover: item.img_url || item.cover || '',
    duration: item.duration || item.interval || 0,
    source,
    source_name: SOURCE_NAMES[source] || source,
    source_url: item.source_url || '',
    provider_id: source,
    provider_track_id: item.listen1_id || item.id,
    audio_from: 'listen1',
    listen1_id: item.listen1_id || item.id,
    listen1_source: source,
    disabled: !!item.disabled,
  };
}

// ------------------------------------------------------------------ Kugou
async function kugouSearch(keyword, limit) {
  const url = 'http://msearchcdn.kugou.com/api/v3/search/song?showtype=14&highlight=em'
    + `&pagesize=${limit}&tag_aggr=1&tagtype=${encodeURIComponent('全部')}&plat=0&sver=5`
    + `&keyword=${encodeURIComponent(keyword)}&correct=1&api_ver=1&version=9108&page=1`
    + '&area_code=1&tag=1&with_res_tag=1';
  const data = await jsonGet(url);
  const songs = (data.data && data.data.info) || [];
  return songs.map((s) => {
    const hash = s.hash || s.HASH || '';
    return normalizeTrack({
      id: `kgtrack_${hash}`,
      listen1_id: `kgtrack_${hash}`,
      title: decodeEntities(s.songname || s.filename || ''),
      artist: decodeEntities(s.singername
        || ((s.authors || []).map((a) => a.author_name).join(' / '))),
      album: decodeEntities(s.album_name || s.albumname || ''),
      img_url: s.album_id ? `http://imge.kugou.com/stdmusic/240/${s.album_id}.jpg` : '',
      duration: Number(s.duration || 0),
      source_url: `https://www.kugou.com/song/#hash=${hash}&album_id=${s.album_id || ''}`,
    }, 'kugou');
  });
}

async function kugouSearchPlaylists(keyword, limit) {
  const url = 'http://msearchcdn.kugou.com/api/v3/search/special?'
    + `keyword=${encodeURIComponent(keyword)}&pagesize=${limit}&page=1&showtype=1&version=9108`;
  const data = await jsonGet(url);
  const items = (data.data && data.data.info) || [];
  return items.map((p) => normalizePlaylist({
    id: `kgplaylist_${p.specialid}`,
    title: decodeEntities(p.specialname || ''),
    cover_img_url: kugouCover(p.imgurl),
    source_url: `https://www.kugou.com/yy/special/single/${p.specialid}.html`,
    count: Number(p.songcount || 0),
    subtitle: decodeEntities(p.nickname || ''),
  }, 'kugou'));
}

async function kugouPlaylist(id) {
  const specialId = String(id || '').replace(/^kgplaylist_/, '');
  if (!specialId) throw new Error('missing kugou playlist id');
  const infoData = await jsonGet('http://mobilecdn.kugou.com/api/v3/special/info?'
    + `version=9108&plat=0&area_code=1&specialid=${encodeURIComponent(specialId)}`);
  const info = (infoData && infoData.data) || {};
  const songData = await jsonGet('http://mobilecdn.kugou.com/api/v3/special/song?'
    + `version=9108&plat=0&area_code=1&specialid=${encodeURIComponent(specialId)}&pagesize=100&page=1`);
  const songs = (songData.data && songData.data.info) || [];
  return {
    info: normalizePlaylist({
      id: `kgplaylist_${specialId}`,
      title: decodeEntities(info.specialname || '酷狗歌单'),
      cover_img_url: kugouCover(info.imgurl),
      source_url: `https://www.kugou.com/yy/special/single/${specialId}.html`,
      count: Number(info.songcount || songs.length),
      subtitle: decodeEntities(info.nickname || ''),
    }, 'kugou'),
    tracks: songs.map((s) => {
      const hash = s.hash || '';
      return normalizeTrack({
        id: `kgtrack_${hash}`,
        listen1_id: `kgtrack_${hash}`,
        title: decodeEntities(s.filename || s.songname || ''),
        artist: decodeEntities(s.singername
          || ((s.authors || []).map((a) => a.author_name).join(' / '))),
        album: decodeEntities(s.album_name || s.albumname || ''),
        img_url: s.album_id ? `http://imge.kugou.com/stdmusic/240/${s.album_id}.jpg` : '',
        duration: Number(s.duration || 0),
        source_url: `https://www.kugou.com/song/#hash=${hash}&album_id=${s.album_id || ''}`,
      }, 'kugou');
    }),
  };
}

async function kugouChartList() {
  const data = await jsonGet('http://mobilecdn.kugou.com/api/v3/rank/list?'
    + 'version=9108&plat=0&area_code=1&pagesize=30&page=1');
  const items = (data.data && data.data.info) || [];
  return items.slice(0, 10).map((c) => normalizePlaylist({
    id: `kgtop_${c.rankid}`,
    title: decodeEntities(c.rankname || ''),
    cover_img_url: kugouCover(c.imgurl || c.img_9 || c.album_img_9 || c.banner_9 || ''),
    source_url: `https://www.kugou.com/yy/rank/home/1-${c.rankid}.html`,
    count: Number(c.songcount || 0),
    subtitle: '每周更新',
  }, 'kugou'));
}

async function kugouChart(id) {
  const rankId = String(id || '').replace(/^kgtop_/, '');
  if (!rankId) throw new Error('missing kugou chart id');
  const charts = await kugouChartList();
  const meta = charts.find((c) => String(c.id) === `kgtop_${rankId}`) || {};
  const data = await jsonGet('http://mobilecdn.kugou.com/api/v3/rank/song?'
    + `version=9108&plat=0&area_code=1&rankid=${encodeURIComponent(rankId)}&pagesize=100&page=1`);
  const songs = (data.data && data.data.info) || [];
  const tracks = songs.map((s) => {
    const hash = s.hash || '';
    return normalizeTrack({
      id: `kgtrack_${hash}`,
      listen1_id: `kgtrack_${hash}`,
      title: decodeEntities(s.filename || s.songname || ''),
      artist: decodeEntities(s.singername
        || ((s.authors || []).map((a) => a.author_name).join(' / '))),
      album: decodeEntities(s.album_name || s.albumname || ''),
      img_url: s.album_id ? `http://imge.kugou.com/stdmusic/240/${s.album_id}.jpg` : '',
      duration: Number(s.duration || 0),
      source_url: `https://www.kugou.com/song/#hash=${hash}&album_id=${s.album_id || ''}`,
      disabled: Number(s.privilege) >= 10 && Number(s.pay_type) >= 3,
    }, 'kugou');
  });
  return {
    info: normalizePlaylist({
      id: `kgtop_${rankId}`,
      title: meta.title || '酷狗榜单',
      cover_img_url: meta.cover || (tracks[0] ? tracks[0].cover : ''),
      count: tracks.length,
      subtitle: '每周更新',
    }, 'kugou'),
    tracks,
  };
}

async function kugouStream(trackId) {
  const hash = String(trackId || '').replace(/^kgtrack_/, '');
  if (!hash) throw new Error('missing kugou track hash');
  const key = crypto.createHash('md5').update(`${hash}kgcloudv2`).digest('hex');
  for (const br of ['hq', 'sq', '128']) {
    const url = 'http://trackercdn.kugou.com/i/v2/'
      + `?key=${key}&hash=${hash}&br=${br}&appid=1005&pid=2&cmd=25&behavior=play`;
    try {
      const data = await jsonGet(url);
      if (data && data.status === 1 && Array.isArray(data.url) && data.url[0]) {
        return { url: data.url[0] };
      }
    } catch (err) { /* try the next bitrate */ }
  }
  throw new Error('酷狗音乐未返回可播放地址（该曲目可能需要会员）');
}

// ------------------------------------------------------------------ Kuwo
async function kuwoSearch(keyword, limit) {
  const url = 'http://search.kuwo.cn/r.s?ft=music&itemset=web_2013&client=kt'
    + `&rformat=json&encoding=utf8&all=${encodeURIComponent(keyword)}&pn=0&rn=${limit}`;
  const data = parseLooseJson(await get(url));
  const songs = (data && data.abslist) || [];
  return songs.map((s) => {
    const rid = String(s.DC_TARGETID || s.MUSICRID || '').replace(/^MUSIC_/, '');
    return normalizeTrack({
      id: `kwtrack_${rid}`,
      listen1_id: `kwtrack_${rid}`,
      title: decodeEntities(s.NAME || s.SONGNAME || ''),
      artist: decodeEntities(s.ARTIST || ''),
      album: decodeEntities(s.ALBUM || ''),
      img_url: kuwoCover(s),
      duration: Number(s.DURATION || 0),
      source_url: `https://www.kuwo.cn/play_detail/${rid}`,
      disabled: String(s.PAY || '0') !== '0' || String(s.fpay || '0') !== '0',
    }, 'kuwo');
  });
}

async function kuwoPlaylist(id) {
  const pid = String(id || '').replace(/^kwplaylist_/, '');
  if (!pid) throw new Error('missing kuwo playlist id');
  const data = parseLooseJson(await get('http://nplserver.kuwo.cn/pl.svc?op=getlistinfo'
    + `&pid=${encodeURIComponent(pid)}&pn=0&rn=100&encode=utf8&keyset=pl2012&identity=kuwo`
    + '&pcmp4=1&vipver=1'));
  const songs = (data && data.musiclist) || [];
  return {
    info: normalizePlaylist({
      id: `kwplaylist_${pid}`,
      title: decodeEntities(data.title || '酷我歌单'),
      cover_img_url: data.pic || '',
      source_url: `https://www.kuwo.cn/playlist/index?pid=${pid}`,
      count: Number(data.total || songs.length),
      subtitle: decodeEntities(data.uname || ''),
    }, 'kuwo'),
    tracks: songs.map((s) => normalizeTrack({
      id: `kwtrack_${s.id}`,
      listen1_id: `kwtrack_${s.id}`,
      title: decodeEntities(s.name || s.displaysongname || s.FSONGNAME || ''),
      artist: decodeEntities(s.artist || s.displayartistname || s.FARTIST || ''),
      album: decodeEntities(s.album || s.displayalbumname || s.FALBUM || ''),
      img_url: kuwoCover(s),
      duration: Number(s.duration || 0),
      source_url: `https://www.kuwo.cn/play_detail/${s.id}`,
      disabled: Number(s.online || 1) === 0,
    }, 'kuwo')),
  };
}

async function kuwoChartList() {
  return KUWO_CHARTS.map((c) => normalizePlaylist({
    id: `kwtop_${c.id}`,
    title: c.name,
    cover_img_url: '',
    source_url: `https://www.kuwo.cn/bang/content?name=${encodeURIComponent(c.name)}`,
    count: 0,
    subtitle: '酷我音乐',
  }, 'kuwo'));
}

async function kuwoChart(id) {
  const chartId = String(id || '').replace(/^kwtop_/, '');
  if (!chartId) throw new Error('missing kuwo chart id');
  const data = parseLooseJson(await get('http://kbangserver.kuwo.cn/ksong.s?from=pc&fmt=json'
    + `&pn=0&rn=100&type=bang&data=content&id=${encodeURIComponent(chartId)}`));
  const songs = (data && data.musiclist) || [];
  return {
    info: normalizePlaylist({
      id: `kwtop_${chartId}`,
      title: decodeEntities(data.name || '酷我榜单'),
      cover_img_url: data.pic || '',
      count: Number(data.num || songs.length),
      subtitle: '酷我音乐',
    }, 'kuwo'),
    tracks: songs.map((s) => normalizeTrack({
      id: `kwtrack_${s.id}`,
      listen1_id: `kwtrack_${s.id}`,
      title: decodeEntities(s.name || ''),
      artist: decodeEntities(s.artist || ''),
      album: decodeEntities(s.album || ''),
      img_url: kuwoCover(s),
      duration: Number(s.duration || 0),
      source_url: `https://www.kuwo.cn/play_detail/${s.id}`,
    }, 'kuwo')),
  };
}

async function kuwoCategoryPlaylists(categoryId, limit) {
  const id = String(categoryId || '37');
  const url = 'https://www.kuwo.cn/www/categoryNew/getPlayListInfoUnderCategory?'
    + `type=taglist&digest=10000&id=${encodeURIComponent(id)}&start=0&count=${limit}`;
  const data = parseLooseJson(await get(url));
  const groups = (data && data.data) || [];
  const items = [];
  for (const group of groups) {
    for (const p of (group.data || [])) items.push(p);
  }
  return items.slice(0, limit).map((p) => normalizePlaylist({
    id: `kwplaylist_${p.id}`,
    title: decodeEntities(p.name || ''),
    cover_img_url: p.img || '',
    source_url: `https://www.kuwo.cn/playlist/index?pid=${p.id}`,
    count: Number(p.total || 0),
    subtitle: decodeEntities(p.uname || ''),
  }, 'kuwo'));
}

async function kuwoStream(trackId) {
  const rid = String(trackId || '').replace(/^kwtrack_/, '');
  if (!rid) throw new Error('missing kuwo track id');
  const body = (await get('http://antiserver.kuwo.cn/anti.s?type=convert_url&format=mp3'
    + `&response=url&rid=MUSIC_${encodeURIComponent(rid)}`)).trim();
  if (!/^https?:\/\//.test(body)) throw new Error('酷我音乐未返回可播放地址');
  return { url: body };
}

// ------------------------------------------------------------------ Qishui
function qishuiPCParams(extra = {}) {
  const params = new URLSearchParams({
    aid: '386088',
    app_name: 'luna_pc',
    region: 'cn',
    geo_region: 'cn',
    os_region: 'cn',
    sim_region: '',
    device_id: '3753066532709850',
    cdid: '',
    iid: '3753066532713946',
    version_name: '3.5.2',
    version_code: '30050200',
    channel: 'official',
    build_mode: 'master',
    network_carrier: '',
    ac: 'wifi',
    tz_name: 'Asia/Shanghai',
    resolution: '',
    device_platform: 'windows',
    device_type: 'Windows',
    os_version: 'Windows 10',
    fp: '3753066532709850',
  });
  for (const [key, value] of Object.entries(extra)) {
    params.set(key, String(value));
  }
  return params.toString();
}

function qishuiHeaders() {
  return {
    'User-Agent': QS_PC_UA,
    'Content-Type': 'application/json; charset=utf-8',
  };
}

function qishuiTrack(track) {
  const t = track || {};
  const artists = (t.artists || []).map((a) => a.name).filter(Boolean).join(' / ');
  return normalizeTrack({
    id: `sodatrack_${t.id}`,
    listen1_id: `sodatrack_${t.id}`,
    title: t.name || '',
    artist: artists,
    album: (t.album && t.album.name) || '',
    img_url: qsCover(t.album && t.album.url_cover),
    duration: Number(t.duration || 0) > 1000
      ? Math.round(Number(t.duration) / 1000)
      : Number(t.duration || 0),
    source_url: `https://www.qishui.com/track/${t.id}`,
  }, 'qishui');
}

async function qishuiSearch(keyword, limit) {
  const url = `${QS_BASE}/luna/pc/search/all?`
    + qishuiPCParams({
      q: keyword,
      cursor: '0',
      search_id: crypto.randomUUID(),
      search_method: 'input',
    });
  const data = await jsonGet(url, qishuiHeaders());
  const groups = (data && data.result_groups) || [];
  const group = groups.find((g) => g.id === 'tracks') || {};
  const items = (group.data || []).slice(0, limit);
  return items.map((item) => qishuiTrack((item.entity && item.entity.track) || {}));
}

async function qishuiDiscover(channel, limit) {
  const url = `${QS_BASE}/luna/pc/discover/mix?aid=386088&app_name=luna`
    + '&device_platform=pc&pc_web=1';
  const payload = {
    block_type: 'discover_playlist_mix',
    feed_discover_extra: {},
    latest_douyin_liked_playlist_show_ts: 0,
    sub_channel_id: Number(channel) || 0,
  };
  const data = JSON.parse(await postJson(url, payload, { 'User-Agent': QS_PC_UA }));
  const blocks = (data && data.inner_block) || [];
  const seen = new Set();
  const items = [];
  for (const block of blocks) {
    for (const resource of (block.resources || [])) {
      if (resource.type !== 'playlist') continue;
      const p = resource.entity && resource.entity.playlist;
      if (!p || !p.id || seen.has(p.id)) continue;
      seen.add(p.id);
      items.push(normalizePlaylist({
        id: `sodaplaylist_${p.id}`,
        title: p.title || '',
        cover_img_url: qsCover(p.url_cover),
        source_url: `https://www.qishui.com/playlist/${p.id}`,
        count: Number(p.count_tracks || 0),
        subtitle: decodeEntities((p.owner && p.owner.nickname) || ''),
      }, 'qishui'));
      if (items.length >= limit) return items;
    }
  }
  return items;
}

async function qishuiPlaylist(id) {
  const pid = String(id || '').replace(/^sodaplaylist_/, '');
  if (!pid) throw new Error('missing qishui playlist id');
  let cursor = 0;
  let info = null;
  const tracks = [];
  for (let page = 0; page < 5; page += 1) {
    const url = `${QS_BASE}/luna/pc/playlist/detail?aid=386088&device_platform=web`
      + `&channel=pc_web&playlist_id=${encodeURIComponent(pid)}&cursor=${cursor}&cnt=50`;
    const data = await jsonGet(url, { 'User-Agent': QS_PC_UA });
    if (!info && data.playlist) info = data.playlist;
    const resources = (data && data.media_resources) || [];
    for (const media of resources) {
      const wrapper = media.entity && (media.entity.track_wrapper || media.entity);
      const track = wrapper && (wrapper.track || wrapper);
      if (track && track.id) tracks.push(qishuiTrack(track));
    }
    const next = Number(data.next_cursor || 0);
    if (!next || next === cursor) break;
    cursor = next;
  }
  const playlist = info || {};
  return {
    info: normalizePlaylist({
      id: `sodaplaylist_${pid}`,
      title: playlist.title || '汽水歌单',
      cover_img_url: qsCover(playlist.url_cover),
      source_url: `https://www.qishui.com/playlist/${pid}`,
      count: Number(playlist.count_tracks || tracks.length),
      subtitle: decodeEntities((playlist.owner && playlist.owner.nickname) || ''),
    }, 'qishui'),
    tracks,
  };
}

async function qishuiChartList() {
  return QISHUI_CHARTS.map((c) => normalizePlaylist({
    id: `sodatop_${c.id}`,
    title: c.name,
    cover_img_url: c.cover || '',
    source_url: `https://www.qishui.com/chart/${c.id}`,
    count: c.count || 0,
    subtitle: '每周更新',
  }, 'qishui'));
}

async function qishuiChart(id) {
  const chartId = String(id || '').replace(/^sodatop_/, '');
  if (!chartId) throw new Error('missing qishui chart id');
  const url = `${QS_BASE}/luna/pc/charts/${encodeURIComponent(chartId)}?`
    + 'aid=386088&app_name=luna&device_platform=pc&pc_web=1';
  const data = await jsonGet(url, { 'User-Agent': QS_PC_UA });
  const chart = (data && data.chart) || {};
  const ranks = chart.track_ranks || [];
  const tracks = ranks.map((row) => qishuiTrack(row.track || {}));
  return {
    info: normalizePlaylist({
      id: `sodatop_${chartId}`,
      title: chart.title || '汽水榜单',
      cover_img_url: '',
      count: Number(chart.count_tracks || tracks.length),
      subtitle: '每周更新',
    }, 'qishui'),
    tracks,
  };
}

async function qishuiStream(trackId) {
  const tid = String(trackId || '').replace(/^sodatrack_/, '');
  if (!tid) throw new Error('missing qishui track id');
  const url = 'https://beta-luna.douyin.com/luna/h5/seo_track?'
    + `track_id=${encodeURIComponent(tid)}&device_platform=web`;
  const data = await jsonGet(url, { 'User-Agent': UA });
  const player = (data && data.track_player) || {};
  const fullMs = Number((data.seo_track && data.seo_track.track && data.seo_track.track.duration) || 0);
  let audio = '';
  let actualMs = 0;
  if (player.video_model) {
    try {
      const model = typeof player.video_model === 'string'
        ? JSON.parse(player.video_model) : player.video_model;
      const videos = model.video_list || model.VideoList || [];
      if (videos[0]) {
        audio = videos[0].main_url || videos[0].play_url || '';
        actualMs = Number(model.video_duration || 0) * 1000;
      }
    } catch (err) { /* fall back to url_player_info */ }
  }
  if (!audio && player.url_player_info) {
    const info = await jsonGet(String(player.url_player_info), { 'User-Agent': QS_PC_UA });
    const list = (info && info.Result && info.Result.Data && info.Result.Data.PlayInfoList)
      || (info && info.PlayInfoList) || [];
    if (list[0]) {
      audio = list[0].MainPlayUrl || list[0].BackupPlayUrl || '';
      actualMs = Number(list[0].Duration || 0) * 1000;
    }
  }
  if (!audio) throw new Error('汽水音乐未返回可播放地址');
  if (fullMs && actualMs && actualMs + 4000 < fullMs) {
    throw new Error('汽水音乐该曲目仅提供会员试听片段');
  }
  return { url: audio };
}

// ------------------------------------------------------------------ direct discover
// The discover page owns playlist metadata only.  NetEase and QQ are read
// straight from their public web endpoints so the page no longer depends on
// Listen1; playback still goes through the stream resolver.

async function neteaseDiscover(limit) {
  const data = await jsonGet('https://music.163.com/api/personalized/playlist'
    + `?limit=${limit}&total=true`);
  return (data.result || []).map((p) => normalizePlaylist({
    id: `neplaylist_${p.id}`,
    title: p.name,
    cover_img_url: p.picUrl,
    source_url: `http://music.163.com/#/playlist?id=${p.id}`,
    count: p.trackCount,
    subtitle: p.copywriter || '',
  }, 'netease'));
}

function neteaseTrack(song) {
  const artists = song.artists || song.ar || [];
  const album = song.album || song.al || {};
  return {
    id: `netrack_${song.id}`,
    listen1_id: `netrack_${song.id}`,
    title: song.name,
    artist: artists.map((a) => a.name).filter(Boolean).join(' / '),
    album: album.name || '',
    img_url: album.picUrl || '',
    duration: song.duration ? Math.round(song.duration / 1000)
      : song.dt ? Math.round(song.dt / 1000) : 0,
    source_url: `http://music.163.com/#/song?id=${song.id}`,
  };
}

async function neteaseSongs(ids) {
  const chunks = [];
  for (let i = 0; i < ids.length; i += 100) chunks.push(ids.slice(i, i + 100));
  const pages = await Promise.all(chunks.map((chunk) => jsonGet(
    `https://music.163.com/api/song/detail?ids=[${chunk.join(',')}]`)));
  const songs = [];
  pages.forEach((page) => { songs.push(...((page && page.songs) || [])); });
  return songs;
}

async function neteasePlaylist(listId) {
  const id = String(listId || '').replace(/^neplaylist_/, '');
  const data = await jsonGet(`https://music.163.com/api/v6/playlist/detail?id=${id}&n=1000`);
  const playlist = data.playlist;
  if (!playlist) throw new Error('网易云没有返回歌单信息');
  let songs = playlist.tracks || [];
  const allIds = (playlist.trackIds || []).map((t) => t.id);
  // Anonymous v6 detail only inlines the first 10 tracks; the rest are
  // fetched through song/detail so a 80-track playlist stays complete.
  if (allIds.length > songs.length) songs = await neteaseSongs(allIds);
  return {
    info: normalizePlaylist({
      id: `neplaylist_${id}`,
      title: playlist.name,
      cover_img_url: playlist.coverImgUrl,
      source_url: `http://music.163.com/#/playlist?id=${id}`,
      count: playlist.trackCount,
    }, 'netease'),
    tracks: songs.filter(Boolean).map((song) => normalizeTrack(
      neteaseTrack(song), 'netease')),
  };
}

async function qqDiscover(limit) {
  const url = 'https://c.y.qq.com/splcloud/fcgi-bin/fcg_get_diss_by_tag.fcg?picmid=1&rnd=0.1'
    + '&g_tk=5381&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8'
    + `&notice=0&platform=yqq.json&needNewCode=0&categoryId=10000000&sortId=3`
    + `&sin=0&ein=${Math.max(0, limit - 1)}`;
  const data = await jsonGet(url);
  return (data.data && data.data.list || []).map((p) => normalizePlaylist({
    id: `qqplaylist_${p.dissid}`,
    title: decodeEntities(p.dissname),
    cover_img_url: p.imgurl,
    source_url: `http://y.qq.com/#type=taoge&id=${p.dissid}`,
    count: p.listennum,
    count_unit: '播放',
  }, 'qq'));
}

function qqTrack(song) {
  const singer = (song.singer || []).map((s) => s.name).filter(Boolean).join(' / ');
  return {
    id: `qqtrack_${song.songmid}`,
    listen1_id: `qqtrack_${song.songmid}`,
    title: decodeEntities(song.songname || song.title || ''),
    artist: decodeEntities(singer),
    album: decodeEntities(song.albumname || ''),
    img_url: qqSongUrl(song.albummid),
    duration: Number(song.interval || 0),
    source_url: `http://y.qq.com/#type=song&mid=${song.songmid}`,
  };
}

async function neteaseStream(trackId) {
  const id = String(trackId || '').replace(/^netrack_/, '');
  const data = await jsonGet('https://music.163.com/api/song/enhance/player/url'
    + `?ids=[${encodeURIComponent(id)}]&br=128000`);
  const item = (data.data || [])[0];
  if (!item || !item.url) {
    throw new Error('网易云该曲目需要登录或无版权，正在回退其他音源');
  }
  return { url: item.url };
}

async function qqPlaylist(listId) {
  const id = String(listId || '').replace(/^qqplaylist_/, '');
  const url = 'https://i.y.qq.com/qzone-music/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg'
    + '?type=1&json=1&utf8=1&onlysong=0&jsonpCallback=jsonCallback&nosign=1'
    + `&disstid=${id}&g_tk=5381&loginUin=0&hostUin=0&format=jsonp&inCharset=GB2312`
    + '&outCharset=utf-8&notice=0&platform=yqq&needNewCode=0';
  const data = parseJsonp(await get(url), 'jsonCallback');
  const cd = (data.cdlist || [])[0];
  if (!cd) throw new Error('QQ音乐没有返回歌单信息');
  return {
    info: normalizePlaylist({
      id: `qqplaylist_${id}`,
      title: decodeEntities(cd.dissname),
      cover_img_url: cd.logo,
      source_url: `http://y.qq.com/#type=taoge&id=${id}`,
      count: (cd.songlist || []).length,
    }, 'qq'),
    tracks: (cd.songlist || []).filter(Boolean).map((song) => normalizeTrack(
      qqTrack(song), 'qq')),
  };
}

// ------------------------------------------------------------------ handlers
async function handleRecommend(params) {
  const source = (params.get('source') || 'netease').toLowerCase();
  const limit = Math.max(1, Math.min(80, Number(params.get('limit') || 24)));
  const category = params.get('category') || '';

  if (category && source === 'netease') {
    const url = 'https://music.163.com/api/playlist/list?cat=' + encodeURIComponent(category)
      + `&order=hot&limit=${limit}&offset=0`;
    const data = await jsonGet(url);
    const items = (data.playlists || []).map((p) => normalizePlaylist({
      id: `neplaylist_${p.id}`,
      title: p.name,
      cover_img_url: p.coverImgUrl,
      source_url: `http://music.163.com/#/playlist?id=${p.id}`,
      count: p.trackCount,
    }, 'netease'));
    return { source, category, items };
  }

  if (category && source === 'qq') {
    const id = Number(params.get('category_id') || category);
    const sort = Number(params.get('sort_id') || 5);
    const url = 'https://c.y.qq.com/splcloud/fcgi-bin/fcg_get_diss_by_tag.fcg?picmid=1&rnd=0.1'
      + '&g_tk=5381&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8'
      + `&notice=0&platform=yqq.json&needNewCode=0&categoryId=${id}&sortId=${sort}&sin=0&ein=${limit - 1}`;
    const data = await jsonGet(url);
    const items = (data.data && data.data.list || []).map((p) => normalizePlaylist({
      id: `qqplaylist_${p.dissid}`,
      title: p.dissname,
      cover_img_url: p.imgurl,
      source_url: `http://y.qq.com/#type=taoge&id=${p.dissid}`,
      count: p.listennum,
      count_unit: '播放',
    }, 'qq'));
    return { source, category, items };
  }

  if (category && source === 'kugou') {
    return { source, category, items: await kugouSearchPlaylists(category, limit) };
  }

  if (category && source === 'kuwo') {
    return { source, category, items: await kuwoCategoryPlaylists(category, limit) };
  }

  if (source === 'qishui') {
    return { source, category, items: await qishuiDiscover(category, limit) };
  }

  if (source === 'netease') {
    return { source, items: await neteaseDiscover(limit) };
  }
  if (source === 'qq') {
    return { source, items: await qqDiscover(limit) };
  }

  const data = await listen1.apiGet(`/show_playlist?source=${source}`);
  const items = (data.result || []).slice(0, limit).map((p) => normalizePlaylist(p, source));
  return { source, items };
}

async function handlePlaylist(params) {
  const listId = params.get('id') || '';
  if (!listId) throw new Error('missing playlist id');
  if (listId.startsWith('kgplaylist_')) return kugouPlaylist(listId);
  if (listId.startsWith('kwplaylist_')) return kuwoPlaylist(listId);
  if (listId.startsWith('sodaplaylist_')) return qishuiPlaylist(listId);
  if (listId.startsWith('neplaylist_')) return neteasePlaylist(listId);
  if (listId.startsWith('qqplaylist_')) return qqPlaylist(listId);
  const data = await listen1.apiGet(`/playlist?list_id=${encodeURIComponent(listId)}`);
  const source = listId.startsWith('qqplaylist_') ? 'qq' : 'netease';
  return {
    info: normalizePlaylist(data.info || {}, source),
    tracks: (data.tracks || []).map((t) => normalizeTrack(t, source)),
  };
}

async function handleCharts(params) {
  const source = (params.get('source') || 'netease').toLowerCase();
  if (source === 'netease') {
    const data = await jsonGet('https://music.163.com/api/toplist');
    const items = (data.list || []).slice(0, 24).map((c) => ({
      id: `neplaylist_${c.id}`,
      title: c.name,
      cover: c.coverImgUrl || '',
      source: 'netease',
      source_name: SOURCE_NAMES.netease,
      subtitle: c.updateFrequency || '',
      count: c.trackCount || 0,
    }));
    return { source, items };
  }
  if (source === 'kugou') return { source, items: await kugouChartList() };
  if (source === 'kuwo') return { source, items: await kuwoChartList() };
  if (source === 'qishui') return { source, items: await qishuiChartList() };

  const items = QQ_CHARTS.map((c) => ({
    id: `qqtop_${c.id}`,
    title: c.name,
    cover: '',
    source: 'qq',
    source_name: SOURCE_NAMES.qq,
    subtitle: 'QQ音乐',
    count: 0,
  }));
  return { source, items };
}

async function handleChart(params) {
  const source = (params.get('source') || 'netease').toLowerCase();
  const id = params.get('id') || '';
  if (!id) throw new Error('missing chart id');

  if (source === 'netease') {
    return handlePlaylist(new URLSearchParams({ id }));
  }
  if (source === 'kugou') return kugouChart(id);
  if (source === 'kuwo') return kuwoChart(id);
  if (source === 'qishui') return qishuiChart(id);

  const topId = id.replace(/^qqtop_/, '');
  const data = await jsonGet('https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg'
    + `?topid=${encodeURIComponent(topId)}&format=json&page=0&needNewCode=1&tpl=3&type=top`);
  const songs = data.songlist || [];
  const tracks = songs.map((row) => {
    const s = row.data || {};
    const singers = (s.singer || []).map((x) => x.name).join(' / ');
    return normalizeTrack({
      id: `qqtrack_${s.songmid}`,
      title: s.songname,
      artist: singers,
      album: s.albumname || '',
      img_url: qqSongUrl(s.albummid),
      duration: s.interval || 0,
      source_url: `http://y.qq.com/#type=song&mid=${s.songmid}`,
    }, 'qq');
  });
  return {
    info: {
      id,
      title: (data.topinfo && data.topinfo.ListName) || 'QQ音乐榜单',
      cover: tracks[0] ? tracks[0].cover : '',
      source: 'qq',
      source_name: SOURCE_NAMES.qq,
    },
    tracks,
  };
}

async function handleCategories(params) {
  const source = (params.get('source') || 'netease').toLowerCase();
  if (source === 'netease') {
    return { source, groups: [{ name: '分类', items: NETEASE_CATEGORIES.map((name) => ({ name, id: name })) }] };
  }
  if (source === 'kugou') {
    return { source, groups: [{ name: '分类', items: KUGOU_CATEGORIES.map((name) => ({ name, id: name })) }] };
  }
  if (source === 'kuwo') {
    return { source, groups: [{ name: '分类', items: KUWO_CATEGORIES.map((c) => ({ name: c.name, id: c.id })) }] };
  }
  if (source === 'qishui') {
    return { source, groups: [{ name: '分类', items: QISHUI_CATEGORIES.map((c) => ({ name: c.name, id: c.id })) }] };
  }
  const data = await jsonGet('https://c.y.qq.com/splcloud/fcgi-bin/fcg_get_diss_tag_conf.fcg?picmid=1&rnd=0.1'
    + '&g_tk=5381&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8'
    + '&notice=0&platform=yqq.json&needNewCode=0');
  const groups = (data.data && data.data.categories || []).map((g) => ({
    name: g.categoryGroupName,
    items: (g.items || []).map((it) => ({
      name: it.categoryName,
      id: String(it.categoryId),
      sort_id: String((it.allsorts || []).find((s) => s.sortName === '最热') ? 3 : 5),
    })),
  }));
  return { source, groups };
}

async function handleStream(params) {
  const trackId = params.get('id') || '';
  if (!trackId) throw new Error('missing track id');
  if (trackId.startsWith('kgtrack_')) return kugouStream(trackId);
  if (trackId.startsWith('kwtrack_')) return kuwoStream(trackId);
  if (trackId.startsWith('sodatrack_')) return qishuiStream(trackId);
  if (trackId.startsWith('netrack_')) return neteaseStream(trackId);

  const data = await listen1.apiGet(`/bootstrap_track?track_id=${encodeURIComponent(trackId)}`);
  if (!data || !data.url) throw new Error('no stream url');
  // Listen1 returns a bare CDN host for QQ when no vkey is available; handing
  // that to the player produces “无音源” instead of a useful fallback.
  let pathname = '';
  try { pathname = new URL(data.url).pathname || ''; } catch (err) { pathname = ''; }
  if (!pathname || pathname === '/') {
    throw new Error('音源未返回可播放地址（QQ 可能需要登录）');
  }
  return { url: data.url };
}

async function handleSearch(params) {
  const source = (params.get('source') || 'netease').toLowerCase();
  const keyword = params.get('keyword') || '';
  if (!keyword) throw new Error('missing keyword');
  const limit = Math.max(1, Math.min(60, Number(params.get('limit') || 30)));

  if (source === 'netease') {
    const url = 'https://music.163.com/api/search/get?csrf_token=&s='
      + encodeURIComponent(keyword) + `&type=1&offset=0&total=true&limit=${limit}`;
    const data = await jsonGet(url);
    const songs = (data && data.result && data.result.songs) || [];
    const tracks = songs.map((s) => normalizeTrack({
      id: `netrack_${s.id}`,
      listen1_id: `netrack_${s.id}`,
      title: s.name,
      artist: (s.artists || []).map((a) => a.name).filter(Boolean).join(' / '),
      album: (s.album || {}).name || '',
      img_url: (s.album || {}).picUrl || '',
      duration: Math.round((s.duration || 0) / 1000),
      source_url: `http://music.163.com/#/song?id=${s.id}`,
    }, 'netease'));
    return { source, tracks, total: (data && data.result && data.result.songCount) || tracks.length };
  }

  if (source === 'qq') {
    const url = 'https://c.y.qq.com/soso/fcgi-bin/client_search_cp?format=json&p=1&n='
      + `${limit}&w=${encodeURIComponent(keyword)}`;
    const data = await jsonGet(url);
    const list = (data && data.data && data.data.song && data.data.song.list) || [];
    const tracks = list.map((s) => normalizeTrack({
      id: `qqsearch_${s.songmid}`,
      listen1_id: `qqtrack_${s.songmid}`,
      title: s.songname,
      artist: (s.singer || []).map((x) => x.name).filter(Boolean).join(' / '),
      album: s.albumname || '',
      img_url: qqSongUrl(s.albummid),
      duration: s.interval || 0,
      source_url: `http://y.qq.com/#type=song&mid=${s.songmid}`,
    }, 'qq'));
    return { source, tracks, total: (data && data.data && data.data.song && data.data.song.totalnum) || tracks.length };
  }

  if (source === 'kugou') {
    const tracks = await kugouSearch(keyword, limit);
    return { source, tracks, total: tracks.length };
  }
  if (source === 'kuwo') {
    const tracks = await kuwoSearch(keyword, limit);
    return { source, tracks, total: tracks.length };
  }
  if (source === 'qishui') {
    const tracks = await qishuiSearch(keyword, limit);
    return { source, tracks, total: tracks.length };
  }

  const data = await listen1.apiGet(`/search?source=${source}&keywords=${encodeURIComponent(keyword)}&curpage=1`);
  return {
    source,
    tracks: (data.result || []).map((t) => normalizeTrack(t, source)),
    total: data.total || 0,
  };
}

function send(res, status, payload) {
  const body = JSON.stringify(payload === undefined ? null : payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

async function route(req, res) {
  if (req.method !== 'GET') {
    send(res, 405, { error: 'method not allowed' });
    return;
  }
  if (TOKEN && req.headers['x-aria-token'] !== TOKEN) {
    send(res, 403, { error: 'forbidden' });
    return;
  }
  const parsed = new URL(req.url, `http://${HOST}`);
  const params = parsed.searchParams;
  try {
    let payload;
    switch (parsed.pathname) {
      case '/health':
        payload = { ok: true, app: 'aria-listen1-sidecar' };
        break;
      case '/providers':
        payload = {
          providers: Object.keys(SOURCE_NAMES).map((id) => ({
            id, name: SOURCE_NAMES[id],
          })),
        };
        break;
      case '/recommend':
        payload = await handleRecommend(params);
        break;
      case '/playlist':
        payload = await handlePlaylist(params);
        break;
      case '/charts':
        payload = await handleCharts(params);
        break;
      case '/chart':
        payload = await handleChart(params);
        break;
      case '/categories':
        payload = await handleCategories(params);
        break;
      case '/stream':
        payload = await handleStream(params);
        break;
      case '/search':
        payload = await handleSearch(params);
        break;
      default:
        send(res, 404, { error: 'not found' });
        return;
    }
    send(res, 200, payload);
  } catch (err) {
    send(res, 502, { error: String((err && err.message) || err).slice(0, 220) });
  }
}

const server = http.createServer((req, res) => {
  route(req, res).catch((err) => send(res, 500, { error: String(err && err.message || err) }));
});

server.listen(0, HOST, () => {
  const port = server.address().port;
  process.stdout.write(JSON.stringify({ event: 'ready', port }) + '\n');
});

function shutdown() {
  try { server.close(); } catch (e) { /* ignore */ }
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

// If Aria is force-killed (task manager / crash), the pipe disappears without
// SIGTERM. Poll the parent pid so the Node sidecar does not linger as an orphan.
const PARENT_PID = parseInt(process.env.ARIA_PARENT_PID || '0', 10);
if (PARENT_PID > 0) {
  const watchdog = setInterval(() => {
    try {
      process.kill(PARENT_PID, 0);
    } catch (err) {
      shutdown();
    }
  }, 3000);
  watchdog.unref();
}
