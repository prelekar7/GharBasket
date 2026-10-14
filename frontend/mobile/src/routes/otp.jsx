import { Button, IconButton, toast } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { OtpBoxes } from "@/components/OtpBoxes.jsx";
import { resumeStep } from "@/features/onboarding/draft.js";
import { exchange, me } from "@/lib/api.js";
import { OtpError, pendingPhone, sendCode, verifyCode } from "@/lib/otp.js";
import { nextSearch } from "@/lib/search.js";
import { useSession } from "@/lib/session.js";

const RESEND_AFTER_S = 30;

/** Maps Firebase auth error codes to copy keys. @param {unknown} e */
const errorKey = (e) => {
  const code = e instanceof OtpError ? e.code : "";
  if (code === "auth/invalid-verification-code") return "otp.invalid";
  if (code === "auth/code-expired") return "otp.expired";
  if (code === "auth/too-many-requests") return "otp.tooMany";
  if (code === "auth/operation-not-allowed") return "otp.region";
  return "otp.failed";
};

/** Enter the SMS code, then exchange the Firebase ID token for our session (design Hf03 OTP). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/otp")({
  validateSearch: nextSearch,
  beforeLoad: () => {
    if (!pendingPhone()) throw redirect({ to: "/login" });
  },
  component: Otp,
});

function Otp() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const signIn = useSession((s) => s.signIn);
  const { next } = Route.useSearch();
  const phone = pendingPhone() ?? "";
  const [code, setCode] = useState("");
  const [error, setError] = useState(/** @type {string | null} */ (null));
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(RESEND_AFTER_S);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  const verify = async () => {
    setBusy(true);
    setError(null);
    try {
      const { phone: verified, idToken } = await verifyCode(code);
      const out = await exchange(idToken ? { firebaseIdToken: idToken } : { devPhone: verified });
      signIn(await me());
      // A new household sets up its society, flat and family first.
      navigate({ to: out.is_new_user ? resumeStep() : (next ?? "/") });
    } catch (e) {
      setError(t(errorKey(e)));
      setBusy(false);
    }
  };

  const resend = async () => {
    setError(null);
    try {
      await sendCode(phone, "recaptcha");
      setWait(RESEND_AFTER_S);
      toast(t("otp.resent"));
    } catch (e) {
      setError(t(errorKey(e)));
    }
  };

  const display = phone.replace(/^\+91(\d{5})(\d{5})$/, "+91 $1 $2");
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-5 px-5 pt-[calc(12px+env(safe-area-inset-top))] pb-6">
      <div className="-ml-3 flex min-h-12 items-center">
        <IconButton
          label={t("otp.back")}
          variant="quiet"
          onPress={() => navigate({ to: "/login", search: { next } })}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
      </div>
      <div className="flex flex-col gap-1">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("otp.title")}</h1>
        <div className="flex flex-wrap items-center gap-1">
          <p className="text-fg-secondary">{t("otp.sent", { phone: display })}</p>
          <Button variant="quiet" onPress={() => navigate({ to: "/login", search: { next } })}>
            {t("otp.change")}
          </Button>
        </div>
      </div>
      <form
        className="flex flex-1 flex-col gap-5"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (code.length === 6) verify();
        }}
      >
        <OtpBoxes legend={t("otp.legend")} value={code} onChange={setCode} error={error} />
        <p className="text-sm text-fg-secondary">{t("otp.auto")}</p>
        <div className="mt-auto flex flex-col gap-3">
          <Button
            type="submit"
            variant="primary"
            size="lg"
            block
            isDisabled={code.length !== 6}
            isPending={busy}
          >
            {t("otp.cta")}
          </Button>
          {wait > 0 ? (
            <p className="text-center text-sm text-fg-secondary tabular-nums">
              {t("otp.resendIn", { time: `0:${String(wait).padStart(2, "0")}` })}
            </p>
          ) : (
            <Button variant="quiet" block onPress={resend}>
              {t("otp.resend")}
            </Button>
          )}
        </div>
      </form>
      <div id="recaptcha" />
    </main>
  );
}
