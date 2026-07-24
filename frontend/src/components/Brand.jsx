/* Brand.jsx — the Altis mark, shared everywhere it appears (header, access
   gate, assistant). One source of truth for the official ice-blue-on-black
   satellite: twin gridded solar panels, body with nose cap, downlink dish. */
export function AltisLogo({ size = 26, idSuffix = '' }) {
  const gid = `altisIce${idSuffix}`;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#DDF1FB"/>
          <stop offset="1" stopColor="#8FC4E8"/>
        </linearGradient>
      </defs>
      <g transform="translate(33,29) rotate(45)">
        <g stroke="#000004" strokeWidth="1.3">
          <rect x="-27" y="-7.5" width="15" height="15" rx="1.5" fill={`url(#${gid})`}/>
          <line x1="-22" y1="-7.5" x2="-22" y2="7.5"/>
          <line x1="-17" y1="-7.5" x2="-17" y2="7.5"/>
          <line x1="-27" y1="-2.5" x2="-12" y2="-2.5"/>
          <line x1="-27" y1="2.5"  x2="-12" y2="2.5"/>
          <rect x="12" y="-7.5" width="15" height="15" rx="1.5" fill={`url(#${gid})`}/>
          <line x1="17" y1="-7.5" x2="17" y2="7.5"/>
          <line x1="22" y1="-7.5" x2="22" y2="7.5"/>
          <line x1="12" y1="-2.5" x2="27" y2="-2.5"/>
          <line x1="12" y1="2.5"  x2="27" y2="2.5"/>
        </g>
        <line x1="-12" y1="0" x2="-7" y2="0" stroke={`url(#${gid})`} strokeWidth="2.4"/>
        <line x1="7"   y1="0" x2="12" y2="0" stroke={`url(#${gid})`} strokeWidth="2.4"/>
        <rect x="-7" y="-9" width="14" height="18" rx="3.5" fill={`url(#${gid})`}/>
        <rect x="-3.5" y="-13" width="7" height="5" rx="2" fill={`url(#${gid})`}/>
        <path d="M -12 12 A 9.5 9.5 0 0 1 7 12 L -12 12 Z"
              transform="rotate(180 -2.5 15)" fill={`url(#${gid})`}/>
        <line x1="-2.5" y1="17" x2="-2.5" y2="23.5" stroke={`url(#${gid})`} strokeWidth="1.8"/>
        <circle cx="-2.5" cy="24.5" r="2" fill={`url(#${gid})`}/>
      </g>
    </svg>
  );
}
