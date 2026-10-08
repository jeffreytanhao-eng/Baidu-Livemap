import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "百度地图15分钟生活圈",
  description: "看清真实步行能到哪，找出菜场、药店、小学的缺口",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
