import assert from "node:assert/strict";
import { test } from "node:test";
import {
  firebaseConfigOf,
  isMockApiIn,
  looksLikeIdToken,
  phoneErrorMessage,
  phoneSignInConfig,
} from "./sign-in.js";

const FIREBASE = {
  GB_FIREBASE_API_KEY: "AIzaTest",
  GB_FIREBASE_AUTH_DOMAIN: "gb-staging.firebaseapp.com",
  GB_FIREBASE_PROJECT_ID: "gb-staging",
  GB_FIREBASE_APP_ID: "1:2:web:3",
};

test("the Firebase config needs all four values", () => {
  assert.deepEqual(firebaseConfigOf(FIREBASE), {
    apiKey: "AIzaTest",
    authDomain: "gb-staging.firebaseapp.com",
    projectId: "gb-staging",
    appId: "1:2:web:3",
    testMode: false,
  });
  assert.equal(firebaseConfigOf({ ...FIREBASE, GB_FIREBASE_APP_ID: " " }), null);
  assert.equal(firebaseConfigOf({}), null);
  assert.equal(firebaseConfigOf({ ...FIREBASE, GB_FIREBASE_TEST_MODE: "true" })?.testMode, true);
});

test("GB_API_MOCK decides when set; unset, only Vercel's production deployment uses the real API", () => {
  assert.equal(isMockApiIn({}), true);
  assert.equal(isMockApiIn({ VERCEL_ENV: "preview" }), true);
  assert.equal(isMockApiIn({ VERCEL_ENV: "production" }), false);
  assert.equal(isMockApiIn({ VERCEL_ENV: "production", GB_API_MOCK: "true" }), true);
  assert.equal(isMockApiIn({ GB_API_MOCK: "false" }), false);
  assert.equal(isMockApiIn({ GB_API_MOCK: "anything" }), true);
});

test("phone sign-in needs the real API and the Firebase config", () => {
  assert.equal(phoneSignInConfig({ ...FIREBASE }), null); // mock mode
  assert.equal(phoneSignInConfig({ GB_API_MOCK: "false" }), null); // no Firebase
  assert.equal(phoneSignInConfig({ ...FIREBASE, GB_API_MOCK: "false" })?.projectId, "gb-staging");
  assert.equal(
    phoneSignInConfig({ ...FIREBASE, VERCEL_ENV: "production" })?.projectId,
    "gb-staging",
  );
});

test("only a JWT-shaped string goes to the API as an ID token", () => {
  assert.equal(looksLikeIdToken("aaa.bbb.ccc"), true);
  assert.equal(looksLikeIdToken("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln-_x"), true);
  assert.equal(looksLikeIdToken("not a token"), false);
  assert.equal(looksLikeIdToken("a.b"), false);
  assert.equal(looksLikeIdToken(42), false);
  assert.equal(looksLikeIdToken(`a.b.${"c".repeat(9000)}`), false);
});

test("Firebase errors become sentences, the hidden billing reason included", () => {
  assert.match(phoneErrorMessage({ code: "auth/invalid-verification-code" }), /code is not right/);
  assert.match(phoneErrorMessage({ code: "auth/too-many-requests" }), /Too many attempts/);
  assert.match(
    phoneErrorMessage({ code: "auth/internal-error", message: "[ BILLING_NOT_ENABLED ]" }),
    /Firebase billing/,
  );
  assert.match(phoneErrorMessage(new Error("boom")), /could not send or check/);
  assert.match(phoneErrorMessage(null), /could not send or check/);
});
