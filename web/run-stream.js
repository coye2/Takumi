/* Transport and replay only. This module cannot execute tools. */
(function(root){
  'use strict';
  class SSEParser {
    constructor(onEvent, maxSize=2100000){
      this.onEvent=onEvent;this.maxSize=maxSize;this.buffer='';this.data=[];this.id='';this.size=0;
      this.decoder=new TextDecoder('utf-8',{fatal:true});
    }
    push(bytes,final=false){
      this.buffer+=this.decoder.decode(bytes,{stream:!final});
      let index;
      while((index=this.buffer.search(/[\r\n]/))>=0){
        if(this.buffer[index]==='\r'&&index===this.buffer.length-1&&!final)break;
        const line=this.buffer.slice(0,index),width=this.buffer.slice(index,index+2)==='\r\n'?2:1;
        this.buffer=this.buffer.slice(index+width);this.line(line);
      }
      if(this.buffer.length+this.size>this.maxSize)throw new Error('Stream event exceeds the size limit');
      if(final&&this.buffer){this.line(this.buffer);this.buffer=''}
      // EOF without a blank line is incomplete; never infer a terminal event.
      if(final&&this.data.length)throw new Error('Incomplete stream event; reconciling saved progress');
    }
    line(line){
      this.size+=line.length;
      if(this.size>this.maxSize)throw new Error('Stream event exceeds the size limit');
      if(line===''){
        if(this.data.length){
          let value;
          try{value=JSON.parse(this.data.join('\n'))}catch{throw new Error('Malformed stream event')}
          this.onEvent(value,this.id);
        }
        this.data=[];this.size=0;return;
      }
      if(line[0]===':')return;
      const colon=line.indexOf(':'),field=colon<0?line:line.slice(0,colon);
      let value=colon<0?'':line.slice(colon+1);if(value[0]===' ')value=value.slice(1);
      if(field==='data')this.data.push(value);
      else if(field==='id'&&!value.includes('\0'))this.id=value;
    }
  }
  class RunCursor {
    constructor(sessionId,runId=null){this.sessionId=sessionId;this.runId=runId;this.seq=0;this.seen=new Map();this.terminal=false}
    accept(event){
      const canonical=event.schema_version!==undefined;
      if(canonical&&event.schema_version!==1)throw new Error('Unsupported run event version');
      if(canonical&&event.session_id!==this.sessionId)throw new Error('Event belongs to another conversation');
      if(!event.run_id||!Number.isSafeInteger(event.seq)||event.seq<1||event.event_id!==`${event.run_id}:${event.seq}`)
        throw new Error('Invalid run event identity');
      if(this.runId&&event.run_id!==this.runId)throw new Error('Event belongs to another run');
      const value=canonical?event.payload:event;
      if(!value||typeof value.type!=='string')throw new Error('Invalid run event payload');
      const fingerprint=JSON.stringify(value);
      if(event.seq<=this.seq){
        if(this.seen.has(event.seq)&&this.seen.get(event.seq)!==fingerprint)throw new Error('Conflicting replay event');
        return null;
      }
      if(event.seq!==this.seq+1)throw new Error('Missing run events; replay required');
      if(this.terminal)throw new Error('Unexpected event after finished run');
      this.runId=event.run_id;this.seq=event.seq;this.seen.set(this.seq,fingerprint);
      if(this.seen.size>256)this.seen.delete(this.seen.keys().next().value);
      this.terminal=['done','error','recovery'].includes(value.type);
      return {...value,run_id:this.runId,seq:this.seq,event_id:event.event_id};
    }
  }
  async function* subscribe(options,token,onConnection=()=>{},runId=null){
    const cursor=new RunCursor(options.session_id,runId);
    const body={...options,request_id:options.request_id||crypto.randomUUID()};
    let failures=0;
    while(!cursor.terminal){
      let reader;
      try{
        const path=cursor.runId?`/api/runs/${encodeURIComponent(cursor.runId)}/events?after=${cursor.seq}`:'/api/run';
        const response=await fetch(path,cursor.runId?{headers:{'X-Takumi-Token':token}}:
          {method:'POST',headers:{'X-Takumi-Token':token,'Content-Type':'application/json'},body:JSON.stringify(body)});
        if(!response.ok){
          let error={};try{error=await response.json()}catch{}
          const failure=new Error(error.error||`Run connection failed (${response.status})`);
          failure.permanent=response.status>=400&&response.status<500;throw failure;
        }
        if(!response.body)throw new Error('Streaming unavailable on this device');
        onConnection('connected',cursor.runId);
        reader=response.body.getReader();const queue=[];
        const parser=new SSEParser(event=>{const item=cursor.accept(event);if(item)queue.push(item)});
        while(true){
          const {value,done}=await reader.read();parser.push(value,done);
          while(queue.length){failures=0;yield queue.shift()}
          if(cursor.terminal)return;
          if(done)throw new Error('Connection ended before the run outcome was received');
        }
      }catch(error){
        if(error.permanent||++failures>5){error.outcomeUnknown=!!cursor.runId;throw error}
        onConnection('reconnecting',cursor.runId,error.message);
        await new Promise(resolve=>setTimeout(resolve,Math.min(4000,250*2**(failures-1))+Math.random()*150));
      }finally{if(reader){try{await reader.cancel()}catch{}}}
    }
  }
  const api={SSEParser,RunCursor,subscribe};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.TakumiRuns=api;
})(globalThis);
