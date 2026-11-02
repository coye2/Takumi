const assert=require('node:assert/strict');
const {SSEParser,RunCursor}=require('../web/run-stream.js');
const event={schema_version:1,session_id:'s',run_id:'r',seq:1,event_id:'r:1',type:'answer.delta',payload:{type:'delta',text:'日本語 🌸'}};
const bytes=new TextEncoder().encode(': heartbeat\r\nid: r:1\r\ndata: '+JSON.stringify(event)+'\r\n\r\n');
for(let split=0;split<=bytes.length;split++){
  const output=[],parser=new SSEParser(e=>output.push(e));
  parser.push(bytes.slice(0,split));parser.push(bytes.slice(split),true);
  assert.deepEqual(output,[event]);
}
let output=[];const parser=new SSEParser(e=>output.push(e));
parser.push(new TextEncoder().encode('data: {"type":\ndata: "notice"}\n\n'),true);
assert.deepEqual(output,[{type:'notice'}]);
assert.throws(()=>new SSEParser(()=>{}).push(new TextEncoder().encode('data: bad\n\n')),/Malformed/);
assert.throws(()=>new SSEParser(()=>{}).push(new Uint8Array([255]),true),/encoded data/);
assert.throws(()=>new SSEParser(()=>{},10).push(new TextEncoder().encode('data: '+ 'x'.repeat(20))),/size limit/);
assert.throws(()=>new SSEParser(()=>{}).push(new TextEncoder().encode('data: {}'),true),/Incomplete/);
const cursor=new RunCursor('s');assert.equal(cursor.accept(event).text,'日本語 🌸');
assert.equal(cursor.accept(event),null);
assert.throws(()=>cursor.accept({...event,payload:{type:'delta',text:'changed'}}),/Conflicting/);
assert.throws(()=>cursor.accept({...event,seq:3,event_id:'r:3'}),/Missing/);
assert.throws(()=>cursor.accept({...event,run_id:'another',event_id:'another:1'}),/another run/);
assert.throws(()=>new RunCursor('wrong').accept(event),/another conversation/);
cursor.accept({...event,seq:2,event_id:'r:2',type:'run.completed',payload:{type:'done'}});
assert.throws(()=>cursor.accept({...event,seq:3,event_id:'r:3'}),/after finished/);
console.log(`Passed ${bytes.length+1} UTF-8/CRLF boundaries, multiline frames, malformed/oversized/EOF input, replay ordering and terminal tests.`);
