import Link from "next/link";

export const metadata = {
  title: "Privacy Policy - PolyPredictor",
};

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-[#0b0d10] px-5 py-8 text-slate-100">
      <article className="mx-auto max-w-3xl">
        <Link href="/" className="text-sm text-emerald-300 hover:text-emerald-200">
          Dashboard
        </Link>
        <h1 className="mt-5 text-3xl font-semibold">Privacy Policy</h1>
        <p className="mt-2 text-sm text-slate-400">Effective April 25, 2026</p>

        <section className="mt-8 space-y-4 text-sm leading-6 text-slate-300">
          <p>
            PolyPredictor collects the information needed to operate the beta:
            account identifiers, connected Polymarket addresses, optional CLOB
            credential metadata, journal entries, tuning settings, push
            preferences, and operational logs.
          </p>
          <p>
            CLOB secrets are encrypted at rest and are not returned by the API
            after submission. In v1 they are used only for read-only journal
            synchronization, not for trading.
          </p>
          <p>
            Journal calls and tuning profiles stay scoped to your account. If
            you opt in to cross-user learning, only grouped, noisy aggregates
            are exported through the differential-privacy pipeline; raw calls
            and market-level user labels are not shared.
          </p>
          <p>
            Browser and worker errors may be recorded with technical context
            such as URL, user agent, stack trace, environment, and sanitized
            metadata. This is used for debugging and reliability monitoring.
          </p>
          <p>
            PolyPredictor stores market data, public wallet data, and provider
            responses for point-in-time modeling and backtesting. Public market
            data may be retained according to the product retention policy.
          </p>
          <p>
            You may request deletion of account-scoped beta data, subject to
            security, abuse-prevention, legal, and backup-retention limits.
          </p>
        </section>
      </article>
    </main>
  );
}
