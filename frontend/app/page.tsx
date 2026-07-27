"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowRight,
  ShieldCheck,
  FlaskConical,
  Bug,
  SearchCode,
  UploadCloud,
  Sparkles,
  PlayCircle,
  FileBarChart2,
  Gauge,
  Play,
} from "lucide-react";

const features = [
  {
    icon: FlaskConical,
    title: "Scenario Generator",
    description:
      "Automatically generates realistic and adversarial test cases, so no edge case is left to guesswork.",
  },
  {
    icon: Bug,
    title: "Failure Injection",
    description:
      "Simulates prompt injection, tool outages, stale retrieval, memory recall, and API/system failures inside a controlled test run.",
  },
  {
    icon: Gauge,
    title: "Reliability Scoring",
    description:
      "Scores every response for accuracy, hallucination, safety, and recovery, rolled up into one clear score.",
  },
  {
    icon: SearchCode,
    title: "Cause + Copyable Fix",
    description:
      "Traces every failure back to its source and hands you a one-line, pasteable fix — not just a red X.",
  },
];

const workflow = [
  { icon: UploadCloud, label: "Connect Agent" },
  { icon: Sparkles, label: "Generate Scenarios" },
  { icon: Bug, label: "Inject Failures" },
  { icon: PlayCircle, label: "Run Tests" },
  { icon: FileBarChart2, label: "Reliability Report" },
];

const fadeUp = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0 },
};

const shapes = [
  { size: 200, top: "-4%", left: "-4%", color: "#7C5CFF", radius: "42% 58% 70% 30% / 45% 45% 55% 55%", rotate: 12, duration: 20, delay: 0 },
  { size: 110, top: "10%", left: "80%", color: "#2DD4BF", radius: "63% 37% 30% 70% / 50% 45% 55% 50%", rotate: -10, duration: 16, delay: 1 },
  { size: 230, top: "46%", left: "66%", color: "#F472B6", radius: "37% 63% 56% 44% / 49% 56% 44% 51%", rotate: 18, duration: 24, delay: 2 },
  { size: 90, top: "66%", left: "10%", color: "#FBBF24", radius: "73% 27% 45% 55% / 39% 49% 51% 61%", rotate: -16, duration: 14, delay: 0.5 },
  { size: 70, top: "30%", left: "40%", color: "#60A5FA", radius: "48% 52% 62% 38% / 40% 62% 38% 60%", rotate: 8, duration: 12, delay: 1.5 },
  { size: 150, top: "80%", left: "46%", color: "#7C5CFF", radius: "58% 42% 40% 60% / 55% 40% 60% 45%", rotate: -12, duration: 18, delay: 1 },
];

export default function Home() {
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
            boxShadow:
              "inset -10px -10px 26px rgba(0,0,0,0.4), inset 6px 6px 14px rgba(255,255,255,0.05), 0 16px 40px rgba(0,0,0,0.3)",
            filter: "blur(6px)",
          }}
          initial={{ rotate: shape.rotate }}
          animate={{ y: [0, -18, 0], x: [0, 12, 0], rotate: [shape.rotate, shape.rotate + 14, shape.rotate] }}
          transition={{ duration: shape.duration, repeat: Infinity, ease: "easeInOut", delay: shape.delay }}
        />
      ))}

      <section className="relative mx-auto flex max-w-4xl flex-col items-center px-6 pb-24 pt-32 text-center lg:pt-40">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: "easeOut" }}
          className="flex flex-col items-center text-center"
        >
          <span className="rounded-full border border-white/12 bg-white/2 px-4 py-1.5 text-sm font-medium text-[#9CA3AF] backdrop-blur-md">
            AI Reliability Engineering
          </span>
          <h1 className="font-logo mt-8 text-5xl font-extrabold tracking-tight text-[#F8FAFC] sm:text-6xl">
            AgentShield
          </h1>
          <p className="mt-4 text-xl font-medium text-[#F8FAFC]/90">
            Reliability Engineering for AI Agents
          </p>
          <p className="mt-5 max-w-lg text-base leading-relaxed text-[#9CA3AF]">
            &ldquo;Before you deploy your AI agent, crash-test it.&rdquo;
          </p>
          <div className="mt-10 flex flex-col justify-center gap-4 sm:flex-row">
            <Link href="/dashboard">
              <motion.span
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                transition={{ duration: 0.3 }}
                className="flex items-center justify-center gap-2 rounded-full border border-white/20 bg-white/4 px-8 py-3.5 text-sm font-medium text-[#F8FAFC] backdrop-blur-md transition-all duration-300 hover:border-white/40 hover:bg-white/[0.12] hover:shadow-[0_0_32px_rgba(255,255,255,0.2)]"
              >
                Start Testing
                <ArrowRight className="h-4 w-4" />
              </motion.span>
            </Link>
            <Link href="#workflow">
              <motion.span
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                transition={{ duration: 0.3 }}
                className="flex items-center justify-center gap-2 rounded-full border border-white/12 bg-white/2 px-8 py-3.5 text-sm font-medium text-[#F8FAFC] backdrop-blur-md transition-all duration-300 hover:border-white/[0.16] hover:bg-white/4"
              >
                <Play className="h-4 w-4" />
                View Demo
              </motion.span>
            </Link>
          </div>
        </motion.div>
      </section>

      <section className="relative mx-auto max-w-7xl px-6 py-24">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          variants={fadeUp}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="mx-auto max-w-2xl text-center"
        >
          <h2 className="font-heading text-3xl font-medium tracking-tight text-[#F8FAFC] sm:text-4xl">
            Core Features
          </h2>
          <p className="mt-4 text-base text-[#9CA3AF]">
            Four AI components working together to stress-test your agent the way production actually will.
          </p>
        </motion.div>

        <div className="mt-14 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {features.map((feature, index) => (
            <motion.div
              key={feature.title}
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={fadeUp}
              transition={{ duration: 0.5, ease: "easeOut", delay: index * 0.1 }}
              whileHover={{ y: -6 }}
              className="flex flex-col rounded-xl border border-white/12 bg-white/2 p-6 shadow-[0_8px_40px_rgba(0,0,0,0.35)] backdrop-blur-md transition-all duration-300 hover:border-white/20"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-white/20 bg-white/4 text-slate-200">
                <feature.icon className="h-6 w-6" strokeWidth={1.5} />
              </div>
              <h3 className="font-heading mt-5 text-lg font-medium text-[#F8FAFC]">{feature.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-[#9CA3AF]">{feature.description}</p>
            </motion.div>
          ))}
        </div>
      </section>

      <section id="workflow" className="relative mx-auto max-w-7xl px-6 py-24">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          variants={fadeUp}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="mx-auto max-w-2xl text-center"
        >
          <h2 className="font-heading text-3xl font-medium tracking-tight text-[#F8FAFC] sm:text-4xl">Workflow</h2>
          <p className="mt-4 text-base text-[#9CA3AF]">One pipeline, five stages, zero manual test writing.</p>
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          variants={fadeUp}
          transition={{ duration: 0.7, ease: "easeOut", delay: 0.2 }}
          className="mt-14 flex flex-col items-center gap-4 lg:flex-row lg:justify-between lg:gap-4"
        >
          {workflow.map((step, index) => (
            <div key={step.label} className="flex flex-col items-center gap-4 lg:flex-row">
              <div className="flex w-40 flex-col items-center gap-3 rounded-xl border border-white/12 bg-white/2 p-6 text-center shadow-[0_8px_40px_rgba(0,0,0,0.35)] backdrop-blur-md transition-all duration-300 hover:border-white/20">
                <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-white/20 bg-white/4 text-slate-200">
                  <step.icon className="h-5 w-5" strokeWidth={1.5} />
                </div>
                <span className="font-heading text-sm font-medium text-[#F8FAFC]">{step.label}</span>
              </div>
              {index < workflow.length - 1 && (
                <ArrowRight className="h-5 w-5 shrink-0 rotate-90 text-[#9CA3AF]/50 lg:rotate-0" />
              )}
            </div>
          ))}
        </motion.div>
      </section>

      <section className="relative mx-auto max-w-4xl px-6 pb-32">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          variants={fadeUp}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="flex flex-col items-center rounded-xl border border-white/12 bg-white/2 px-8 py-16 text-center shadow-[0_8px_40px_rgba(0,0,0,0.35)] backdrop-blur-md"
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-lg border border-white/20 bg-white/4 text-slate-200">
            <ShieldCheck className="h-7 w-7" strokeWidth={1.5} />
          </div>
          <h2 className="font-heading mt-6 text-3xl font-medium tracking-tight text-[#F8FAFC] sm:text-4xl">
            Ship AI with confidence.
          </h2>
          <p className="mt-4 max-w-xl text-base text-[#9CA3AF]">Stress-test your AI agent before your users do.</p>
          <Link href="/dashboard" className="mt-8">
            <motion.span
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.98 }}
              transition={{ duration: 0.3 }}
              className="flex items-center gap-2 rounded-full border border-white/20 bg-white/4 px-10 py-4 text-base font-medium text-[#F8FAFC] backdrop-blur-md transition-all duration-300 hover:border-white/40 hover:bg-white/[0.12] hover:shadow-[0_0_32px_rgba(255,255,255,0.2)]"
            >
              Start Testing
              <ArrowRight className="h-5 w-5" />
            </motion.span>
          </Link>
        </motion.div>
      </section>
    </main>
  );
}
