import { afterEach, describe, expect, it, vi } from "vitest";

/** @type {Record<string, (event: any) => void>} */
const listeners = {};
const plugin = {
  addListener: vi.fn(async (/** @type {string} */ name, /** @type {(e: any) => void} */ fn) => {
    listeners[name] = fn;
    return { remove: vi.fn() };
  }),
  signInWithPhoneNumber: vi.fn(async () => {}),
  confirmVerificationCode: vi.fn(async () => {}),
  getIdToken: vi.fn(async () => ({ token: "id-token" })),
  signOut: vi.fn(async () => {}),
};
vi.mock("@capacitor-firebase/authentication", () => ({ FirebaseAuthentication: plugin }));

async function loadNative() {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: true, MOCK: false, API_BASE: "https://api.test" }));
  return import("@/lib/otp.js");
}

afterEach(() => {
  vi.doUnmock("@/lib/env.js");
  for (const k of Object.keys(listeners)) delete listeners[k];
});

describe("codeFromMessage", () => {
  it.each([
    [
      "SMS unable to be sent until this region enabled by the app developer.",
      "operation-not-allowed",
    ],
    [
      "This operation is not allowed. You must enable this service in the console.",
      "operation-not-allowed",
    ],
    [
      "We have blocked all requests from this device due to unusual activity. Try again later.",
      "too-many-requests",
    ],
    ["The SMS quota for the project has been exceeded.", "quota-exceeded"],
    [
      "A network error (such as timeout, interrupted connection or unreachable host) has occurred.",
      "network-request-failed",
    ],
    ["The format of the phone number provided is incorrect.", "invalid-phone-number"],
    [
      "The sms code has expired. Please re-send the verification code to try again.",
      "code-expired",
    ],
    [
      "The sms verification code used to create the phone auth credential is invalid.",
      "invalid-verification-code",
    ],
    ["This app is not authorized to use Firebase Authentication.", "app-not-authorized"],
    ["Something else", "unknown"],
    [undefined, "unknown"],
  ])("%s -> %s", async (message, code) => {
    const { codeFromMessage } = await loadNative();
    expect(codeFromMessage(message)).toBe(`auth/${code}`);
  });
});

describe("otpErrorKey", () => {
  it("gives every sign-in failure a clear copy key", async () => {
    const { OtpError, otpErrorKey } = await loadNative();
    const key = (/** @type {string} */ code) => otpErrorKey(new OtpError(code));
    expect(key("auth/operation-not-allowed")).toBe("otp.region");
    expect(key("auth/too-many-requests")).toBe("otp.tooMany");
    expect(key("auth/invalid-phone-number")).toBe("otp.badPhone");
    expect(key("auth/invalid-verification-code")).toBe("otp.invalid");
    expect(key("auth/code-expired")).toBe("otp.expired");
    expect(key("auth/network-request-failed")).toBe("otp.network");
    expect(key("auth/unknown")).toBe("otp.failed");
    expect(otpErrorKey(new OtpError("auth/unknown"), "login.sendFailed")).toBe("login.sendFailed");
    expect(otpErrorKey(Object.assign(new Error("slow down"), { status: 429 }))).toBe("otp.tooMany");
    expect(otpErrorKey(new TypeError("Failed to fetch"))).toBe("otp.network");
  });
});

describe("native phoneVerificationFailed", () => {
  it("rejects sendCode with a code that maps to the region message", async () => {
    const { sendCode, otpErrorKey } = await loadNative();
    plugin.signInWithPhoneNumber.mockImplementationOnce(async () => {
      queueMicrotask(() =>
        listeners.phoneVerificationFailed({
          message: "SMS unable to be sent until this region enabled by the app developer.",
        }),
      );
    });
    const error = await sendCode("+919800000002", "recaptcha").catch((e) => e);
    expect(error.code).toBe("auth/operation-not-allowed");
    expect(otpErrorKey(error)).toBe("otp.region");
  });

  it("signs in with a code after phoneCodeSent, and signs out of Firebase", async () => {
    const { sendCode, verifyCode } = await loadNative();
    plugin.signInWithPhoneNumber.mockImplementationOnce(async () => {
      queueMicrotask(() => listeners.phoneCodeSent({ verificationId: "vid" }));
    });
    await sendCode("+919800000002", "recaptcha");
    expect(await verifyCode("123456")).toEqual({ phone: "+919800000002", idToken: "id-token" });
    expect(plugin.confirmVerificationCode).toHaveBeenCalledWith({
      verificationId: "vid",
      verificationCode: "123456",
    });
    expect(plugin.signOut).toHaveBeenCalled();
  });
});
