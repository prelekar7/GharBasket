import { PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";

export const metadata = {
  title: { absolute: "Privacy notice · GharBasket" },
  description:
    "What GharBasket collects, why, who sees it, and your rights under India's Digital Personal Data Protection Act, 2023.",
  alternates: { canonical: "/privacy" },
  robots: { index: true, follow: true },
};

const TOC = [
  ["what", "What we collect"],
  ["why", "Why, and your consent"],
  ["health", "Health information"],
  ["who", "Who sees it"],
  ["rights", "Your rights"],
  ["keep", "How long we keep it"],
  ["contact", "Questions and complaints"],
];

/** @param {{ id: string, title: string, last?: boolean, children: import("react").ReactNode }} props */
function Section({ id, title, last = false, children }) {
  return (
    <section
      id={id}
      className={`flex scroll-mt-6 flex-col gap-2.5 pb-5 [&_ul]:list-disc [&_ul]:pl-5 ${last ? "" : "border-b border-line"}`}
    >
      <h2 className="text-[21px] leading-7 font-bold">{title}</h2>
      {children}
    </section>
  );
}

/** The privacy notice of Privacy.dc.html (draft wording, DPDP Act 2023). Fully static. */
export default function PrivacyPage() {
  return (
    <>
      <PublicHeader wide />
      <div className="grid flex-1 gap-8 px-4 py-8 min-[900px]:grid-cols-[240px_minmax(0,720px)] min-[900px]:gap-16 min-[900px]:px-16 min-[900px]:py-12">
        <nav
          aria-label="On this page"
          className="min-[900px]:sticky min-[900px]:top-6 min-[900px]:self-start"
        >
          <p className="mb-1.5 text-[15px] leading-[22px] font-bold text-fg-secondary">
            On this page
          </p>
          <ul className="flex flex-col">
            {TOC.map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`} className="block py-1.5 text-[15px] text-fg no-underline">
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <main id="content" className="flex flex-col gap-6 text-[17px] leading-7">
          <div className="flex flex-col gap-2">
            <h1 className="font-display text-[32px] leading-10 font-semibold min-[900px]:text-[40px] min-[900px]:leading-[46px]">
              Privacy notice
            </h1>
            <p className="text-[15px] leading-[22px] text-fg-secondary">
              Version v1 · under India’s Digital Personal Data Protection Act, 2023. Plain words
              first; ask us if anything is unclear. Draft wording, to be reviewed before launch.
            </p>
          </div>
          <Section id="what" title="What we collect">
            <ul>
              <li>Your phone number, to sign you in with a one-time code.</li>
              <li>
                Your household: who eats, food rules, fasting days, and your society, tower and
                flat.
              </li>
              <li>Your weeks and orders, and your wallet history.</li>
            </ul>
            <p>We never collect or store Aadhaar.</p>
          </Section>
          <Section id="why" title="Why, and your consent">
            <p>
              You choose each purpose separately, and you can change your mind any time in Profile:
            </p>
            <ul>
              <li>
                <strong>Delivering your food</strong> · needed to use GharBasket.
              </li>
              <li>
                <strong>Personalising your week</strong> · needed for Autopilot to plan.
              </li>
              <li>
                <strong>Health-aware planning</strong> · optional.
              </li>
              <li>
                <strong>Offers and news</strong> · optional.
              </li>
              <li>
                <strong>Anonymised research</strong> · optional; used in our college project’s paper
                with nothing that identifies you.
              </li>
            </ul>
          </Section>
          <Section id="health" title="Health information">
            <p>
              We keep health tags (for example “low sugar”) only if you turn on health-aware
              planning. Turning it off deletes them.
            </p>
          </Section>
          <Section id="who" title="Who sees it">
            <p>
              Your Society Captain sees your flat and your bag, never your phone number. Packers,
              riders and kitchens see only what their job needs. We don’t sell your data.
            </p>
          </Section>
          <Section id="rights" title="Your rights">
            <p>
              See and download your data, correct it, withdraw a consent, or delete your account.
              Profile → Your data does all of these.
            </p>
          </Section>
          <Section id="keep" title="How long we keep it">
            <p>
              [RETENTION PERIODS, to be filled in before launch.] Money records are kept as long as
              the law requires.
            </p>
          </Section>
          <Section id="contact" title="Questions and complaints" last>
            <p>Write to our grievance contact: [NAME], [EMAIL]. We reply within [N] days.</p>
          </Section>
        </main>
      </div>
      <PublicFooter wide />
    </>
  );
}
