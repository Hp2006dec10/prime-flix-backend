"use client";

import { Check, X } from "lucide-react";

interface Props {
  password: string;
}

export default function PasswordStrengthMeter({ password }: Props) {
  const hasMinLen = password.length >= 8;
  const hasUpper = /[A-Z]/.test(password);
  const hasLower = /[a-z]/.test(password);
  const hasNumber = /[0-9]/.test(password);
  const hasSpecial = /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>/?]/.test(password);

  const criteriaPassed = [hasMinLen, hasUpper, hasLower, hasNumber, hasSpecial].filter(Boolean).length;

  let scoreLabel = "";
  let barColor = "bg-slate-300 dark:bg-zinc-700";
  let percent = "0%";

  if (password.length > 0) {
    if (criteriaPassed <= 2) {
      scoreLabel = "Weak";
      barColor = "bg-red-500";
      percent = "25%";
    } else if (criteriaPassed === 3) {
      scoreLabel = "Fair";
      barColor = "bg-amber-500";
      percent = "50%";
    } else if (criteriaPassed === 4) {
      scoreLabel = "Good";
      barColor = "bg-yellow-500";
      percent = "75%";
    } else if (criteriaPassed === 5) {
      scoreLabel = "Strong";
      barColor = "bg-emerald-500";
      percent = "100%";
    }
  }

  const rules = [
    { label: "At least 8 characters", met: hasMinLen },
    { label: "Uppercase letter (A-Z)", met: hasUpper },
    { label: "Lowercase letter (a-z)", met: hasLower },
    { label: "Numeric digit (0-9)", met: hasNumber },
    { label: "Special character (!@#$%^&*)", met: hasSpecial },
  ];

  if (!password) return null;

  return (
    <div className="mt-2 p-2.5 rounded-lg bg-slate-50 dark:bg-zinc-900/60 border border-slate-200 dark:border-zinc-800 text-[11px] space-y-1.5 transition-all">
      {/* Strength Bar */}
      <div className="flex items-center justify-between font-medium">
        <span className="text-slate-600 dark:text-zinc-400">Password Strength:</span>
        <span className={`font-semibold ${
          scoreLabel === "Weak" ? "text-red-500" :
          scoreLabel === "Fair" ? "text-amber-500" :
          scoreLabel === "Good" ? "text-yellow-500" :
          scoreLabel === "Strong" ? "text-emerald-500" : "text-slate-500"
        }`}>
          {scoreLabel}
        </span>
      </div>

      <div className="w-full h-1 rounded-full bg-slate-200 dark:bg-zinc-800 overflow-hidden">
        <div
          className={`h-full transition-all duration-300 ${barColor}`}
          style={{ width: percent }}
        />
      </div>

      {/* Rules Checklist */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 pt-0.5">
        {rules.map((rule, idx) => (
          <div key={idx} className="flex items-center gap-1 text-[10px] sm:text-[11px]">
            {rule.met ? (
              <Check className="w-3 h-3 text-emerald-500 shrink-0" />
            ) : (
              <X className="w-3 h-3 text-slate-400 dark:text-zinc-600 shrink-0" />
            )}
            <span className={rule.met ? "text-slate-800 dark:text-zinc-200 font-medium" : "text-slate-500 dark:text-zinc-500"}>
              {rule.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
