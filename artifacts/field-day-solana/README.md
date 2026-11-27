# Field-day Settle: real runs on Solana devnet (26 Nov 2026)

Four jobs run through the app against the live Telegraph node and Solana devnet, on real past days. Each job: a
deposit into escrow, two Telegraph verifications each paid with Solana x402 ($0.01), and a settlement whose memo
carries the evidence. `ledger-2026-11-26.jsonl` is the app's append-only ledger for these runs (no keys in it).
The first three ran with Counterproof's 731004 as the cross-check; after they agreed to the decimal with our 64174
(both read the ERA5 archive), the cross-check moved to SecondSky's 77031 (NASA POWER), and the fourth job is the
first real disagreement: a HOLD.

| Job | Work, place, day | Ranked route | Cross-check | Outcome | Deposit | Settlement |
|---|---|---|---|---|---|---|
| `fds-775c19d12234` | spray, Lleida, Spain, 2026-11-20 | 64174: 0 mm ([paid](https://explorer.solana.com/tx/5BzT7U6eLswUxcJxipLfQg2dM5DQQ3yoTbCG6snLpYDSgLydb2MTFYuVGYpbq4idBcSZqeame2CqJaBzHL5A9KVY?cluster=devnet)) | 731004: 0 mm ([paid](https://explorer.solana.com/tx/39cU5ob5HVLLSvc6bK838xTdaBKik4wFHyQ4rPwLu8yWALLVoLr2ur9aA2FVyRB9E24QwaZSBiirWVMrmMWSVwg?cluster=devnet)) | **RELEASE** | [24BkdaSv…](https://explorer.solana.com/tx/24BkdaSvoqJZ8TRExb8ZPVDggUb2ZPYXbba7Uuwcx3degD73SsHZBVrnacKtKcXeJS2jVXoez1qZV9325o9hMYCG?cluster=devnet) | [2XZ8fV1m…](https://explorer.solana.com/tx/2XZ8fV1mM4JkwCV632wxKmUkTvzqH6fwwECA3B97nH8A7rkFAmEzg1GKBhwJqE2eR7pmq5ibCabckyVctP2gaoFf?cluster=devnet) |
| `fds-410333001bd7` | harvest, Galway, Ireland, 2026-11-17 | 64174: 17.4 mm ([paid](https://explorer.solana.com/tx/E6gMzzxWdQdSr4yBt9vqS34bGEeHtuCdjoyP7uXB3UTbCAKt9o3zEhBNsH47DHgedLDzmhRRPHUFfrkRH577Ju7?cluster=devnet)) | 731004: 17.4 mm ([paid](https://explorer.solana.com/tx/3E3p2AcvvyBhXkdLrBu5tiioY6swu8C1JTy9Zgh3K1GwqsNmuwbM8Rt5jm8nHt6ZpRPKLddUQGUi7SSTZ6Jzw39E?cluster=devnet)) | **REFUND** | [5ccmScmY…](https://explorer.solana.com/tx/5ccmScmYNKw6e9Uz18dNneVBZycZowtix2pRta4gjrZ6XQr9znVUsgvpdwiuPB8pNQjQGsSRS4b9MkmxXvhYqHPR?cluster=devnet) | [3K536CAj…](https://explorer.solana.com/tx/3K536CAjLkFmhs4LnUmkqphamzJ1RXcuBen8JdJjQTK1HsyzZnTLTpnCEaSgxU2H4f3WSr3L1P8a1DTzCn3xzunv?cluster=devnet) |
| `fds-ec871f93df5d` | spray, Cork, Ireland, 2026-11-20 | 64174: 0.7 mm ([paid](https://explorer.solana.com/tx/bPCKx5oRzFdW66KvpJugxveAAdEYahSRXSmw1yseTveGYyq2KNoX7P4BY7g277UKcPwALgGiyggUJ7rmYojcqCm?cluster=devnet)) | 731004: 0.7 mm ([paid](https://explorer.solana.com/tx/49knjvBAWeYke21mc3UAGn9fJqiCpR3rece3XemknwDx35NdbxuSQJ5TZuzxqGhLMYfT7B7BSzsvG9PQRdTMyQ3J?cluster=devnet)) | **RELEASE** | [64goVSbB…](https://explorer.solana.com/tx/64goVSbBpvzC5X3TpdSJCcyThyA6D5zKvAbwQphUUtadAZMTQcrt9CnQy16H3FA8PzSrrZg1SW9zJEh9FnrLVDqA?cluster=devnet) | [5tL6dJmE…](https://explorer.solana.com/tx/5tL6dJmEyVCFUFmYEy6zSCxqZBif6qTY7gEpcc4jARfsc5jrYUvkBtBT5tvVFAMffCSc1dgzKPj1EWa8r8WVNaup?cluster=devnet) |
| `fds-f01866a67411` | spray, Lleida, Spain, 2026-11-18 | 64174: 3.7 mm ([paid](https://explorer.solana.com/tx/2XgZSSTCgYGXFQwVoBYUNXVfANwG6a9xi88GFwtHaZYyhP8QGGG5Hasgji9R6K99DaF1USLwSJXjKoaTdiJGMrdL?cluster=devnet)) | 77031: 0.62 mm ([paid](https://explorer.solana.com/tx/57yGWX7bWYiBnpshqF5kNpHLDCjUgUnvsnYYvNSAxfMhBQnvUPANrq9bkh7CYmMV9C2sXUxCemizNDNq3cX5DJXw?cluster=devnet)) | **HOLD** | [4rQta2ME…](https://explorer.solana.com/tx/4rQta2MER8mTgh9BMheCjrwUEgA6fHifvo72dGC8DtiAho6eB9vcEGagF31aSYPFFPtagMN4nBrWttipbQNwibWA?cluster=devnet) | [AqH4CZo4…](https://explorer.solana.com/tx/AqH4CZo4wSZdKhJt4dn8EE9Ag36ytouGNv8a2p38kAXqnEyNCDPARBQtJiGkrtV9yB9ohP7y8zbvNWEtpPy1SqY?cluster=devnet) |

Audit any settlement from its transaction alone (`GET /field-day-solana/api/audit?tx=<signature>`): the transaction
succeeded; the decision follows from the memo's readings under the published rule; the USDC moved as the decision
says (to the contractor, back to the buyer, or not at all); and each reading matches its Telegraph signal record,
fetched by hash, including the miner that answered. All four pass every check (checked 26–27 Nov).

Wallets (devnet): operator / demo buyer `2BGoFrhXmt6sDR9N6CNfgKtepbvkjJB6TuV53Tpvv5yw`; escrow agent
`FBwcvbwpzXD8AhACvEHkzy4MMjHF4KRZ31uDYrjULkKH`; test contractor `EdXXxivUbLJNN1qqnYrKn3T6mfUmg5E3o9i6W2GXj1Qb`.
The HOLD (`fds-f01866a67411`) is left unresolved: resolving it is a person's call.
