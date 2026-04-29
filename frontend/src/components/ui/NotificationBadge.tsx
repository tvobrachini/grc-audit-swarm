interface Props {
  count?: number;
}

export function NotificationBadge({ count = 1 }: Props) {
  return (
    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-black leading-none">
      {count}
    </span>
  );
}
