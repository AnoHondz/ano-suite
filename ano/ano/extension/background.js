// Connects to Ano on this PC only (127.0.0.1) and answers its tab requests.
// config.js is written by `python -m ano.configure browser` and holds the port and a private token.
importScripts('config.js');
importScripts('workflow.js');
importScripts('forms.js');

let socket = null;

function browserName() {
  if (navigator.brave) return 'brave';
  const brands = (navigator.userAgentData && navigator.userAgentData.brands || []).map(b => b.brand);
  if (brands.includes('Microsoft Edge')) return 'edge';
  if (brands.includes('Google Chrome')) return 'chrome';
  return 'chromium';
}

function connect() {
  if (socket && socket.readyState <= WebSocket.OPEN) return;
  socket = new WebSocket(`ws://127.0.0.1:${ANO.port}`);
  socket.onopen = () => socket.send(JSON.stringify({ type: 'hello', token: ANO.token, browser: browserName(),
    version: chrome.runtime.getManifest().version }));
  socket.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'ping') return;
    try {
      socket.send(JSON.stringify({ id: message.id, result: await handle(message) }));
    } catch (error) {
      socket.send(JSON.stringify({ id: message.id, error: message.action === 'fill_login' ? 'The approved login could not be filled.' : String(error && error.message || error) }));
    }
  };
  socket.onclose = () => { socket = null; };
}

async function handle(message) {
  switch (message.action) {
    case 'form_inspect':
    case 'form_fill':
    case 'form_verify':
    case 'form_cancel':
      return formRequest(message);
    case 'workflow_inspect':
    case 'workflow_act':
      return workflowRequest(message);
    case 'list': {
      const tabs = await chrome.tabs.query({});
      return tabs.map(tab => ({
        id: tab.id, windowId: tab.windowId, title: tab.title || '', url: tab.url || '', active: tab.active,
        audible: !!tab.audible, muted: !!(tab.mutedInfo && tab.mutedInfo.muted), pinned: tab.pinned,
        lastAccessed: tab.lastAccessed || null,
      }));
    }
    case 'open': {
      const url = new URL(message.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw Error('Use an ordinary website URL.');
      const tab = await chrome.tabs.create({ url: url.href, active: true });
      return { id: tab.id, url: tab.url || url.href };
    }
    case 'navigate': {
      const url = new URL(message.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw Error('Use an ordinary website URL.');
      const tab = await chrome.tabs.update(message.tabId, { url: url.href });
      return { id: tab.id, dispatched: true };
    }
    case 'close':
      await chrome.tabs.remove(message.tabIds);
      return { closed: message.tabIds.length };
    case 'activate': {
      const tab = await chrome.tabs.update(message.tabId, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      return { title: tab.title };
    }
    case 'mute':
      await chrome.tabs.update(message.tabId, { muted: message.muted });
      return { muted: message.muted };
    case 'read': {
      const [frame] = await chrome.scripting.executeScript({
        target: { tabId: message.tabId },
        func: () => ({ title: document.title, url: location.href, text: document.body ? document.body.innerText : '' }),
      });
      return frame.result;
    }
    case 'login_context': {
      const tab = await chrome.tabs.get(message.tabId);
      const url = new URL(tab.url);
      if (url.protocol !== 'https:') throw new Error('Logins require HTTPS.');
      const [frame] = await chrome.scripting.executeScript({
        target: { tabId: message.tabId },
        args: [!!message.submit],
        func: (requireSubmit) => {
          const fields = [...document.querySelectorAll('input[type="password"]')].filter(field =>
            !field.disabled && !field.readOnly && field.getBoundingClientRect().width > 0 &&
            field.getBoundingClientRect().height > 0 && getComputedStyle(field).visibility !== 'hidden' &&
            getComputedStyle(field).display !== 'none');
          const visible = el => el.isConnected && !el.disabled && el.getBoundingClientRect().width > 0 &&
            el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          let canSubmit = false;
          if (requireSubmit && fields.length === 1) {
            const form = fields[0].form;
            const protectedStep = [...document.querySelectorAll('input[autocomplete="one-time-code"],iframe[src*="captcha"],iframe[src*="challenges.cloudflare.com"],[data-sitekey]')].some(visible);
            const buttons = form ? [...form.querySelectorAll('button[type="submit"],button:not([type]),input[type="submit"]')].filter(visible) : [];
            if (form && buttons.length === 1 && !protectedStep) {
              const button = buttons[0];
              const action = button.getAttribute?.('formaction') || form.action || location.href;
              const method = button.getAttribute?.('formmethod') || form.method;
              canSubmit = method.toLowerCase() === 'post' && new URL(action, location.href).origin === location.origin &&
                typeof form.requestSubmit === 'function' && (!button.form || button.form === form);
            }
          }
          return { origin: location.origin, count: fields.length, newPassword: fields.some(f => f.autocomplete === 'new-password'), canSubmit };
        },
      });
      if (!frame || !frame.documentId || !frame.result || frame.result.origin !== url.origin ||
          frame.result.count !== 1 || frame.result.newPassword) throw new Error('Use a main-page login form with one password field.');
      return { origin: url.origin, documentId: frame.documentId, canSubmit: !!frame.result.canSubmit };
    }
    case 'fill_login': {
      const tab = await chrome.tabs.get(message.tabId);
      if (new URL(tab.url).origin !== message.origin || !message.origin.startsWith('https://') || !message.documentId) {
        throw new Error('The approved website changed.');
      }
      const [frame] = await chrome.scripting.executeScript({
        target: { tabId: message.tabId, documentIds: [message.documentId] },
        args: [message.origin, message.username, message.password, !!message.submit],
        func: (approvedOrigin, username, password, submit) => {
          if (location.origin !== approvedOrigin || location.protocol !== 'https:') return { filled: false };
          const visible = field => !field.disabled && !field.readOnly &&
            field.getBoundingClientRect().width > 0 && field.getBoundingClientRect().height > 0 &&
            getComputedStyle(field).visibility !== 'hidden' && getComputedStyle(field).display !== 'none';
          const passwords = [...document.querySelectorAll('input[type="password"]')].filter(visible);
          if (passwords.length !== 1 || passwords[0].autocomplete === 'new-password') return { filled: false };
          const pw = passwords[0];
          const scope = pw.form || document;
          const submitTarget = () => {
            if (!pw.form || pw.form !== scope || scope.method.toLowerCase() !== 'post' ||
                new URL(scope.action || location.href).origin !== approvedOrigin || typeof scope.requestSubmit !== 'function') return null;
            if ([...document.querySelectorAll('input[autocomplete="one-time-code"],iframe[src*="captcha"],iframe[src*="challenges.cloudflare.com"],[data-sitekey]')].some(visible)) return null;
            const buttons = [...scope.querySelectorAll('button[type="submit"],button:not([type]),input[type="submit"]')].filter(visible);
            if (buttons.length !== 1) return null;
            const button = buttons[0];
            const action = button.getAttribute?.('formaction') || scope.action || location.href;
            const method = button.getAttribute?.('formmethod') || scope.method;
            if (method.toLowerCase() !== 'post' || new URL(action, location.href).origin !== approvedOrigin || (button.form && button.form !== scope)) return null;
            return button;
          };
          if (submit && !submitTarget()) return { filled: false, submitted: false };
          const user = [...scope.querySelectorAll('input[autocomplete="username"],input[type="email"],input[type="text"]')].find(visible);
          const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          if (user) {
            set.call(user, username);
            user.dispatchEvent(new Event('input', { bubbles: true }));
            user.dispatchEvent(new Event('change', { bubbles: true }));
          }
          // Re-check after username handlers, which could replace or change the password field.
          if (!pw.isConnected || pw.type !== 'password' || !visible(pw) || location.origin !== approvedOrigin) return { filled: false };
          set.call(pw, password);
          pw.dispatchEvent(new Event('input', { bubbles: true }));
          pw.dispatchEvent(new Event('change', { bubbles: true }));
          if (submit) {
            const button = submitTarget();
            if (!button || !pw.isConnected || pw.type !== 'password' || !visible(pw) || location.origin !== approvedOrigin ||
                (typeof scope.checkValidity === 'function' && !scope.checkValidity())) return { filled: true, submitted: false };
            scope.requestSubmit(button);
            return { filled: true, submitted: true };
          }
          return { filled: true }; // Never return field values or submit the form.
        },
      });
      return { filled: !!(frame && frame.result && frame.result.filled), submitted: !!(frame && frame.result && frame.result.submitted) };
    }
    default:
      throw new Error('Unknown action');
  }
}

// Service workers sleep when idle; the alarm reconnects and Ano's pings keep the connection alive.
chrome.alarms.create('ano-connect', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
