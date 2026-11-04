// Runs only in the extension's isolated world; page scripts cannot access saved node references.
async function workflowRequest(message) {
  const tab = await chrome.tabs.get(message.tabId);
  if (!/^https?:$/.test(new URL(tab.url).protocol)) throw Error('Use an ordinary website tab.');
  const target = { tabId: message.tabId };
  if (message.action === 'workflow_act') {
    if (!message.documentId) throw Error('Inspect the page first.');
    target.documentIds = [message.documentId];
  }
  const [frame] = await chrome.scripting.executeScript({
    target,
    args: [message.action, message.observationId || '', message.elementId || '', message.operation || '', message.text || ''],
    func: (action, observationId, elementId, operation, text) => {
      const visible = el => el.isConnected && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
        el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0 &&
        getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
      const protectedPage = () => [...document.querySelectorAll('input[type="password"],input[autocomplete="one-time-code"],iframe[src*="captcha"]')].some(visible);
      const describe = el => ({ tag: el.tagName.toLowerCase(), type: el.type || '',
        label: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') ||
          el.labels?.[0]?.innerText || el.getAttribute('name') || '').trim().slice(0, 180),
        href: el.tagName === 'A' ? el.href : '' });
      if (protectedPage()) {
        delete globalThis.__anoWorkflow;
        throw Error('Login, verification, or CAPTCHA page: hand this step to the user or the approved vault.');
      }
      if (action === 'workflow_inspect') {
        const elements = [...document.querySelectorAll('a[href],button,input,textarea,select,[role="button"]')]
          .filter(visible).slice(0, 150);
        const items = elements.map((el, i) => ({ id: String(i + 1), ...describe(el) }));
        globalThis.__anoWorkflow = { id: observationId, url: location.href, at: Date.now(), elements, items };
        return { title: document.title.slice(0, 200), url: location.href,
          text: (document.body?.innerText || '').slice(0, 18000), elements: items };
      }
      const saved = globalThis.__anoWorkflow;
      delete globalThis.__anoWorkflow; // Every attempt consumes its observation, including errors.
      if (!saved || saved.id !== observationId || saved.url !== location.href || Date.now() - saved.at > 120000) {
        throw Error('The page observation expired or changed. Inspect again.');
      }
      const index = Number(elementId) - 1;
      const el = saved.elements[index];
      if (!el || !visible(el) || JSON.stringify(describe(el)) !== JSON.stringify((({id, ...rest}) => rest)(saved.items[index]))) {
        throw Error('The observed element changed. Inspect again.');
      }
      if (operation === 'click') {
        if (el.tagName === 'INPUT' && !['submit', 'button', 'checkbox', 'radio'].includes(el.type)) throw Error('This is not a clickable control.');
        if (el.tagName === 'A' && !/^https?:$/.test(new URL(el.href).protocol)) throw Error('Only web links are supported.');
        el.click();
      } else if (operation === 'fill') {
        if (!['INPUT', 'TEXTAREA'].includes(el.tagName) || el.readOnly ||
            (el.tagName === 'INPUT' && !['text', 'search', 'url', 'email', 'tel'].includes(el.type)) ||
            /password|one-time-code|cc-|transaction|new-password/i.test(el.autocomplete || '') || text.length > 4000) {
          throw Error('Use a normal text field; credentials and payment fields are excluded.');
        }
        const prototype = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value').set.call(el, text);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else throw Error('Unsupported browser operation.');
      return { performed: true }; // Dispatch is not proof of success. The caller must inspect again.
    },
  });
  if (!frame?.documentId || !frame.result) throw Error('The page did not answer. Inspect before retrying.');
  return { ...frame.result, documentId: frame.documentId };
}
