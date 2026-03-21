"use client";

import { useAuth } from "./auth-gate";

export function HeaderLogout() {
  const { logout, session } = useAuth();

  if (!session) return null;

  return (
    <div className="flex items-center gap-2 sm:gap-3 text-nowrap">
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
