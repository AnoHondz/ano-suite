/**
 * charts.js — Global Chart.js defaults and shared utilities for ScorPred.
 * Chart.js is loaded from CDN in base.html before this file.
 */

// ── Global Chart.js defaults ───────────────────────────────────────────────
if (typeof Chart !== 'undefined') {
  Chart.defaults.color = '#8b949e';
  Chart.defaults.borderColor = '#21262d';
  Chart.defaults.font.family = "'Inter', sans-serif";
  Chart.defaults.plugins.tooltip.backgroundColor = '#1a2332';
  Chart.defaults.plugins.tooltip.borderColor = '#21262d';
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.titleColor = '#e6edf3';
  Chart.defaults.plugins.tooltip.bodyColor = '#8b949e';
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
}

// ── Mobile nav toggle ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('navToggle');
  const links  = document.querySelector('.nav-links');
  if (toggle && links) {
    toggle.addEventListener('click', () => links.classList.toggle('open'));
  }
});

// ── Utility: animate progress bars on page load ────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.win-fill, .score-pred-fill').forEach(el => {
    const target = el.style.width;
    el.style.width = '0';
    requestAnimationFrame(() => {
      requestAnimationFrame(() => { el.style.width = target; });
    });
  });
});
