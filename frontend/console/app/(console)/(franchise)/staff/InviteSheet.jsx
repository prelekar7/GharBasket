"use client";

import { Button, Select, Sheet, TextField, toast } from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { maskPhone } from "@/lib/money.js";
import { inviteSomeone } from "./actions.js";

const ROLES = ["Kitchen partner", "Rider", "Packer", "Society Captain"].map((r) => ({
  value: r,
  label: r,
}));

/** A 10-digit Indian mobile number. @param {string} phone */
const validPhone = (phone) => /^[6-9]\d{9}$/.test(phone.replace(/\D/g, ""));

/**
 * "Invite someone": a sheet with a name, a mobile number and a role. The idempotency key is made when
 * the sheet opens, so pressing Send twice invites once.
 */
export function InviteSheet() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [role, setRole] = useState(ROLES[0].value);
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [sending, startSending] = useTransition();
  const phoneBad = phone !== "" && !validPhone(phone);

  /** @param {boolean} next */
  function onOpenChange(next) {
    setOpen(next);
    if (next) {
      setKey(crypto.randomUUID());
      setName("");
      setPhone("");
      setRole(ROLES[0].value);
      setError("");
    }
  }

  function send() {
    startSending(async () => {
      const result = await inviteSomeone(
        { name: name.trim(), phone: phone.replace(/\D/g, ""), role },
        key,
      );
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setOpen(false);
      toast(`Invite sent to ${name.trim()} on ${maskPhone(phone)}`);
    });
  }

  const ready = name.trim() !== "" && validPhone(phone);

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Invite someone"
      description="They get a message to join Kharghar hub. An admin grants any console role on Users and roles."
      trigger={<Button variant="primary">Invite someone</Button>}
      footer={
        <>
          <Button onPress={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" isDisabled={!ready || sending} onPress={send}>
            Send invite
          </Button>
        </>
      }
    >
      <TextField
        label="Name"
        value={name}
        onChange={setName}
        autoComplete="off"
        className="!max-w-none"
      />
      <TextField
        label="Mobile number"
        prefix="+91"
        inputMode="numeric"
        value={phone}
        onChange={setPhone}
        isInvalid={phoneBad}
        errorMessage="Enter a 10-digit mobile number."
        className="!max-w-none"
      />
      <Select label="Works as" options={ROLES} value={role} onChange={setRole} />
      {error && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {error}
        </p>
      )}
    </Sheet>
  );
}
