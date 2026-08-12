"use client";

import { useEffect, useState, createContext, useContext } from "react";
import { usePathname } from "next/navigation";
import { verifySession, logout, getToken, clearToken, type VerifyResponse } from "@/lib/api";
import { loadDeploymentConfig } from "@/lib/countries";
import LoadingBar from "@/app/loading-bar";

const PUBLIC_PATHS = ["/login", "/register", "/setup", "/seed", "/forgot-password", "/reset-password"];

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
    // Load deployment config (country, tiers, tags) on startup.
    // This is fire-and-forget — the config caches itself and
    // components access it synchronously via getDeploymentConfig().
    loadDeploymentConfig();

    if (isPublic) {
      setLoading(false);
      return;
    }

    const token = getToken();
    if (!token) {
      // Anon visitors see the landing feed at "/"; everywhere else still
      // redirects to login. The page component branches on `session === null`.
      if (pathname === "/") {
        setLoading(false);
        return;
      }
      window.location.href = "/login";
      return;
    }

    verifySession().then((result) => {
      if (!result || !result.valid) {
        clearToken();
        window.location.href = "/login";
        return;
      }
      if (!result.setup_complete) {
        window.location.href = "/setup";
        return;
      }
      if (!result.seeding_complete) {
        window.location.href = "/seed";
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
