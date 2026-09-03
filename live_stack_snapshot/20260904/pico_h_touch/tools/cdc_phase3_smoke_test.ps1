[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $PortName
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
    Start-Sleep -Milliseconds 500

    $lines = New-Object System.Collections.Generic.List[string]
    while ($true) {
        try {
            $lines.Add($Port.ReadLine().Trim())
        } catch [TimeoutException] {
            break
        }
    }

    $commands = @(
        "HELLO 1",
        "PING 300",
        "STATUS 301",
        "CANCEL 302",
        "STATUS 303"
    )

    foreach ($cmd in $commands) {
        $lines.Add("# > $cmd")
        $Port.WriteLine($cmd)
        $lines.Add($Port.ReadLine().Trim())
    }

    $lines
} finally {
    if ($Port.IsOpen) {
        $Port.Close()
    }
}
