import Link from "next/link";

export const metadata = {
  title: "Terms of Service - PolyPredictor",
};

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-[#0b0d10] px-5 py-8 text-slate-100">
      <article className="mx-auto max-w-3xl">
        <Link href="/" className="text-sm text-emerald-300 hover:text-emerald-200">
          Dashboard
        </Link>
        <h1 className="mt-5 text-3xl font-semibold">Terms of Service</h1>
        <p className="mt-2 text-sm text-slate-400">Effective April 25, 2026</p>

        <section className="mt-8 space-y-4 text-sm leading-6 text-slate-300">
          <p>
            PolyPredictor is decision-support software for prediction-market
            research. It provides analytics, model probabilities, alerts, and
            journaling tools. It does not place trades, custody assets, provide
            investment advice, or guarantee market outcomes.
          </p>
          <p>
            You are responsible for your own trading decisions and for complying
            with laws, platform terms, and tax obligations that apply to you. Do
            not use PolyPredictor where prediction-market access is restricted
            or unlawful.
          </p>
          <p>
            During beta, access may be limited, modified, suspended, or revoked
            to protect system stability, data providers, or users. Features and
            model outputs may change without notice.
          </p>
          <p>
            You may connect read-only Polymarket addresses and optional CLOB
            credentials for journaling. In v1, those credentials are not used to
            submit orders. You must not provide credentials you are not
            authorized to use.
          </p>
          <p>
            The service is provided as-is, without warranties. To the maximum
            extent permitted by law, PolyPredictor is not liable for trading
            losses, missed alerts, data delays, model errors, provider outages,
            or indirect damages.
          </p>
          <p>
            Feedback you submit may be used to improve the product. You retain
            ownership of your own content, but grant PolyPredictor permission to
            process it for support, debugging, and product improvement.
          </p>
        </section>
      </article>
    </main>
  );
}
