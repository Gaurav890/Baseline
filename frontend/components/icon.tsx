type IconName =
  | "overview"
  | "flask"
  | "trace"
  | "database"
  | "compare"
  | "settings"
  | "arrow"
  | "play"
  | "refresh"
  | "check"
  | "warning"
  | "clock"
  | "coins"
  | "chevron";

const paths: Record<IconName, React.ReactNode> = {
  overview: <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />,
  flask: <path d="M9 3h6m-5 0v5l-5.4 8.8A2.7 2.7 0 0 0 6.9 21h10.2a2.7 2.7 0 0 0 2.3-4.2L14 8V3M7.5 15h9" />,
  trace: <path d="M6 3v5m0 8v5m12-5v5M6 8c0 5 12 3 12 8M3 8h6M15 16h6" />,
  database: <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />,
  compare: <path d="M7 4v16m0 0-3-3m3 3 3-3M17 20V4m0 0-3 3m3-3 3 3" />,
  settings: <path d="M12 15.2A3.2 3.2 0 1 0 12 8.8a3.2 3.2 0 0 0 0 6.4ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />,
  arrow: <path d="m5 12 14 0m-5-5 5 5-5 5" />,
  play: <path d="m9 7 8 5-8 5V7Z" />,
  refresh: <path d="M20 6v5h-5M4 18v-5h5m10.2-2a7.5 7.5 0 0 0-12.7-3L4 11m16 2-2.5 3a7.5 7.5 0 0 1-12.7-2" />,
  check: <path d="m5 12 4 4L19 6" />,
  warning: <path d="M12 3 2.7 20h18.6L12 3Zm0 6v5m0 3h.01" />,
  clock: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4l3 2" />,
  coins: <path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v5c0 1.7 3.6 3 8 3s8-1.3 8-3V7m-16 5v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5" />,
  chevron: <path d="m9 18 6-6-6-6" />,
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
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
    >
      {paths[name]}
    </svg>
  );
}

