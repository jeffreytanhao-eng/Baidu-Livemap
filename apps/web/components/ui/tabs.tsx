"use client";

import { ReactNode, useState } from "react";

export interface TabItem {
  id: string;
  label: ReactNode;
  content: ReactNode;
}

export function Tabs({
  items,
  defaultId,
  onChange,
  activeId: controlledId,
}: {
  items: TabItem[];
  defaultId?: string;
  activeId?: string;
  onChange?: (id: string) => void;
}) {
  const [inner, setInner] = useState(defaultId ?? items[0]?.id);
  const activeId = controlledId ?? inner;
  const active = items.find((t) => t.id === activeId) ?? items[0];

  return (
    <div>
      <div className="flex rounded-lg bg-slate-100 p-1">
        {items.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setInner(t.id);
              onChange?.(t.id);
            }}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150 ${
              t.id === active.id
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="mt-3">{active?.content}</div>
    </div>
  );
}
