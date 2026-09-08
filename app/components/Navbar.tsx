"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "../context/ThemeContext";
import { useAuth } from "../context/AuthContext";
import { Sun, Moon, Film, LogOut, User as UserIcon } from "lucide-react";

export default function Navbar() {
  const { theme, toggleTheme } = useTheme();
  const { user, logout } = useAuth();
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 w-full border-b border-slate-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-950/80 backdrop-blur-md transition-colors duration-300">
      <div className="max-w-7xl mx-auto px-3 sm:px-6 lg:px-8 h-12 sm:h-14 lg:h-16 flex items-center justify-between">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-1.5 sm:gap-2 group">
          <div className="p-1.5 sm:p-2 rounded-lg bg-red-600 text-white shadow-md shadow-red-600/30 group-hover:scale-105 transition-transform duration-200">
            <Film className="w-4 h-4 sm:w-5 sm:h-5" />
          </div>
          <span className="font-bold text-md sm:text-xl tracking-tight text-slate-900 dark:text-white">
            PRIME<span className="text-red-600">FLIX</span>
          </span>
        </Link>

        {/* Navigation & Theme Toggle */}
        <div className="flex items-center gap-2 sm:gap-3">
          {/* Theme Toggle Button */}
          <button
            onClick={toggleTheme}
            className="p-1.5 sm:p-2.5 rounded-full text-slate-600 hover:text-slate-900 dark:text-zinc-400 dark:hover:text-white bg-slate-100 hover:bg-slate-200 dark:bg-zinc-900 dark:hover:bg-zinc-800 border border-slate-200 dark:border-zinc-800 transition-all duration-200"
            aria-label="Toggle Theme"
            title={theme === "dark" ? "Switch to Light Mode" : "Switch to Dark Mode"}
          >
            {theme === "dark" ? <Sun className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-amber-400" /> : <Moon className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-slate-700" />}
          </button>

          {/* User Status / Action Links */}
          {user ? (
            <div className="flex items-center gap-2 sm:gap-3">
              <Link
                href="/dashboard"
                className={`flex items-center gap-1.5 text-xs sm:text-sm font-medium px-2.5 sm:px-3.5 py-1.5 sm:py-2 rounded-lg transition-all ${
                  pathname === "/dashboard"
                    ? "bg-red-600 text-white shadow-sm shadow-red-600/20"
                    : "text-slate-700 hover:text-slate-900 dark:text-zinc-300 dark:hover:text-white bg-slate-100 dark:bg-zinc-900"
                }`}
              >
                <UserIcon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                <span className="hidden sm:inline">{user.full_name.split(" ")[0]}</span>
              </Link>
              <button
                onClick={logout}
                className="flex items-center gap-1 text-xs sm:text-sm font-medium px-2 sm:px-3 py-1.5 sm:py-2 rounded-lg text-slate-600 hover:text-red-600 dark:text-zinc-400 dark:hover:text-red-400 transition-colors"
                title="Logout"
              >
                <LogOut className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                <span className="hidden sm:inline">Logout</span>
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 sm:gap-2">
              <Link
                href="/login"
                className={`text-[10px] md:text-sm font-medium px-2.5 sm:px-4 py-1.5 sm:py-2 rounded-lg transition-all ${
                  pathname === "/login"
                    ? "text-red-600 font-semibold"
                    : "text-slate-700 hover:text-slate-900 dark:text-zinc-300 dark:hover:text-white"
                }`}
              >
                Sign In
              </Link>
              <Link
                href="/register"
                className="text-[10px] md:text-sm font-medium px-3 sm:px-4 py-1.5 sm:py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white shadow-md shadow-red-600/25 hover:scale-[1.02] active:scale-[0.98] transition-all"
              >
                Get Started
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
