// Therap-shaped synthetic form: no live accounts, healthcare records or network calls.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let nodes=[],url='https://fixture.test/isp',documentId='document-1',writes=0,cancelAfterChunk=false;
class Field {
  constructor(tag,type,id,label,context='') {
    Object.assign(this,{tagName:tag,type,id,name:id,context,label,isConnected:true,disabled:false,readOnly:false,
      autocomplete:'',maxLength:tag==='TEXTAREA'?3000:-1,_value:'',_checked:false,validity:{valid:true},options:[]});
  }
  get value(){return this._value} set value(v){this._value=v;writes++}
  get checked(){return this._checked} set checked(v){this._checked=v;writes++}
  getAttribute(name){return name==='aria-label'?this.label:null}
  getBoundingClientRect(){return {width:180,height:40}}
  closest(){return {cells:[{innerText:this.context}]}}
  dispatchEvent(event){if(event.type==='input'&&this.onInput)this.onInput()}
}
function reset() {
  nodes=[];writes=0;url='https://fixture.test/isp';documentId='document-1';cancelAfterChunk=false;
  for(let i=0;i<30;i++) {
    const select=new Field('SELECT','select-one',`score-${i}`,'Score',`Support outcome ${i+1}`);
    select.options=[{text:'Select Score',value:'-1',disabled:false},{text:'Monitor',value:'1',disabled:false},
      {text:'No Reportable (Non Reportable)',value:'8',disabled:false}];
    select._value='-1';nodes.push(select,new Field('TEXTAREA','textarea',`comment-${i}`,'Comment',`Support outcome ${i+1}`));
  }
  nodes.push(new Field('INPUT','text','begin','Begin Time'),new Field('INPUT','text','end','End Time'),
    new Field('INPUT','radio','billable','Billable Yes'),new Field('TEXTAREA','textarea','summary','Overall comment'));
}
const page=vm.createContext({document:{title:'ISP data fixture',body:{innerText:'Individual: Synthetic Person. Date: 10/24/2026.'},
  querySelectorAll(selector){return selector.includes('password') ? nodes.filter(n=>n.type==='password') : nodes}},
  location:{get href(){return url}},getComputedStyle:()=>({visibility:'visible',display:'block'}),
  HTMLInputElement:Field,HTMLTextAreaElement:Field,HTMLSelectElement:Field,Event:class{constructor(type){this.type=type}},Date,URL});
const chrome={tabs:{get:async()=>({url})},scripting:{executeScript:async request=>{
  if(request.target.documentIds && request.target.documentIds[0]!==documentId)throw Error('Document changed');
  page.args=request.args;
  const result=vm.runInContext(`(${request.func.toString()})(...args)`,page);
  if(request.args[0]==='apply'&&request.args[2]===0&&cancelAfterChunk) {
    cancelAfterChunk=false;await worker.formRequest({action:'form_cancel'});
  }
  return [{documentId,result}];
}}};
const worker=vm.createContext({chrome,URL});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../ano/extension/forms.js'),'utf8'),worker);
const inspect=()=>worker.formRequest({action:'form_inspect',tabId:7,observationId:'observation-1'});
const fill=fields=>worker.formRequest({action:'form_fill',tabId:7,documentId:'document-1',observationId:'observation-1',receiptId:'receipt-1',fields});
const verify=()=>worker.formRequest({action:'form_verify',tabId:7,documentId:'document-1',receiptId:'receipt-1'});
function allFields() {
  const fields=[];
  for(let i=0;i<30;i++)fields.push({element_id:'f'+(i*2+1),operation:'select',value:i%4===0?'1':'8'},
    {element_id:'f'+(i*2+2),operation:'fill',value:`Synthetic note for outcome ${i+1}.`});
  fields.push({element_id:'f61',operation:'fill',value:'03:00 pm'},
    {element_id:'f62',operation:'fill',value:'10:00 pm'},
    {element_id:'f63',operation:'check',checked:true},
    {element_id:'f64',operation:'fill',value:'Synthetic seven-hour shift summary.'});
  return fields;
}
(async()=>{
  reset();let pageInfo=await inspect();assert.equal(pageInfo.fields.length,64);
  assert.equal(pageInfo.fields[0].options[1].value,'1');assert.equal(pageInfo.fields[1].context,'Support outcome 1');
  let result=await fill(allFields());assert.equal(result.matched,64);assert.equal(result.all_matched,true);
  assert.equal(result.submitted,false);assert.equal(nodes[61].value,'10:00 pm');assert.equal(nodes[62].checked,true);
  nodes[1]._value='Changed by the user';result=await verify();assert.equal(result.matched,63);assert.equal(result.all_matched,false);
  const beforeReplay=writes;result=await fill(allFields());assert.equal(result.blocked,true);assert.equal(writes,beforeReplay);
  reset();await inspect();let bad=allFields();bad[63].value='x'.repeat(3001);
  result=await fill(bad);assert.equal(result.blocked,true);assert.equal(writes,0); // Entire batch prechecked.
  reset();await inspect();nodes[3]._value='User changed this';result=await fill(allFields());assert.equal(result.blocked,true);assert.equal(writes,0);
  reset();await inspect();nodes[0].options[1].value='different';result=await fill(allFields());assert.equal(result.blocked,true);assert.equal(writes,0);
  reset();await inspect();documentId='replacement-document';result=await fill(allFields());assert.equal(result.blocked,true);assert.equal(writes,0);
  reset();await inspect();url='https://fixture.test/other';result=await fill(allFields());assert.equal(result.blocked,true);assert.equal(writes,0);
  reset();await inspect();cancelAfterChunk=true;result=await fill(allFields());assert.equal(result.cancelled,true);assert.equal(result.applied,8);assert.equal(writes,8);
  reset();await inspect();nodes[0].onInput=()=>{nodes[1]._value='Framework overrode this field'};
  result=await fill(allFields());assert.equal(result.blocked,true);assert.equal(result.uncertain,true);assert.ok(writes>0);
  reset();nodes.push(new Field('INPUT','password','password','Password'));await assert.rejects(inspect());assert.equal(writes,0);
  console.log('64-field form fixture passed: dropdowns, text, radio, exact readback, no submit, preflight, stale document/URL, cancellation, uncertain partial writes, and password protection.');
})().catch(error=>{console.error(error);process.exitCode=1});
