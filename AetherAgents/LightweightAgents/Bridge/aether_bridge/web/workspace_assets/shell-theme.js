/* Owner: appearance preference and theme-toggle behavior.
 * Must never: render navigation, inject CSS rules or change workspace records.
 */
window.aetherShellParts=window.aetherShellParts||{};
window.aetherShellParts.theme=function(appearance){
  const themeKey = 'aether.appearance';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const savedTheme = () => {
    try { const value = localStorage.getItem(themeKey); return ['light', 'dark'].includes(value) ? value : null; }
    catch (_) { return null; }
  };
  let explicitTheme = savedTheme();
  function applyTheme(theme) {
    document.documentElement.dataset.aetherTheme = theme;
    appearance.textContent = theme === 'dark' ? '☀ Light mode' : '☾ Dark mode';
    appearance.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    appearance.title = appearance.getAttribute('aria-label');
  }
  appearance.onclick = () => {
    explicitTheme = document.documentElement.dataset.aetherTheme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(themeKey, explicitTheme); } catch (_) { /* Session choice still works. */ }
    applyTheme(explicitTheme);
  };
  systemTheme.addEventListener('change', () => {
    if (!explicitTheme) applyTheme(systemTheme.matches ? 'dark' : 'light');
  });
  window.addEventListener('storage', event => {
    if (event.key === themeKey || event.key === null) {
      explicitTheme = savedTheme();
      applyTheme(explicitTheme || (systemTheme.matches ? 'dark' : 'light'));
    }
  });
  applyTheme(explicitTheme || (systemTheme.matches ? 'dark' : 'light'));
};
