const api=require('./dist/listen1-api.js');
(async()=>{try{const d=await api.apiGet('/playlist?list_id=neplaylist_3778678'); console.log(JSON.stringify(d).slice(0,1200));}catch(e){console.log('ERR',e && (e.stack||e.message||JSON.stringify(e)));}process.exit(0)})();
