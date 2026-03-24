"use client";

import { useState } from "react";
import { requestPasswordReset } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await requestPasswordReset(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-stone-50">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-stone-800" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-sm text-stone-500 mt-1 font-[family-name:var(--font-dm-sans)]">Reset your password</p>
        </div>

        {sent ? (
          <div className="bg-white border border-stone-200 rounded-lg p-6">
            <p className="text-sm text-stone-700 mb-4">
              If an account exists for <strong>{email}</strong>, we've sent a password reset link. Check your inbox.
            </p>
            <p className="text-xs text-stone-500">
              The link expires in 15 minutes. Didn't receive it? Check your spam folder or{" "}
              <button onClick={() => setSent(false)} className="text-blue-600 hover:underline">try again</button>.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="bg-white border border-stone-200 rounded-lg p-6 space-y-4">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-xs text-red-700">
                {error}
              </div>
            )}

            <p className="text-xs text-stone-500">
              Enter the email address you used to register and we'll send you a link to reset your password.
            </p>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
                className="w-full px-3 py-2 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400"
                placeholder="you@org.com"
              />
            </div>

            {loading && <LoadingBar label="Sending reset link..." />}
            <button
              type="submit"
              disabled={loading}
              className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Sending..." : "Send reset link"}
            </button>
          </form>
        )}

        <p className="text-center text-xs text-stone-400 mt-4">
          <a href="/login" className="text-blue-600 hover:underline">Back to sign in</a>
        </p>
      </div>
    </div>
  );
}
