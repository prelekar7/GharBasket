import { Button, TextField } from "@gharbasket/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Controller, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { MOCK } from "@/lib/env.js";
import { sendCode } from "@/lib/otp.js";
import { nextSearch } from "@/lib/search.js";

// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/login")({ validateSearch: nextSearch, component: Login });

// Indian mobile numbers: 10 digits starting 6-9 (spaces allowed while typing).
const schema = z.object({
  phone: z
    .string()
    .transform((v) => v.replace(/\s/g, ""))
    .pipe(z.string().regex(/^[6-9]\d{9}$/)),
});

function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { next } = Route.useSearch();
  const { control, handleSubmit, formState, setError } = useForm({
    resolver: zodResolver(schema),
    defaultValues: { phone: "" },
  });

  const onSubmit = handleSubmit(async ({ phone }) => {
    try {
      await sendCode(`+91${phone}`, "recaptcha");
      navigate({ to: "/otp", search: { next } });
    } catch (e) {
      const region = /** @type {any} */ (e)?.code === "auth/operation-not-allowed";
      setError("root", { message: t(region ? "otp.region" : "login.sendFailed") });
    }
  });

  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-5 px-5 pt-[calc(48px+env(safe-area-inset-top))] pb-6">
      <h1 className="font-display text-[28px] leading-[34px] font-semibold text-balance">
        {t("login.title")}
      </h1>
      <p className="-mt-3 text-fg-secondary">{t("login.sub")}</p>
      <form onSubmit={onSubmit} className="flex flex-1 flex-col gap-5" noValidate>
        <Controller
          name="phone"
          control={control}
          render={({ field, fieldState }) => (
            <TextField
              label={t("login.phone")}
              prefix="+91"
              type="tel"
              inputMode="tel"
              autoComplete="tel-national"
              placeholder="98200 12345"
              value={field.value}
              onChange={field.onChange}
              onBlur={field.onBlur}
              isInvalid={fieldState.invalid}
              errorMessage={t("login.invalid")}
            />
          )}
        />
        {formState.errors.root && (
          <p role="alert" className="text-sm font-semibold text-danger">
            {formState.errors.root.message}
          </p>
        )}
        <div className="mt-auto flex flex-col gap-3">
          {MOCK && <p className="text-sm text-fg-secondary">{t("login.mock")}</p>}
          <Button
            type="submit"
            variant="primary"
            size="lg"
            block
            isPending={formState.isSubmitting}
          >
            {t("login.cta")}
          </Button>
        </div>
      </form>
      <div id="recaptcha" />
    </main>
  );
}
