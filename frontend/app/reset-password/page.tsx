"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { resetPassword } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const passwordError = password && password.length < 8 ? "Must be at least 8 characters" : "";
  const confirmError = confirm && confirm !== password ? "Passwords don't match" : "";
  const canSubmit = password.length >= 8 && password === confirm && !loading;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError("");
    setLoading(true);
    try {
      await resetPassword(token, password);
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="bg-white border border-stone-200 rounded-lg p-6 text-center">
        <p className="text-sm text-stone-700 mb-4">Invalid reset link. No token provided.</p>
        <a href="/forgot-password" className="text-sm text-blue-600 hover:underline">Request a new reset link</a>
      </div>
    );
  }

  if (success) {
    return (
      <div className="bg-white border border-stone-200 rounded-lg p-6 text-center">
        <p className="text-sm text-stone-700 mb-4">Your password has been reset.</p>
        <a
          href="/login"
          className="inline-block px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          Sign in
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white border border-stone-200 rounded-lg p-6 space-y-4">
      {error && (
        <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-xs text-red-700">
          {error}{" "}
          {error.toLowerCase().includes("expired") || error.toLowerCase().includes("invalid") ? (
            <a href="/forgot-password" className="underline">Request a new link</a>
          ) : null}
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-stone-600 mb-1">New password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
          className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none focus:border-blue-400 ${
            passwordError ? "border-red-300" : "border-stone-300"
          }`}
          placeholder="At least 8 characters"
        />
        {passwordError && <p className="text-xs text-red-500 mt-1">{passwordError}</p>}
      </div>

      <div>
        <label className="block text-xs font-medium text-stone-600 mb-1">Confirm password</label>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
          className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none focus:border-blue-400 ${
            confirmError ? "border-red-300" : "border-stone-300"
          }`}
          placeholder="Re-enter your password"
        />
        {confirmError && <p className="text-xs text-red-500 mt-1">{confirmError}</p>}
      </div>

      {loading && <LoadingBar label="Resetting password..." />}
      <button
        type="submit"
        disabled={!canSubmit}
        className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Resetting..." : "Reset password"}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-stone-50">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-stone-800" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-sm text-stone-500 mt-1 font-[family-name:var(--font-dm-sans)]">Set a new password</p>
        </div>

        <Suspense fallback={<div className="bg-white border border-stone-200 rounded-lg p-6 text-center text-sm text-stone-500">Loading...</div>}>
          <ResetPasswordForm />
        </Suspense>

        <p className="text-center text-xs text-stone-400 mt-4">
          <a href="/login" className="text-blue-600 hover:underline">Back to sign in</a>
        </p>
      </div>
    </div>
  );
}
