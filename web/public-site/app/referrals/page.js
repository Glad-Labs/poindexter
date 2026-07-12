import * as Sentry from '@sentry/nextjs';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { AffiliateDisclosure } from '../../components/AffiliateDisclosure';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

// Time-based ISR backstop (1h) — see app/page.js and app/archive/[page]/page.tsx.
// On-demand revalidateTag('referrals') from the poindexter CLI is primary;
// this floor self-heals if a CLI republish is ever skipped.
export const revalidate = 3600;

export const metadata = {
  title: `Referrals — ${SITE_NAME}`,
  description: `Tools, services, and products ${SITE_NAME} actually uses day to day — with full affiliate disclosure.`,
  alternates: {
    canonical: `${SITE_URL}/referrals`,
  },
};

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

async function fetchReferralsIndex() {
  const response = await fetch(`${STATIC_URL}/affiliate-referrals.json`, {
    // Tag-based cache: invalidated by revalidateTag('referrals') from the
    // poindexter CLI's add/enable/disable/rm commands.
    next: { tags: ['referrals'] },
  });

  if (response.status === 404) {
    // Not published yet — treat as empty, not an error.
    return [];
  }

  if (!response.ok) {
    const err = new Error(
      `fetchReferralsIndex: R2 returned ${response.status} ${response.statusText}`
    );
    Sentry.captureException(err);
    throw err;
  }

  return response.json();
}

async function getReferrals() {
  try {
    return await fetchReferralsIndex();
  } catch {
    return [];
  }
}

function ReferralCard({ referral, accent }) {
  return (
    <Card accent={accent}>
      <Card.Title>{referral.name}</Card.Title>
      <Card.Body>{referral.description}</Card.Body>
      <div className="mt-4">
        <Button
          as="a"
          href={`/go/${referral.code}`}
          target="_blank"
          rel="sponsored noopener"
          variant="secondary"
        >
          Check it out →
        </Button>
      </div>
    </Card>
  );
}

export default async function ReferralsPage() {
  const referrals = await getReferrals();
  const services = referrals.filter((r) => r.category === 'service');
  const products = referrals.filter((r) => r.category === 'product');

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Hero */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-3xl">
          <Eyebrow>GLAD LABS · REFERRALS</Eyebrow>
          <Display xl>
            Tools we use. <Display.Accent>Gear we recommend.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-6">
            Everything below is something Glad Labs actually uses day to day —
            business tools that keep the pipeline running, and products worth
            recommending to readers.
          </p>
        </div>
      </section>

      {/* Disclosure — unconditional, this whole page is affiliate content */}
      <section className="px-4 sm:px-6 lg:px-8 pb-4">
        <div className="container mx-auto max-w-3xl">
          <AffiliateDisclosure />
        </div>
      </section>

      {referrals.length === 0 ? (
        <section className="px-4 sm:px-6 lg:px-8 pb-20">
          <div className="container mx-auto max-w-3xl text-center">
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NOTHING HERE YET</Card.Meta>
              <h2 className="gl-h2 mt-2">Nothing published here yet.</h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                Check back soon — this page fills in as links get added.
              </p>
            </Card>
          </div>
        </section>
      ) : (
        <>
          {services.length > 0 && (
            <section className="px-4 sm:px-6 lg:px-8 pb-12">
              <div className="container mx-auto max-w-3xl">
                <h2 className="gl-h2 mb-6">Tools &amp; Services</h2>
                <div className="grid sm:grid-cols-2 gap-6">
                  {services.map((r) => (
                    <ReferralCard key={r.code} referral={r} accent="cyan" />
                  ))}
                </div>
              </div>
            </section>
          )}

          {products.length > 0 && (
            <section className="px-4 sm:px-6 lg:px-8 pb-20">
              <div className="container mx-auto max-w-3xl">
                <h2 className="gl-h2 mb-6">Amazon Picks</h2>
                <div className="grid sm:grid-cols-2 gap-6">
                  {products.map((r) => (
                    <ReferralCard key={r.code} referral={r} accent="amber" />
                  ))}
                </div>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
