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
    <div className="min-h-screen flex items-center justify-center auth-bg relative overflow-hidden">
      <div className="orb orb-blue" style={{ top: '20%', right: '15%' }} />
      <div className="orb orb-yellow" style={{ bottom: '25%', left: '20%' }} />

      <div className="w-full max-w-sm relative z-10">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-stone-900 [text-shadow:_-1px_-1px_0_white,_1px_-1px_0_white,_-1px_1px_0_white,_1px_1px_0_white]" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-sm text-stone-700 mt-2 font-[family-name:var(--font-dm-sans)]">Reset your password</p>
        </div>

        {sent ? (
          <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 shadow-lg">
            <p className="text-sm text-stone-800 mb-4">
              If an account exists for <strong>{email}</strong>, we've sent a password reset link. Check your inbox.
            </p>
            <p className="text-sm text-stone-600">
              The link expires in 15 minutes. Didn't receive it? Check your spam folder or{" "}
              <button onClick={() => setSent(false)} className="text-blue-600 hover:underline font-medium">try again</button>.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 space-y-4 shadow-lg">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            )}

            <p className="text-sm text-stone-600">
              Enter the email address you used to register and we'll send you a link to reset your password.
            </p>

            <div>
              <label className="block text-sm font-medium text-stone-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
                className="w-full px-3 py-2.5 text-sm border border-stone-300 rounded-md focus:outline-none focus:border-blue-400 text-stone-900"
                placeholder="you@org.com"
              />
            </div>

            {loading && <LoadingBar label="Sending reset link..." />}
            <button
              type="submit"
              disabled={loading}
              className="w-full px-4 py-2.5 text-sm btn-gradient rounded-md disabled:opacity-50"
            >
              {loading ? "Sending..." : "Send reset link"}
            </button>
          </form>
        )}

        <p className="text-center text-sm text-stone-600 mt-4">
          <a href="/login" className="text-blue-600 hover:underline font-medium">Back to sign in</a>
        </p>
      </div>
    </div>
  );
}
