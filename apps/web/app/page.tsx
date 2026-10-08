import Link from "next/link";

const ENTRIES = [
  {
    href: "/nearby",
    title: "附近小区生活圈",
    desc: "地图点选任意位置，查看周边 1 公里内小区列表与距离，可一键定位小区位置或进入生活圈评估详情。",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M3 11l9-8 9 8M5.5 9.5V20h13V9.5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M10 20v-5h4v5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    href: "/evaluate",
    title: "评估小区生活圈",
    desc: "指定小区或任意位置，沿真实路网生成 5/15 分钟步行等时圈，出具七类设施体检报告与生活圈得分。",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M12 21s-7-5.1-7-11a7 7 0 1 1 14 0c0 5.9-7 11-7 11z"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <circle cx="12" cy="10" r="2.6" fill="currentColor" />
      </svg>
    ),
  },
];

export default function Page() {
  return (
    <div className="flex min-h-dvh flex-col bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-4xl items-center px-4">
          <span className="text-lg font-bold text-brand">百度地图15分钟生活圈</span>
          <span className="ml-2 hidden text-xs font-medium tracking-wide text-slate-400 sm:inline">
            Livemap（版本支持北京六环内区域高精度结果）
          </span>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col justify-center px-4 py-10">
        <h1 className="text-2xl font-bold text-slate-900">选择体验场景</h1>

        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          {ENTRIES.map((e) => (
            <Link
              key={e.href}
              href={e.href}
              className="group rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:border-brand hover:shadow-md"
            >
              <div className="flex items-center gap-2.5">
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-light text-brand">
                  {e.icon}
                </span>
                <span className="text-base font-semibold text-slate-900 group-hover:text-brand">
                  {e.title}
                </span>
                <span className="ml-auto text-slate-300 transition-colors group-hover:text-brand">
                  →
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-slate-500">{e.desc}</p>
            </Link>
          ))}
        </div>
      </main>

      <footer className="py-6 text-center text-xs text-slate-400">
        2026 上海开源软件应用创新大赛 · 百度地图企业命题
      </footer>
    </div>
  );
}
