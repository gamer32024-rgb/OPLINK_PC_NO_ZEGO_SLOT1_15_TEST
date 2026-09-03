[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $PortName,
    [uint32] $Sequence = 900
)

$ErrorActionPreference = "Stop"

$Port = New-Object System.IO.Ports.SerialPort $PortName,115200,'None',8,'One'
$Port.NewLine = "`n"
$Port.ReadTimeout = 2000
$Port.WriteTimeout = 2000
$Port.DtrEnable = $true
$Port.RtsEnable = $true

try {
    $Port.Open()
    Start-Sleep -Milliseconds 200
    while ($true) {
        try {
            [void]$Port.ReadLine()
        } catch [TimeoutException] {
            break
        }
    }

    $Port.WriteLine("HELLO 1")
    $ready = $Port.ReadLine().Trim()
    if ($ready -notmatch '^READY proto=1 .*hid=1$') {
        throw "Unexpected HELLO response: $ready"
    }

    $Port.WriteLine("BOOTSEL $Sequence")
    $response = $Port.ReadLine().Trim()
    if ($response -ne "ACK $Sequence BOOTSEL pending=1") {
        throw "Unexpected BOOTSEL response: $response"
    }

    [pscustomobject]@{
        Port = $PortName
        Ready = $ready
        Response = $response
    }
} finally {
    if ($Port.IsOpen) {
        $Port.Close()
    }
}
