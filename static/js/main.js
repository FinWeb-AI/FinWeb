document.addEventListener('DOMContentLoaded', () => {
  /* 這邊各種行情得功能 */
  const stockForm   = document.getElementById('stock-form');
  const stockResult = document.getElementById('stock-result');
  if (stockForm) {
    stockForm.addEventListener('submit', async e => {
      e.preventDefault();
      const symbol = document.getElementById('stock-symbol').value.trim();
      if (!symbol) return;
      stockResult.textContent = 'Loading…';
      const res = await fetch(`/api/stock?symbol=${symbol}`);
      stockResult.textContent = JSON.stringify(await res.json(), null, 2);
    });
  }

  const cryptoForm   = document.getElementById('crypto-form');
  const cryptoResult = document.getElementById('crypto-result');
  if (cryptoForm) {
    cryptoForm.addEventListener('submit', async e => {
      e.preventDefault();
      const id = document.getElementById('crypto-id').value.trim();
      if (!id) return;
      cryptoResult.textContent = 'Loading…';
      const res = await fetch(`/api/crypto?id=${id}`);
      cryptoResult.textContent = JSON.stringify(await res.json(), null, 2);
    });
  }

  /* 這邊是ai助理的頁面，未來會引入Gemini */
  const toggleBtn = document.getElementById('chat-toggle');
  const panel     = document.getElementById('chat-panel');
  const closeBtn  = document.getElementById('chat-close');

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      panel.style.display = 'flex';
      panel.classList.remove('animate__fadeOutDown');
      panel.classList.add('animate__fadeInUp');
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      panel.classList.remove('animate__fadeInUp');
      panel.classList.add('animate__fadeOutDown');
      panel.addEventListener('animationend', () => {
        panel.style.display = 'none';
      }, { once: true });
    });
  }
});