const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const events={},cached={shell:true};let fetched=0;
vm.runInNewContext(fs.readFileSync(require.resolve('../web/sw.js'),'utf8'),{
 self:{location:{origin:'https://test.ts.net'},addEventListener:(name,fn)=>events[name]=fn},
 URL,Response,fetch:async()=>{fetched++;return {network:true}},
 caches:{match:async()=>cached},
});
(async()=>{
 for(const path of ['/api/bootstrap','/api/run','/api/training','/api/pair']){
  let handled=false;events.fetch({request:{method:'GET',url:'https://test.ts.net'+path},respondWith:()=>handled=true});
  assert.equal(handled,false,'private API responses never enter the shell cache');
 }
 let result;events.fetch({request:{method:'GET',url:'https://test.ts.net/mobile.js?revision=1'},respondWith:p=>result=p});
 assert.equal(await result,cached);assert.equal(fetched,0,'warm shell avoids network round trip');
 let handled=false;events.fetch({request:{method:'POST',url:'https://test.ts.net/'},respondWith:()=>handled=true});assert.equal(handled,false);
 console.log('Passed cached shell, query-string assets, API exclusion and POST exclusion.');
})().catch(e=>{console.error(e);process.exitCode=1});
