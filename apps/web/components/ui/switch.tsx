"use client";

export function Switch({
  checked,
  onCheckedChange,
  disabled,
  id,
}: {
  checked: boolean;
  onCheckedChange?: (v: boolean) => void;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange?.(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-150 ${
        disabled
          ? "cursor-not-allowed bg-slate-200"
          : checked
            ? "bg-brand"
            : "bg-slate-300"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-150 ${
          checked ? "translate-x-[18px]" : "translate-x-[2px]"
        }`}
      />
    </button>
  );
}
