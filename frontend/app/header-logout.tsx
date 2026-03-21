"use client";

import { useAuth } from "./auth-gate";
import { useEffect, useState } from "react";

function formatCountdown(totalSeconds: number): string {
  if (totalSeconds <= 0) return "Ready";
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const mins = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  if (days > 0) return `${days}d ${hours}h ${mins}m`;
  if (hours > 0) return `${hours}h ${mins}m ${secs}s`;
  return `${mins}m ${secs}s`;
}

function CycleCountdown({ expiresAt }: { expiresAt: string }) {
  const [remaining, setRemaining] = useState(() => {
    const diff = Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000);
    return Math.max(0, diff);
  });

  useEffect(() => {
    const interval = setInterval(() => {
      const diff = Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000);
      setRemaining(Math.max(0, diff));
    }, 1000);
    return () => clearInterval(interval);
  }, [expiresAt]);

  const isExpired = remaining <= 0;

  return (
    <span className={`text-xs font-mono tabular-nums ${isExpired ? "text-green-600" : "text-slate-500"}`}>
      {isExpired ? (
        <span className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 bg-green-500 rounded-full" />
          Ready
        </span>
      ) : (
        <span className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 bg-amber-400 rounded-full animate-pulse" />
          {formatCountdown(remaining)}
        </span>
      )}
    </span>
  );
}

export function HeaderLogout() {
  const { logout, session } = useAuth();

  if (!session) return null;

  const cycleTimer = session.cycle_timer;

  return (
    <div className="flex items-center gap-2 sm:gap-3 text-nowrap">
      {/* Cycle status */}
      {cycleTimer && !cycleTimer.expired ? (
        <CycleCountdown expiresAt={cycleTimer.expires_at} />
      ) : (
        <span className="text-xs font-mono flex items-center gap-1 text-blue-600">
          <span className="w-1.5 h-1.5 bg-blue-500 rounded-full" />
          Cycle ready
        </span>
      )}
      {session.org_name && (
        <span className="text-xs text-slate-400 hidden sm:inline">{session.org_name}</span>
      )}
      <a href="/settings" className="text-xs text-slate-400 hover:text-slate-600 transition-colors">
        Settings
      </a>
      <span className="flex items-center gap-1.5">
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
        </span>
        <span className="text-xs text-green-600 hidden sm:inline">Online</span>
      </span>
      <button
        onClick={logout}
        className="text-xs text-slate-400 hover:text-slate-600 transition-colors"
      >
        Sign out
      </button>
    </div>
  );
}
