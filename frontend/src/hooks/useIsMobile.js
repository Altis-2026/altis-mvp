import { useState, useEffect } from 'react';

/* Shared viewport hooks. "Mobile" is a phone-width viewport; "narrow" also
   catches small tablets in portrait. Components adapt themselves so the
   layout never overlaps on an iPad or phone. */
function useMediaQuery(query) {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches
  );
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = e => setMatches(e.matches);
    mql.addEventListener('change', onChange);
    setMatches(mql.matches);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

export function useIsMobile() { return useMediaQuery('(max-width: 640px)'); }
export function useIsNarrow() { return useMediaQuery('(max-width: 900px)'); }
