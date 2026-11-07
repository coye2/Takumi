const assert=require('node:assert/strict');
const listeners={};
globalThis.document={visibilityState:'visible',addEventListener:(n,f)=>{listeners[n]=f},removeEventListener:()=>{}};
const {subscribe}=require('../web/run-stream.js');
const encode=event=>new TextEncoder().encode('id: x\ndata: '+JSON.stringify(event)+'\n\n');
const frame=(seq,type,extra={})=>encode({type,run_id:'r',seq,event_id:`r:${seq}`,...extra});
function stream(chunks,hold=false,signal){let i=0;return {ok:true,body:{getReader:()=>({read:()=>i<chunks.length?Promise.resolve({value:chunks[i++],done:false}):
  hold?new Promise((_,no)=>signal.addEventListener('abort',()=>no(new Error('Fetch is aborted')))):Promise.resolve({value:undefined,done:true}),cancel:async()=>{}})}}}
(async()=>{
  // Returning to the app aborts the socket. That must resume without any outage notice.
  let calls=0,notices=[];
  globalThis.fetch=async(path,init)=>{calls++;return calls===1?stream([frame(1,'start')],true,init.signal):stream([frame(2,'done',{text:'ok'})])};
  const seen=[];
  for await(const ev of subscribe({session_id:'s',prompt:'p'},'t',(state)=>notices.push(state))){seen.push(ev.type);if(ev.type==='start')setTimeout(()=>listeners.visibilitychange(),5)}
  assert.deepEqual(seen,['start','done']);
  assert.ok(!notices.includes('reconnecting'),'a routine resume is not an outage');
  // One dropped request stays quiet, and a second is reported.
  calls=0;notices=[];
  globalThis.fetch=async()=>{calls++;if(calls<3)throw new Error('network');return stream([frame(1,'done',{text:'ok'})])};
  for await(const ev of subscribe({session_id:'s',prompt:'p'},'t',(state)=>notices.push(state))){}
  assert.equal(notices.filter(n=>n==='reconnecting').length,1);
  assert.equal(notices.at(-1),'connected');
  console.log('Passed routine resume and repeated failure notices.');
})().catch(e=>{console.error(e);process.exit(1)});
