import type { Metadata } from "next";
import { Inter, Noto_Sans_SC } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const notoSans = Noto_Sans_SC({ subsets: ["latin"], variable: "--font-noto-sc" });

export const metadata: Metadata = {
  title: "Commerce Lens · 电商数据分析 Agent",
  description: "通过自然语言完成电商数据查询、图表分析与业务洞察。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${inter.variable} ${notoSans.variable}`}>{children}</body>
    </html>
  );
}
