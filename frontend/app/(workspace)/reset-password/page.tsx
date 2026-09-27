"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { resetPassword } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import PasswordInput from "@/app/password-input";
import { useI18n } from "@/lib/i18n";

function ResetPasswordForm() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const passwordError = password && password.length < 8 ? t("auth.min_8_chars") : "";
  const confirmError = confirm && confirm !== password ? t("auth.passwords_dont_match") : "";
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
      setError(err instanceof Error ? err.message : t("auth.reset_failed"));
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 text-center shadow-lg">
        <p className="text-base text-stone-800 mb-4">{t("auth.invalid_reset_link")}</p>
        <a href="/forgot-password" className="text-base text-blue-600 hover:underline font-medium">{t("auth.request_new_link")}</a>
      </div>
    );
  }

  if (success) {
    return (
      <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 text-center shadow-lg">
        <p className="text-base text-stone-800 mb-4">{t("auth.password_reset_done")}</p>
        <a
          href="/login"
          className="inline-block px-4 py-2.5 text-base btn-gradient rounded-md"
        >
          {t("auth.sign_in")}
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 space-y-4 shadow-lg">
      {error && (
        <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-base text-red-700">
          {error}{" "}
          {error.toLowerCase().includes("expired") || error.toLowerCase().includes("invalid") ? (
            <a href="/forgot-password" className="underline font-medium">{t("auth.request_new_link")}</a>
          ) : null}
        </div>
      )}

      <div>
        <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.new_password")}</label>
        <PasswordInput
          value={password}
          onChange={setPassword}
          required
          autoFocus
          className={`px-3 py-2.5 text-base border rounded-md focus:outline-none focus:border-blue-400 text-stone-900 ${
            passwordError ? "border-red-300" : "border-stone-300"
          }`}
          placeholder={t("auth.at_least_8_chars")}
        />
        {passwordError && <p className="text-sm text-red-600 mt-1">{passwordError}</p>}
      </div>

      <div>
        <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.confirm_password")}</label>
        <PasswordInput
          value={confirm}
          onChange={setConfirm}
          required
          className={`px-3 py-2.5 text-base border rounded-md focus:outline-none focus:border-blue-400 text-stone-900 ${
            confirmError ? "border-red-300" : "border-stone-300"
          }`}
          placeholder={t("auth.reenter_password")}
        />
        {confirmError && <p className="text-sm text-red-600 mt-1">{confirmError}</p>}
      </div>

      {loading && <LoadingBar label={t("auth.resetting_password")} />}
      <button
        type="submit"
        disabled={!canSubmit}
        className="w-full px-4 py-2.5 text-base btn-gradient rounded-md disabled:opacity-50"
      >
        {loading ? t("auth.resetting") : t("auth.reset_password")}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  const { t } = useI18n();
  return (
    <div className="min-h-screen flex items-center justify-center auth-bg relative overflow-hidden">
      <div className="orb orb-blue" style={{ top: '15%', left: '20%' }} />
      <div className="orb orb-red" style={{ bottom: '20%', right: '15%' }} />

      <div className="w-full max-w-sm relative z-10">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-stone-900 [text-shadow:_-1px_-1px_0_white,_1px_-1px_0_white,_-1px_1px_0_white,_1px_1px_0_white]" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-base text-stone-700 mt-2 font-[family-name:var(--font-dm-sans)]">{t("auth.set_new_password")}</p>
        </div>

        <Suspense fallback={<div className="card-gradient border border-stone-200/60 p-6 text-center text-base text-stone-600 shadow-lg">{t("common.loading")}</div>}>
          <ResetPasswordForm />
        </Suspense>

        <p className="text-center text-base text-stone-600 mt-4">
          <a href="/login" className="text-blue-600 hover:underline font-medium">{t("auth.back_to_sign_in")}</a>
        </p>
      </div>
    </div>
  );
}
