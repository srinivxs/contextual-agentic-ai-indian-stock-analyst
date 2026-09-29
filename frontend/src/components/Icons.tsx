/**
 * The few line icons the app uses, drawn inline (no icon library). Each is decorative: the text
 * next to it names the action, so the SVG is hidden from assistive technology.
 */
import type { ReactNode } from 'react';

function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const LeafIcon = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M5 19c0-8 5-14 14-14 0 9-6 14-14 14Z" />
    <path d="M5 19 13 11" />
  </Icon>
);
export const HomeIcon = () => (
  <Icon>
    <path d="M4 11 12 4l8 7" />
    <path d="M6 10v10h12V10" />
  </Icon>
);
export const ChatIcon = () => (
  <Icon>
    <path d="M5 5h14v10H9l-4 4V5Z" />
  </Icon>
);
export const StarIcon = () => (
  <Icon>
    <path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.3 7.2 18.9l.9-5.4-3.9-3.8 5.4-.8L12 4Z" />
  </Icon>
);
export const FileIcon = () => (
  <Icon>
    <path d="M7 3h7l4 4v14H7V3Z" />
    <path d="M14 3v4h4M10 12h5M10 16h5" />
  </Icon>
);
export const TargetIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="4" />
    <circle cx="12" cy="12" r="0.8" />
  </Icon>
);
export const SearchIcon = () => (
  <Icon>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </Icon>
);
export const SendIcon = () => (
  <Icon>
    <path d="M4 12 20 4l-5 16-3-7-8-1Z" />
  </Icon>
);
export const PencilIcon = () => (
  <Icon size={16}>
    <path d="m15 5 4 4L9 19H5v-4L15 5Z" />
  </Icon>
);
export const ShieldIcon = () => (
  <Icon>
    <path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3Z" />
  </Icon>
);
export const TrendIcon = () => (
  <Icon>
    <path d="m4 16 5-5 4 4 7-7" />
    <path d="M15 8h5v5" />
  </Icon>
);
export const ClockIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 8v4l3 2" />
  </Icon>
);
export const PlusIcon = () => (
  <Icon size={16}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
