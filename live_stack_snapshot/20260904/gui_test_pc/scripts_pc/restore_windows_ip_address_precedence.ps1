$ErrorActionPreference = "Stop"

# Restore the Windows default precedence used before GUI_TEST_PC setup.
& netsh.exe interface ipv6 set prefixpolicy prefix=::ffff:0:0/96 precedence=35 label=4 store=persistent
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restore the IPv4-mapped prefix policy."
}

& netsh.exe interface ipv6 show prefixpolicies

