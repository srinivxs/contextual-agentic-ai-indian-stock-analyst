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
export const NewsIcon = () => (
  <Icon>
    <path d="M5 5h11v14H7a2 2 0 0 1-2-2V5Z" />
    <path d="M16 9h3v8a2 2 0 0 1-2 2M8 9h5M8 12.5h5M8 16h3" />
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
export const SunIcon = () => (
  <Icon size={14}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Icon>
);
export const MoonIcon = () => (
  <Icon size={14}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
  </Icon>
);
export const ExternalIcon = () => (
  <Icon size={15}>
    <path d="M14 4h6v6" />
    <path d="M20 4 11 13" />
    <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </Icon>
);
export const ArrowRightIcon = () => (
  <Icon size={14}>
    <path d="M5 12h14" />
    <path d="m13 6 6 6-6 6" />
  </Icon>
);
export const RefreshIcon = () => (
  <Icon size={15}>
    <path d="M20 11a8 8 0 1 0-2.3 5.7" />
    <path d="M20 4v7h-7" />
  </Icon>
);
export const TrashIcon = () => (
  <Icon size={15}>
    <path d="M4 7h16" />
    <path d="M10 11v6M14 11v6" />
    <path d="m6 7 1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
    <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
  </Icon>
);
export const ChevronDownIcon = () => (
  <Icon size={16}>
    <path d="m6 9 6 6 6-6" />
  </Icon>
);
export const ChevronRightIcon = () => (
  <Icon size={14}>
    <path d="m9 6 6 6-6 6" />
  </Icon>
);
export const ArrowUpIcon = () => (
  <Icon>
    <path d="M12 19V5" />
    <path d="m6 11 6-6 6 6" />
  </Icon>
);
/** The assistant's mark: a four-pointed spark, filled. */
export const SparkleIcon = ({ size = 18 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path
      fill="currentColor"
      d="M12 2.5c.5 4.6 2.9 7 7.5 7.5-4.6.5-7 2.9-7.5 7.5-.5-4.6-2.9-7-7.5-7.5 4.6-.5 7-2.9 7.5-7.5Z"
    />
    <path
      fill="currentColor"
      d="M18.5 15c.2 1.7 1 2.6 2.8 2.8-1.7.2-2.6 1-2.8 2.8-.2-1.7-1-2.6-2.8-2.8 1.7-.2 2.6-1 2.8-2.8Z"
    />
  </svg>
);
export const ShieldCheckIcon = () => (
  <Icon size={14}>
    <path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Icon>
);
export const BarsIcon = () => (
  <Icon size={16}>
    <path d="M6 20v-6M12 20V9M18 20V4" />
  </Icon>
);
export const CoinsIcon = () => (
  <Icon size={16}>
    <ellipse cx="12" cy="6" rx="7" ry="3" />
    <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
    <path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
  </Icon>
);
export const PercentIcon = () => (
  <Icon size={16}>
    <path d="M19 5 5 19" />
    <circle cx="7" cy="7" r="2.5" />
    <circle cx="17" cy="17" r="2.5" />
  </Icon>
);
