import { Button, Sheet, toast } from "@gharbasket/ui";
import { CopyIcon, WhatsappLogoIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { inviteLink, whatsappShare } from "./invite.js";
import { useRecordInvite } from "./queries.js";

/**
 * Invite neighbours: the message they'll get, the reward rule, then WhatsApp or Copy link
 * (design Core-S3, M9 referrals). Every invite is recorded, so rewards can be matched later.
 * `message` replaces the invite text, e.g. with this month's impact (HcImpact); the link stays.
 * @param {{ open: boolean, onClose: () => void, society: { id: string, name: string, captain: { name: string } | null, unlock: { joined: number, threshold: number }, status: string }, message?: (link: string) => string }} props
 */
export function ShareSheet({ open, onClose, society: s, message: custom }) {
  const { t } = useTranslation();
  const record = useRecordInvite();
  const link = inviteLink(s.id);
  const message = custom
    ? custom(link)
    : s.captain
      ? t("society.share.message", { captain: s.captain.name, link })
      : t("society.share.messageUnlocking", {
          n: Math.max(0, s.unlock.threshold - s.unlock.joined),
          society: s.name,
          link,
        });

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={t("society.share.title", { society: s.name })}
      footer={
        <div className="flex flex-col gap-2">
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => {
              whatsappShare(message);
              record.mutate({ societyId: s.id, channel: "whatsapp" });
              onClose();
            }}
          >
            <WhatsappLogoIcon aria-hidden size={22} weight="fill" />
            {t("society.share.whatsapp")}
          </Button>
          <Button
            variant="secondary"
            size="lg"
            block
            onPress={async () => {
              try {
                await navigator.clipboard.writeText(message);
                toast(t("onboarding.done.copied"));
              } catch {
                toast(t("society.share.copyFailed"));
              }
              record.mutate({ societyId: s.id, channel: "copy" });
            }}
          >
            <CopyIcon aria-hidden size={20} />
            {t("society.share.copy")}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("society.share.preview")}
        </span>
        <p className="rounded-[16px_16px_16px_4px] bg-surface px-3.5 py-2.5 text-sm leading-5">
          {message}
        </p>
        <p className="text-sm leading-5 text-fg-secondary">{t("society.share.reward")}</p>
      </div>
    </Sheet>
  );
}
