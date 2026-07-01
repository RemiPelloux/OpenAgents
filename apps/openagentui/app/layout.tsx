import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Logo } from "@/components/ui/Logo";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenAgentUI",
  description: "OpenPro's local visual workflow builder for OpenAgents",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="oaui-shell">
          <header className="oaui-topbar">
            <a href="/" style={{ color: "inherit" }}>
              <Logo />
            </a>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
