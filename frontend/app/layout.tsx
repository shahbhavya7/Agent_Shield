import "./globals.css";
import type { Metadata } from "next";
import { Inter, Fredoka, Unbounded } from "next/font/google";

const inter = Inter({
  subsets: ["latin"],
});

const fredoka = Fredoka({
  subsets: ["latin"],
  weight: ["500"],
  variable: "--font-heading",
});

const unbounded = Unbounded({
  subsets: ["latin"],
  weight: ["800"],
  variable: "--font-logo",
});

export const metadata: Metadata = {
  title: "AgentShield",
  description: "AI Reliability Testing Platform for AI Agents",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${inter.className} ${fredoka.variable} ${unbounded.variable}`}>
        {children}
      </body>
    </html>
  );
}
