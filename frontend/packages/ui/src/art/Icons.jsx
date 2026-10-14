// The 15 custom GharBasket icons (design system v12, 1.5px stroke on a 24px grid). Generated from
// the Claude Design export; ink is currentColor, so they follow the text colour in both themes.

/** @typedef {{ size?: number, className?: string, label?: string }} ArtProps */

/** Decorative unless `label` is given. @param {ArtProps & { children: import("react").ReactNode }} props */
function Icon({ size = 24, className, label, children }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={className}
    >
      {children}
    </svg>
  );
}

/** @param {ArtProps} props */
export function BhindiIcon(props) {
  return (
    <Icon {...props}>
      <path d="M14.6 6.2C11.9 10.6 8.6 15.7 5.2 20.8c5-3.6 9.4-8.2 13-13.1" />
      <path d="M16.4 6.9C13.4 11.4 9.7 16.4 5.2 20.8" />
      <path d="M14.6 6.2c.1-1.3 1.2-2.2 2.5-2 1.2.2 1.7 1.7 1.1 3.5" />
      <path d="M16.9 4.3l1.4-2.1" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function CaptainBadgeIcon(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="9" r="5.5" />
      <path d="M9.2 13.8 7.75 21 12 18.75 16.25 21l-1.45-7.2" />
      <path d="M9.6 10 12 7.8l2.4 2.2v1.8H9.6z" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function CrateIcon(props) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="7" width="17" height="12.5" rx="1" />
      <path d="M3.5 11.25h17M3.5 15.25h17" />
      <path d="M10 9.1h4" />
      <path d="M6 19.5v1.5M18 19.5v1.5" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function GullakIcon(props) {
  return (
    <Icon {...props}>
      <path d="M12 6.5c4.4 0 8 3.3 8 7.4 0 3.1-2.1 5.6-5 6.6H9c-2.9-1-5-3.5-5-6.6 0-4.1 3.6-7.4 8-7.4z" />
      <path d="M10 6.5V5.3a2 2 0 0 1 4 0v1.2" />
      <path d="M9.5 11h5" />
      <path d="M5 15c4.5 1.3 9.5 1.3 14 0" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function KadaiIcon(props) {
  return (
    <Icon {...props}>
      <path d="M4 10.5h16" />
      <path d="M4.5 10.5c.5 4.4 3.6 7.5 7.5 7.5s7-3.1 7.5-7.5" />
      <path d="M4 10.5c-1.4 0-2.5-.8-2.5-1.8M20 10.5c1.4 0 2.5-.8 2.5-1.8" />
      <path d="M9.5 20.5h5" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function LaukiIcon(props) {
  return (
    <Icon {...props}>
      <path d="M9.3 4.3c1.6-.5 3.2.5 3.6 2.1l.8 3.3c.3 1.2 1.1 2.2 2.2 2.8 2 1.2 2.9 3.6 2.1 5.8-.9 2.5-3.8 3.7-6.3 2.6-2.1-.9-3.2-3.2-2.7-5.4.3-1.3.2-2.6-.3-3.8L7.6 8.4c-.6-1.6.2-3.6 1.7-4.1z" />
      <path d="M9.6 4.2 9 2.5" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function LeafyGreensIcon(props) {
  return (
    <Icon {...props}>
      <path d="M12 14v7" />
      <path d="M12 14c-3.1 0-5.5-2.5-5.5-5.5 3 0 5.5 2.4 5.5 5.5z" />
      <path d="M12 14c3.1 0 5.5-2.5 5.5-5.5-3 0-5.5 2.4-5.5 5.5z" />
      <path d="M12 11.2c-1.8-1.8-1.8-4.8 0-6.7 1.8 1.9 1.8 4.9 0 6.7z" />
      <path d="M10.25 17.5h3.5" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function MirchiIcon(props) {
  return (
    <Icon {...props}>
      <path d="M6.3 6.6c.5 6 4.5 11 12 13.6.9.3 1.4-.8.6-1.3C13.2 15.1 10.7 11.1 9.7 6.6" />
      <path d="M6.3 6.6c1-.7 2.5-.7 3.4 0" />
      <path d="M8 6.4c-.3-1.5.3-2.8 1.6-3.4" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function OnionIcon(props) {
  return (
    <Icon {...props}>
      <path d="M12 3.5c0 3 6 5 6 10.5a6 6 0 0 1-12 0C6 8.5 12 6.5 12 3.5z" />
      <path d="M12 8c-1.6 2-2.5 4-2.5 6.5M12 8c1.6 2 2.5 4 2.5 6.5" />
      <path d="M10.5 20.2 10 21.5M12 20.5v1.25M13.5 20.2l.5 1.3" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function ParchiIcon(props) {
  return (
    <Icon {...props}>
      <path d="M6 3.5h12v16l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3z" />
      <path d="M9 8h6M9 11.5h6M9 15h3" />
      <path d="M13.6 15.1l1.1 1.1 2-2.4" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function SocietyTowerIcon(props) {
  return (
    <Icon {...props}>
      <path d="M6 21V4.5a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1V21" />
      <path d="M15 9h3a1 1 0 0 1 1 1v11" />
      <path d="M3 21h18" />
      <path d="M9 7h1M11.5 7h1M9 10.5h1M11.5 10.5h1M9 14h1M11.5 14h1" />
      <path d="M9.75 21v-3h2.5v3" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function TarazuIcon(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="4" r="1" />
      <path d="M12 5v15M8.5 20.5h7" />
      <path d="M4.5 7.5h15" />
      <path d="M4.5 7.5 2.5 13M4.5 7.5l2 5.5M19.5 7.5l-2 5.5M19.5 7.5l2 5.5" />
      <path d="M2 13h5a2.5 2.5 0 0 1-5 0zM17 13h5a2.5 2.5 0 0 1-5 0z" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function ThaliIcon(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9.25" />
      <circle cx="7.6" cy="11.4" r="1.9" />
      <circle cx="10.4" cy="7" r="1.9" />
      <circle cx="15.2" cy="7.4" r="1.9" />
      <path d="M11.2 17.6a4.6 4.6 0 0 1 6.4-5.9" />
      <path d="M8.6 16.2h1.2" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function TiffinDabbaIcon(props) {
  return (
    <Icon {...props}>
      <path d="M9 7.5V6a3 3 0 0 1 6 0v1.5" />
      <rect x="6.5" y="7.5" width="11" height="4.25" rx="1" />
      <rect x="6.5" y="11.75" width="11" height="4.25" rx="1" />
      <path d="M6.5 16h11v3.5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z" />
      <path d="M4.75 9v10.5M19.25 9v10.5" />
    </Icon>
  );
}

/** @param {ArtProps} props */
export function TomatoIcon(props) {
  return (
    <Icon {...props}>
      <path d="M12 7.5c4.4 0 8 2.9 8 6.8S16.4 21 12 21s-8-2.8-8-6.7 3.6-6.8 8-6.8z" />
      <path d="M12 7.5V4.5M12 7.5 9.8 5.6M12 7.5l2.2-1.9M12 7.5 8.6 8.4M12 7.5l3.4.9" />
    </Icon>
  );
}
