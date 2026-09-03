$ErrorActionPreference = "Stop"

# Keep IPv6 enabled, but prefer IPv4 when both DNS record families are present.
& netsh.exe interface ipv6 set prefixpolicy prefix=::ffff:0:0/96 precedence=46 label=4 store=persistent
if ($LASTEXITCODE -ne 0) {
    throw "Failed to set the IPv4-mapped prefix policy."
}

& netsh.exe interface ipv6 show prefixpolicies

