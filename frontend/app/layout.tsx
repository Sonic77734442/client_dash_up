import type { Metadata } from "next";
import { AuthGate } from "../components/AuthGate";
import { ImpersonationBanner } from "../components/ImpersonationBanner";
import { RuntimeI18n } from "../components/RuntimeI18n";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ops Center Dashboard",
  description: "Envidicy dashboard frontend",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <RuntimeI18n />
        <ImpersonationBanner />
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
