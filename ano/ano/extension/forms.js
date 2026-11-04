// Typed, bounded form operations. No script strings or arbitrary selectors from the model.
let formGeneration = 0;

async function formRequest(message) {
  if (message.action === 'form_cancel') { formGeneration++; return { cancelled: true }; }
  const epoch = formGeneration;
  const tab = await chrome.tabs.get(message.tabId);
  if (!/^https?:$/.test(new URL(tab.url).protocol)) throw Error('Use an ordinary website tab.');
  const target = { tabId: message.tabId };
  if (message.action !== 'form_inspect') {
    if (!message.documentId) throw Error('Inspect this form first.');
    target.documentIds = [message.documentId];
  }
  async function execute(phase, offset=0) {
    const [frame] = await chrome.scripting.executeScript({
      target, args: [phase, message, offset],
      func: (phase, request, offset) => {
        const visible = el => el.isConnected && el.getBoundingClientRect().width > 0 &&
          el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility !== 'hidden' &&
          getComputedStyle(el).display !== 'none';
        const secret = el => /password|one-time-code|cc-|transaction/i.test(el.autocomplete || '') ||
          /password|passwd|secret|token|credit.?card|cvv|cvc/i.test((el.name || '')+' '+(el.id || ''));
        const protectedPage = () => [...document.querySelectorAll('input[type="password"],input[autocomplete="one-time-code"],iframe[src*="captcha"]')].some(visible);
        const state = el => ['checkbox','radio'].includes(el.type) ? el.checked : el.value;
        const descriptor = el => ({tag:el.tagName.toLowerCase(),type:el.type || '',dom_id:el.id || '',
          name:el.name || '',label:(el.getAttribute('aria-label') || el.labels?.[0]?.innerText ||
            el.getAttribute('placeholder') || el.name || el.id || '').trim().slice(0,200),
          context:(el.closest('tr')?.cells?.[0]?.innerText || '').trim().slice(0,700),
          options:el.tagName==='SELECT' ? [...el.options].map(o=>({label:o.text,value:o.value,disabled:o.disabled})).slice(0,150) : undefined,
          max_length:el.maxLength > 0 ? el.maxLength :
            (Number(el.parentElement?.innerText?.match(/Maximum\s+(\d+)\s+characters/i)?.[1]) || 4000)});
        const supported = el => !el.disabled && !el.readOnly && el.getAttribute('aria-disabled')!=='true' &&
          !secret(el) && (['TEXTAREA','SELECT'].includes(el.tagName) || (el.tagName==='INPUT' &&
          ['text','search','email','tel','url','number','date','time','datetime-local','radio','checkbox'].includes(el.type)));
        if (protectedPage()) {
          delete globalThis.__anoForm;
          throw Error('Login, verification, or CAPTCHA page: use the approved vault or ask the user.');
        }
        if (phase === 'inspect') {
          const nodes = [...document.querySelectorAll('input,textarea,select')].filter(el=>visible(el)&&supported(el));
          const elements = nodes.slice(0,400);
          const items = elements.map((el,i)=>({id:'f'+(i+1),...descriptor(el),value:state(el)}));
          globalThis.__anoForm = {id:request.observationId,url:location.href,at:Date.now(),elements,items};
          return {title:document.title.slice(0,200),url:location.href,context:(document.body?.innerText || '').slice(0,3500),
            fields:items,truncated:nodes.length>elements.length};
        }
        const saved = globalThis.__anoForm;
        if (!saved || saved.url!==location.href || Date.now()-saved.at>120000) throw Error('The form changed or expired. Inspect again.');
        const resolve = field => {
          const i=saved.items.findIndex(item=>item.id===field.element_id);
          const el=saved.elements[i], item=saved.items[i];
          if (!el || !visible(el) || !supported(el) || JSON.stringify(descriptor(el))!==JSON.stringify((({id,value,...rest})=>rest)(item))) {
            throw Error('A form field changed. Inspect again before continuing.');
          }
          return {el,item};
        };
        const report = () => {
          const receipt=saved.receipt;
          if (!receipt || receipt.id!==request.receiptId) throw Error('No matching form verification receipt.');
          const fields=receipt.fields.map(field=>{
            try {
              const {el}=resolve(field);
              return {id:field.element_id,matched:state(el)===field.expected,valid:el.validity?.valid!==false};
            } catch (_) {return {id:field.element_id,matched:false,valid:false};}
          });
          return {fields,total:fields.length,matched:fields.filter(f=>f.matched).length,
            all_matched:fields.every(f=>f.matched&&f.valid),applied:receipt.applied,submitted:false};
        };
        if (phase==='prepare') {
          if (saved.id!==request.observationId || saved.consumed) throw Error('Inspect again before another form batch.');
          saved.consumed=true;
          if (!Array.isArray(request.fields) || !request.fields.length || request.fields.length>100) throw Error('Use 1 to 100 fields.');
          const used=new Set(), radios=new Set();
          const fields=request.fields.map(field=>{
            if (used.has(field.element_id)) throw Error('A field was listed twice.');
            used.add(field.element_id);
            const {el,item}=resolve(field);
            if (state(el)!==item.value) throw Error('A field was edited since inspection. Inspect again.');
            let expected;
            if (field.operation==='fill') {
              if (!['INPUT','TEXTAREA'].includes(el.tagName) || ['checkbox','radio'].includes(el.type) ||
                  typeof field.value!=='string' || field.value.length>Math.min(item.max_length,4000)) throw Error('Invalid text field or text length.');
              expected=field.value;
            } else if (field.operation==='select') {
              if (el.tagName!=='SELECT' || typeof field.value!=='string' ||
                  ![...el.options].some(o=>o.value===field.value&&!o.disabled)) throw Error('Choose an enabled option value from inspection.');
              expected=field.value;
            } else if (field.operation==='check') {
              if (!['checkbox','radio'].includes(el.type) || typeof field.checked!=='boolean') throw Error('Use a boolean for checkboxes or radios.');
              if (el.type==='radio' && field.checked && el.name) {
                if (radios.has(el.name)) throw Error('Choose only one radio in a group.');
                radios.add(el.name);
              }
              expected=field.checked;
            } else throw Error('Use fill, select, or check. This tool never submits.');
            return {...field,expected};
          });
          saved.receipt={id:request.receiptId,fields,applied:0};
          return {prepared:fields.length};
        }
        if (phase==='apply') {
          const receipt=saved.receipt;
          if (!receipt || receipt.id!==request.receiptId || receipt.applied!==offset) throw Error('Batch is out of sequence. Do not replay it.');
          for (const field of receipt.fields.slice(offset,offset+8)) {
            if (protectedPage() || saved.url!==location.href) throw Error('The page changed during form filling.');
            const {el,item}=resolve(field);
            if (state(el)!==item.value && state(el)!==field.expected) throw Error('A field changed during the batch. Inspect before retrying.');
            const proto=el.tagName==='TEXTAREA' ? HTMLTextAreaElement.prototype :
              el.tagName==='SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
            const property=field.operation==='check' ? 'checked' : 'value';
            Object.getOwnPropertyDescriptor(proto,property).set.call(el,field.expected);
            // Mark delivery before page handlers run; an exception must not cause an automatic replay.
            receipt.applied++;
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
            el.dispatchEvent(new Event('blur',{bubbles:false}));
          }
          return {applied:receipt.applied};
        }
        if (phase==='verify') return report();
        throw Error('Unsupported form action.');
      },
    });
    if (!frame?.result || !frame.documentId) throw Error('The form did not answer.');
    return {...frame.result,documentId:frame.documentId};
  }
  if (message.action==='form_inspect') return execute('inspect');
  if (message.action==='form_verify') return execute('verify');
  if (message.action!=='form_fill') throw Error('Unknown form action.');
  let applied=0;
  try {
    await execute('prepare'); // Validate the entire batch before writing any field.
    for (let offset=0;offset<message.fields.length;offset+=8) {
      if (epoch!==formGeneration) return {cancelled:true,applied,submitted:false};
      const chunk=await execute('apply',offset);
      applied=chunk.applied;
    }
    if (epoch!==formGeneration) return {cancelled:true,applied,submitted:false};
    return await execute('verify');
  } catch (_) {
    // Do not forward page exceptions, which could contain private field values.
    return {blocked:true,applied,uncertain:true,submitted:false,
      reason:'The form changed or a field could not be applied. Inspect current values before retrying; do not replay the batch.'};
  }
}
