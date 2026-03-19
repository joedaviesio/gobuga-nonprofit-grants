"use client";

import { useAuth } from "./auth-gate";

export function HeaderLogout() {
  const { logout, session } = useAuth();

  if (!session) return null;

  return (
    <div className="flex items-center gap-3">
      {session.org_name && (
        <span className="text-xs text-stone-400">{session.org_name}</span>
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
