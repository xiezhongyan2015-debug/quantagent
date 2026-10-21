// Same frozen-JS checker can check later isolated repairs. No network/process IO.
// Usage: node check_monitor_async_identity.js EXTRACTED_JS NEW_RESULT_JSON
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(process.argv[2],'utf8');
const flush=async()=>{for(let n=0;n<16;n++)await Promise.resolve()};
function element(){return {textContent:'',hidden:false,disabled:false,value:'',className:'',children:[],
  replaceChildren(){this.children=[];this.textContent=''},appendChild(x){this.children.push(x)},
  setAttribute(k,v){this[k]=v},addEventListener(k,f){this['on'+k]=f}}}
const status={campaigns:[{id:'saved_A',cases:[]},{id:'saved_B',cases:[]}],stage:'OFFLINE_STAGE',phase:'OFFLINE_PHASE'};
async function setup(){
  const elements=new Map(),pending=[];
  const state={elements,pending,queueStatus:false};
  state.el=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  const context=vm.createContext({URLSearchParams,URL,Date,console,AbortController,
    setInterval(){return 0},setTimeout,clearTimeout,
    document:{getElementById:state.el,createElement:element},
    fetch(url){
      if(url==='/api/status'&&!state.queueStatus)return Promise.resolve({ok:true,json:async()=>status});
      if(!url.startsWith('/api/'))throw Error('Unexpected network '+url);
      return new Promise((resolve,reject)=>pending.push({url,resolve,reject}));
    }});
  vm.runInContext(source,context);
  state.exec=code=>vm.runInContext(code,context);
  state.take=route=>{
    const i=pending.findIndex(x=>x.url.startsWith(route));
    if(i<0)throw Error('missing pending '+route);
    return pending.splice(i,1)[0];
  };
  state.select=campaign=>{state.el('campaign').value=campaign;state.el('campaign').onchange?.()};
  await flush();
  return state;
}
async function resolve(request,payload){request.resolve({ok:true,json:async()=>payload});await flush()}
function calls(campaign,ids){return {campaign_id:campaign,offset:0,next_offset:null,total:ids.length,
 rows:ids.map((id,index)=>({id,case_id:id,round_index:index,status:'completed',action_status:'rejected',answer_available:true}))}}
function answer(campaign,call,text,offset=0,total=text.length){return {campaign_id:campaign,call_id:call,text,offset,
 total_characters:total,next_offset:offset+text.length<total?offset+text.length:null}}
function labels(state){return state.el('callList').children.map(line=>line.children[0]?.textContent||line.textContent).join('|')}
async function available(state){state.select('saved_A');state.exec('loadCalls(0)');
 await resolve(state.take('/api/calls'),calls('saved_A',['call_A','call_B']));}
function clickCall(state,index){const line=state.el('callList').children[index];line.children.find(x=>typeof x.onclick==='function').onclick()}
function record(name,passed,observed){return {name,passed,observed}}
(async()=>{
 const results=[];
 {
  const s=await setup();await available(s);clickCall(s,0);const a=s.take('/api/answer');clickCall(s,1);const b=s.take('/api/answer');
  await resolve(b,answer('saved_A','call_B','ANSWER_B'));await resolve(a,answer('saved_A','call_A','ANSWER_A'));
  results.push(record('late_previous_answer_cannot_replace_new_call',s.el('answer').textContent==='ANSWER_B',
   {label:s.el('answerLabel').textContent,text:s.el('answer').textContent}));
 }
 {
  const s=await setup();await available(s);clickCall(s,0);const a=s.take('/api/answer');clickCall(s,1);const b=s.take('/api/answer');
  await resolve(b,answer('saved_A','call_B','ANSWER_B'));const label=s.el('answerLabel').textContent;
  a.reject(Error('STALE_ANSWER_ERROR'));await flush();
  results.push(record('late_previous_answer_error_cannot_replace_current_status',s.el('answerLabel').textContent===label,
   {label:s.el('answerLabel').textContent,text:s.el('answer').textContent}));
 }
 {
  const s=await setup();s.select('saved_A');s.exec('loadCalls(0)');const a=s.take('/api/calls');
  s.select('saved_B');s.exec('loadCalls(0)');const b=s.take('/api/calls');
  await resolve(b,calls('saved_B',['B_ONLY']));await resolve(a,calls('saved_A',['A_ONLY']));
  results.push(record('late_previous_campaign_calls_cannot_replace_new_list',labels(s).includes('B_ONLY')&&!labels(s).includes('A_ONLY'),
   {selected:s.el('campaign').value,rows:labels(s)}));
 }
 {
  const s=await setup();s.select('saved_A');s.exec('loadCalls(0)');const a=s.take('/api/calls');
  s.select('saved_B');s.exec('loadCalls(0)');const b=s.take('/api/calls');
  await resolve(b,calls('saved_B',['B_ONLY']));const label=s.el('answerLabel').textContent;
  a.reject(Error('STALE_CALLS_ERROR'));await flush();
  results.push(record('late_previous_campaign_error_cannot_replace_current_status',s.el('answerLabel').textContent===label,
   {selected:s.el('campaign').value,label:s.el('answerLabel').textContent,rows:labels(s)}));
 }
 {
  const s=await setup();await available(s);clickCall(s,0);const a=s.take('/api/answer');
  s.select('saved_B');s.exec('loadCalls(0)');await resolve(s.take('/api/calls'),calls('saved_B',['B_ONLY']));
  await resolve(a,answer('saved_A','call_A','OLD_CAMPAIGN_ANSWER'));
  results.push(record('campaign_change_invalidates_previous_pending_answer',!s.el('answer').textContent.includes('OLD_CAMPAIGN'),
   {selected:s.el('campaign').value,label:s.el('answerLabel').textContent,text:s.el('answer').textContent}));
 }
 {
  const s=await setup();await available(s);clickCall(s,0);
  await resolve(s.take('/api/answer'),answer('saved_A','call_A','X'.repeat(4096),0,10000));
  s.el('nextAnswer').onclick();await resolve(s.take('/api/answer'),answer('saved_A','call_A','Y'.repeat(4096),4096,10000));
  s.el('nextAnswer').onclick();const old=s.take('/api/answer');s.el('prevAnswer').onclick();const latest=s.take('/api/answer');
  await resolve(latest,answer('saved_A','call_A','X'.repeat(4096),0,10000));
  await resolve(old,answer('saved_A','call_A','Z'.repeat(1808),8192,10000));
  results.push(record('late_page_cannot_override_newer_navigation',s.el('answer').textContent.startsWith('X'),
   {label:s.el('answerLabel').textContent,text_prefix:s.el('answer').textContent.slice(0,8)}));
 }
 {
  const s=await setup();s.queueStatus=true;s.exec('refresh()');const older=s.take('/api/status');
  s.exec('refresh()');const newer=s.take('/api/status');await resolve(newer,status);const label=s.el('updated').textContent;
  older.reject(Error('STALE_STATUS_ERROR'));await flush();
  results.push(record('older_status_error_cannot_replace_new_success',s.el('updated').textContent===label,
   {status:s.el('updated').textContent}));
 }
 const result={source_sha256:crypto.createHash('sha256').update(source).digest('hex'),
  results,passed:results.filter(x=>x.passed).length,total:results.length,
  scope:'offline deterministic out-of-order responses using unchanged extracted UI code; no real browser assertion',
  real_network_calls:0,real_campaign_reads:0,model_calls:0,source_modified:false};
 fs.writeFileSync(process.argv[3],JSON.stringify(result,null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify(result));
 process.exitCode=result.passed===result.total?0:1;
})().catch(error=>{console.error(error);process.exitCode=2});
