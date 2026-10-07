<#
.SYNOPSIS
  Switch the AWS demo off: save the database, destroy infra/stack, prove nothing billable is left.

.DESCRIPTION
  Run it yourself after every session, in Windows PowerShell 5.1 or PowerShell 7:

      & "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-down.ps1"

  It destroys ONLY infra/stack. The edge (the permanent URL registered with Google), the registry
  and the state bucket are never touched: no script can reach them (see Invoke-Terraform).
  Terraform shows the plan and waits for `yes`; the plan should say about 49 to destroy.

  The destroy first saves the database as a snapshot (the name demo-up chose; a few minutes more),
  so the next demo-up restores it instead of starting empty. The newest two saves are kept.

  Afterwards the site still serves and /api/* returns 502: that pair is the correct resting state.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')

Initialize-DemoSession

Write-Step '1/3  Save the database, then destroy the application stack (type yes: about 49 to destroy)'
Invoke-Terraform -Root 'stack' -Arguments @('destroy')

Write-Step '2/3  Keep the newest two saved databases'
Remove-OldDemoSnapshots

Write-Step '3/3  Check nothing billable survived'
if (Test-NothingBillable) {
    Write-Host ''
    Write-Host 'The demo is off. Nothing left costs anything by the hour; the saved database costs a'
    Write-Host 'few cents a month, and the next demo-up restores it.' -ForegroundColor Green
} else {
    Write-Host ''
    Write-Host 'Something survived the destroy: it is still billing. Re-run this script, or delete it by hand.' -ForegroundColor Red
    exit 1
}
