# Telegraph Track 2 Clarification and Resolution Record

## Scoring interface resolved 2026-08-15

Telegraph's updated authoritative guide removes the `breakdown_answer`
requirement entirely. The change was independently verified at
`telegraphprotocol/telegraph-docs@cfe6fbda517f09d3097790778d2b9cbaa4d8f272`,
path `scoring/build-a-scoring-module.md`. A scoring module now needs these
function exports:

- `alloc(i32) -> i32`;
- `dealloc(i32, i32)`; and
- `rank_answer(i32, i32, i32, i32, i32, i32) -> f32`.

The six rank parameters are pointer/byte-length pairs for question, ground
truth, and Miner answer, in that order. The return value is in `[0, 1]`, and an
empty or blank Miner answer must return exactly `0`.

No `breakdown_answer` return signature, field encoding, result-pointer
ownership, lifetime, deallocation rule, or struct layout is required for that
removed result. The ordinary `alloc`/`dealloc` input-buffer contract remains.
The earlier ABI follow-up is closed and must not remain a release or
registration blocker.

The scorer has now been rebuilt and tested against the current three-function
contract. Two clean builds are byte-identical, and the v4 machine-readable
record freezes the exact candidate bytes, hashes, and local Stage 1-equivalent
checks. Telegraph has since reported that the breakdown-related validator
rejection, registry mismatch, and Intent binding are fixed. Ahmed confirmed that
a corrected registration was allowed and reported a minimum score of `0.60` on
the Intent. The exact candidate is now registered in the current registry at ID
`7`, but the score interpretation and aggregation formula are not independently
documented, so no threshold pass or validator acceptance is claimed.

The earlier portal source at commit
`ee3724eeddf25177c2d2135ae4c9a77e091cdf98` displays a stale hint saying that
uploaded modules must export `breakdown_answer`; retain that as historical
source evidence. The current live portal build is
`D8HL6V9WUTFV9A7Ryk0W0`, page chunk
`_next/static/chunks/app/page-abd375eb1c96558e.js`. It targets
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and submits
`registerWasm(bytes32,string,string)` with selector `0xfe1e40f7`, carrying the
exact hash, existing gateway URL, and `WEATHER_FORECAST` Intent.

One live validator record from 2026-08-14 surfaced a portal/API error saying
that `breakdown_answer` was missing. Telegraph later checked the node logs and
reported that the actual failure was `module[env] not instantiated`. The
surfaced message remains historical evidence, but it must not be treated as
proof that the validator enforced the removed export.

### Follow-up sent before the response

> Thanks, the updated rank-only guide resolves the ABI question. I verified it
> at docs commit `cfe6fbda517f09d3097790778d2b9cbaa4d8f272` and my module now
> exports only `alloc`, `dealloc`, and `rank_answer`. Before registering, could
> you confirm two operational details? First, has the live validator deployment
> been updated to accept the rank-only contract? A validator record from August
> 14 rejected a missing `breakdown_answer`, while the then-current portal source
> still displayed that old requirement. Second, how is a WASM registration bound
> to the selected canonical Intent for the per-Intent Stage 2 champion
> comparison? The portal required an Intent selection but submitted
> `registerWasm(hash, url, [])`, and I could not find a backend write carrying
> the selection. A validator/version confirmation and the authoritative binding
> mechanism would let me register the correct bytes without guessing.

### Telegraph follow-up received 2026-08-15

> Hey Fexx, hope you're doing well. The breakdown answer rejection is fixed. You
> can register your wasm and check if it works out.
>
> Just a heads up, we checked the node logs and your Aug 14 rejection was
> actually a module[env] not instantiated error, so you might want to double
> check your env setup before you retry.
>
> Regarding intents, We're working on the contract side right now, and soon will
> be fixed. Thanks

This response was relayed directly by the user; retain a permalink or screenshot
before using it as submission evidence. It closed the rollout-confirmation gate
at the team-response level and invited a retry. The first two confirmed portal
transactions did not reach the validator-indexed registry; corrected transaction
`0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`
later did. Dashboard indexing and a live validator Stage 1 result nevertheless
remain unobserved. The exact current candidate contains no WASM import section,
imports no host functions or memory, and instantiates in a fresh wazero runtime
without an `env` module. If the exact frozen SHA later receives the same error,
verify the hosted bytes and Telegraph wrapper/validator path rather than adding
an `env` import. Telegraph's subsequent binding and registry fixes are recorded
below.

## Provisional registration attempt 2026-08-15

After Telegraph instructed OathCast to proceed before the mapping fix, the exact
16,292-byte candidate was uploaded through the portal's IPFS flow. The portal
returned gateway URL
`https://gateway.pinata.cloud/ipfs/QmSww9z6Dp1LPitKj3HsTRY8pjNNzhwvDLiAufKxskA3P1`,
re-fetched it, and reported a matching raw-byte Keccak-256. Only the
`WEATHER_FORECAST` canonical Intent was selected. The connected Base Sepolia
wallet is `0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE`; the account was signed in
and the wallet was linked before submission.

The portal then showed `Submitted Successfully`, registration ID `5`, and
Intent ID
`0x1821e010856eb733af536890e0a65e83f1253c39796c9b5ab73301000d6729b6` for
transaction
`0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`.
Postflight RPC decoding found a successful zero-value Base Sepolia transaction
using the `redeemDelegations(bytes[],bytes32[],bytes[])` wrapper. Its nested call
had the expected `registerWasm(bytes32,string,string[])` selector, exact WASM
hash and gateway URL, and an empty URL allowlist, but its target was
`0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3`.

That target did not match the independently verified validator-indexed registry
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` at the time. The portal bundle then
embedded the `0xac683...` address; the portal Dashboard/API reported zero WASM
registrations, `getWasm(5)` was empty at both addresses, and the old portal
target's WASM entity count was zero. The transaction therefore proves only a
successful event on the old portal-configured, unindexed registry surface. It
does not prove current registry enrollment, validator Stage 1 acceptance, Stage
2 promotion, or canonical Intent participation. Telegraph later reported the
registry mismatch and Intent binding fixed; the old event remains historical and
unmigrated.

The public portal source at that time confirmed that its write and read paths
were configured independently. `WasmWizard` submitted to `DIAMOND_ADDRESS`, sourced from
`NEXT_PUBLIC_REGISTRY_CONTRACT`, while `/api/registrations/[address]` proxies
`VALIDATOR_BASE_URL/engine/validator/v1/addresses/{address}`. Commit
`1ff2f7db1139657aff8f9073cac34e61c91cbef2` moved the Dashboard to that live
validator API, but `.env.local.example` continued to point writes at
`0xac683...`. This source-level split matched the observed transaction-versus-
Dashboard mismatch. It did not by itself establish which deployment Telegraph
intended to be authoritative; the later live build and team response resolve
that question for a fresh registration.

The complete postflight record is
`artifacts/registration-drafts/oathcast-weather-wasm-registration-postflight-2026-08-15T141838Z.json`.

The old transaction must not be treated as migrated. At this postflight snapshot,
the current registry reported `entityCount(2) == 6`, with IDs `5` and `6`
belonging to other authors; the old `0xac683...` target reported an empty
`getWasm(5)` and `entityCount(2) == 0`. A later corrected registration is recorded
below. This snapshot remains the evidence boundary for the failed ID `5` attempt.

## Fix confirmation and corrected re-registration preflight 2026-08-15

The user relayed three subsequent team responses:

> hey fexx, the intent binding is fixed. You can register wasm, but I suggest you
> wait a while because we're working on reducing the strict criteria of wasm.
>
> It's fixed, are you still facing this?
>
> You can reregister, it just have to score 0.60 or above on intents.

The first two responses report the Intent binding and registry mismatch fixed.
The third, attributed to Ahmed, confirmed that re-registration was intended at
that point and reported a minimum `0.60` score on the Intent. These responses
were relayed by the user; retain a permalink or screenshot before treating them
as public submission evidence.

The refreshed portal was independently inspected at live build
`D8HL6V9WUTFV9A7Ryk0W0`, page chunk
`_next/static/chunks/app/page-abd375eb1c96558e.js`. It now targets
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and encodes
`registerWasm(bytes32,string,string)` with selector `0xfe1e40f7`. The decoded
inner-call arguments are the exact frozen hash
`0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`,
the existing gateway URL, and `WEATHER_FORECAST`. The existing CID was re-fetched
independently and remained byte-identical to the 16,292-byte candidate.

A read-only direct simulation from wallet
`0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE` succeeded on Base Sepolia and
returned prospective registration ID `7`. The simulation generated no wallet
transaction and nothing was broadcast. The Dashboard/API remains at
`wasm_count: 0` for this wallet before re-registration. The current registry has
`entityCount(2) == 6`; IDs `5` and `6` belong to other authors. The old
`0xac683...` registry still has `entityCount(2) == 0` and an empty `getWasm(5)`.
This confirms that the earlier transaction's event ID `5` was not migrated.

The official example scores `0.8500`. The weakest known valid local paraphrase
scores `0.5875`, below Ahmed's reported `0.60` minimum. The validator replay
corpus and aggregation formula are not public, so this is hidden aggregate risk,
not proof that the candidate fails or passes the threshold. No validator
acceptance is claimed.

The unbroadcast preflight record is
`artifacts/registration-drafts/oathcast-weather-wasm-reregistration-preflight-2026-08-15T204924Z.json`.

## Historical second transaction and packet split 2026-08-15

After the read-only simulation, a separately authorized wallet confirmation
produced transaction
`0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1`, with
receipt status `1`. The portal displayed registration ID `7` and
`WEATHER_FORECAST`. That UI result does not match the corrected inner call that
was simulated. Decoding the confirmed delegated wallet packet found outer target
`0xdb9b1e94b5b69df7e401ddbede43491141047db3`, selector `0xcef6d209`, and
`redeemDelegations(bytes[],bytes32[],bytes[])`. Its nested call instead targeted
the old `0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3`, selector `0x19238d1c`,
legacy `registerWasm(bytes32,string,string[])`, the exact frozen hash/CID, and
an empty URL array.

At that second-transaction postflight snapshot, the corrected registry
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` still had
`entityCount(2) == 6` and an empty `getWasm(7)`. The old registry had
`entityCount(2) == 0` and an empty `getWasm(7)`, while Dashboard/API
`wasm_count` remained `0`. Classify that attempt as a portal UI/read-only
simulation versus delegated wallet packet split. The authorization was consumed.
The receipt and portal ID did not establish validator acceptance, Intent binding,
or the reported `0.60` threshold. The later corrected transaction is recorded
below.

The complete second postflight record is
`artifacts/registration-drafts/oathcast-weather-wasm-reregistration-postflight-2026-08-15T212134Z.json`.

## Corrected current-registry registration 2026-08-16

After a hard refresh loaded the corrected portal bundle, the user confirmed
transaction
`0x3997dfd5b514cf56b434fb4a475e6cc015e5ae9d42064073ff044bc4f67be51e`.
The Base Sepolia receipt has status `1`, block `45541793`, timestamp
`2026-08-16T03:44:34Z`, and zero native value. The decoded delegated wallet
packet uses outer target `0xdb9b1e94b5b69df7e401ddbede43491141047db3`, selector
`0xcef6d209`, and `redeemDelegations(bytes[],bytes32[],bytes[])`. Its nested call
targets the current registry
`0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`, selector `0xfe1e40f7`, and
`registerWasm(bytes32,string,string)` with the exact frozen hash
`0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`,
the verified gateway URL, and `WEATHER_FORECAST`.

The current registry emitted `IntentRegistered` ID `7` for wallet
`0x6D4192Bca39641F9aA22DB17EfF991D6adD005dE`. Its WASM entity count increased to
`7`, and non-empty `getWasm(7)` contains the wallet, exact candidate hash,
gateway URL, and Intent. This proves on-chain registration and canonical Intent
binding in the current registry. It does not prove validator processing.

The Dashboard/API still returned HTTP `200` with `wasm_count: 0` and an empty
WASM list. Telegraph reported that an IPFS gateway timeout caused the indexing
delay, that the issue was solved, and that its indexing PR would be merged. The
user later reported that the PR was merged but the Dashboard remained empty, and
that Telegraph suggested trying another re-registration. Those team statements
are user-relayed operational guidance only. No new preflight or transaction is
present, and no fresh explicit user authorization was received. Because the
current registry already contains the exact candidate, no re-registration is
currently authorized; any later replacement attempt requires a new
complete decoded preflight followed by fresh explicit authorization.

Validator Stage 1 acceptance, Ahmed's reported `0.60` per-Intent threshold
result, and Stage 2 promotion remain unobserved. Do not infer any of them from
the successful receipt, registry event, or non-empty `getWasm(7)`.

The complete corrected postflight record is
`artifacts/registration-drafts/oathcast-weather-wasm-corrected-postflight-2026-08-16T034434Z.json`.

### Historical paste-ready report to Telegraph (superseded)

> I tested the corrected portal path after the first transaction
> `0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`.
> The read-only simulation targeted the current registry and returned
> prospective ID `7`, but my separately authorized transaction
> `0xde08c7a66627b98cf1a55fc7a3b4d2e8065b08d9b20d09af5c015852faa140d1`
> confirmed with receipt status `1` while the portal displayed ID `7` and
> `WEATHER_FORECAST`. I decoded the wallet packet: outer target
> `0xdb9b1e94b5b69df7e401ddbede43491141047db3`, selector `0xcef6d209`
> (`redeemDelegations(bytes[],bytes32[],bytes[])`), then an inner call to the
> old `0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3`, selector `0x19238d1c`,
> legacy `registerWasm(bytes32,string,string[])`, with my exact frozen hash/CID
> and `[]`. The corrected registry `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`
> still returns empty `getWasm(7)` with `entityCount(2)=6`; the old registry
> returns empty `getWasm(7)` with `entityCount(2)=0`, and the Dashboard/API still
> shows `wasm_count: 0`. This appears to be a portal UI/simulation versus
> delegated wallet-packet split, not validator acceptance or proof of Intent
> binding. My authorization is consumed. Please confirm the supported wallet
> wrapper and inner registration path before I authorize anything further.

The report above was accurate for the failed second transaction but is now
superseded by the corrected current-registry transaction and postflight.

### Precise Telegraph follow-up after registration ID 5

> I followed your instruction to register the rank-only WASM provisionally.
> Transaction
> `0x82db3d5ade954cf4995cbc01ed4f2a0a3b24c352b0ce9efa15ceb1f18d7d7471`
> confirmed on Base Sepolia and the portal displayed registration ID `5`. The
> decoded inner call had zero value, selector `0x19238d1c`, my exact WASM hash
> `0xea169bc97fc43c3de086d26765714a28c909d29a6d79181f93d2f9e236776ab8`,
> the verified Pinata URL, and `[]` for `whitelistedUrls`. However, the live
> portal bundle and transaction targeted
> `0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3`, while the existing validator-
> indexed WASM registrations `1` through `4` are readable at
> `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8`. The portal Dashboard/API still
> reports zero WASM registrations for my wallet, and `getWasm(5)` is empty at
> both addresses. The public portal source writes through
> `NEXT_PUBLIC_REGISTRY_CONTRACT`, but its Dashboard reads through the validator
> node configured by `VALIDATOR_BASE_URL`; commit `1ff2f7db...` introduced that
> validator-backed Dashboard while the repository environment example still
> points writes at `0xac683...`. Is the public write address stale, which registry
> and wrapper are the supported registration path, and will ID `5` be
> indexed/migrated or discarded? I will not retry until the split is resolved
> and I can preflight the exact replacement transaction.

## Earlier team response received 2026-08-14

Before the guide was revised, Telegraph relayed that `breakdown_answer` would
receive the same logical inputs as `rank_answer`, expose `Relevance`,
`Correctness`, `Lexical`, `LengthQuality`, and `Composite`, and keep `Composite`
equal to the rank score. That response is superseded by the removal of the
export and is retained only to explain the historical investigation.

The same response also confirmed facts that were current for the old portal path:

- Empty `whitelistedUrls` is valid.
- `whitelistedUrls` is not currently used internally for ground truths.
- Intent binding was pending Telegraph's contract-side fix.

The response was relayed directly by the user. Retain a permalink or screenshot
before using it as submission evidence.

## Historical evidence behind the clarification

## Historical `breakdown_answer` evidence

When the request was drafted, the public scoring guide and official Rust
example/tester documented `alloc`, `dealloc`, and `rank_answer`, while the live
portal/API response appeared to require `breakdown_answer`. The observations
below remain useful only as evidence of surfaced historical behavior.

Observed validator records:

- Registration `1`, author
  `0xa5fdb69F410fF432b2033B01c45C794e1F5949D8`, WASM Keccak-256
  `0x34220f7244084b2542c34b114189963db5924812e170e54997f9241c9b6807ac`:
  portal/API response
  `structural validation failed: module load failed: wasm/runtime: missing required export "breakdown_answer"`.
- Registration `3`, author
  `0x89fa09831c33A9651dA38aC37B25E058B6409Cc8`, WASM Keccak-256
  `0x25262ecd9fa03a0c56d35cac63baa461a3cde5f11bb039966df431b530a49336`:
  exported `breakdown_answer(i32,i32,i32,i32,i32,i32)->f32`, loaded far
  enough for the self-match check, then failed because self and unrelated
  scores were both `0.0000`.

Telegraph later reported that registration `1`'s node-log root cause was
`module[env] not instantiated`. That correction supersedes the inference that
the surfaced message proved a live `breakdown_answer` requirement. The updated
guide supersedes both records as current ABI evidence; neither record establishes
that a current module needs the removed export or any five-field result layout.

## Canonical Intent and registry fix

The earlier portal implementation used selector `0x19238d1c`:

```solidity
registerWasm(bytes32 wasmHash, string wasmUrl, string[] whitelistedUrls)
```

It wrote to `0xac683bFa8F1C892E23e8300d14c20678C6FC0CA3`, passed `[]`, and did
not transmit the selected canonical Intent. Telegraph confirmed that the empty
URL list was valid and not used internally for ground truths. Those facts remain
historical context for transaction `0x82db...`, not the current registration
contract.

The corrected live portal build `D8HL6V9WUTFV9A7Ryk0W0` uses selector
`0xfe1e40f7`:

```solidity
registerWasm(bytes32 wasmHash, string wasmUrl, string intent)
```

It targets `0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8` and encodes
`WEATHER_FORECAST` directly. The historical `0xde08...` delegated packet did not
preserve that call, but corrected transaction `0x3997...e51e` did. Non-empty
`getWasm(7)` now independently proves the exact candidate and Intent binding in
the current registry. This does not retroactively migrate either old-registry
event and does not prove validator acceptance.

The remaining documentation and evidence questions are:

1. How is Ahmed's reported `0.60` per-Intent minimum aggregated across validator
   cases?
2. When will current-registry ID `7` appear in the Dashboard/validator API, and
   what Stage 1 state will be exposed?
3. Which replay corpus, sampling rule, and Stage 2 acceptance state will be
   exposed for a submitted registration?
4. Does the current portal still display the historical `breakdown_answer` hint?

The official `0.8500` example and local scores, including the valid `0.5875`
paraphrase, cannot establish any validator result above. Actual Stage 1
acceptance and the threshold result remain unobserved.

## Current action boundary

The exact rank-only candidate is registered in the current registry at ID `7`,
and its `WEATHER_FORECAST` binding is proven on-chain. The immediate gate is
Dashboard or validator indexing, followed by an authoritative Stage 1 and
reported threshold result; Stage 2 remains after that. Do not claim validator
acceptance or a `0.60` threshold pass from the receipt or registry state alone.

No further registration is currently authorized. Telegraph's later
suggestion to try re-registering is retained as user-relayed guidance, not as a
transaction instruction or user authorization. If Telegraph determines that ID
`7` must be replaced despite the non-empty current-registry record, decode a new
complete outer wrapper and nested call first, then obtain fresh explicit user
authorization before confirmation.
