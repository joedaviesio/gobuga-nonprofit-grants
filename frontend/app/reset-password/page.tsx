"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { resetPassword } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import PasswordInput from "@/app/password-input";

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
      <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 text-center shadow-lg">
        <p className="text-sm text-stone-800 mb-4">Invalid reset link. No token provided.</p>
        <a href="/forgot-password" className="text-sm text-blue-600 hover:underline font-medium">Request a new reset link</a>
      </div>
    );
  }

  if (success) {
    return (
      <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 text-center shadow-lg">
        <p className="text-sm text-stone-800 mb-4">Your password has been reset.</p>
        <a
          href="/login"
          className="inline-block px-4 py-2.5 text-sm btn-gradient rounded-md"
        >
          Sign in
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 space-y-4 shadow-lg">
      {error && (
        <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-sm text-red-700">
          {error}{" "}
          {error.toLowerCase().includes("expired") || error.toLowerCase().includes("invalid") ? (
            <a href="/forgot-password" className="underline font-medium">Request a new link</a>
          ) : null}
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-stone-700 mb-1">New password</label>
        <PasswordInput
          value={password}
          onChange={setPassword}
          required
          autoFocus
          className={`px-3 py-2.5 text-sm border rounded-md focus:outline-none focus:border-blue-400 text-stone-900 ${
            passwordError ? "border-red-300" : "border-stone-300"
          }`}
          placeholder="At least 8 characters"
        />
        {passwordError && <p className="text-xs text-red-600 mt-1">{passwordError}</p>}
      </div>

      <div>
        <label className="block text-sm font-medium text-stone-700 mb-1">Confirm password</label>
        <PasswordInput
          value={confirm}
          onChange={setConfirm}
          required
          className={`px-3 py-2.5 text-sm border rounded-md focus:outline-none focus:border-blue-400 text-stone-900 ${
            confirmError ? "border-red-300" : "border-stone-300"
          }`}
          placeholder="Re-enter your password"
        />
        {confirmError && <p className="text-xs text-red-600 mt-1">{confirmError}</p>}
      </div>

      {loading && <LoadingBar label="Resetting password..." />}
      <button
        type="submit"
        disabled={!canSubmit}
        className="w-full px-4 py-2.5 text-sm btn-gradient rounded-md disabled:opacity-50"
      >
        {loading ? "Resetting..." : "Reset password"}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="min-h-screen flex items-center justify-center auth-bg relative overflow-hidden">
      <div className="orb orb-blue" style={{ top: '15%', left: '20%' }} />
      <div className="orb orb-red" style={{ bottom: '20%', right: '15%' }} />

      <div className="w-full max-w-sm relative z-10">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-stone-900" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-sm text-stone-700 mt-2 font-[family-name:var(--font-dm-sans)]">Set a new password</p>
        </div>

        <Suspense fallback={<div className="card-gradient border border-stone-200/60 p-6 text-center text-sm text-stone-600 shadow-lg">Loading...</div>}>
          <ResetPasswordForm />
        </Suspense>

        <p className="text-center text-sm text-stone-600 mt-4">
          <a href="/login" className="text-blue-600 hover:underline font-medium">Back to sign in</a>
        </p>
      </div>
    </div>
  );
}
