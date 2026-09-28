"use client";

import { useState } from "react";
import { requestPasswordReset } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import { useI18n } from "@/lib/i18n";

export default function ForgotPasswordPage() {
  const { t } = useI18n();
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
      setError(err instanceof Error ? err.message : t("errors.generic"));
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
          <h1 className="text-4xl font-bold text-stone-900 [text-shadow:_-1px_-1px_0_white,_1px_-1px_0_white,_-1px_1px_0_white,_1px_1px_0_white]" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-base text-stone-700 mt-2 font-[family-name:var(--font-dm-sans)]">{t("auth.reset_title")}</p>
        </div>

        {sent ? (
          <div className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 shadow-lg">
            <p className="text-base text-stone-800 mb-4">
              {t("auth.reset_sent_before")} <strong>{email}</strong>{t("auth.reset_sent_after")}
            </p>
            <p className="text-base text-stone-600">
              {t("auth.reset_expiry")}{" "}
              <button onClick={() => setSent(false)} className="text-blue-600 hover:underline font-medium">{t("auth.try_again")}</button>.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="card-gradient border border-stone-200/60 backdrop-blur-sm p-6 space-y-4 shadow-lg">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-base text-red-700">
                {error}
              </div>
            )}

            <p className="text-base text-stone-600">
              {t("auth.reset_instructions")}
            </p>

            <div>
              <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.email")}</label>
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

            {loading && <LoadingBar label={t("auth.sending_reset")} />}
            <button
              type="submit"
              disabled={loading}
              className="w-full px-4 py-2.5 text-base btn-gradient rounded-md disabled:opacity-50"
            >
              {loading ? t("auth.sending") : t("auth.send_reset_link")}
            </button>
          </form>
        )}

        <p className="text-center text-base text-stone-600 mt-4">
          <a href="/login" className="text-blue-600 hover:underline font-medium">{t("auth.back_to_sign_in")}</a>
        </p>
      </div>
    </div>
  );
}
