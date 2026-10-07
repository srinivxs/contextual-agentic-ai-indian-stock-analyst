<#
.SYNOPSIS
  Switch the AWS demo on: the runbook's "Switching it on" steps 1 to 6 in one command.

.DESCRIPTION
  Run it yourself, from any directory, in Windows PowerShell 5.1 or PowerShell 7:

      & "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-up.ps1"

  Terraform shows each plan and waits for you to type `yes`: nothing is applied without you. The
  plan is never saved to a file, because a plan file holds the Google client secret in plaintext.

    1. apply infra/stack (about 7 minutes; RDS is most of it)
    2. create the runtime database role (the provision task) and the schema (the migrate task)
    3. point CloudFront at the new load balancer (1 to 7 minutes)
    4. start the api (desired count 1) and wait until ECS calls it stable
    5. wait until https://<edge>/api/readyz answers {"status":"ready"}

  The database is restored from the newest save demo-down made, so the app has its data within a
  few minutes; with no save yet it starts empty and the worker refills it (30 to 45 minutes, about
  $0.56 of Bedrock). Costs about $0.10 an hour from step 1 until scripts\demo-down.ps1 has finished.

.PARAMETER ImageTag
  A full commit SHA to run instead of the newest image CI pushed.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ImageTag
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')

Initialize-DemoSession
$started = Get-Date

# The newest saved database to restore, and the name the next demo-down saves it under.
$restoreFrom = Get-LatestDemoSnapshot
$saveAs = New-DemoSnapshotName
if (Test-DemoDatabaseRunning) {
    Write-Host 'The database is already running: it is kept exactly as it is.'
} elseif ($restoreFrom) {
    Write-Host "Restoring the database saved as $($restoreFrom): only what is new gets fetched."
} else {
    Write-Host 'No saved database yet: it starts empty and the worker fills it (30 to 45 minutes).'
}

Write-Step '1/5  Apply the application stack (type yes when the plan looks right: about 59 to add)'
$stackArgs = @('apply', "-var=db_final_snapshot_identifier=$saveAs")
if ($restoreFrom) { $stackArgs += "-var=db_snapshot_identifier=$restoreFrom" }
if ($ImageTag) { $stackArgs += "-var=image_tag=$ImageTag" }
Invoke-Terraform -Root 'stack' -Arguments $stackArgs
$outputs = Get-StackOutputs
$network = 'awsvpcConfiguration={subnets=[' + ($outputs.public_subnet_ids -join ',') +
    '],securityGroups=[' + $outputs.task_security_group_id + '],assignPublicIp=ENABLED}'

Write-Step '2/5  Create the runtime database role, then the schema'
# The runtime role first: it is idempotent and order-independent, and without it every real query
# fails. Both must succeed before the api starts, or it would start against an empty database.
Invoke-OneOffTask -Family 'stock-analyst-demo-provision' -Network $network
Invoke-OneOffTask -Family 'stock-analyst-demo-migrate' -Network $network

Write-Step '3/5  Point CloudFront at the new load balancer (type yes: 1 to change)'
Invoke-Terraform -Root 'edge' -Arguments @('apply', "-var=alb_origin_domain=$($outputs.alb_dns_name)")

Write-Step '4/5  Start the api'
Invoke-Aws -Arguments @('ecs', 'update-service', '--cluster', $DemoCluster, '--service', $DemoService,
    '--desired-count', '1', '--query', 'service.desiredCount') | Out-Null
Invoke-Aws -Arguments @('ecs', 'wait', 'services-stable', '--cluster', $DemoCluster, '--services', $DemoService)

Write-Step '5/5  Wait for /api/readyz through CloudFront'
Wait-DemoReady

$minutes = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
Write-Host ''
Write-Host "The demo is up after $minutes minutes: $DemoUrl" -ForegroundColor Green
if ($restoreFrom) {
    Write-Host "The database came back from $($restoreFrom): the worker only fetches what is new since"
    Write-Host 'then (new filings, the missing days of prices), usually a few minutes.'
} else {
    Write-Host 'The database starts empty: the worker now fetches the filings, prices and RBI releases'
    Write-Host 'and reads them (about 30 to 45 minutes before search, facts and chat have everything).'
}
Write-Host 'It costs about $0.10 an hour. When you are done:' -ForegroundColor Yellow
Write-Host '    & "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-down.ps1"' -ForegroundColor Yellow
