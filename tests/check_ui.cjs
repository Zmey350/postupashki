// Offline rendering checks. This is a Node VM with DOM stubs, not a browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function run(path,input=null){
 const data=input||JSON.parse(fs.readFileSync(path,'utf8')),els={};
 const el=id=>els[id]??=( {id,value:'',hidden:false,disabled:false,dataset:{},innerHTML:'',textContent:'',files:[],classList:{toggle(){}},setAttribute(){},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}} );
 const document={getElementById:el,querySelectorAll:()=>[],addEventListener(){},createElement:()=>({click(){}}),hidden:false};
 const box={document,window:{},SNAPSHOT:{report:data,meta:{start:'2026-05-01',end:'2026-09-11'}},Intl,Date,URL,URLSearchParams,setTimeout:()=>{},setInterval:()=>{},Blob,console};
 box.window.SNAPSHOT=box.SNAPSHOT;vm.createContext(box);vm.runInContext(fs.readFileSync('dashboard/app.js','utf8'),box);
 for(const tab of ['overview','placements','growth','events','funnel','forecast','cohorts','ml']){
  vm.runInContext(`tab='${tab}';render()`,box);
  assert(els.content.innerHTML.length>50,tab+' rendered');assert(!/undefined|NaN|Infinity/.test(els.content.innerHTML),tab+' finite values');
 }
 vm.runInContext("tab='funnel';mode='mixed';render()",box);
 if(data.deals.length){vm.runInContext('showDeal(0)',box);assert(els['dialog-body'].innerHTML.includes('История касаний'));}
 if(data.cohorts.length){vm.runInContext('showCohort(0)',box);assert(els.detail.open);}
 console.log(path,'8 tabs including ML unavailable + mixed funnel + available drill-downs: OK');
}
if(process.argv[2]){run(process.argv[2]);if(process.argv[3])run(process.argv[3]);}else{
 const {execFileSync}=require('node:child_process');
 for(const path of ['data/demo.sqlite3',':memory:']){
  const code="import json;from store import Store;from analytics import report\nwith Store("+JSON.stringify(path)+") as s: print(json.dumps(report(s.db,'2026-05-01','2026-09-12')))";
  run(path,JSON.parse(execFileSync(process.env.PYTHON||'python3',['-c',code],{encoding:'utf8',maxBuffer:16*1024*1024})));
 }
}
