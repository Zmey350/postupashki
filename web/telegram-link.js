/* Include with defer. Example: <a data-telegram-bot="your_bot">Написать менеджеру</a> */
(() => {
  const key = 'postupashki_placement';
  const query = new URLSearchParams(window.location.search);
  let token = '';
  try { token = sessionStorage.getItem(key) || ''; } catch (_) {}
  if (query.has('utm_placement')) {
    const value = query.get('utm_placement') || '';
    token = /^[A-Za-z0-9_-]{1,64}$/.test(value) ? value : '';
    try {
      if (token) sessionStorage.setItem(key, token);
      else sessionStorage.removeItem(key);
    } catch (_) {}
  }
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(token)) token = '';
  const update = () => document.querySelectorAll('[data-telegram-bot]').forEach(a => {
    const name = a.dataset.telegramBot;
    if (!/^[A-Za-z0-9_]{5,32}$/.test(name)) return;
    a.href = `https://t.me/${name}` + (token ? `?start=${encodeURIComponent(token)}` : '');
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', update);
  else update();
})();
