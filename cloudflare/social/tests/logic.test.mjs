import test from "node:test";
import assert from "node:assert/strict";
import { Limiter, NAME_RE, bucket, cleanState, estimate, randomToken, sha256hex, shown, timingSafeEqual } from "../src/logic.js";

test("names", () => {
  for (const ok of ["Thrall", "a_b-c", "abc", "x".repeat(20)]) assert.ok(NAME_RE.test(ok), ok);
  for (const bad of ["", "ab", "x".repeat(21), "bad name", "<b>", "emoji\u263a"]) assert.ok(!NAME_RE.test(bad), bad);
});

test("sha256 of a known value", async () => {
  assert.equal(await sha256hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

test("tokens are long, URL-safe and different each time", () => {
  const a = randomToken(32), b = randomToken(32);
  assert.match(a, /^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(a, b);
});

test("timingSafeEqual", () => {
  assert.ok(timingSafeEqual("abc", "abc"));
  assert.ok(!timingSafeEqual("abc", "abd"));
  assert.ok(!timingSafeEqual("abc", "abcd"));
});

test("state is validated and invisible keeps nothing", () => {
  assert.deepEqual(cleanState({ status: "dnd", activity: "in_game", realm: "cthun" }), { st: "dnd", ac: "in_game", rm: "cthun" });
  assert.deepEqual(cleanState({ status: "root", activity: "x", realm: "<>" }), { st: "online", ac: "launcher", rm: "" });
  assert.deepEqual(cleanState({ status: "online", activity: "launcher", realm: "nzoth" }), { st: "online", ac: "launcher", rm: "" });
  assert.deepEqual(cleanState({ status: "invisible", activity: "in_game", realm: "nzoth" }), { st: "invisible", ac: "launcher", rm: "" });
});

test("what others see", () => {
  assert.deepEqual(shown(null), { state: "offline", activity: "", realm: "" });
  assert.deepEqual(shown({ st: "invisible", ac: "in_game", rm: "nzoth" }), { state: "offline", activity: "", realm: "" });
  assert.deepEqual(shown({ st: "away", ac: "in_game", rm: "yshaarj" }), { state: "away", activity: "in_game", realm: "yshaarj" });
  assert.deepEqual(shown({ st: "online", ac: "launcher", rm: "nzoth" }), { state: "online", activity: "launcher", realm: "" });
  assert.deepEqual(shown({ st: "online", ac: "launcher", rm: "", gone: true }), { state: "offline", activity: "", realm: "" });
});

test("limiter and bucket", () => {
  const l = new Limiter();
  for (let i = 0; i < 3; i++) assert.ok(l.hit("k", 3, 1000, 0));
  assert.ok(!l.hit("k", 3, 1000, 10));
  assert.ok(l.hit("k", 3, 1000, 2000));
  const b = bucket(2, 1, 0);
  assert.ok(b(0) && b(0));
  assert.ok(!b(0));
  assert.ok(b(5000));
});

test("the estimate counts incoming messages 20:1 and bounds duration", () => {
  const e = estimate({ http: 10, ws_connects: 2, alarms: 3, ws_in: 40 }, 0);
  assert.equal(e.do_requests, 10 + 2 + 3 + 2);
  assert.equal(e.duration_gb_s_upper_bound, 10800);
});
