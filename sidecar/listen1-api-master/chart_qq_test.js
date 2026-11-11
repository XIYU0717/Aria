const api=require('./dist/listen1-api.js');
(async()=>{for(const id of [26,27,4,5,31]){try{const d=await api.apiGet('/playlist?list_id=qqplaylist_'+id); console.log(id,d.info&&d.info.title,(d.tracks||[]).length);}catch(e){console.log(id,'ERR',String(e&&(e.message||JSON.stringify(e))));}}process.exit(0)})();
