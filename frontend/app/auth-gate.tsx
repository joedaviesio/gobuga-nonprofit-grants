"use client";

import { useEffect, useState, createContext, useContext } from "react";
import { verifySession, logout, getToken, type VerifyResponse } from "@/lib/api";

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
  const [session, setSession] = useState<VerifyResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      // No token — redirect to login
      window.location.href = "/login";
      return;
    }

    verifySession().then((result) => {
      if (!result || !result.valid) {
        window.location.href = "/login";
        return;
      }
      if (!result.setup_complete) {
        // Token valid but setup incomplete — redirect to wizard
        window.location.href = "/setup";
        return;
      }
      setSession(result);
      setLoading(false);
    });
  }, []);

  const handleLogout = async () => {
    await logout();
    window.location.href = "/login";
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-sm text-stone-400">Loading...</div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ logout: handleLogout, session, loading }}>
      {children}
    </AuthContext.Provider>
  );
}
