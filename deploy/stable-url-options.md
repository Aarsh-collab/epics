# Stable public URL options (issue #18)

## Decision

Choose **Tailscale Funnel**, conditional on the owner's approval and an
appropriate account plan. It avoids a domain purchase and separate DNS setup,
fits the current budget, and uses the existing Tailscale connection on Spark.
This matches the [issue discussion](https://github.com/Aarsh-collab/epics/issues/18#issuecomment-6890873158).
Lab approval and restored access remain pending. Scripts do not grant either.

- **Funnel:** available on all Tailscale plans, but the applicable plan must
  permit institutional use and may have a cost. No purchased domain or public
  IP is needed. Requires a Tailscale account, owner-approved node, MagicDNS,
  HTTPS and Funnel policy. Setup: approve/configure the node, proxy its local
  app with background Funnel, verify public HTTPS and transfers. On Spark the
  lab controls these permissions; on Jetstream a team-controlled tailnet is an
  option. Uses a ts.net address, with TLS at the node; beta and non-configurable
  bandwidth limits require testing with uploads/downloads. DuckDNS does not
  supply a custom Funnel hostname.
- **Cloudflare named tunnel:** tunnel publication is available without a paid
  Access plan; a domain may have registration costs unless provided. Requires
  a Cloudflare account and compatible authorized domain/DNS configuration.
  Setup: create the tunnel and published route, protect the connector token
  in a mode-600 local file and start it. Works on Spark and Jetstream without
  inbound public ports. TLS terminates at Cloudflare. Not selected because
  suitable domain access is unconfirmed and Funnel avoids that dependency.
  Cloudflare Access seat limits are separate from ordinary public website
  visitor counts; do not interpret a 50-user Access plan as a 50-visitor cap.
- **Caddy:** free software and automatic HTTPS; public VM/IP and domain costs
  depend on institutional resources. Requires server/admin permission,
  suitable DNS and inbound ports 80/443 for the standard setup. Setup: point
  DNS at the public VM, configure firewall and proxy, persist certificate
  storage. Not directly usable on private Spark; a strong Jetstream alternative
  if public networking/hostname are supplied. Revisit when the allocation is
  known. It is not inherently more expensive than Funnel.

## Delivery and remaining verification

See [Spark setup and rollback](spark/README.md). Quick Cloudflare tunneling is
preserved as the default until a local Funnel opt-in exists and as an explicit
rollback. No Cloudflare named-tunnel token is needed for the chosen option.

Only local scripts/docs and offline tests are delivered now. Approval,
authorized server access, public TLS/passcode checks, upload/download performance,
and two app-container restarts must be verified before issue #18 is complete.
Only test photos are permitted pending consent/storage approvals. Nothing here
changes the lab tailnet, machine enrollment, other sharing routes or services.

## Primary sources

- [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
- [Funnel CLI and persistence](https://tailscale.com/docs/reference/tailscale-cli/funnel)
- [Cloudflare published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)
- [Caddy HTTPS setup](https://caddyserver.com/docs/quick-starts/https)

