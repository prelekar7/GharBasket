// The few icons the console pages need. The console app does not depend on a icon package (the design
// system's phosphor set is internal to @gharbasket/ui), so these are inline SVG: 20px, current colour.

/** @param {{ d: string }} props */
function Icon({ d }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <path d={d} />
    </svg>
  );
}

export const PlusIcon = () => <Icon d="M12 5v14M5 12h14" />;
export const CheckIcon = () => <Icon d="M5 12.5l4.5 4.5L19 7.5" />;
export const DownloadIcon = () => <Icon d="M12 4v11M7 11l5 5 5-5M5 20h14" />;
export const UploadIcon = () => <Icon d="M12 16V5M7 9l5-5 5 5M5 20h14" />;
export const WarnIcon = () => <Icon d="M12 3.5 2.5 20h19zM12 10v4.5M12 17.5v.5" />;
