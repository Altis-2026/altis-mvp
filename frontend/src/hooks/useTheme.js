import { useState, useEffect } from 'react';
import { getTheme } from '../theme.js';

/* Live theme value ('dark' | 'light'). CSS variables handle themselves via
   [data-theme]; this is for the few surfaces that aren't CSS — chiefly the
   Mapbox globe atmosphere — so they can re-style the moment the user toggles. */
export function useTheme() {
  const [theme, setTheme] = useState(getTheme());
  useEffect(() => {
    const onChange = (e) => setTheme(e.detail || getTheme());
    window.addEventListener('altis-theme', onChange);
    return () => window.removeEventListener('altis-theme', onChange);
  }, []);
  return theme;
}
