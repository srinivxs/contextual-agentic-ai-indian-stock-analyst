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

  Costs about $0.06 an hour from step 1 until scripts\demo-down.ps1 has finished.

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

Write-Step '1/5  Apply the application stack (type yes when the plan looks right: about 49 to add)'
$stackArgs = @('apply')
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
Write-Host 'It costs about $0.06 an hour. When you are done:' -ForegroundColor Yellow
Write-Host '    & "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-down.ps1"' -ForegroundColor Yellow
