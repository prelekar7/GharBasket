"use client";

import { Badge, Button, Dialog, EmptyState, Select, TextField, toast } from "@gharbasket/ui";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHead, TABLE, TD, TD_ACT, TH } from "@/components/admin/Page.jsx";
import { formatPhone, lastSeenLabel, maskPhone, tenDigits } from "@/lib/admin/format.js";
import { initialsOf } from "@/lib/session.js";
import { grantRole, removeRole } from "./actions.js";

/** @typedef {import("@/lib/admin/types.js").AdminUser} AdminUser */
/** @typedef {import("@/lib/admin/types.js").UserRole} UserRole */
/** @typedef {{ kind: "staff", people: AdminUser[] } | { kind: "found", person: AdminUser } | { kind: "none" } | { kind: "invalid" }} Lookup */

const ROLE_LABEL = /** @type {Record<string, string>} */ ({
  customer: "Customer",
  admin: "Admin",
  hub_manager: "Hub manager",
  franchise_owner: "Franchise owner",
  ngo_coordinator: "NGO coordinator",
  captain: "Captain",
  kitchen_partner: "Kitchen partner",
  rider: "Rider",
  packer: "Packer",
});
/** The roles an admin gives and removes here; the rest come through Partners and KYC. */
const STAFF = ["admin", "hub_manager", "franchise_owner", "ngo_coordinator"];
const ROLE_OPTIONS = STAFF.map((value) => ({ value, label: ROLE_LABEL[value] }));
const NEEDS_HUB = ["hub_manager", "franchise_owner"];

/** What the person will see from their next sign-in. @param {string} role @param {string} hub */
function effect(role, hub) {
  if (role === "admin") return "They'll see every console page from their next sign-in.";
  if (role === "hub_manager")
    return `They'll see the Hub pages for ${hub} from their next sign-in.`;
  if (role === "franchise_owner")
    return `They'll see the Franchise pages for ${hub} from their next sign-in.`;
  return "They'll see the NGO hand-offs from their next sign-in.";
}

/**
 * Users and roles: look someone up by mobile number and give or remove staff roles. The number is
 * shown in full only for the person the admin just looked up; lists show it masked. There is no
 * Aadhaar in this data (M1 does not hold it).
 * @param {{ phone: string, lookup: Lookup, hubs: { id: string, name: string }[], now: number }} props
 */
export function UsersView({ phone, lookup, hubs, now }) {
  const [, setPhone] = useQueryState("phone", parseAsString.withOptions({ shallow: false }));
  const [text, setText] = useState(phone);
  const [problem, setProblem] = useState(lookup.kind === "invalid" ? INVALID : "");
  const [pickedId, setPickedId] = useState(/** @type {string | null} */ (null));

  function find() {
    if (text.trim() === "") {
      setProblem("");
      return void setPhone(null);
    }
    const digits = tenDigits(text);
    if (!digits) return setProblem(INVALID);
    setProblem("");
    void setPhone(digits);
  }

  const found = lookup.kind === "found" ? lookup.person : null;
  const person =
    found ??
    (lookup.kind === "staff" ? lookup.people.find((p) => p.id === pickedId) : null) ??
    null;

  return (
    <>
      <PageHead title="Users and roles" />
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          find();
        }}
      >
        <TextField
          label="Find by mobile number"
          type="tel"
          prefix="+91"
          placeholder="98000 01008"
          value={text}
          onChange={setText}
          isInvalid={!!problem}
          errorMessage={problem}
          className="max-w-80 min-w-64 flex-1"
        />
        <Button type="submit">Find</Button>
        {phone && (
          <Button
            variant="quiet"
            onPress={() => {
              setText("");
              setProblem("");
              void setPhone(null);
            }}
          >
            Show staff
          </Button>
        )}
      </form>

      {lookup.kind === "none" && (
        <EmptyState
          title="No one with that number"
          body="People appear here after they sign in for the first time. Check the number and search again."
        />
      )}

      {person && (
        <PersonCard
          key={person.id}
          person={person}
          fullPhone={found !== null}
          hubs={hubs}
          now={now}
          onBack={found ? null : () => setPickedId(null)}
        />
      )}

      {lookup.kind === "staff" && !person && (
        <StaffList people={lookup.people} onPick={setPickedId} />
      )}
    </>
  );
}

const INVALID = "Enter a 10-digit mobile number, like 98000 01008.";

/**
 * The staff today, with a way into each person's roles.
 * @param {{ people: AdminUser[], onPick: (id: string) => void }} props
 */
function StaffList({ people, onPick }) {
  if (people.length === 0)
    return (
      <EmptyState
        title="No staff yet"
        body="Find someone by mobile number to give them a staff role."
      />
    );
  return (
    <div className="flex flex-col gap-2">
      <h2 className="text-[17px] leading-6 font-bold">Staff today</h2>
      <div className="overflow-x-auto rounded-md border border-line bg-bg">
        <table className={TABLE}>
          <caption className="sr-only">People with staff roles</caption>
          <thead>
            <tr>
              <th className={TH}>Person</th>
              <th className={TH}>Mobile</th>
              <th className={TH}>Staff roles</th>
              <th className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {people.map((p) => (
              <tr key={p.id}>
                <td className={`${TD} font-semibold`}>{p.name}</td>
                <td className={`${TD} whitespace-nowrap text-fg-secondary`}>
                  {maskPhone(p.phone_e164)}
                </td>
                <td className={TD}>
                  <div className="flex flex-wrap gap-1.5">
                    {p.roles
                      .filter((r) => STAFF.includes(r.role))
                      .map((r) => (
                        <Badge key={r.id} tone="outline">
                          {ROLE_LABEL[r.role]}
                          {r.scope_label ? ` · ${r.scope_label}` : ""}
                        </Badge>
                      ))}
                  </div>
                </td>
                <td className={TD_ACT}>
                  <Button
                    variant="quiet"
                    aria-label={`Manage roles for ${p.name}`}
                    onPress={() => onPick(p.id)}
                  >
                    Manage roles
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * One person: who they are, their roles, and the buttons to give or remove a staff role.
 * @param {{ person: AdminUser, fullPhone: boolean, hubs: { id: string, name: string }[], now: number, onBack: (() => void) | null }} props
 */
function PersonCard({ person, fullPhone, hubs, now, onBack }) {
  const [giving, setGiving] = useState(false);
  const [role, setRole] = useState("franchise_owner");
  const [hub, setHub] = useState(hubs[0]?.id ?? "");
  const [, run] = useTransition();
  const key = useRef(/** @type {string | null} */ (null));
  const first = person.name.split(" ")[0];
  const hubName = hubs.find((h) => h.id === hub)?.name ?? "this hub";
  const needsHub = NEEDS_HUB.includes(role);

  function give() {
    run(async () => {
      key.current ??= crypto.randomUUID();
      const result = await grantRole(key.current, {
        userId: person.id,
        role,
        hubId: needsHub ? hub : null,
      });
      if (!result.ok) {
        toast(result.message);
        return;
      }
      key.current = null;
      const given = result.data.id;
      toast(
        `${first} is now ${/^[aeiou]/i.test(ROLE_LABEL[role]) ? "an" : "a"} ${ROLE_LABEL[role].toLowerCase()}`,
        {
          action: { label: "Undo", onClick: () => void removeRole(given) },
        },
      );
    });
  }

  /** @param {UserRole} r */
  function remove(r) {
    run(async () => {
      const result = await removeRole(r.id);
      if (!result.ok) {
        toast(result.message);
        return;
      }
      toast(`${ROLE_LABEL[r.role]} role removed from ${first}`, {
        action: {
          label: "Undo",
          onClick: () =>
            void grantRole(crypto.randomUUID(), {
              userId: person.id,
              role: r.role,
              hubId: r.scope_id,
            }),
        },
      });
    });
  }

  return (
    <section
      className="flex flex-col gap-3 rounded-md border border-line p-5"
      aria-label={person.name}
    >
      {onBack && (
        <Button variant="quiet" onPress={onBack} className="-ml-4 self-start">
          Back to all staff
        </Button>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className="grid size-11 place-items-center rounded-full bg-info-soft font-bold text-info"
          >
            {initialsOf(person.name)}
          </span>
          <div className="flex flex-col">
            <h2 className="text-[17px] leading-6 font-bold">{person.name}</h2>
            <span className="text-sm leading-5 text-fg-secondary">
              {fullPhone ? formatPhone(person.phone_e164) : maskPhone(person.phone_e164)} ·{" "}
              {LANGUAGE[person.locale]} · {lastSeenLabel(person.last_seen_at, now)}
            </span>
          </div>
        </div>
        <Button variant="primary" onPress={() => setGiving(true)}>
          Give a staff role
        </Button>
      </div>

      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">Roles of {person.name}</caption>
          <thead>
            <tr>
              <th className={TH}>Role</th>
              <th className={TH}>Where</th>
              <th className={TH}>Status</th>
              <th className={TH}>Given by</th>
              <th className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {person.roles.map((r) => (
              <tr key={r.id}>
                <td className={`${TD} font-semibold`}>{ROLE_LABEL[r.role] ?? r.role}</td>
                <td className={TD}>{r.scope_label ?? "—"}</td>
                <td className={TD}>
                  <Badge tone={r.status === "verified" ? "primary" : "accent"}>
                    {r.status === "verified" ? "Verified" : "Pending"}
                  </Badge>
                </td>
                <td className={`${TD} text-fg-secondary`}>{r.given_by}</td>
                <td className={TD_ACT}>
                  {STAFF.includes(r.role) && (
                    <Button
                      variant="quiet"
                      aria-label={`Remove ${ROLE_LABEL[r.role]} role from ${person.name}`}
                      onPress={() => remove(r)}
                    >
                      Remove
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        Role changes take effect at the person's next sign-in and are saved to the audit log.
        Captains, cooks, riders and packers get their roles through Partners and KYC, not here.
      </p>

      <Dialog
        title={`Give ${person.name} a staff role?`}
        confirmLabel="Give role"
        cancelLabel="Cancel"
        isOpen={giving}
        onOpenChange={setGiving}
        onConfirm={give}
      >
        <div className="flex flex-col gap-3">
          <Select
            label="Role"
            options={ROLE_OPTIONS}
            value={role}
            onChange={(v) => {
              key.current = null; // another choice is another request, so it needs another key
              setRole(v);
            }}
          />
          {needsHub && (
            <Select
              label="For hub"
              options={hubs.map((h) => ({ value: h.id, label: h.name }))}
              value={hub}
              onChange={(v) => {
                key.current = null;
                setHub(v);
              }}
            />
          )}
          <span className="text-sm leading-5">{effect(role, hubName)}</span>
        </div>
      </Dialog>
    </section>
  );
}

const LANGUAGE = /** @type {Record<string, string>} */ ({
  "en-IN": "English",
  "hi-IN": "Hindi",
  "mr-IN": "Marathi",
});
