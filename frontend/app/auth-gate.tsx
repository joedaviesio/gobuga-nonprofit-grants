"use client";

import { useEffect, useState, createContext, useContext } from "react";
import { usePathname } from "next/navigation";
import { verifySession, logout, getToken, clearToken, type VerifyResponse } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";

const PUBLIC_PATHS = ["/login", "/register", "/setup", "/seed"];

interface AuthContextType {
  logout: () => void;
  session: VerifyResponse | null;
  loading: boolean;
}

const AuthContext = createContext<AuthContextType>({
  logout: () => {},
  session: null,
  loading: true,
});

export const useAuth = () => useContext(AuthContext);

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const [session, setSession] = useState<VerifyResponse | null>(null);
  const [loading, setLoading] = useState(!isPublic);

  useEffect(() => {
    if (isPublic) {
      setLoading(false);
      return;
    }

    const token = getToken();
    if (!token) {
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

  if (isPublic) {
    return (
      <AuthContext.Provider value={{ logout: handleLogout, session, loading: false }}>
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
    <AuthContext.Provider value={{ logout: handleLogout, session, loading }}>
      {children}
    </AuthContext.Provider>
  );
}
