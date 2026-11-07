/* Restore appearance before painting; keep supported system chrome in sync. */
(() => {
  const key = 'takumi-appearance';
  const meta = (name, value) => document.querySelector(`meta[name="${name}"]`)?.setAttribute('content', value);
  const paint = (color, scheme) => {
    document.body.dataset.scheme = scheme;
    document.documentElement.style.backgroundColor = color;
    document.documentElement.style.colorScheme = scheme;
    document.body.style.colorScheme = scheme;
    meta('theme-color', color);
    meta('color-scheme', scheme);
  };
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (saved && /^[a-z]{2,16}$/.test(saved.theme) && /^#[\da-f]{6}$/i.test(saved.color) && ['light','dark'].includes(saved.scheme)) {
      document.body.dataset.theme = saved.theme;
      paint(saved.color, saved.scheme);
    }
  } catch { /* Appearance storage is optional, including private browsing. */ }
  window.syncPhoneChrome = () => {
    const color = getComputedStyle(document.body).getPropertyValue('--paper').trim();
    if (!/^#[\da-f]{6}$/i.test(color)) return;
    const rgb = [1,3,5].map(offset => parseInt(color.slice(offset, offset+2), 16));
    const scheme = (rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722) < 140 ? 'dark' : 'light';
    paint(color, scheme);
    try { localStorage.setItem(key, JSON.stringify({theme:document.body.dataset.theme, color, scheme})); } catch {}
  };
  window.syncPhoneChrome();
})();
