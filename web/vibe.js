/* Verified build panel. Uses the same drawer, cards and buttons as every other workspace panel. */
(function(){
const STATE_WORDS={running:'Working',verified:'Verified',partially_verified:'Partly verified',unverified:'Not verified',failed:'Checks failed',stuck:'Stuck on one failure',no_change:'No usable edit',model_unavailable:'Model unavailable',cancelled:'Stopped'};
let poll=null;
function stopPoll(){if(poll){clearInterval(poll);poll=null}}
function row(label,detail,status){const li=el('li');li.append(el('b','',label),el('small','',status?`${status}${detail?' · '+detail:''}`:detail||''));return li}
function drawPlan(box,plan){box.replaceChildren();box.append(el('h3','',plan.platforms.length?plan.platforms.join(' · '):'Project type not recognized'));
 if(!plan.steps.length){box.append(el('p','muted','Nothing here can check an edit yet. Add tests or a takumi.tasks.json, or start from a new project below.'));return}
 const list=el('ol','drawer-events');for(const step of plan.steps)list.append(row(step.name,step.blocked||'',step.blocked?'Blocked':'Ready'));box.append(list);
 if(!plan.can_prove)box.append(el('p','muted','Only static checks can run, so a result here will be reported as partly verified.'))}
function drawRun(box,current){box.replaceChildren();if(!current)return;
 const report=current.report||{};box.append(el('h3','',STATE_WORDS[current.state]||current.state));if(report.detail)box.append(el('p','',report.detail));
 if(report.tests_edited)box.append(el('p','muted','This run changed test files. Check those changes first.'));
 if(report.rolled_back)box.append(el('p','muted','Your files were put back as they were.'));
 if(report.rollback_conflicts?.length)box.append(el('p','','Left in place because they changed during the run: '+report.rollback_conflicts.join(', ')));
 const list=el('ol','drawer-events');for(const event of (current.events||[]).filter(e=>e.kind!=='step').slice(-14))list.append(row(event.title,event.detail||(event.files||[]).join(', '),event.name||''));box.append(list);
 const failures=report.diagnostics||[...(current.events||[])].reverse().find(e=>e.diagnostics?.length)?.diagnostics||[];
 if(failures.length&&current.state!=='verified'){const details=el('details','');details.append(el('summary','','First failures'));const pre=el('pre','');pre.textContent=failures.slice(0,8).map(d=>`${d.file?d.file+(d.line?':'+d.line:'')+': ':''}${d.message}`).join('\n');details.append(pre);box.append(details)}
 if(report.diff){const details=el('details','');details.open=current.state==='verified';details.append(el('summary','',`Changes · ${(report.changed_files||[]).length} file(s)`));const pre=el('pre','');pre.textContent=report.diff;details.append(pre);box.append(details)}
 const verification=report.verification;if(verification)box.append(el('p','muted',`Source ${verification.source_changed?'changed':'unchanged'} · source check ${verification.source_validated?'passed':'not passed'} · build ${verification.build_passed?'passed':'not passed'} · tests ${verification.tests_passed?'passed':'not passed'} · runtime ${verification.runtime_passed?'passed':'not passed'} · end to end ${verification.end_to_end?'yes':'no'}`));
 if(report.attempts)box.append(el('p','muted',`${report.attempts} attempt(s) · ${report.seconds}s · ${report.output_tokens} tokens · ${report.model||current.model}`))}
window.drawVibe=async function(){
 stopPoll();const root=$('drawerBody');root.replaceChildren();
 root.append(el('p','','Describe the change. Sumi edits the project, runs its real build and tests, and repairs from the errors. A result only says verified when those checks actually ran and passed.'));
 const project=typeof selectedProject==='function'?selectedProject():null;
 if(!project){root.append(button('Choose project',openProjectDialog,'action-btn'))}
 const planBox=el('div','card'),runBox=el('div','card');
 const task=el('textarea','inline-input');task.rows=4;task.placeholder='Add a dark mode toggle and keep the choice after a reload';task.setAttribute('aria-label','What to build or fix');
 const model=el('select','inline-input');model.setAttribute('aria-label','Model');for(const [value,label] of [['sumi-1.0:latest','Sumi 1.1'],['rei-1.0:latest','Rei 1.1']]){const option=el('option','',label);option.value=value;model.append(option)}
 const configLabel=el('label','subtle-toggle'),config=document.createElement('input');config.type='checkbox';configLabel.append(config,document.createTextNode(' Allow edits to build configuration'));
 let noted=0,wasRunning=false;
 const refresh=async()=>{try{const r=await api('/api/vibe');const events=r.current?.events||[];const busy=r.current?.state==='running';if(busy||wasRunning){for(const event of events.slice(noted))window.noteEngineering?.(event)}noted=events.length;drawRun(runBox,r.current);runBox.hidden=!r.current;if(wasRunning&&!busy&&state.session&&typeof renderConversation==='function'){try{const session=await api('/api/session/'+encodeURIComponent(state.session));state.events=session.events||state.events;renderConversation(session)}catch{}}wasRunning=busy;startButton.disabled=busy||!project;stopButton.disabled=!busy;if(!busy)stopPoll()}catch(e){stopPoll();toast(e.message)}};
 const watch=()=>{stopPoll();poll=setInterval(()=>{if($('drawer').hidden||$('drawer').dataset.kind!=='vibe'){stopPoll();return}refresh()},1500)};
 const startButton=button('Start',async()=>{try{noted=0;await api('/api/vibe/start',{project_id:state.project,task:task.value,model:model.value,allow_config:config.checked,session_id:state.session||''});watch();await refresh()}catch(e){toast(e.message)}},'action-btn');
 const stopButton=button('Stop',async()=>{try{await api('/api/vibe/stop',{});await refresh()}catch(e){toast(e.message)}});
 const actions=el('div','form-row');actions.append(startButton,stopButton);
 root.append(task,model,configLabel,actions,runBox);runBox.hidden=true;
 if(project){section(root,'How this project is checked');root.append(planBox);planBox.append(el('p','muted','Looking at the project…'));api('/api/vibe/plan/'+encodeURIComponent(state.project)).then(plan=>drawPlan(planBox,plan)).catch(e=>{planBox.textContent=e.message})}
 if(!state.remote){section(root,'New project');
  const platform=el('select','inline-input');platform.setAttribute('aria-label','Project type');for(const [value,label] of [['web','Web app'],['pwa','Installable PWA'],['dotnet','.NET console app'],['cpp','C++ with CMake'],['android','Android app'],['ios','iOS Swift package']]){const option=el('option','',label);option.value=value;platform.append(option)}
  const folder=el('input','inline-input');folder.placeholder='Empty or new folder, such as C:\\Users\\you\\ForgeProjects\\notes';folder.setAttribute('aria-label','Folder');
  root.append(platform,folder,button('Create project',async()=>{try{const r=await api('/api/vibe/scaffold',{platform:platform.value,path:folder.value});toast(`Created ${r.files.length} files.`);if(!state.projects.some(x=>x.id===r.project.id))state.projects.push(r.project);state.project=r.project.id;drawVibe()}catch(e){toast(e.message)}},'action-btn'));
  section(root,'Forge Universal');const forge=el('div','card');root.append(forge);
  api('/api/vibe/forge').then(r=>{forge.append(el('p','',r.installed?`Installed${r.version?' · '+r.version:''}. Takumi does not start, stop or change it.`:(r.detail||'Not installed.')));
   if(r.mcp_available){const label=el('label','subtle-toggle'),box=document.createElement('input');box.type='checkbox';box.checked=state.settings?.forge_enabled===true;box.addEventListener('change',()=>persistSettings({forge_enabled:box.checked}));label.append(box,document.createTextNode(' Give coding agents Forge tools (they run with your permissions)'));forge.append(label)}}).catch(e=>{forge.textContent=e.message})}
 await refresh();
 try{const r=await api('/api/vibe');if(r.current?.state==='running')watch()}catch{}
};
})();
