# Stable public URL options (issue #18)

## Decision (2026-11-18)

Use a **Cloudflare named tunnel on a domain the team buys**, set up when we move
to Jetstream. Until then the Spark keeps the Cloudflare quick tunnel
(`deploy/spark/tunnel.sh`), whose URL changes on every restart.

Tailscale Funnel was implemented and then removed: the Spark is on the lab's
tailnet, which is not ours to publish through.

## Options compared

- **Cloudflare named tunnel (chosen):** needs a Cloudflare account and a domain
  whose DNS is on Cloudflare (registration cost). No paid Access plan is needed
  to publish the app, and no inbound ports are opened on the VM. The connector
  token must live in a mode-600 file outside the repo. TLS ends at Cloudflare, so
  test photos only until consent and storage approvals exist. Cloudflare Access
  seat limits are separate from ordinary visitor counts.
- **Tailscale Funnel (rejected):** no domain needed, but it publishes through a
  tailnet. On the Spark that tailnet belongs to the lab.
- **Caddy on a public-IP VM:** free, automatic HTTPS with TLS ending on our VM.
  Needs a public IP, a domain and inbound ports 80/443. A fallback if Jetstream
  gives us public networking and we prefer not to route through Cloudflare.

## Still to do (on Jetstream)

Buy the domain, create the named tunnel and its route, store the token in a
mode-600 file, add a start script next to `tunnel.sh`, and verify that HTTPS,
the passcode gate and the URL all survive app and VM restarts.

## Sources

- [Cloudflare published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)
- [Caddy HTTPS setup](https://caddyserver.com/docs/quick-starts/https)
