/* ── ScorPred — UI Interactions ─────────────────────────────────────────────── */

/* Premium once-per-session intro loader */
(function initScorPredIntroLoader() {
  const loader = document.getElementById('scorpredIntroLoader');
  if (!loader) return;

  const storageKey = 'scorpredIntroSeen';
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  function setSeen() {
    try {
      sessionStorage.setItem(storageKey, '1');
    } catch (error) {
      /* Ignore private-mode storage failures; the loader will still close. */
    }
  }

  function removeLoader(delay = 0) {
    window.setTimeout(() => {
      loader.classList.add('is-hiding');
      setSeen();
      window.setTimeout(() => {
        loader.remove();
        document.documentElement.classList.add('sp-loader-seen');
      }, reducedMotion ? 0 : 650);
    }, delay);
  }

  try {
    if (sessionStorage.getItem(storageKey) === '1') {
      loader.remove();
      document.documentElement.classList.add('sp-loader-seen');
      return;
    }
  } catch (error) {
    removeLoader(0);
    return;
  }

  const media = loader.querySelector('.sp-intro-loader__media');
  if (media) {
    media.addEventListener('error', () => {
      loader.classList.add('sp-intro-loader--asset-missing');
    }, { once: true });
  }

  const delay = reducedMotion ? 120 : 2600;
  if (document.readyState === 'complete') {
    removeLoader(delay);
  } else {
    window.addEventListener('load', () => removeLoader(delay), { once: true });
  }
})();

/* ── Universal tab system ──────────────────────────────────────────────────── */
document.querySelectorAll('[data-sp-tabs]').forEach(tabGroup => {
  const tabs   = tabGroup.querySelectorAll('.sp-tab');
  const panels = tabGroup.querySelectorAll(':scope > .sp-tab-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      panels.forEach(p => {
        p.classList.toggle('active', p.dataset.panel === target);
      });
    });
  });
});

/* Guard: only run when GSAP is available */
if (typeof gsap !== 'undefined') {

  gsap.registerPlugin(ScrollTrigger);

  /* ── Card entrance animations ─────────────────────────────────────────────── */
  const cardSelectors = '.glow-card, .sp-panel, .sp-fixture, .sp-kpi, .sp-model-card, .sp-action-card, .sp-why-card, .sp-decision-card, .sp-empty-state, .sp-info-card, .sp-result-card';
  gsap.utils.toArray(cardSelectors).forEach((card, i) => {
    gsap.fromTo(card,
      { opacity: 0, y: 24 },
      {
        opacity: 1,
        y: 0,
        duration: 0.5,
        delay: Math.min(i * 0.04, 0.6),
        ease: 'power3.out',
        scrollTrigger: {
          trigger: card,
          start: 'top 92%',
          once: true
        }
      }
    );
  });

}

/* ── Animated stat counters ────────────────────────────────────────────────── */
function animateCounter(el) {
  const target = parseFloat(el.dataset.target);
  if (isNaN(target)) return;
  const isDecimal = target % 1 !== 0;
  let current = 0;
  const increment = target / 60;

  const timer = setInterval(() => {
    current += increment;
    if (current >= target) {
      current = target;
      clearInterval(timer);
    }
    el.textContent = isDecimal ? current.toFixed(1) : Math.floor(current);
  }, 16);
}

const counterObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      animateCounter(entry.target);
      counterObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.2 });

document.querySelectorAll('.stat-number[data-target]').forEach(el => {
  counterObserver.observe(el);
});

/* ── Probability bars: animate fill on scroll into view ─────────────────────── */
const barObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      const fill = entry.target.querySelector('.prob-bar-fill');
      if (fill) {
        setTimeout(() => {
          fill.style.width = (fill.dataset.width || '0') + '%';
        }, 200);
      }
      barObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.prob-bar').forEach(bar => {
  barObserver.observe(bar);
});

const decisionBarObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.querySelectorAll('.sp-confidence__fill').forEach(fill => {
        const width = fill.dataset.width || '0';
        requestAnimationFrame(() => {
          fill.style.width = `${width}%`;
        });
      });
      decisionBarObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.15 });

document.querySelectorAll('.sp-confidence').forEach(bar => {
  decisionBarObserver.observe(bar);
});

/* Also animate existing win-fill bars (prediction page) */
const winFillObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.querySelectorAll('.win-fill, .fpred-prob-fill').forEach(fill => {
        const w = fill.style.width;
        fill.style.width = '0%';
        requestAnimationFrame(() => {
          setTimeout(() => { fill.style.width = w; }, 150);
        });
      });
      winFillObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.win-probs, .fpred-probs').forEach(el => {
  winFillObserver.observe(el);
});

/* ── Shimmer → real content swap ─────────────────────────────────────────────── */
document.querySelectorAll('.shimmer-wrap').forEach(wrap => {
  const content = wrap.querySelector('.shimmer-content');
  if (content && content.innerHTML.trim() !== '') {
    wrap.classList.remove('shimmer');
  }
});

/* ── Mobile nav toggle + bottom nav Menu button ─────────────────────────────── */
const navToggle    = document.getElementById('navToggle');
const mobileMenuBtn = document.getElementById('mobileMenuBtn');
const sidebar      = document.querySelector('.sp-sidebar');

if (sidebar) {
  const overlay = document.createElement('div');
  overlay.className = 'sp-sidebar-overlay';
  document.body.appendChild(overlay);

  const openSidebar = () => {
    sidebar.classList.add('open');
    overlay.classList.add('visible');
    if (navToggle) navToggle.setAttribute('aria-expanded', 'true');
    if (mobileMenuBtn) mobileMenuBtn.setAttribute('aria-expanded', 'true');
  };
  const closeSidebar = () => {
    sidebar.classList.remove('open');
    overlay.classList.remove('visible');
    if (navToggle) navToggle.setAttribute('aria-expanded', 'false');
    if (mobileMenuBtn) mobileMenuBtn.setAttribute('aria-expanded', 'false');
  };

  if (navToggle) {
    navToggle.addEventListener('click', () => {
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });
  }

  if (mobileMenuBtn) {
    mobileMenuBtn.addEventListener('click', () => {
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });
  }

  overlay.addEventListener('click', closeSidebar);

  document.addEventListener('click', e => {
    if (
      sidebar.classList.contains('open') &&
      !sidebar.contains(e.target) &&
      e.target !== navToggle &&
      e.target !== mobileMenuBtn &&
      !mobileMenuBtn?.contains(e.target)
    ) {
      closeSidebar();
    }
  });
}

/* ── Legacy tab compat (old .tab-btn / .nba-tab) ────────────────────────────── */
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    btn.closest('.tab-bar')
       ?.querySelectorAll('.tab-btn')
       .forEach(b => b.classList.remove('tab-active'));
    btn.classList.add('tab-active');
  });
});
document.querySelectorAll('.nba-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    btn.closest('.nba-tabs')
       ?.querySelectorAll('.nba-tab')
       .forEach(b => b.classList.remove('tab-active'));
    btn.classList.add('tab-active');
  });
});

/* Chat bubble */
const chatToggle = document.getElementById('chat-toggle');
const chatWindow = document.getElementById('chat-window');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatMessages = document.getElementById('chat-messages');
const chatClear = document.getElementById('chat-clear');
const chatSuggestions = document.getElementById('chat-suggestions');
const defaultChatSuggestions = [
  'Why was this team favored?',
  'Why did this parlay lose?',
  'What does this confidence mean?'
];

function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

function csrfHeaders(extra = {}) {
  const token = getCsrfToken();
  return token ? { ...extra, 'X-CSRF-Token': token } : extra;
}

document.querySelectorAll('form').forEach(form => {
  const method = (form.getAttribute('method') || 'GET').toUpperCase();
  if (method !== 'POST') return;
  if (form.querySelector('input[name="csrf_token"]')) return;
  const token = getCsrfToken();
  if (!token) return;
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = 'csrf_token';
  input.value = token;
  form.appendChild(input);
});

function appendChatMessage(role, text) {
  if (!chatMessages || !text) return;
  const row = document.createElement('div');
  row.className = `sp-chat-message ${role}`;
  row.textContent = text;
  chatMessages.appendChild(row);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderChatSuggestions(suggestions) {
  if (!chatSuggestions) return;
  const items = Array.isArray(suggestions) && suggestions.length ? suggestions : defaultChatSuggestions;
  chatSuggestions.innerHTML = '';
  items.slice(0, 3).forEach((text) => {
    if (!text) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-suggestion';
    button.textContent = text;
    chatSuggestions.appendChild(button);
  });
}

renderChatSuggestions(defaultChatSuggestions);

if (chatToggle && chatWindow) {
  chatToggle.addEventListener('click', () => {
    const open = chatWindow.classList.toggle('open');
    chatWindow.setAttribute('aria-hidden', open ? 'false' : 'true');
    chatToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open && chatInput) chatInput.focus();
  });
}

if (chatForm && chatInput) {
  chatForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;
    appendChatMessage('user', message);
    chatInput.value = '';
    try {
      const response = await fetch('/chat', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/x-www-form-urlencoded' }),
        body: new URLSearchParams({
          message,
          csrf_token: getCsrfToken(),
          page_path: window.location.pathname,
          page_title: document.title
        }).toString()
      });
      const data = await response.json();
      appendChatMessage('assistant', data.reply || data.error || 'No reply available.');
      renderChatSuggestions(data.suggestions);
    } catch (error) {
      appendChatMessage('assistant', 'Chat is temporarily unavailable.');
      renderChatSuggestions(defaultChatSuggestions);
    }
  });
}

if (chatSuggestions && chatInput) {
  chatSuggestions.addEventListener('click', (event) => {
    const button = event.target.closest('.chat-suggestion');
    if (!button) return;
    chatInput.value = button.textContent || '';
    chatInput.focus();
  });
}

if (chatClear) {
  chatClear.addEventListener('click', async () => {
    try {
      await fetch('/chat/clear', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/x-www-form-urlencoded' }),
        body: new URLSearchParams({ csrf_token: getCsrfToken() }).toString()
      });
    } catch (error) {
      console.error(error);
    }
    if (chatMessages) {
      chatMessages.innerHTML = '<div class="sp-chat-message assistant">Ask about predictions, props, injuries, or where to find a page.</div>';
    }
    renderChatSuggestions(defaultChatSuggestions);
  });
}
