// Disposable page fixture; no network, browser profile, account, or credentials.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Input {
  constructor() { this.type='search'; this.tagName='INPUT'; this.isConnected=true; this._value=''; }
  get value(){return this._value} set value(v){this._value=v}
  getAttribute(name){return name==='aria-label' ? 'Song search' : null}
  getBoundingClientRect(){return {width:100,height:30}}
  dispatchEvent(){}
}
const input = new Input();
let clicks=0, text='Search songs', docId='doc1', url='https://fixture.test/search', protectedPage=false;
const button = {tagName:'BUTTON',type:'button',isConnected:true,innerText:'Search',
  getAttribute(){return null}, getBoundingClientRect(){return {width:80,height:30}},
  click(){clicks++;text='Found one song'}};
const page = vm.createContext({document:{title:'Song catalog',body:{get innerText(){return text}},
  querySelectorAll(selector){return selector.includes('password') ? (protectedPage ? [input] : []) : [input,button]}},
  location:{get href(){return url}}, getComputedStyle:()=>({visibility:'visible',display:'block'}),
  HTMLInputElement:Input, HTMLTextAreaElement:class {}, Event:class {}, URL, Date});
const chrome = {tabs:{get:async()=>({url})}, scripting:{executeScript:async request=>{
  assert(request.args.every(value => value !== undefined), 'Chrome rejects undefined executeScript arguments');
  if(request.target.documentIds && request.target.documentIds[0]!==docId) throw Error('Document changed');
  page.args=request.args;
  const result=vm.runInContext(`(${request.func.toString()})(...args)`,page);
  return [{documentId:docId,result}];
}}};
const worker=vm.createContext({chrome,URL});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../ano/extension/workflow.js'),'utf8'),worker);
const inspect=id=>worker.workflowRequest({action:'workflow_inspect',tabId:1,observationId:id});
const act=(id,elementId='2',operation='click',value='')=>worker.workflowRequest({action:'workflow_act',tabId:1,
  observationId:id,documentId:'doc1',elementId,operation,text:value});
(async()=>{
  let observed=await inspect('a'); assert.equal(observed.elements[0].label,'Song search');
  await act('a','1','fill','Original UK rap'); assert.equal(input.value,'Original UK rap');
  await assert.rejects(act('a')); assert.equal(clicks,0);
  await inspect('b'); await act('b'); assert.equal(clicks,1);
  observed=await inspect('c'); assert.equal(observed.text,'Found one song');
  button.innerText='Buy now'; await assert.rejects(act('c')); assert.equal(clicks,1);
  await inspect('d'); docId='doc2'; await assert.rejects(act('d')); assert.equal(clicks,1); docId='doc1';
  await inspect('e'); url='https://fixture.test/other'; await assert.rejects(act('e')); assert.equal(clicks,1);
  protectedPage=true; await assert.rejects(inspect('f')); protectedPage=false;
  await inspect('g'); input.type='password'; await assert.rejects(act('g','1','fill','never write'));
  assert.equal(input.value,'Original UK rap');
  console.log('Browser fixture: fill → inspect → click → verified result; replay, changed target/document/URL and password protection passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
