"use client";

import { useState } from "react";
import { registerAccount, getToken } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";
import PasswordInput from "@/app/password-input";
import { useI18n, type MessageKey } from "@/lib/i18n";

function validateEmail(v: string): MessageKey | "" {
  if (!v.trim()) return "auth.email_required";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return "auth.email_invalid";
  return "";
}

function validatePassword(v: string): MessageKey | "" {
  if (!v) return "auth.password_required";
  if (v.length < 8) return "auth.password_min_8";
  return "";
}

function validateConfirmPassword(password: string, confirm: string): MessageKey | "" {
  if (!confirm) return "auth.confirm_required";
  if (confirm !== password) return "auth.passwords_dont_match";
  return "";
}

function validateOrgName(v: string): MessageKey | "" {
  if (!v.trim()) return "auth.org_name_required";
  if (v.trim().length < 2) return "auth.org_name_too_short";
  return "";
}

function validateUrl(v: string): MessageKey | "" {
  if (!v.trim()) return "auth.url_required";
  const withScheme = v.match(/^https?:\/\//) ? v : `https://${v}`;
  try {
    const url = new URL(withScheme);
    if (!url.hostname.includes(".")) return "auth.url_invalid";
  } catch {
    return "auth.url_invalid";
  }
  return "";
}

export default function RegisterPage() {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  const fieldErrors = {
    orgName: validateOrgName(orgName),
    websiteUrl: validateUrl(websiteUrl),
    email: validateEmail(email),
    password: validatePassword(password),
    confirmPassword: validateConfirmPassword(password, confirmPassword),
  };

  const hasErrors = Object.values(fieldErrors).some(Boolean);

  const markTouched = (field: string) =>
    setTouched((prev) => ({ ...prev, [field]: true }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({ orgName: true, websiteUrl: true, email: true, password: true, confirmPassword: true });
    if (hasErrors) return;
    setError("");
    setLoading(true);
    try {
      await registerAccount(email, password, orgName, websiteUrl);
      window.location.href = "/setup";
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.registration_failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center auth-bg relative overflow-hidden">
      {/* Floating orbs */}
      <div className="orb orb-blue" style={{ top: '5%', right: '20%' }} />
      <div className="orb orb-yellow" style={{ bottom: '15%', left: '10%' }} />
      <div className="orb orb-red" style={{ top: '40%', left: '5%' }} />

      <div className="w-full max-w-md px-4 py-16 relative z-10">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-stone-900 [text-shadow:_-1px_-1px_0_white,_1px_-1px_0_white,_-1px_1px_0_white,_1px_1px_0_white]" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</h1>
          <p className="text-base text-stone-700 mt-2 font-[family-name:var(--font-dm-sans)]">{t("auth.create_free_account")}</p>
        </div>

        <form onSubmit={handleSubmit} className="card-gradient border border-stone-200/60 backdrop-blur-sm p-8 space-y-6 shadow-lg">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-base text-red-700">
              {error}
            </div>
          )}

          <div>
            <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.org_name")}</label>
            <input
              type="text"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              onBlur={() => markTouched("orgName")}
              autoFocus
              className={`w-full px-3 py-2.5 text-base border rounded-md focus:outline-none text-stone-900 ${touched.orgName && fieldErrors.orgName ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder={t("auth.org_name_placeholder")}
            />
            {touched.orgName && fieldErrors.orgName && (
              <p className="text-sm text-red-600 mt-1">{t(fieldErrors.orgName)}</p>
            )}
          </div>

          <div>
            <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.website_url")}</label>
            <input
              type="text"
              value={websiteUrl}
              onChange={(e) => setWebsiteUrl(e.target.value)}
              onBlur={() => markTouched("websiteUrl")}
              className={`w-full px-3 py-2.5 text-base border rounded-md focus:outline-none text-stone-900 ${touched.websiteUrl && fieldErrors.websiteUrl ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder="yourorg.com"
            />
            {touched.websiteUrl && fieldErrors.websiteUrl && (
              <p className="text-sm text-red-600 mt-1">{t(fieldErrors.websiteUrl)}</p>
            )}
          </div>

          <div>
            <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.email")}</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => markTouched("email")}
              className={`w-full px-3 py-2.5 text-base border rounded-md focus:outline-none text-stone-900 ${touched.email && fieldErrors.email ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder="you@org.com"
            />
            {touched.email && fieldErrors.email && (
              <p className="text-sm text-red-600 mt-1">{t(fieldErrors.email)}</p>
            )}
          </div>

          <div>
            <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.password")}</label>
            <PasswordInput
              value={password}
              onChange={setPassword}
              onBlur={() => markTouched("password")}
              className={`px-3 py-2.5 text-base border rounded-md focus:outline-none text-stone-900 ${touched.password && fieldErrors.password ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder={t("auth.min_8_chars_placeholder")}
            />
            {touched.password && fieldErrors.password && (
              <p className="text-sm text-red-600 mt-1">{t(fieldErrors.password)}</p>
            )}
          </div>

          <div>
            <label className="block text-base font-medium text-stone-700 mb-1">{t("auth.confirm_password")}</label>
            <PasswordInput
              value={confirmPassword}
              onChange={setConfirmPassword}
              onBlur={() => markTouched("confirmPassword")}
              className={`px-3 py-2.5 text-base border rounded-md focus:outline-none text-stone-900 ${touched.confirmPassword && fieldErrors.confirmPassword ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder={t("auth.reenter_password")}
            />
            {touched.confirmPassword && fieldErrors.confirmPassword && (
              <p className="text-sm text-red-600 mt-1">{t(fieldErrors.confirmPassword)}</p>
            )}
          </div>

          {loading && (
            <LoadingBar label={t("auth.creating_account")} />
          )}
          <button
            type="submit"
            disabled={loading || hasErrors}
            className="w-full px-4 py-2.5 text-base btn-gradient rounded-md disabled:opacity-50"
          >
            {loading ? t("auth.creating_account") : t("auth.create_account")}
          </button>
        </form>

        <p className="text-center text-base text-stone-600 mt-4">
          {t("auth.already_have_account")}{" "}
          <a href="/login" className="text-blue-600 hover:underline font-medium">
            {t("auth.sign_in")}
          </a>
        </p>
      </div>
    </div>
  );
}
