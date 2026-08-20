"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, ShieldCheck, UploadCloud, ListChecks } from "lucide-react";

const shapes = [
  { size: 190, top: "-6%", left: "-4%", color: "#7C5CFF", radius: "42% 58% 70% 30% / 45% 45% 55% 55%", rotate: 10, duration: 20, delay: 0 },
  { size: 110, top: "20%", left: "88%", color: "#2DD4BF", radius: "63% 37% 30% 70% / 50% 45% 55% 50%", rotate: -12, duration: 15, delay: 1 },
  { size: 160, top: "70%", left: "-4%", color: "#F472B6", radius: "37% 63% 56% 44% / 49% 56% 44% 51%", rotate: 16, duration: 22, delay: 0.5 },
  { size: 90, top: "55%", left: "72%", color: "#FBBF24", radius: "73% 27% 45% 55% / 39% 49% 51% 61%", rotate: -14, duration: 13, delay: 1.5 },
];

const options = [
  {
    href: "/dashboard",
    icon: UploadCloud,
    title: "Start New Agent Test",
    description:
      "Connect an agent that hasn't been set up yet. You'll point AgentShield at its endpoint, then generate and run tests.",
    cta: "Connect a new agent",
  },
  {
    href: "/existing-agent",
    icon: ListChecks,
    title: "Test Existing Agent",
    description:
      "Pick a customer and one of their already-registered agents, and run a fresh test against it.",
    cta: "Choose an existing agent",
  },
];

export default function StartTesting() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-[#0B0B0F]">
      {shapes.map((shape, index) => (
        <motion.div
          key={index}
          aria-hidden
          className="pointer-events-none absolute"
          style={{
            width: shape.size,
            height: shape.size,
            top: shape.top,
            left: shape.left,
            borderRadius: shape.radius,
            background: `radial-gradient(circle at 30% 28%, ${shape.color}66, ${shape.color}22 40%, rgba(10,10,14,0.35) 75%)`,
            border: `1px solid ${shape.color}40`,
            boxShadow: "inset -10px -10px 26px rgba(0,0,0,0.4), inset 6px 6px 14px rgba(255,255,255,0.05), 0 16px 40px rgba(0,0,0,0.3)",
            filter: "blur(6px)",
          }}
          initial={{ rotate: shape.rotate }}
          animate={{ y: [0, -16, 0], x: [0, 10, 0], rotate: [shape.rotate, shape.rotate + 14, shape.rotate] }}
          transition={{ duration: shape.duration, repeat: Infinity, ease: "easeInOut", delay: shape.delay }}
        />
      ))}

      <header className="sticky top-0 z-50 border-b border-white/12 bg-[#0B0B0F]/70 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/20 bg-white/4 text-slate-200">
              <ShieldCheck className="h-5 w-5" strokeWidth={1.5} />
            </div>
            <span className="font-logo text-lg font-extrabold tracking-tight text-[#F8FAFC]">AgentShield</span>
          </Link>
        </div>
      </header>

      <section className="relative mx-auto max-w-4xl px-6 pb-24 pt-16">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          className="text-center"
        >
          <h1 className="font-heading text-3xl font-medium tracking-tight text-[#F8FAFC] sm:text-4xl">
            How do you want to start?
          </h1>
          <p className="mt-4 text-base text-[#9CA3AF]">
            Test a brand-new agent, or run a fresh test against one you&apos;ve already registered.
          </p>
        </motion.div>

        <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2">
          {options.map((option, index) => (
            <motion.div
              key={option.title}
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, ease: "easeOut", delay: 0.1 + index * 0.1 }}
              whileHover={{ y: -6 }}
            >
              <Link
                href={option.href}
                className="flex h-full flex-col rounded-xl border border-white/12 bg-white/2 p-8 shadow-[0_8px_40px_rgba(0,0,0,0.35)] backdrop-blur-md transition-all duration-300 hover:border-white/30 hover:bg-white/4"
              >
                <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-white/20 bg-white/4 text-slate-200">
                  <option.icon className="h-6 w-6" strokeWidth={1.5} />
                </div>
                <h2 className="font-heading mt-5 text-xl font-medium text-[#F8FAFC]">{option.title}</h2>
                <p className="mt-2 flex-1 text-sm leading-relaxed text-[#9CA3AF]">{option.description}</p>
                <span className="mt-6 flex items-center gap-2 text-sm font-medium text-[#F8FAFC]">
                  {option.cta}
                  <ArrowRight className="h-4 w-4" strokeWidth={1.5} />
                </span>
              </Link>
            </motion.div>
          ))}
        </div>
      </section>
    </main>
  );
}
