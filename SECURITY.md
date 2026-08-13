# Security policy

OathCast handles API keys, wallet configuration, payment artifacts, and local
forecast receipts. Never open a public issue containing a credential, private
key, wallet material, or sensitive operational detail.

## Reporting

Use GitHub's private vulnerability reporting or a private contact method to
report a suspected security issue. Include reproduction steps and impact, but
redact all secrets. Do not test against the public staging service beyond the
documented, non-destructive canary behavior.

## Repository rules

- Keep runtime secrets in the host secret store or GitHub Actions secrets.
- Do not commit `.env` files, databases, wallet files, private keys, or API
  tokens. The repository `.gitignore` covers the local forms of these files.
- Treat a settlement header alone as unverified. The payment canary performs
  independent Solana RPC verification, but that isolated check is not yet a
  production Application payment boundary.
- Keep payment tests and development fixtures separate from live Telegraph
  traffic.
