"use client";

import { useState } from "react";
import { login, getToken } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import PasswordInput from "@/app/password-input";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await login(email, password);
      if (!result.setup_complete) {
        window.location.href = "/setup";
      } else {
        window.location.href = "/";
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center auth-bg relative overflow-hidden">
      {/* Floating orbs */}
      <div className="orb orb-blue" style={{ top: '10%', left: '15%' }} />
      <div className="orb orb-yellow" style={{ top: '60%', right: '10%' }} />
      <div className="orb orb-red" style={{ bottom: '20%', left: '50%' }} />

      <div className="w-full max-w-sm relative z-10">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-stone-900 [text-shadow:_-1px_-1px_0_white,_1px_-1px_0_white,_-1px_1px_0_white,_1px_1px_0_white]" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-base text-stone-700 mt-2 font-[family-name:var(--font-dm-sans)]">Sign in to your account</p>
        </div>

        <form onSubmit={handleSubmit} className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 space-y-4 shadow-lg">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-base text-red-700">
              {error}
            </div>
          )}

          <div>
            <label className="block text-base font-medium text-stone-700 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              className="w-full px-3 py-2.5 text-base border border-stone-300 rounded-md focus:outline-none focus:border-blue-400 text-stone-900"
              placeholder="you@org.com"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block text-base font-medium text-stone-700">Password</label>
              <a href="/forgot-password" className="text-sm text-blue-600 hover:underline font-medium">Forgot password?</a>
            </div>
            <PasswordInput
              value={password}
              onChange={setPassword}
              required
              placeholder="Your password"
              className="px-3 py-2.5 text-base border border-stone-300 rounded-md focus:outline-none focus:border-blue-400 text-stone-900"
            />
          </div>

          {loading && (
            <LoadingBar label="Signing in..." />
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2.5 text-base btn-gradient rounded-md disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="text-center text-base text-stone-600 mt-4">
          No account?{" "}
          <a href="/register" className="text-blue-600 hover:underline font-medium">
            Create one for free
          </a>
        </p>
      </div>
    </div>
  );
}
