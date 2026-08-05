# Telegraph authority matrix

Last reviewed: 2026-08-04

This matrix prevents the protocol whitepaper, Hackathon 1 instructions, and
local engineering assumptions from being merged into one unverified contract.

| Topic | Whitepaper / protocol concept | Hackathon 1 authority | OathCast implementation rule |
|---|---|---|---|
| Miner registration | Permissionless on-chain registration; the whitepaper describes Intent IDs, fee address, price floor, credential/schema metadata, and output mapping. | For Hackathon 1, the integration-interface YAML overrides the whitepaper. Ahmed says the Machina bond was removed; the YAML is still being frozen, and released hashes/schema URI/other requirements apply with pre-submission validation. | Record an immutable registration declaration and raw-YAML digest. Do not encode or submit a transaction until the frozen integration YAML and portal accept the exact draft. |
| Miner price | 0.01 USDC protocol floor in the whitepaper. | Ahmed's Discord answer confirms 0.01 USDC minimum for each Hackathon 1 YAML. | Store and validate integer micro-USDC internally; convert decimal YAML values only at an explicit boundary. |
| Payment | x402 challenge, wallet authorization, Telegraph verification, routing, and receipt. | Hackathon 1 uses Base Sepolia USDC; every Telegraph request must use x402 or another supported method. Ahmed says payment is attached to each served request and recorded on-chain; the Explorer is the current checking path and API docs are forthcoming. | A settlement header is an artifact, not proof. Use manual Explorer reconciliation for the first capped test; later add the official API adapter. No standalone public verifier is assumed. |
| Protocol receipt | Cryptographic Signal Receipt after protocol finalization. | Served requests/signals are public in the Explorer; exact client-side artifact/reconciliation format remains implementation-dependent until API docs arrive. | Keep OathCast forecast receipts, x402 artifacts, and Telegraph receipts as separate evidence types. |
| Script Author | Permissionless WASM registration and sandbox constraints; the whitepaper mentions a Machina bond. | The official Hackathon 1 boilerplate, ABI, harness, and guides, which are not yet public; H1 Machina bond has been removed. | Keep the local semantic proxy and Brier benchmark separate. Do not register WASM until the H1 harness is released. |
| Flow vs Workflow | Paid Intent flow ends in routing/validation/receipt; stateful orchestration belongs to the client. | Track 3 must use real Telegraph Miners and genuine requests. | Keep payment/routing in the protocol adapter and case state, decisions, and outcome resolution in the Application. |
| Mainnet / testnet | Phase 1 Base Sepolia, Phase 2 Base mainnet. | Current Hackathon 1 is Base Sepolia. | No mainnet wallet, mainnet contract, escrow, or MPC path is enabled. |

Sources: [Telegraph Protocol whitepaper](https://telegraphprotocol.com/Whitepapers%20-%20Telegraph%20Protocol.pdf), [official hackathon rules](https://hackathon.telegraphprotocol.com/rules), and the [Miner registration portal](https://integrate.telegraphprotocol.com/).
