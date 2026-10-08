import { HTMLAttributes, ReactNode } from "react";

export function Card({
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  extra,
  className = "",
}: {
  title: ReactNode;
  extra?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex items-center justify-between border-b border-slate-100 px-4 py-2.5 ${className}`}
    >
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      {extra}
    </div>
  );
}

export function CardBody({
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={`px-4 py-3 ${className}`} {...props} />;
}
