"use client";

import React, { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../context/AuthContext";
import { ShieldCheck, User as UserIcon, Mail, Calendar, Key, LogOut, Film, CheckCircle2 } from "lucide-react";

export default function DashboardPage() {
  const router = useRouter();
  const { user, accessToken, loading, logout } = useAuth();

  useEffect(() => {
    if (!loading && !user) {
      router.push("/login");
    }
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 bg-slate-50 dark:bg-black">
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 border-4 border-red-600 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm font-medium text-slate-600 dark:text-zinc-400">Loading your profile...</span>
        </div>
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-4 sm:p-6 lg:p-8 bg-slate-50 dark:bg-black transition-colors duration-300">
      <div className="w-full max-w-lg mx-auto space-y-4 my-auto flex flex-col items-center justify-center">
        {/* Account Overview */}
        <div className="w-full glass-card p-6 rounded-2xl border border-slate-200 dark:border-zinc-800 shadow-md space-y-5">
          <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2 border-b border-slate-200 dark:border-zinc-800 pb-3">
            <UserIcon className="w-5 h-5 text-red-600" />
            <span>Account Overview</span>
          </h2>

          <div className="space-y-4">
            <div className="flex items-center justify-between p-3.5 rounded-xl bg-slate-100/70 dark:bg-zinc-900/70 border border-slate-200/60 dark:border-zinc-800/60">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-slate-200 dark:bg-zinc-800 text-slate-700 dark:text-zinc-300">
                  <UserIcon className="w-4 h-4" />
                </div>
                <div>
                  <div className="text-xs text-slate-500 dark:text-zinc-500">Full Name</div>
                  <div className="text-sm font-semibold text-slate-900 dark:text-white">{user.full_name}</div>
                </div>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 p-3 sm:p-3.5 rounded-xl bg-slate-100/70 dark:bg-zinc-900/70 border border-slate-200/60 dark:border-zinc-800/60">
              <div className="flex items-start sm:items-center gap-3">
                <div className="p-2 rounded-lg bg-slate-200 dark:bg-zinc-800 text-slate-700 dark:text-zinc-300 mt-0.5 sm:mt-0">
                  <Mail className="w-4 h-4" />
                </div>
                <div className="space-y-1 sm:space-y-0">
                  <div className="text-xs text-slate-500 dark:text-zinc-500">Email Address</div>
                  <div className="text-sm font-semibold text-slate-900 dark:text-white break-all sm:break-normal">{user.email}</div>
                  {user.is_verified && (
                    <div className="sm:hidden pt-1">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400 text-[11px] font-semibold">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Verified</span>
                      </span>
                    </div>
                  )}
                </div>
              </div>
              {user.is_verified && (
                <span className="hidden sm:inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400 text-xs font-semibold">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Verified</span>
                </span>
              )}
            </div>

            <div className="flex items-center justify-between p-3.5 rounded-xl bg-slate-100/70 dark:bg-zinc-900/70 border border-slate-200/60 dark:border-zinc-800/60">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-slate-200 dark:bg-zinc-800 text-slate-700 dark:text-zinc-300">
                  <Calendar className="w-4 h-4" />
                </div>
                <div>
                  <div className="text-xs text-slate-500 dark:text-zinc-500">Member Since</div>
                  <div className="text-sm font-semibold text-slate-900 dark:text-white">
                    {new Date(user.created_at).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "long",
                      day: "numeric",
                    })}
                  </div>
                </div>
              </div>
            </div>

            <button
              onClick={() => {
                logout();
                router.push("/login");
              }}
              className="w-full mt-2 py-2.5 px-4 rounded-xl bg-slate-200 hover:bg-red-600 hover:text-white dark:bg-zinc-900 dark:hover:bg-red-600 text-slate-800 dark:text-zinc-200 text-xs sm:text-sm font-semibold flex items-center justify-center gap-2 transition-all"
            >
              <LogOut className="w-4 h-4" />
              <span>Sign Out</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
