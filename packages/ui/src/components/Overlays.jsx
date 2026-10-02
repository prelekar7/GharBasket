import { Heading, Modal, ModalOverlay, Dialog as RACDialog } from "react-aria-components";
import { Toaster as SonnerToaster } from "sonner";
import { Drawer } from "vaul";
import { Button } from "./Button.jsx";

/**
 * @typedef {object} SheetProps
 * @property {string} title
 * @property {string} [description]
 * @property {boolean} [open]
 * @property {(open: boolean) => void} [onOpenChange]
 * @property {import("react").ReactNode} [trigger] Element that opens the sheet (rendered asChild).
 * @property {(number | string)[]} [snapPoints] e.g. ["320px", 1]
 * @property {import("react").ReactNode} [footer] Usually one quiet and one primary Button.
 * @property {import("react").ReactNode} [children]
 */

/**
 * Bottom sheet with snap points (Vaul). The default way to edit anything; never a full-page form.
 * @param {SheetProps} props
 */
export function Sheet({
  title,
  description,
  open,
  onOpenChange,
  trigger,
  snapPoints,
  footer,
  children,
}) {
  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange} snapPoints={snapPoints}>
      {trigger && <Drawer.Trigger asChild>{trigger}</Drawer.Trigger>}
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 bg-scrim" />
        <Drawer.Content className="fixed inset-x-0 bottom-0 mx-auto flex max-h-[96dvh] w-full max-w-[480px] flex-col gap-3 rounded-t-lg bg-surface px-5 pt-3 pb-6 text-fg shadow-sheet outline-none">
          <Drawer.Handle className="mx-auto h-1 w-10 rounded-full bg-line-strong" />
          <Drawer.Title className="text-[22px] leading-[30px] font-semibold">{title}</Drawer.Title>
          {description && (
            <Drawer.Description className="text-sm leading-5 text-fg-secondary">
              {description}
            </Drawer.Description>
          )}
          <div className="flex flex-col gap-3 overflow-y-auto">{children}</div>
          {footer && <div className="flex gap-2 pt-2 *:flex-1">{footer}</div>}
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

/**
 * @typedef {object} DialogProps
 * @property {string} title A question with the amount in it ("Top up ₹1,000?").
 * @property {string} confirmLabel Repeats the verb and amount ("Pay ₹1,000 with UPI").
 * @property {string} cancelLabel
 * @property {"primary" | "danger"} [tone]
 * @property {boolean} [isOpen]
 * @property {(open: boolean) => void} [onOpenChange]
 * @property {() => void} [onConfirm]
 * @property {import("react").ReactNode} [children] What will happen, in one or two lines.
 */

/**
 * Blocking confirm, only for money and irreversible steps. Everything else uses an undo toast.
 * @param {DialogProps} props
 */
export function Dialog({
  title,
  confirmLabel,
  cancelLabel,
  tone = "primary",
  isOpen,
  onOpenChange,
  onConfirm,
  children,
}) {
  return (
    <ModalOverlay
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      className="fixed inset-0 z-50 grid place-items-center bg-scrim p-5"
    >
      <Modal className="w-full max-w-[340px] rounded-md bg-surface text-fg shadow-sheet outline-none">
        <RACDialog role="alertdialog" className="flex flex-col gap-3 p-5 outline-none">
          {({ close }) => (
            <>
              <Heading slot="title" className="text-[22px] leading-[30px] font-semibold">
                {title}
              </Heading>
              <div className="text-base text-fg-secondary">{children}</div>
              <div className="mt-2 flex flex-wrap justify-end gap-2">
                <Button onPress={close}>{cancelLabel}</Button>
                <Button
                  variant={tone}
                  onPress={() => {
                    onConfirm?.();
                    close();
                  }}
                >
                  {confirmLabel}
                </Button>
              </div>
            </>
          )}
        </RACDialog>
      </Modal>
    </ModalOverlay>
  );
}

/**
 * Mount once in the app root. Show messages with `toast("Methi removed", { action: { label: "Undo", onClick } })`.
 * Inked background (`fg`) with `bg` text reads in both themes.
 */
export function Toaster() {
  return (
    <SonnerToaster
      position="bottom-center"
      offset={88}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "flex w-[calc(100vw-32px)] max-w-[380px] items-center gap-3 rounded-sm bg-fg py-2 pr-2 pl-4 font-sans text-sm leading-5 font-medium text-bg shadow-card",
          title: "flex-1",
          actionButton:
            "min-h-10 rounded-xs px-3 font-bold text-bg underline underline-offset-3 outline-none focus-visible:shadow-focus",
        },
      }}
    />
  );
}
export { toast } from "sonner";
