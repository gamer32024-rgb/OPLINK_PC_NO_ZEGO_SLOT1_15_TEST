import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

let offer = [
    "v=0",
    "m=video 9 UDP/TLS/RTP/SAVPF 96",
    "a=ice-ufrag:unchanged",
    "a=ice-pwd:unchanged",
    "a=candidate:1 1 udp 123 192.0.2.1 8189 typ host",
    "a=candidate:2 1 udp 123 100.100.20.30 8189 typ host",
    "a=candidate:3 1 tcp 123 fd7a:115c:a1e0::123 9 typ host tcptype active",
    "a=end-of-candidates",
    ""
].joined(separator: "\r\n")
let result = TailnetCandidateFilter.filter(offer)
require(result.changed, "Tailscale candidates should be preferred")
require(!result.sdp.contains("192.0.2.1"), "unrelated candidate must be removed")
require(result.sdp.contains("100.100.20.30"), "Tailscale IPv4 must be kept")
require(result.sdp.contains("fd7a:115c:a1e0::123"), "Tailscale IPv6 must be kept")
require(result.sdp.contains("a=ice-pwd:unchanged"), "ICE credentials must be preserved")
require(result.sdp.hasSuffix("\r\n"), "SDP line endings must be preserved")
let noTailnet = "v=0\r\nm=video 9 x\r\na=candidate:1 1 udp 1 192.0.2.1 9 typ host\r\n"
let unchanged = TailnetCandidateFilter.filter(noTailnet)
require(!unchanged.changed && unchanged.sdp == noTailnet, "offers without Tailscale candidates must be unchanged")
require(TailnetCandidateFilter.isTailnetHost("pc.example.ts.net"), "MagicDNS host must be eligible")
require(TailnetCandidateFilter.isTailnetHost("100.100.20.30"), "Tailscale IPv4 host must be eligible")
require(!TailnetCandidateFilter.isTailnetHost("example.com"), "unrelated host must not be filtered")
print("Tailnet candidate filter tests passed")
