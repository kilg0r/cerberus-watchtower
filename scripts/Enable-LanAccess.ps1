<#
.SYNOPSIS
    Expose Watchtower to the home LAN and the tailnet (e.g. an iPhone browser).

.DESCRIPTION
    Watchtower itself stays bound to 127.0.0.1:8765. This adds a Windows port
    proxy (IP Helper service, survives reboots, no extra process) listening on
    0.0.0.0:8766 and forwarding to it, plus one inbound firewall rule for TCP
    8766 limited to the local subnet and Tailscale's CGNAT range. The Wi-Fi is
    classified Public, so the rule covers every profile; the address scope is
    what keeps it to this network.

    Watchtower has no login: anything that can reach 8766 can read sessions,
    diffs and processes and trigger AI summaries. Keep the scope as it is.

    Needs an elevated shell.

.PARAMETER Disable
    Remove the port proxy and the firewall rule.
#>
param(
    [switch]$Disable,
    [int]$ListenPort = 8766,
    [int]$TargetPort = 8765
)

$RuleName = "Cerberus Watchtower (TCP $ListenPort, LAN + Tailscale)"

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'Run this from an elevated (Administrator) shell.'
    exit 1
}

if ($Disable) {
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort | Out-Null
    Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    Write-Host "Watchtower LAN access removed (port $ListenPort)." -ForegroundColor Yellow
    return
}

# the port proxy lives in the IP Helper service
$iphlp = Get-Service iphlpsvc
if ($iphlp.StartType -eq 'Disabled') { Set-Service iphlpsvc -StartupType Automatic }
if ($iphlp.Status -ne 'Running') { Start-Service iphlpsvc }

# idempotent: replace any earlier mapping/rule
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort 2>&1 | Out-Null
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort connectaddress=127.0.0.1 connectport=$TargetPort | Out-Null

Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $RuleName `
    -Description 'Watchtower dashboard via port proxy to 127.0.0.1:8765. Remove with scripts\Enable-LanAccess.ps1 -Disable.' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ListenPort `
    -RemoteAddress LocalSubnet, '100.64.0.0/10' -Profile Any | Out-Null

$lan = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -in 'Dhcp', 'Manual' -and $_.InterfaceAlias -notmatch 'vEthernet|Tailscale|Loopback' } |
    Select-Object -First 1).IPAddress
$ts = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'Tailscale' -ErrorAction SilentlyContinue).IPAddress

Write-Host 'Watchtower reachable at:' -ForegroundColor Green
if ($lan) { Write-Host "  LAN        http://${lan}:$ListenPort" }
if ($ts) { Write-Host "  Tailscale  http://${env:COMPUTERNAME}:$ListenPort  (or http://${ts}:$ListenPort)".ToLower() }
