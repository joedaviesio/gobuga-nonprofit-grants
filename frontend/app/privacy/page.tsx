"use client";

export default function PrivacyPage() {
  return (
    <div className="min-h-screen app-bg">
      <div className="max-w-2xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold text-stone-900 mb-6">Privacy & Data Policy</h1>

        <div className="card-gradient border border-stone-200 p-6 space-y-6">
          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">What We Collect</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              Account details (email, hashed password, organisation name), organisation profile (country, sectors, geographies), uploaded documents, grant cases and conversations, and usage metrics.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">How We Use It</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              Your data is used to scan for grant opportunities, assist with applications, and operate the service (rate limiting, billing). We do not sell your data or use it for advertising.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">Security</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              All connections are encrypted via HTTPS. Passwords are hashed with bcrypt. Each organisation's data is fully isolated. Sessions expire after 30 days.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">Your Rights</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              You can view, update, and export your data at any time through the platform. You can delete uploaded documents from settings. For full account deletion, email us at{" "}
              <a href="mailto:privacy@gobuga.org" className="text-blue-600 underline hover:text-blue-800">privacy@gobuga.org</a>.
            </p>
          </div>

          <div>
            <h2 className="text-lg font-bold text-stone-800 mb-1">Retention</h2>
            <p className="text-base text-stone-700 leading-relaxed">
              Data is retained while your account is active. If you close your account, all data is permanently deleted within 30 days.
            </p>
          </div>

          <p className="text-sm text-stone-600 pt-2 border-t border-stone-100">
            Last updated: March 2026. Questions? Contact <a href="mailto:privacy@gobuga.org" className="text-blue-600 underline hover:text-blue-800">privacy@gobuga.org</a>.
            <br />GoBuga is owned by <a href="https://proxymatches.com" target="_blank" rel="noopener" className="text-blue-600 underline hover:text-blue-800">Proxy Matches</a>.
          </p>
        </div>
      </div>
    </div>
  );
}
