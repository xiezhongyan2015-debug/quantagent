// Independent offline UI scheduling counterexample. No network or real DOM.
// Executes unchanged JavaScript extracted from the frozen v11 PAGE.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const elements = new Map();
function element() {
  return {textContent:'',hidden:false,value:'saved',children:[],
    replaceChildren(){this.children=[]},appendChild(x){this.children.push(x)}};
}
const pending = [];
const context = vm.createContext({URLSearchParams,Date,console,
  setInterval(){return 0},
  document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},
            createElement(){return element()}},
  fetch(url){
    if(url==='/api/status')return Promise.resolve({ok:true,json:async()=>({campaigns:[],stage:'offline',phase:'offline'})});
    if(!url.startsWith('/api/answer?'))throw Error('Unexpected IO '+url);
    return new Promise(resolve=>pending.push({url,resolve}));
  }});
vm.runInContext(fs.readFileSync(path.join(__dirname,'frozen_monitor.js'),'utf8'),context);
function reply(call,text){return {ok:true,json:async()=>({campaign_id:'saved',call_id:call,text,
  offset:0,total_characters:text.length,next_offset:null})}}
(async()=>{
  const first=vm.runInContext("selectedCampaign='saved';selectedCall='call_A';loadAnswer(0)",context);
  const second=vm.runInContext("selectedCampaign='saved';selectedCall='call_B';loadAnswer(0)",context);
  assert.equal(pending.length,2);
  pending[1].resolve(reply('call_B','ANSWER_B'));
  await second;
  const afterSecond={label:elements.get('answerLabel').textContent,text:elements.get('answer').textContent};
  pending[0].resolve(reply('call_A','ANSWER_A'));
  await first;
  const afterLateFirst={label:elements.get('answerLabel').textContent,text:elements.get('answer').textContent};
  assert(afterSecond.label.startsWith('call_B'));
  assert.equal(afterSecond.text,'ANSWER_B');
  assert(afterLateFirst.label.startsWith('call_B'));
  assert.equal(afterLateFirst.text,'ANSWER_A');
  const result={status:'reproduced_display_identity_race',afterSecond,afterLateFirst,
    explanation:'A delayed earlier answer overwrites newer B selection and is labelled B.',
    network_calls:0,real_campaign_reads:0,model_calls:0,source_modified:false};
  fs.writeFileSync(path.join(__dirname,'answer_race_result.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1});
