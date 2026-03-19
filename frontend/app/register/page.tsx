"use client";

import { useState } from "react";
import { registerAccount, getToken } from "@/lib/api";
import LoadingBar from "@/app/loading-bar";

function validateEmail(v: string): string {
  if (!v.trim()) return "Email is required";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return "Enter a valid email address";
  return "";
}

function validatePassword(v: string): string {
  if (!v) return "Password is required";
  if (v.length < 8) return "Password must be at least 8 characters";
  return "";
}

function validateOrgName(v: string): string {
  if (!v.trim()) return "Organisation name is required";
  if (v.trim().length < 2) return "Organisation name is too short";
  return "";
}

function validateUrl(v: string): string {
  if (!v.trim()) return "Website or social URL is required";
  const withScheme = v.match(/^https?:\/\//) ? v : `https://${v}`;
  try {
    const url = new URL(withScheme);
    if (!url.hostname.includes(".")) return "Enter a valid URL (e.g. yourorg.com)";
  } catch {
    return "Enter a valid URL (e.g. yourorg.com)";
  }
  return "";
}

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
  };

  const hasErrors = Object.values(fieldErrors).some(Boolean);

  const markTouched = (field: string) =>
    setTouched((prev) => ({ ...prev, [field]: true }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({ orgName: true, websiteUrl: true, email: true, password: true });
    if (hasErrors) return;
    setError("");
    setLoading(true);
    try {
      await registerAccount(email, password, orgName, websiteUrl);
      window.location.href = "/setup";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-stone-50">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-stone-800">GoBuga</h1>
          <p className="text-sm text-stone-500 mt-1">Create your free account</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-white border border-stone-200 rounded-lg p-6 space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-stone-600 mb-1">Organisation name</label>
            <input
              type="text"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              onBlur={() => markTouched("orgName")}
              autoFocus
              className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none ${touched.orgName && fieldErrors.orgName ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder="Your Nonprofit"
            />
            {touched.orgName && fieldErrors.orgName && (
              <p className="text-xs text-red-500 mt-1">{fieldErrors.orgName}</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-stone-600 mb-1">Website or social media URL</label>
            <input
              type="text"
              value={websiteUrl}
              onChange={(e) => setWebsiteUrl(e.target.value)}
              onBlur={() => markTouched("websiteUrl")}
              className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none ${touched.websiteUrl && fieldErrors.websiteUrl ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder="yourorg.com"
            />
            {touched.websiteUrl && fieldErrors.websiteUrl && (
              <p className="text-xs text-red-500 mt-1">{fieldErrors.websiteUrl}</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-stone-600 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => markTouched("email")}
              className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none ${touched.email && fieldErrors.email ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder="you@org.com"
            />
            {touched.email && fieldErrors.email && (
              <p className="text-xs text-red-500 mt-1">{fieldErrors.email}</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-stone-600 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onBlur={() => markTouched("password")}
              className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none ${touched.password && fieldErrors.password ? "border-red-300 focus:border-red-400" : "border-stone-300 focus:border-blue-400"}`}
              placeholder="Min 8 characters"
            />
            {touched.password && fieldErrors.password && (
              <p className="text-xs text-red-500 mt-1">{fieldErrors.password}</p>
            )}
          </div>

          {loading && (
            <LoadingBar label="Creating account..." />
          )}
          <button
            type="submit"
            disabled={loading || hasErrors}
            className="w-full px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Creating account..." : "Create account"}
          </button>

          <p className="text-xs text-stone-400 text-center">
            Free plan includes 2 scans/month and 1 active case.
          </p>
        </form>

        <p className="text-center text-xs text-stone-400 mt-4">
          Already have an account?{" "}
          <a href="/login" className="text-blue-600 hover:underline">
            Sign in
          </a>
        </p>
      </div>
    </div>
  );
}
