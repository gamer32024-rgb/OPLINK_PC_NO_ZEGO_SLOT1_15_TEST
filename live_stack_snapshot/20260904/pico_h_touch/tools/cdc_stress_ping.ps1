[CmdletBinding()]
param(
    [string] $PortName = "COM4",
    [int] $Count = 1000,
    [int] $StartSeq = 1000
)

$ErrorActionPreference = "Stop"

$Port = New-Object System.IO.Ports.SerialPort $PortName,115200,'None',8,'One'
$Port.NewLine = "`n"
$Port.ReadTimeout = 2000
$Port.WriteTimeout = 2000
$Port.DtrEnable = $true
$Port.RtsEnable = $true

$Started = Get-Date
$Failures = New-Object System.Collections.Generic.List[string]

try {
    $Port.Open()
    Start-Sleep -Milliseconds 500

    while ($true) {
        try {
            [void]$Port.ReadLine()
        } catch [TimeoutException] {
            break
        }
    }

    for ($i = 0; $i -lt $Count; $i++) {
        $seq = $StartSeq + $i
        $cmd = "PING $seq"
        $expected = "ACK $seq PONG"

        $Port.WriteLine($cmd)
        try {
            $line = $Port.ReadLine().Trim()
            if ($line -ne $expected) {
                $Failures.Add("seq=$seq expected='$expected' actual='$line'")
            }
        } catch [TimeoutException] {
            $Failures.Add("seq=$seq timeout")
        }
    }
} finally {
    if ($Port.IsOpen) {
        $Port.Close()
    }
}

$Elapsed = (Get-Date) - $Started
[pscustomobject]@{
    Port = $PortName
    Count = $Count
    StartSeq = $StartSeq
    Failures = $Failures.Count
    ElapsedMs = [int]$Elapsed.TotalMilliseconds
}

if ($Failures.Count -gt 0) {
    $Failures | Select-Object -First 20
    exit 1
}
