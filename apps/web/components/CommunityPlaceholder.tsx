/** 小区照片占位效果图：版权合规考虑，不展示实拍照片，统一用自绘渐变 + 建筑图标。 */
export default function CommunityPlaceholder({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`flex flex-col items-center justify-center gap-1 bg-gradient-to-br from-slate-100 via-slate-50 to-slate-200 ${className}`}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-6 w-6 text-slate-400"
      >
        <path d="M3 21h18" />
        <path d="M5 21V7l5-4v18" />
        <path d="M14 21v-8l5 2v6" />
        <path d="M8 9h.01M8 13h.01M8 17h.01M17 17h.01" />
      </svg>
      <span className="text-[10px] leading-none text-slate-400">示意图</span>
    </div>
  );
}
