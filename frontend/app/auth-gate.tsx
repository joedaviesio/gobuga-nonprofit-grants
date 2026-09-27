"use client";

import { useEffect, useState, createContext, useContext } from "react";
import { usePathname } from "next/navigation";
import { verifySession, logout, getToken, clearToken, type VerifyResponse } from "@/lib/api";
import { loadDeploymentConfig } from "@/lib/countries";
import { onboardingRedirect } from "@/lib/onboarding";
import LoadingBar from "@/app/loading-bar";

// Workspace-layout pages that render without a session. /seed checks the
// session itself; /setup is redirected to /seed in next.config.ts.
const PUBLIC_PATHS = ["/login", "/register", "/seed", "/forgot-password", "/reset-password", "/privacy", "/terms"];

interface AuthContextType {
  logout: () => void;
  session: VerifyResponse | null;
  loading: boolean;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  logout: () => {},
  session: null,
  loading: true,
  refreshSession: async () => {},
});

export const useAuth = () => useContext(AuthContext);

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const [session, setSession] = useState<VerifyResponse | null>(null);
  const [loading, setLoading] = useState(!isPublic);

  useEffect(() => {
    // Load deployment config (country, taxonomy, languages) on startup.
    // This is fire-and-forget — the config caches itself and
    // components access it synchronously via getDeploymentConfig().
    loadDeploymentConfig();

    // Public paths start with loading=false and render without waiting
    if (isPublic) return;

    const token = getToken();
    if (!token) {
      // The public pages (/, /grants, ...) have their own layout and never
      // reach this gate; every workspace page needs a session.
      window.location.href = "/login";
      return;
    }

    verifySession().then((result) => {
      if (!result || !result.valid) {
        clearToken();
        window.location.href = "/login";
        return;
      }
      const redirect = onboardingRedirect(result, pathname);
      if (redirect) {
        window.location.href = redirect;
        return;
      }
      setSession(result);
      setLoading(false);
    });
  }, [isPublic]);

  const handleLogout = async () => {
    await logout();
    window.location.href = "/login";
  };

  const refreshSession = async () => {
    const result = await verifySession();
    if (result && result.valid) {
      setSession(result);
    }
  };

  if (isPublic) {
    return (
      <AuthContext.Provider value={{ logout: handleLogout, session, loading: false, refreshSession }}>
        {children}
      </AuthContext.Provider>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-white">
        <div className="w-48">
          <LoadingBar />
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ logout: handleLogout, session, loading, refreshSession }}>
      {children}
    </AuthContext.Provider>
  );
}
