import Link from "next/link";
import { fetchSystemStatus } from "@/lib/api";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      {/* Header */}
      <header className="border-b border-gray-800 bg-black/50 px-6 py-4 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div className="font-semibold text-white tracking-wide">PolyPredictor</div>
          <nav className="flex items-center gap-6 text-sm">
            <Link href="#features" className="hover:text-white">Features</Link>
            <Link href="#pricing" className="hover:text-white">Pricing</Link>
            <Link href="/status" className="hover:text-white">Status</Link>
            <Link href="/signup" className="rounded bg-gray-800 px-3 py-1.5 text-white hover:bg-gray-700">
              Join Waitlist
            </Link>
          </nav>
        </div>
      </header>

      <main>
        {/* Hero */}
        <section className="mx-auto max-w-4xl px-6 py-24 text-center">
          <Badge>Version 1.0 Closed Beta</Badge>
          <h1 className="mt-6 text-5xl md:text-6xl font-bold tracking-tight text-white leading-tight">
            Institutional edge for <br/><span className="text-sky-400">Polymarket traders</span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-gray-400">
            Real-time conformal probabilities, smart-money tracking, and automated
            journaling for prediction markets. Find the edge the crowd missed.
          </p>
          <div className="mt-10 flex cursor-pointer items-center justify-center gap-4">
            <Link
              href="/signup"
              className="rounded bg-sky-600 px-6 py-3 font-semibold text-white hover:bg-sky-500"
            >
              Request Access
            </Link>
            <Link
              href="#features"
              className="rounded border border-gray-700 bg-black/20 px-6 py-3 font-semibold hover:bg-gray-800"
            >
              View Features
            </Link>
          </div>
          <div className="mt-16 overflow-hidden rounded-lg border border-gray-800 bg-black/40 shadow-2xl">
            {/* Fake dashboard UI screenshot */}
            <div className="flex items-center gap-2 border-b border-gray-800 bg-gray-900/50 px-4 py-2">
              <div className="h-3 w-3 rounded-full bg-rose-500"></div>
              <div className="h-3 w-3 rounded-full bg-yellow-500"></div>
              <div className="h-3 w-3 rounded-full bg-green-500"></div>
            </div>
            <div className="aspect-video bg-gradient-to-br from-gray-900 to-black p-6 text-left">
              <div className="mb-4 h-6 w-1/3 rounded bg-gray-800"></div>
              <div className="mb-2 h-4 w-1/4 rounded bg-gray-800"></div>
              <div className="mb-2 flex items-center justify-between rounded border border-gray-800 bg-gray-900/40 p-4">
                 <div className="h-4 w-1/2 rounded bg-gray-700"></div>
                 <div className="h-4 w-16 rounded bg-sky-900/60"></div>
              </div>
              <div className="flex items-center justify-between rounded border border-yellow-900/30 bg-yellow-900/10 p-4">
                 <div className="h-4 w-1/3 rounded bg-gray-700"></div>
                 <div className="h-4 w-16 rounded bg-yellow-900/50"></div>
              </div>
            </div>
          </div>
        </section>

        {/* Features */}
        <section id="features" className="mx-auto max-w-6xl px-6 py-24">
          <div className="mb-16 text-center">
            <h2 className="text-3xl font-bold text-white">Every signal you need.</h2>
          </div>
          <div className="grid gap-8 md:grid-cols-3">
            <div className="rounded border border-gray-800 bg-gray-900/40 p-6">
              <div className="mb-4 inline-block rounded border border-sky-800 bg-sky-900/30 p-2 text-sky-400">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg>
              </div>
              <h3 className="mb-2 text-lg font-semibold text-white">Conformal Bands</h3>
              <p className="text-sm text-gray-400">
                Stop guessing on binary outcomes. We project full probability distributions and render rigorous confidence intervals that adapt to market volatility.
              </p>
            </div>
            <div className="rounded border border-gray-800 bg-gray-900/40 p-6">
              <div className="mb-4 inline-block rounded border-emerald-800 bg-emerald-900/30 p-2 text-emerald-400">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
              </div>
              <h3 className="mb-2 text-lg font-semibold text-white">Smart Money Tracker</h3>
              <p className="text-sm text-gray-400">
                Watch the whales. We continuously cluster wallets across the CLOB to detect adversarial flow, hidden concentration, and smart-money consensus.
              </p>
            </div>
            <div className="rounded border border-gray-800 bg-gray-900/40 p-6">
              <div className="mb-4 inline-block rounded border border-purple-800 bg-purple-900/30 p-2 text-purple-400">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
              </div>
              <h3 className="mb-2 text-lg font-semibold text-white">Automated Journal</h3>
              <p className="text-sm text-gray-400">
                Link your read-only proxy wallet and we automatically log your PnL against our PIT-accurate predictions. Perfect your calibration organically.
              </p>
            </div>
          </div>
        </section>

        {/* Pricing */}
        <section id="pricing" className="mx-auto max-w-4xl px-6 py-24 pb-32">
          <div className="mb-12 text-center">
            <h2 className="text-3xl font-bold text-white">Simple pricing.</h2>
            <p className="mt-4 text-gray-400">One tier, everything included.</p>
          </div>
          
          <div className="mx-auto max-w-sm rounded-xl border border-sky-800 bg-sky-950/10 p-8 shadow-2xl relative overflow-hidden">
            <div className="absolute top-0 right-0 rounded-bl bg-sky-600 px-3 py-1 text-xs font-bold uppercase tracking-wider text-white">
              V1 Beta
            </div>
            <h3 className="text-xl font-semibold text-white">Pro</h3>
            <div className="mt-4 flex items-baseline gap-1">
              <span className="text-5xl font-bold text-white">$49</span>
              <span className="text-gray-400">/ mo</span>
            </div>
            <ul className="mt-8 space-y-4 text-sm text-gray-300">
              <li className="flex items-center gap-3">
                <CheckIcon /> All ensemble models + baseline priors
              </li>
              <li className="flex items-center gap-3">
                <CheckIcon /> Live signal feed (Arb, Whale flow)
              </li>
              <li className="flex items-center gap-3">
                <CheckIcon /> Read-only wallet tracking
              </li>
              <li className="flex items-center gap-3">
                <CheckIcon /> Personal tuning profiles
              </li>
            </ul>
            <Link
              href="/signup"
              className="mt-8 block w-full rounded bg-sky-600 py-3 text-center font-semibold text-white hover:bg-sky-500"
            >
              Join Waitlist
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-gray-800 bg-black/50 py-12 text-sm text-gray-500">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 md:flex-row px-6">
          <div>&copy; 2026 PolyPredictor. All rights reserved.</div>
          <div className="flex gap-6">
            <Link href="/terms" className="hover:text-gray-300">Terms</Link>
            <Link href="/privacy" className="hover:text-gray-300">Privacy Policy</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function CheckIcon() {
  return (
    <svg className="h-5 w-5 text-sky-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-sky-800/60 bg-sky-900/30 px-3 py-1 text-xs font-medium text-sky-300">
      {children}
    </span>
  );
}
