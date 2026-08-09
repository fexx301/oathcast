# Telegraph authority matrix

Last reviewed: 2026-08-09

This matrix prevents the protocol whitepaper, Hackathon 1 instructions, and
local engineering assumptions from being merged into one unverified contract.

| Topic | Whitepaper / protocol concept | Hackathon 1 authority | OathCast implementation rule |
|---|---|---|---|
| Miner registration | Permissionless on-chain registration; the whitepaper describes Intent IDs, fee address, price floor, credential/schema metadata, and output mapping. | For Hackathon 1, the integration-interface YAML overrides the whitepaper. Ahmed says the Machina bond was removed; the YAML is still being frozen, and released hashes/schema URI/other requirements apply with pre-submission validation. | Record an immutable registration declaration and raw-YAML digest. Do not encode or submit a transaction until the frozen integration YAML and portal accept the exact draft. |
| Miner price | 0.01 USDC protocol floor in the whitepaper. | Ahmed's Discord answer confirms 0.01 USDC minimum for each Hackathon 1 YAML. | Store and validate integer micro-USDC internally; convert decimal YAML values only at an explicit boundary. |
| Payment | x402 challenge, wallet authorization, Telegraph verification, routing, and receipt. | Ahmed's earlier Discord guidance named Base Sepolia, but the current live API challenge observed on 2026-08-09 offers x402 v2 exact payment in Solana Devnet USDC. The live API is authoritative for the request being paid. | Use `payment-canary/` for the current Solana route. Pin network, mint, amount, recipient, fee payer, URL, and Miner path before signing; independently verify the public transaction and then reconcile it in Telegraph Explorer. |
| Protocol receipt | Cryptographic Signal Receipt after protocol finalization. | Served requests/signals are public in the Explorer; exact client-side artifact/reconciliation format remains implementation-dependent until API docs arrive. | Keep OathCast forecast receipts, x402 artifacts, and Telegraph receipts as separate evidence types. |
| Script Author | Permissionless WASM registration and sandbox constraints; the whitepaper mentions a Machina bond. | The official Hackathon 1 boilerplate, ABI, harness, and guides, which are not yet public; H1 Machina bond has been removed. | Keep the local semantic proxy and Brier benchmark separate. Do not register WASM until the H1 harness is released. |
| Flow vs Workflow | Paid Intent flow ends in routing/validation/receipt; stateful orchestration belongs to the client. | Track 3 must use real Telegraph Miners and genuine requests. | Keep payment/routing in the protocol adapter and case state, decisions, and outcome resolution in the Application. |
| Mainnet / testnet | Phase 1 Base Sepolia, Phase 2 Base mainnet. | The registration repository targets Base Sepolia; the current consumption challenge targets Solana Devnet. Both are test environments and must not be conflated. | No mainnet wallet, mainnet contract, escrow, or MPC path is enabled. Use a dedicated Solana-devnet wallet only for current paid canaries. |

Sources: [Telegraph Protocol whitepaper](https://telegraphprotocol.com/Whitepapers%20-%20Telegraph%20Protocol.pdf), [official hackathon rules](https://hackathon.telegraphprotocol.com/rules), and the [Miner registration portal](https://integrate.telegraphprotocol.com/).
