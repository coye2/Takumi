'use strict';
/* Kage — optional Sumi companions within the existing Takumi chat.
   UI emits only observed activity states, never model private deliberation. */
const KAGE_STATES=['idle','thinking','researching','coding','checking','writing','done'];
let kageCache=null,kageArtwork={},kageDoneTimer=0;
const kageNotified=new Set();
window.kageSetState=function(next){
  const fab=document.getElementById('companionButton');
  if(!fab||!KAGE_STATES.includes(next))return;
  fab.classList.remove(...KAGE_STATES.map(x=>'kage-'+x));fab.classList.add('kage-'+next);
  fab.title='Kage · '+next[0].toUpperCase()+next.slice(1);
  if(next==='done'){
    clearTimeout(kageDoneTimer);
    kageDoneTimer=setTimeout(()=>window.kageSetState('idle'),2900);
  }
};
window.refreshKage=async function(){
  try{const data=await api('/api/kage');kageCache=data;if(Array.isArray(data.busy_sessions))statusBadge({busy:data.busy_sessions});state.kageMember=data.selected||'ren';
      const pic=document.querySelector('#companionButton img');
      const unread=data.unread||[];let badge=document.querySelector('#companionButton .kage-unread');if(!badge){badge=document.createElement('span');badge.className='kage-unread';document.getElementById('companionButton')?.append(badge)}badge.textContent=String(unread.length);badge.hidden=!unread.length;badge.setAttribute('aria-label',unread.length+' unread Kage notifications');
      const latest=unread.at(-1);if(latest&&!kageNotified.has(latest.id)){kageNotified.add(latest.id);if(latest.session_id!==state.session)toast('Kage · '+latest.message,8000)}
      if(pic){const result=await api('/api/kage/avatar/'+state.kageMember);if(pic.getAttribute('src')!==result.src)pic.src=result.src;pic.alt=(data.members.find(m=>m.id===state.kageMember)?.name||'Kage')+' · Kage companion';}
      return data;
  }catch{return null;}
};
async function kageArt(member){
  if(kageArtwork[member])return kageArtwork[member];
  try{const r=await api('/api/kage/avatar/'+member);kageArtwork[member]=r.src;return r.src;}catch{return kageCache?.members.find(m=>m.id===member)?.avatar||'/kage/ren-256.webp';}
}
function kageLabel(text){const n=document.createElement('p');n.className='kage-section-label';n.textContent=text;return n;}
function kageAction(label,action,cls='text-btn'){
  const n=document.createElement('button');n.className=cls;n.type='button';n.textContent=label;n.onclick=async()=>{n.disabled=true;try{await action();}catch(e){toast(e.message||'Operation failed',8000)}finally{n.disabled=false;}};return n;
}
function field(type,placeholder,value=''){
  const n=document.createElement(type==='select'?'select':'input');n.className='kage-input';
  if(n.tagName==='INPUT'){n.type=type||'text';n.placeholder=placeholder||'';n.value=value;}
  else n.setAttribute('aria-label',placeholder||'Selection');return n;
}
function option(select,value,label){const n=document.createElement('option');n.value=value;n.textContent=label;select.append(n);return n;}
function shortTime(s){if(!s)return 'Not run yet';return new Date(Number(s)*1000).toLocaleString();}
function kageCard(member,selected,root){
  const b=document.createElement('button');b.type='button';b.className='kage-member'+(selected===member.id?' selected':'');
  b.setAttribute('aria-pressed',String(selected===member.id));
  const img=document.createElement('img');img.src=member.avatar;img.alt='';
  kageArt(member.id).then(src=>img.src=src);
  const label=document.createElement('span');label.className='kage-member-copy';
  const name=document.createElement('strong');name.textContent=member.name;
  const role=document.createElement('small');role.textContent=member.role;
  label.append(name,role);b.append(img,label);root.append(b);
  b.onclick=async()=>{await api('/api/kage/select',{member:member.id});await window.refreshKage();window.drawKage();};
}
window.drawKage=async function(){
  const root=document.getElementById('drawerBody');if(!root)return;
  const data=await window.refreshKage();root.replaceChildren();
  if(!data){root.append(kageLabel('Kage is unavailable. Check that the Takumi service is running.'));return;}
  const hero=document.createElement('div');hero.className='kage-hero';
  const text=document.createElement('div');text.className='kage-hero-copy';
  const eyebrow=document.createElement('small');eyebrow.textContent='TAKUMI · KAGE';
  const heading=document.createElement('h3');heading.textContent='Meet Kage';
  const description=document.createElement('p');description.textContent='Choose a companion for focused engineering work with your selected local model.';
  text.append(eyebrow,heading,description);hero.append(text);root.append(hero);
  const status=document.createElement('div');status.className='kage-monitor';status.textContent=(data.monitoring?'● Monitoring while Takumi runs':'○ Monitoring paused')+(data.active?' · Task running':'');root.append(status);
  root.append(kageLabel('YOUR COMPANION'));
  const grid=document.createElement('div');grid.className='kage-people';data.members.forEach(m=>kageCard(m,data.selected,grid));root.append(grid);
  const selected=data.members.find(x=>x.id===data.selected);const desc=document.createElement('p');desc.className='kage-description';desc.textContent=selected?.description||'';root.append(desc);
  root.append(kageAction('Ask '+(selected?.name||'Kage')+' in chat',async()=>{closeDrawer();await openKageChat();document.getElementById('prompt').focus();},'kage-ask'));
  root.append(kageLabel('NOTIFICATIONS'));
  const unread=data.unread||[];
  if(!unread.length)root.append(kageLabel('You are up to date.'));else root.append(kageAction('Mark all read',async()=>{await api('/api/kage/notifications/read',{ids:unread.map(x=>x.id)});await window.drawKage()}));
  for(const ev of unread.slice().reverse().slice(0,12)){
    const card=document.createElement('div');card.className='kage-task';const text=document.createElement('p');text.textContent=ev.message;card.append(text);
    if(ev.session_id)card.append(kageAction('Open conversation',async()=>{closeDrawer();await openSession(ev.session_id)}));
    card.append(kageAction('Mark read',async()=>{await api('/api/kage/notifications/read',{ids:[ev.id]});await window.drawKage()}));root.append(card)
  }
  root.append(kageLabel('INDEPENDENT PROJECT WORK'));
  const goalText=document.createElement('textarea');goalText.className='kage-input';goalText.rows=3;goalText.placeholder='Describe the project goal and the checks that will prove it is done';goalText.setAttribute('aria-label','Independent Kage goal');
  const goalProject=field('select','Goal project');option(goalProject,'','Choose project');(state.projects||[]).forEach(p=>option(goalProject,p.id,p.name));goalProject.value=state.project||'';
  const budget=field('select','Goal time limit');[[1800,'30 minutes'],[7200,'2 hours'],[21600,'6 hours'],[43200,'12 hours']].forEach(([v,n])=>option(budget,String(v),n));budget.value='43200';
  const explanation=document.createElement('p');explanation.className='muted';explanation.textContent='Kage queues work through the coding agent in its own conversation. Local model work runs one task at a time. Waiting counts toward the deadline; Stop and saved checkpoints remain available.';
  root.append(explanation,goalProject,goalText,budget,kageAction('Queue project goal',async()=>{
    const r=await api('/api/kage/goals',{project_id:goalProject.value,prompt:goalText.value,seconds:Number(budget.value),model:state.model});toast('Kage goal queued');await refreshBootstrap();await window.drawKage();
  },'kage-submit'));
  for(const job of (data.jobs||[]).slice().reverse()){
    const card=document.createElement('div');card.className='kage-task';const info=document.createElement('p');info.textContent=job.title+' · '+job.state+' · deadline '+shortTime(job.deadline)+(job.reason?' · '+job.reason:'');card.append(info);
    card.append(kageAction('Open work',async()=>{closeDrawer();await openSession(job.session_id)}));
    if(['queued','starting','running'].includes(job.state))card.append(kageAction('Stop',async()=>{await api('/api/kage/goals/stop',{job_id:job.id});await window.drawKage()}));root.append(card)
  }
  root.append(kageLabel('SET UP A CHECK-IN'));
  const builder=document.createElement('form');builder.className='kage-builder';
  const title=field('text','What should Kage keep an eye on?');title.required=true;title.maxLength=95;
  const kind=field('select','Task type');[['brief','Daily or periodic briefing'],['repository','Watch Git status'],['research','Research a technical question'],['test','Run a declared project test']].forEach(x=>option(kind,...x));
  const schedule=field('select','Schedule');[['manual','Run on demand'],['30m','Every 30 minutes'],['hourly','Every hour'],['2h','Every 2 hours'],['4h','Every 4 hours'],['daily','Daily at a time']].forEach(x=>option(schedule,...x));schedule.value='daily';
  const clock=field('time','Local time','09:00');
  const proj=field('select','Project');option(proj,'','No project selected');(state.projects||[]).forEach(p=>option(proj,p.id,p.name));proj.value=state.project||'';
  const query=field('text','Research question');query.maxLength=360;
  const test=field('select','Project test');option(test,'','Choose an approved test');
  const net=document.createElement('label');net.className='kage-toggle';const checkbox=document.createElement('input');checkbox.type='checkbox';net.append(checkbox,document.createTextNode('Allow public internet research for this task'));
  const inference=document.createElement('label');inference.className='kage-toggle';const summary=document.createElement('input');summary.type='checkbox';inference.append(summary,document.createTextNode('Ask Sumi to summarize the results'));
  const clockLabel=document.createElement('label');clockLabel.textContent='Local time';clockLabel.append(clock);
  async function loadTests(){test.replaceChildren();option(test,'','Choose an approved test');if(!proj.value)return;try{const x=await api('/api/harness/tasks/'+encodeURIComponent(proj.value));for(const item of x.tasks||[])option(test,item.id,item.name);}catch{}}
  function refreshFields(){clockLabel.hidden=schedule.value!=='daily';query.hidden=kind.value!=='research';net.hidden=kind.value!=='research';test.hidden=kind.value!=='test';inference.hidden=kind.value==='test';if(kind.value==='test')loadTests();}
  kind.onchange=refreshFields;schedule.onchange=refreshFields;proj.onchange=loadTests;refreshFields();
  const add=kageAction('Create task',async()=>{
    const body={title:title.value,kind:kind.value,cadence:schedule.value,clock:clock.value,
      project_id:proj.value||null,member:state.kageMember,query:query.value,
      test_id:test.value,allow_network:checkbox.checked,use_sumi:summary.checked};
    const r=await api('/api/kage/tasks',body);toast('Kage task saved: '+r.task.title);await window.drawKage();
  },'kage-submit');builder.onsubmit=e=>{e.preventDefault();add.click()};
  builder.append(title,kind,schedule,clockLabel,proj,query,test,net,inference,add);root.append(builder);
  root.append(kageLabel('SCHEDULED & ON-DEMAND'));
  const tasks=document.createElement('div');tasks.className='kage-task-list';
  for(const item of data.tasks){const card=document.createElement('div');card.className='kage-task';
    const main=document.createElement('div');const headline=document.createElement('strong');headline.textContent=item.title;
    const meta=document.createElement('small');meta.textContent=`${item.cadence==='manual'?'On demand':item.cadence==='daily'?'Daily · '+item.clock:item.cadence} · ${item.status} · ${shortTime(item.last_finished)}`;
    main.append(headline,meta);if(item.last_summary){const d=document.createElement('details');const summaryEl=document.createElement('summary');summaryEl.textContent='Last result';const pre=document.createElement('p');pre.textContent=item.last_summary;d.append(summaryEl,pre);main.append(d)}card.append(main);
    const actions=document.createElement('div');actions.className='kage-task-actions';
    actions.append(kageAction('Run',async()=>{await api('/api/kage/tasks/run',{task_id:item.id});toast('Kage task started');await window.drawKage();}),
      kageAction(item.enabled?'Pause':'Resume',async()=>{await api('/api/kage/tasks/enable',{task_id:item.id,enabled:!item.enabled});window.drawKage()}),
      kageAction('Delete',async()=>{if(!confirm('Delete this Kage task?'))return;await api('/api/kage/tasks/delete',{task_id:item.id});window.drawKage()}));card.append(actions);tasks.append(card);}
  if(!data.tasks.length){const p=document.createElement('p');p.textContent='No tasks yet. Schedule a daily briefing or ask Kage to watch your project.';tasks.append(p)}root.append(tasks);
  root.append(kageLabel('ACTIVITY'));
  const feed=document.createElement('ol');feed.className='kage-feed';
  for(const ev of data.feed.slice(0,16)){const li=document.createElement('li');
    const marker=document.createElement('span');marker.className='kage-feed-dot';
    const text=document.createElement('div');const title=document.createElement('strong');title.textContent=ev.message;
    const time=document.createElement('small');time.textContent=`${shortTime(ev.at)} · ${ev.kind}`;
    text.append(title,time);if(ev.detail){const det=document.createElement('details');const cap=document.createElement('summary');cap.textContent='Evidence & result';const p=document.createElement('p');p.textContent=ev.detail;det.append(cap,p);text.append(det)}li.append(marker,text);feed.append(li);}
  if(!data.feed.length){const p=document.createElement('p');p.textContent='Kage will record actual task activity here.';feed.append(p)}root.append(feed);
  root.append(kageLabel('CUSTOM ARTWORK'));
  const artText=document.createElement('p');artText.className='kage-legal';artText.textContent='Import only artwork you have permission to use. This personal image stays on your PC and is never added to Takumi’s public release.';
  const upload=field('file','');upload.type='file';upload.accept='image/png,image/jpeg,image/webp';
  const source=field('text','Artwork source or license URL');const credit=field('text','Creator credit / license');
  const rights=document.createElement('label');rights.className='kage-toggle';const checked=document.createElement('input');checked.type='checkbox';rights.append(checked,document.createTextNode('I have the rights to use this artwork in Takumi'));
  root.append(artText,upload,source,credit,rights);
  root.append(kageAction('Import avatar for '+(selected?.name||'Kage'),async()=>{
    const file=upload.files?.[0];if(!file||file.size>2e6)return toast('Choose a PNG/JPEG/WebP under 2 MB');
    if(!checked.checked)return toast('Confirm you have permission to use the artwork');
    const base64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('Could not read image'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file)});
    await api('/api/kage/avatar/import',{member:state.kageMember,data:base64,source:source.value,attribution:credit.value,rights_confirmed:true});
    delete kageArtwork[state.kageMember];toast('Local avatar imported');await window.drawKage();
  },'text-btn'));
  root.append(kageAction('Refresh activity',()=>window.drawKage(),'text-btn'));
};
setTimeout(()=>window.refreshKage(),1500);
setInterval(()=>window.refreshKage(),5000);
