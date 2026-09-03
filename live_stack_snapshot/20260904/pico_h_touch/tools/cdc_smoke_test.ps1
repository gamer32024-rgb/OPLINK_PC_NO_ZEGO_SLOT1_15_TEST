[CmdletBinding()]
param(
    [string] $PortName = "COM3"
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
    $lines.Add("# initial")
    while ($true) {
        try {
            $lines.Add($Port.ReadLine().Trim())
        } catch [TimeoutException] {
            break
        }
    }

    $commands = @(
        "HELLO 1",
        "PING 200",
        "STATUS 201",
        "RESET 202",
        "PING 203",
        "PING 203",
        "PING 100"
    )

    foreach ($cmd in $commands) {
        $lines.Add("# > $cmd")
        $Port.WriteLine($cmd)
        try {
            $lines.Add($Port.ReadLine().Trim())
        } catch [TimeoutException] {
            $lines.Add("TIMEOUT after $cmd")
        }
    }

    $lines
} finally {
    if ($Port.IsOpen) {
        $Port.Close()
    }
}
