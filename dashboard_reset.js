/* Visual matcher UI bootstrap.
 * Load this after app.js while validating a fresh run.
 * It prevents stale AI verdicts from being presented as current results.
 */
(function () {
  'use strict';
  const verdictSelectors = '.ak-ai-badge, .ak-card-ai-reason, .ak-card-critic-note, .ak-card-ai-metrics';

  function clearStaleVerdicts() {
    document.querySelectorAll(verdictSelectors).forEach((node) => node.remove());
    document.querySelectorAll('.ak-side-card').forEach((card) => {
      card.classList.remove('ai-match', 'ai-mismatch', 'invalid-flagged');
      const button = card.querySelector('.ak-validation-toggle-btn');
      if (button) {
        button.className = 'ak-validation-toggle-btn';
        button.innerHTML = '<i class="fa-regular fa-circle"></i> Not checked';
        button.title = 'Run Visual Check first';
      }
    });
  }

  function simplifyControls() {
    // Keep one verification action: the gallery-level AI Check Photos button.
    const batch = document.getElementById('batchAiCheckAllBtn');
    if (batch) batch.remove();
    const headerChecks = document.querySelectorAll('.ak-header-buttons #runAiAutoCheckBtn');
    headerChecks.forEach((button) => button.remove());
  }

  window.addEventListener('DOMContentLoaded', () => {
    clearStaleVerdicts();
    simplifyControls();
    const observer = new MutationObserver(() => {
      simplifyControls();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
})();
