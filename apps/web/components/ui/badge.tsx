import { HTMLAttributes } from "react";

type Tone = "gray" | "green" | "red" | "yellow" | "teal" | "blue";

const toneCls: Record<Tone, string> = {
  gray: "bg-slate-100 text-slate-600 border-slate-200",
  green: "bg-green-50 text-green-700 border-green-200",
  red: "bg-red-50 text-red-700 border-red-200",
  yellow: "bg-amber-50 text-amber-700 border-amber-200",
  teal: "bg-teal-50 text-teal-700 border-teal-200",
  blue: "bg-blue-50 text-blue-700 border-blue-200",
};

export function Badge({
  tone = "gray",
  className = "",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${toneCls[tone]} ${className}`}
      {...props}
    />
  );
}
