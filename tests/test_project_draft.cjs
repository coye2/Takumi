const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js','utf8');
const code = source.slice(source.indexOf('async function useSelectedProject('), source.indexOf('async function reviewGitHubPush('));
const prompt = {value:'draft',focus(){this.focused=true}};
const dialog = {close(){this.closed=true}};
const state = {projectDraft:'Add a GitHub link to my actual website'};
const context = {state,$:id=>id==='prompt'?prompt:dialog,
  refreshBootstrap:async()=>{},newSession:async id=>{state.session=id;prompt.value=''}};
vm.createContext(context);vm.runInContext(code,context);
(async()=>{
  await context.useSelectedProject({id:'website'});
  assert.equal(prompt.value,'Add a GitHub link to my actual website');
  assert.equal(state.project,'website');assert.equal(state.session,'website');
  assert.equal(state.projectDraft,undefined);assert.equal(prompt.focused,true);
  prompt.value='Another unsent request';
  await context.useSelectedProject({id:'second'});
  assert.equal(prompt.value,'Another unsent request');
  console.log('Project selection preserves unsent requests across new-session reset.');
})().catch(e=>{console.error(e);process.exitCode=1});
