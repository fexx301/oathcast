# Miner 212 Signal-fix canary

- Result: paid request completed with verified settlement
- Operation: `signal-fix-execute-20260906-01`
- Route: `https://devnode.telegraphprotocol.com/miner-dispatcher/v1/212/forecast`
- Parameters: `q=6.524400,3.379200`, `days=1`
- HTTP response: `200`

## Signal observation

- Public `signal_hash` in the paid response: `not present`
- Opaque Signal receipt header digest: `not present`
- Explorer reconciliation: no Miner 212 forecast match in the checked recent pages

## Payment verification

- Network: `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1`
- Amount: `10000` micro-USDC
- Signer: `2BGoFrhXmt6sDR9N6CNfgKtepbvkjJB6TuV53Tpvv5yw`
- Transaction: `N2YWKfJ1bpxzWq6PdYJ4UzmpEAuArs8G6JxmPjVPbPzSNkWwHRM3DuwytLWyz3hTyTLtjiXUEDRjt9KzxCHAbSB`
- On-chain status: confirmed; token movement and fee payer verified

This sanitized artifact contains no private key, payment authorization header, or raw paid response.
