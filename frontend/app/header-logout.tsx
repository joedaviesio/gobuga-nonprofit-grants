"use client";

import { useAuth } from "./auth-gate";

export function HeaderLogout() {
  const { logout, session } = useAuth();

  if (!session) return null;

  return (
    <div className="flex items-center gap-2 sm:gap-3 text-nowrap">
      {session.org_name && (
        <span className="text-xs text-stone-400 hidden sm:inline">{session.org_name}</span>
      )}
      <a href="/settings" className="text-xs text-stone-400 hover:text-stone-600">
        Settings
      </a>
      <button
        onClick={logout}
        className="text-xs text-stone-400 hover:text-stone-600"
      >
        Sign out
      </button>
    </div>
  );
}
