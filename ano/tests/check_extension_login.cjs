// Synthetic DOM and Chrome API fixtures; no browser or real credentials are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
let site = 'https://example.com', documentId = 'doc1', submissions = 0;
class Input {
  constructor(type) { this.type = type; this._value = ''; this.autocomplete = ''; this.disabled = false; this.readOnly = false; this.isConnected = true; }
  get value() { return this._value; }
  set value(value) { this._value = value; }
  getBoundingClientRect() { return { width: 100, height: 25 }; }
  dispatchEvent() { if (this.onInput) this.onInput(); }
}
let user = new Input('text'), password = new Input('password');
let protectedStep = false, extraSubmit = false;
const submitButton = new Input('submit');
const form = { method:'post', action:'https://example.com/login', checkValidity:()=>true,
  querySelectorAll: selector => selector.includes('button') ? (extraSubmit ? [submitButton,submitButton] : [submitButton]) : [user],
  submit: () => submissions++, requestSubmit:()=>submissions++ };
password.form = form;
const chrome = {
  tabs: { get: async () => ({url:site + '/login'}), create:async value=>({id:4,...value}), update:async (id,value)=>({id,...value}) },
  scripting: { executeScript: async request => {
    if (request.target.documentIds && !request.target.documentIds.includes(documentId)) throw Error('Document changed');
    return [{documentId, result:request.func(...(request.args || []))}];
  }},
  alarms: {create(){}, onAlarm:{addListener(){}}}, runtime:{onStartup:{addListener(){}}, onInstalled:{addListener(){}}},
};
const context = vm.createContext({ chrome, URL, JSON, navigator:{}, importScripts(){}, ANO:{port:1,token:'fixture'},
  WebSocket:class { constructor(){this.readyState=0;} },
  document:{querySelectorAll:selector=>selector.includes('one-time-code') ? (protectedStep ? [user] : []) : (password.type === 'password' ? [password] : [])},
  location:{get origin(){return site}, protocol:'https:'}, HTMLInputElement:Input,
  getComputedStyle:()=>({visibility:'visible',display:'block'}), Event:class {constructor(type){this.type=type;}} });
vm.runInContext(fs.readFileSync(path.join(__dirname,'../ano/extension/background.js'),'utf8'), context);
(async()=>{
  let info = await context.handle({action:'login_context',tabId:3});
  assert.equal(info.documentId, 'doc1');
  let result = await context.handle({action:'fill_login',tabId:3,origin:'https://example.com',documentId:'doc1',username:'dummy-user',password:'dummy-secret'});
  assert.equal(result.filled,true);
  assert.equal(password.value,'dummy-secret');
  assert.equal(submissions,0);
  password.value=''; site='https://example.com.evil.test';
  await assert.rejects(context.handle({action:'fill_login',tabId:3,origin:'https://example.com',documentId:'doc1',username:'x',password:'dummy-secret'}));
  assert.equal(password.value,'');
  site='https://example.com'; documentId='doc2';
  await assert.rejects(context.handle({action:'fill_login',tabId:3,origin:site,documentId:'doc1',username:'x',password:'dummy-secret'}));
  assert.equal(password.value,'');
  documentId='doc1'; user.onInput=()=>{password.type='text'};
  result=await context.handle({action:'fill_login',tabId:3,origin:site,documentId:'doc1',username:'x',password:'dummy-secret'});
  assert.equal(result.filled,false);
  assert.equal(password.value,'');
  user.onInput=null; password.type='password';
  info=await context.handle({action:'login_context',tabId:3,submit:true});
  assert.equal(info.canSubmit,true);
  result=await context.handle({action:'fill_login',tabId:3,origin:site,documentId:'doc1',username:'dummy-user',password:'dummy-secret',submit:true});
  assert.equal(result.submitted,true); assert.equal(submissions,1);
  assert.equal(JSON.stringify(result).includes('dummy-secret'),false);
  password.value=''; form.action='https://evil.test/collect';
  result=await context.handle({action:'fill_login',tabId:3,origin:site,documentId:'doc1',username:'x',password:'dummy-secret',submit:true});
  assert.equal(result.filled,false); assert.equal(password.value,''); assert.equal(submissions,1);
  form.action=site+'/login'; protectedStep=true;
  info=await context.handle({action:'login_context',tabId:3,submit:true}); assert.equal(info.canSubmit,false);
  result=await context.handle({action:'fill_login',tabId:3,origin:site,documentId:'doc1',username:'x',password:'dummy-secret',submit:true});
  assert.equal(result.filled,false); assert.equal(submissions,1); protectedStep=false;
  extraSubmit=true;
  info=await context.handle({action:'login_context',tabId:3,submit:true}); assert.equal(info.canSubmit,false); extraSubmit=false;
  submitButton.getAttribute=name=>name==='formaction'?'https://evil.test/collect':null;
  info=await context.handle({action:'login_context',tabId:3,submit:true}); assert.equal(info.canSubmit,false);
  result=await context.handle({action:'fill_login',tabId:3,origin:site,documentId:'doc1',username:'x',password:'dummy-secret',submit:true});
  assert.equal(result.filled,false); assert.equal(submissions,1);
  submitButton.getAttribute=name=>name==='formmethod'?'get':null;
  info=await context.handle({action:'login_context',tabId:3,submit:true}); assert.equal(info.canSubmit,false);
  submitButton.getAttribute=()=>null;
  form.method='get';
  info=await context.handle({action:'login_context',tabId:3,submit:true}); assert.equal(info.canSubmit,false);
  result=await context.handle({action:'open',url:'https://example.com/task'}); assert.equal(result.id,4);
  result=await context.handle({action:'navigate',tabId:3,url:'https://example.com/result'}); assert.equal(result.dispatched,true);
  await assert.rejects(context.handle({action:'navigate',tabId:3,url:'javascript:alert(1)'}));
  await assert.rejects(context.handle({action:'open',url:'https://user:secret@example.com'}));
  console.log('Extension: fill-only defaults, approved POST sign-in, exact origin/document, CAPTCHA/GET/ambiguous-submit rejection, and safe navigation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
