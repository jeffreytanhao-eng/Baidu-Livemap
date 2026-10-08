import { forwardRef, InputHTMLAttributes } from "react";

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }
>(({ className = "", invalid, ...props }, ref) => (
  <input
    ref={ref}
    className={`h-9 rounded-md border bg-white px-3 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 ${
      invalid
        ? "border-red-400 focus:ring-red-200"
        : "border-slate-300 focus:border-brand focus:ring-teal-100"
    } ${className}`}
    {...props}
  />
));
Input.displayName = "Input";
