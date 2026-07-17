# Security boundary

The source code may be public. Access to the running service is still controlled by the user's Tailnet membership and Tailscale ACLs.

Public source does not make every local file safe to publish. Never commit:

- Tailscale auth keys, OAuth credentials, ACL exports, or private Tailnet configuration.
- TLS private keys or certificates generated on the Windows host.
- Apple signing identities, provisioning profiles, or account credentials.
- ZEGO credentials copied from the older app.
- Runtime logs, hostnames, game account data, screenshots, or recorded scripts.

GitHub Actions does not join the Tailnet. The built iOS app receives the host URL from the user at runtime and can connect only when the device already has access through Tailscale.

The input endpoint also requires a random pairing token generated at each Windows host start. The token is stored only under the ignored `host/runtime` directory and must not be pasted into GitHub.
