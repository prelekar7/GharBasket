import { Button, Sheet, TextField } from "@gharbasket/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { normalizeCode } from "@/lib/scan.js";

/**
 * Typing a bag or item code when the scan does not work (torn label, no camera, bad light).
 * @param {{ kind: "bag" | "item", open: boolean, onOpenChange: (open: boolean) => void, onCode: (code: string) => void }} props
 */
export function CodeSheet({ kind, open, onOpenChange, onCode }) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const code = normalizeCode(value);
  const submit = () => {
    if (!code) return;
    onOpenChange(false);
    setValue("");
    onCode(code);
  };
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={t(`packer.manual.${kind}Title`)}
      footer={
        <>
          <Button variant="quiet" size="lg" onPress={() => onOpenChange(false)}>
            {t("packer.manual.cancel")}
          </Button>
          <Button variant="primary" size="lg" isDisabled={!code} onPress={submit}>
            {t("packer.manual.use")}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <TextField
          label={t(`packer.manual.${kind}Label`)}
          description={t(`packer.manual.${kind}Hint`)}
          value={value}
          onChange={setValue}
          autoFocus
          autoComplete="off"
        />
      </form>
    </Sheet>
  );
}
