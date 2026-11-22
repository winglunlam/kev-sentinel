# Security

## Reporting

Report a vulnerability privately with GitHub's security advisory form
(`Security` → `Report a vulnerability`) or by email to the maintainers listed on
the repository profile. Please do not open a public issue for a live
vulnerability.

Include the version (`sentinel --version`), the config shape (redact URLs and
tokens), and a reproduction. Expect an acknowledgement within a few days.

## What the tool handles

- **Endpoint payloads.** Payloads are fetched by the machine running `sentinel`
  and sent to the Kev server configured in `sem.base_url`. With the default
  config that is a local process; if you point it at a remote server, payloads
  leave the machine.
- **Secrets.** Headers, API keys, and webhook URLs are read from the config and
  from environment variables (`SENTINEL_KEV_API_KEY`, `SENTINEL_WEBHOOK_URL`).
  Do not commit real tokens into `sentinel.yaml`; use `${VAR}` expansion in the
  config instead.
- **Webhooks.** Alert payloads contain endpoint names, timestamps, and failure
  reasons, never the payload body. The webhook URL is called with a short
  timeout and its failures are ignored.
- **State.** Baselines and run history are stored in `.sentinel/sentinel.db`
  next to the config. That database can contain recorded response bodies; treat
  it as sensitive if your payloads are.

## Supported versions

The project is at 0.x. Security fixes land on `main`; pin a commit or tag if you
need stability.
