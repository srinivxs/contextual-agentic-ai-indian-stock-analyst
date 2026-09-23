<#
.SYNOPSIS
  Switch the AWS demo off: destroy infra/stack, then prove nothing billable is left.

.DESCRIPTION
  Run it yourself after every session, in Windows PowerShell 5.1 or PowerShell 7:

      & "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-down.ps1"

  It destroys ONLY infra/stack. The edge (the permanent URL registered with Google), the registry
  and the state bucket are never touched: no script can reach them (see Invoke-Terraform).
  Terraform shows the plan and waits for `yes`; the plan should say about 49 to destroy.

  Afterwards the site still serves and /api/* returns 502: that pair is the correct resting state.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')

Initialize-DemoSession

Write-Step '1/2  Destroy the application stack (type yes when the plan says about 49 to destroy)'
Invoke-Terraform -Root 'stack' -Arguments @('destroy')

Write-Step '2/2  Check nothing billable survived'
if (Test-NothingBillable) {
    Write-Host ''
    Write-Host 'The demo is off. Nothing left costs anything by the hour.' -ForegroundColor Green
} else {
    Write-Host ''
    Write-Host 'Something survived the destroy: it is still billing. Re-run this script, or delete it by hand.' -ForegroundColor Red
    exit 1
}
