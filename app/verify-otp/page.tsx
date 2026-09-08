"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import OTPInput from "../components/OTPInput";
import { useAuth } from "../context/AuthContext";
import { ShieldCheck, RefreshCw, AlertCircle, ArrowLeft, Mail } from "lucide-react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

function VerifyOTPContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const emailParam = searchParams.get("email") || "";
  const { login: authLogin } = useAuth();

  const [email, setEmail] = useState(emailParam);
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(60);

  useEffect(() => {
    if (emailParam) setEmail(emailParam);
  }, [emailParam]);

  // Resend timer countdown
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setInterval(() => setCooldown((prev) => prev - 1), 1000);
    return () => clearInterval(timer);
  }, [cooldown]);

  const handleVerify = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (otp.length < 6) {
      setError("Please enter the complete 6-digit OTP code.");
      return;
    }

    setError(null);
    setSuccessMsg(null);
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE_URL}/auth/verify-registration-otp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), otp }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.detail || "Verification failed.");
        setLoading(false);
        return;
      }

      setSuccessMsg("Account verified successfully! Redirecting...");
      authLogin(data.access_token, data.refresh_token, data.user);
      setTimeout(() => {
        router.push("/dashboard");
      }, 1000);
    } catch (err: any) {
      setError(err.message || "Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    if (cooldown > 0 || resending) return;
    setError(null);
    setSuccessMsg(null);
    setResending(true);

    try {
      const res = await fetch(`${API_BASE_URL}/auth/resend-otp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), otp_type: "registration" }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.detail || "Failed to resend OTP.");
      } else {
        setSuccessMsg(data.message);
        setCooldown(60);
      }
    } catch (err: any) {
      setError(err.message || "Failed to connect to server.");
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-3 sm:p-6 lg:p-8 bg-slate-50 dark:bg-black transition-colors duration-300">
      <div className="w-full max-w-md flex flex-col items-center justify-center mx-auto space-y-3 sm:space-y-4">
        {/* Header */}
        <div className="text-center space-y-1.5 sm:space-y-2">
          <div className="inline-flex p-2 sm:p-3 rounded-2xl bg-red-600/10 text-red-600 dark:bg-red-600/20 dark:text-red-500">
            <ShieldCheck className="w-6 h-6 sm:w-8 sm:h-8" />
          </div>
          <h1 className="text-xl sm:text-3xl font-bold tracking-tight text-slate-900 dark:text-white">
            Verify Email Address
          </h1>
          <p className="text-xs sm:text-sm text-slate-600 dark:text-zinc-400">
            Enter the 6-digit verification code sent to
          </p>
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-200/70 dark:bg-zinc-800 text-xs font-semibold text-slate-800 dark:text-zinc-200">
            <Mail className="w-3.5 h-3.5" />
            <span>{email || "your email"}</span>
          </div>
        </div>

        {/* Form Card */}
        <div className="glass-card w-full p-4 sm:p-6 lg:p-8 rounded-2xl border border-slate-200 dark:border-zinc-800 shadow-xl transition-all">
          {error && (
            <div className="mb-5 p-3.5 rounded-xl bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800/50 flex items-start gap-2.5 text-red-600 dark:text-red-400 text-sm">
              <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {successMsg && (
            <div className="mb-5 p-3.5 rounded-xl bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800/50 flex items-start gap-2.5 text-emerald-700 dark:text-emerald-400 text-sm">
              <ShieldCheck className="w-5 h-5 shrink-0 mt-0.5" />
              <span>{successMsg}</span>
            </div>
          )}

          <form onSubmit={handleVerify} className="space-y-5">
            {!emailParam && (
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-zinc-300 mb-1.5">
                  Email Address
                </label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@example.com"
                  className="w-full px-4 py-2.5 text-sm rounded-xl border border-slate-200 dark:border-zinc-800 bg-slate-50 dark:bg-zinc-900 text-slate-900 dark:text-white outline-none focus:border-red-600"
                />
              </div>
            )}

            <div>
              <label className="block text-center text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-zinc-300 mb-2">
                6-Digit Security Code
              </label>
              <OTPInput value={otp} onChange={setOtp} disabled={loading} />
            </div>

            <button
              type="submit"
              disabled={loading || otp.length < 6}
              className="w-full py-3 px-4 rounded-xl bg-red-600 hover:bg-red-700 text-white font-semibold shadow-lg shadow-red-600/25 flex items-center justify-center gap-2 hover:scale-[1.01] active:scale-[0.99] transition-all disabled:opacity-50"
            >
              {loading ? (
                <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : (
                <span>Verify Account</span>
              )}
            </button>
          </form>

          {/* Resend Section */}
          <div className="mt-6 pt-4 border-t border-slate-200 dark:border-zinc-800/80 flex flex-col sm:flex-row items-center justify-between gap-3 text-sm">
            <span className="text-slate-600 dark:text-zinc-400 text-xs sm:text-sm">
              Didn&apos;t receive the code?
            </span>
            <button
              type="button"
              onClick={handleResend}
              disabled={cooldown > 0 || resending}
              className="inline-flex items-center gap-1.5 font-semibold text-red-600 hover:text-red-500 disabled:text-slate-400 dark:disabled:text-zinc-600 transition-colors text-xs sm:text-sm"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${resending ? "animate-spin" : ""}`} />
              <span>
                {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend Code"}
              </span>
            </button>
          </div>
        </div>

        {/* Back Link */}
        <div className="text-center">
          <button
            onClick={() => router.push("/register")}
            className="inline-flex items-center gap-1 text-sm text-slate-600 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-white transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Registration</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export default function VerifyOTPPage() {
  return (
    <Suspense fallback={
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-8 h-8 border-4 border-red-600 border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <VerifyOTPContent />
    </Suspense>
  );
}
