# Allow Game Shop's port through Windows Firewall (run as administrator).
param([int]$Port = 8750)
$name = "Game Shop ($Port)"
if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
    Write-Host "Rule '$name' already exists."
} else {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private,Public | Out-Null
    Write-Host "Added inbound rule '$name' for Private and Public networks."
}
