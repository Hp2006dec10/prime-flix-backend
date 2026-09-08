"use client";

import Link from "next/link";
import { useAuth } from "./context/AuthContext";
import { Film, Shield, Zap, Lock, ArrowRight, PlayCircle, CheckCircle2 } from "lucide-react";

export default function Home() {
  const { user } = useAuth();

  return (
    <div className="flex-1 flex flex-col justify-between bg-slate-50 dark:bg-black transition-colors duration-300">
      {/* Hero Section */}
      <div className="relative overflow-hidden py-16 sm:py-24 lg:py-32 px-4 sm:px-6 lg:px-8">
        {/* Glow Effects */}
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-red-600/15 dark:bg-red-600/20 blur-[120px] rounded-full pointer-events-none" />

        <div className="max-w-4xl mx-auto text-center space-y-8 relative z-10">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-red-600/10 border border-red-600/20 text-red-600 dark:text-red-400 text-xs font-semibold tracking-wide uppercase">
            <Film className="w-4 h-4" />
            <span>Next-Gen Streaming Platform</span>
          </div>

          <h1 className="text-4xl sm:text-6xl font-extrabold tracking-tight text-slate-900 dark:text-white leading-[1.15]">
            Unlimited movies, TV shows, and <span className="text-red-600">secure streaming.</span>
          </h1>

          <p className="text-lg sm:text-xl text-slate-600 dark:text-zinc-400 max-w-2xl mx-auto font-normal">
            Experience production-grade authentication with cryptographically secure 6-digit OTP verification, bcrypt password protection, and JWT session tokens.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-4">
            {user ? (
              <Link
                href="/dashboard"
                className="w-full sm:w-auto px-8 py-3.5 rounded-xl bg-red-600 hover:bg-red-700 text-white font-semibold shadow-lg shadow-red-600/25 flex items-center justify-center gap-2 hover:scale-105 transition-all text-base"
              >
                <span>Go to Account Dashboard</span>
                <ArrowRight className="w-5 h-5" />
              </Link>
            ) : (
              <>
                <Link
                  href="/register"
                  className="w-full sm:w-auto px-8 py-3.5 rounded-xl bg-red-600 hover:bg-red-700 text-white font-semibold shadow-lg shadow-red-600/25 flex items-center justify-center gap-2 hover:scale-105 transition-all text-base"
                >
                  <span>Start Free Trial</span>
                  <ArrowRight className="w-5 h-5" />
                </Link>
                <Link
                  href="/login"
                  className="w-full sm:w-auto px-8 py-3.5 rounded-xl glass-card text-slate-900 dark:text-white hover:bg-slate-100 dark:hover:bg-zinc-800 font-semibold flex items-center justify-center gap-2 transition-all text-base border border-slate-300 dark:border-zinc-800"
                >
                  <PlayCircle className="w-5 h-5 text-red-600" />
                  <span>Existing Member Sign In</span>
                </Link>
              </>
            )}
          </div>

          {/* Key Feature Chips */}
          <div className="pt-8 flex flex-wrap items-center justify-center gap-6 text-xs sm:text-sm text-slate-600 dark:text-zinc-400">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span>Light & Dark Theme</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span>6-Digit Email OTP</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span>Brute Force Lockout</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span>JWT Bearer Auth</span>
            </div>
          </div>
        </div>
      </div>

      {/* Features Grid */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-12 border-t border-slate-200 dark:border-zinc-900">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="glass-card p-6 rounded-2xl border border-slate-200 dark:border-zinc-800 space-y-3">
            <div className="p-3 rounded-xl bg-red-600/10 text-red-600 w-fit">
              <Shield className="w-6 h-6" />
            </div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Robust OTP Security</h3>
            <p className="text-sm text-slate-600 dark:text-zinc-400">
              Only hashed OTP values are stored. Active OTP reuse prevention and 5-attempt verification lockout keep accounts safe.
            </p>
          </div>

          <div className="glass-card p-6 rounded-2xl border border-slate-200 dark:border-zinc-800 space-y-3">
            <div className="p-3 rounded-xl bg-red-600/10 text-red-600 w-fit">
              <Lock className="w-6 h-6" />
            </div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Bcrypt Hashing</h3>
            <p className="text-sm text-slate-600 dark:text-zinc-400">
              Passwords are salted and hashed with bcrypt. 3 failed login attempts trigger an automatic 30-minute lockout.
            </p>
          </div>

          <div className="glass-card p-6 rounded-2xl border border-slate-200 dark:border-zinc-800 space-y-3">
            <div className="p-3 rounded-xl bg-red-600/10 text-red-600 w-fit">
              <Zap className="w-6 h-6" />
            </div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">Seamless Dual Theme</h3>
            <p className="text-sm text-slate-600 dark:text-zinc-400">
              Toggle effortlessly between clean crisp Light Mode and obsidian glassmorphic Dark Mode with full state persistence.
            </p>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-slate-200 dark:border-zinc-900 py-6 text-center text-xs text-slate-500 dark:text-zinc-500">
        &copy; 2026 PrimeFlix Streaming Platform. All authentication features strictly follow AUTH.md specifications.
      </footer>
    </div>
  );
}
